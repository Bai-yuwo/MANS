"""
core/base_agent.py

主管(Manager)基类 — 跑 ReAct 循环、对外编排、负责 KB 写入。

为什么主管要继承本类:
    1. ReAct 循环骨架在所有主管中是一样的:首轮 [system, user] → 收 LLM 流 →
       完成 → 检查 tool_calls → 派发 → 用 function_call_output 续接 → 再发一轮。
       重复实现 5 次没意义。
    2. previous_response_id 续接、流式包转发、子专家流式注入、超过 max_turns 兜底,
       这些细节在每个主管里都一样,集中到基类后子类只需关心"我的 system prompt + tool_scope"。
    3. 子专家(Writer 是流式)的 token 转发需要 sink 注入与队列协同,把这层复杂度藏在
       基类里,业务主管不应感知。

子类必须声明:
    agent_name           : 主管名(PascalCase),必须在 AGENT_DEFINITIONS 且 kind="manager"
    description          : 一句自然语言说明(给 Director 看,或前端展示)
    system_prompt_path   : system prompt 文件路径(相对 prompts/ 根)
    tool_scope           : tool 名列表(KB 共享读 + 自身写组 + 可调专家)

可选:
    user_prompt_template : 默认 user prompt 模板(子类可不声明,直接传 raw user_prompt)
    max_turns            : 最大 ReAct 轮数(默认 20,触发即兜底退出)
    thinking             : 思考模式("enabled" / "disabled",默认 "enabled")

主入口:
    run(user_prompt: str, **context) -> AsyncIterator[StreamPacket]
        # 主管执行一次任务,流式 yield 所有 packets(包括子专家的)。
        # context 用于 Jinja2 模板渲染(若声明了 user_prompt_template)。

参考:`D:\\AI协作任务\\NovelAgent\\main.py` 的 run_agent 循环骨架。
"""

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, ClassVar, Optional

from core.base_tool import BaseTool
from core.config import AGENT_DEFINITIONS, get_config
from core.context import require_current_project_id
from core.expert_tool import (
    _read_prompt_file,
    _render_template,
)
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.stream_packet import CompletedPayload, ConfirmPayload, StreamPacket, ToolCallData
from core.tool_manager import ToolManager, get_tool_manager

logger = get_logger("core.base_agent")


class BaseAgent:
    """
    主管基类。子类只声明类属性即可获得完整 ReAct 能力。

    线程模型:
        每次 `run()` 调用是独立的协程任务,内部 LLMClient 与 ToolManager 是单例,
        但 ExpertTool 的 _stream_sink 是实例字段,**多个主管同时调用同一个 ExpertTool
        会发生 sink 互相覆盖**——P0 阶段不解决并发主管(单用户 web 通常无此问题),
        将来如果支持并发主管,需要给 ExpertTool 改成"每次 execute 创建副本"的模式。

    用法:
        class WorldArchitect(BaseAgent):
            agent_name = "WorldArchitect"
            description = "INIT 阶段世界观主管"
            system_prompt_path = "system/managers/WorldArchitect.j2"
            tool_scope = [
                "read_project_meta", "read_bible",
                "save_bible", "append_foreshadowing",
                "call_geographer", "call_rule_smith",
            ]

        agent = WorldArchitect()
        async for packet in agent.run(user_prompt="开始构建《XX》的世界观"):
            ...  # 转发到 SSE
    """

    # 子类必须覆盖
    agent_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    system_prompt_path: ClassVar[str] = ""
    tool_scope: ClassVar[list[str]] = []

    # 子类可选覆盖
    user_prompt_template: ClassVar[Optional[str]] = None
    max_turns: ClassVar[int] = 20
    thinking: ClassVar[str] = "enabled"

    # 内部
    _shared_client: ClassVar[Optional[LLMClient]] = None

    def __init__(self, tool_manager: Optional[ToolManager] = None):
        self._validate_class_attrs()
        self.config = get_config()
        self.runtime = self.config.get_for_agent(self.agent_name)
        self.tool_manager = tool_manager or get_tool_manager()
        # 最近一次 run 的状态(便于 Orchestrator/调试访问)
        self.last_response_id: str = ""
        self.last_total_tokens: int = 0
        self.last_turns: int = 0
        # _dispatch_tools 与 run 之间的临时存储,实例级避免并发主管互相覆盖
        self._last_dispatch_outputs: list[dict] = []
        # 同一工具连续失败计数(熔断保护)
        self._tool_failure_counts: dict[str, int] = {}
        self._max_tool_failures: int = 3

    # --------------------------------------------------------
    # 启动期校验
    # --------------------------------------------------------
    def _validate_class_attrs(self) -> None:
        cls_name = type(self).__name__
        if not self.agent_name:
            raise ValueError(f"BaseAgent 子类 {cls_name} 未声明 agent_name")
        if self.agent_name not in AGENT_DEFINITIONS:
            raise ValueError(
                f"BaseAgent 子类 {cls_name} 的 agent_name='{self.agent_name}' "
                f"未注册到 AGENT_DEFINITIONS,可用主管:"
                f"{[n for n, s in AGENT_DEFINITIONS.items() if s['kind'] == 'manager']}"
            )
        if AGENT_DEFINITIONS[self.agent_name]["kind"] != "manager":
            raise ValueError(
                f"BaseAgent 子类 {cls_name} 的 agent_name='{self.agent_name}' "
                f"在 AGENT_DEFINITIONS 中是 expert,应使用 ExpertTool"
            )
        if not self.system_prompt_path:
            raise ValueError(f"BaseAgent 子类 {cls_name} 未声明 system_prompt_path")
        if not self.tool_scope:
            logger.warning(
                f"BaseAgent 子类 {cls_name} 的 tool_scope 为空,"
                f"主管将无法调用任何工具(只会单轮回答)"
            )

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    async def run(
        self,
        user_prompt: Optional[str] = None,
        *,
        previous_response_id: Optional[str] = None,
        max_turns: Optional[int] = None,
        **context,
    ) -> AsyncIterator[StreamPacket]:
        """
        执行一次主管任务,流式 yield 所有 StreamPacket。

        Args:
            user_prompt: 直接给主管的 user 内容。若为 None,则要求子类声明
                         user_prompt_template 并通过 context 渲染。
            previous_response_id: 续接已有 ARK 响应(主管多任务串行时使用)。
                                  None 表示新开会话。
            max_turns: 覆盖类级默认 max_turns。
            **context: 渲染 user_prompt_template 时的变量。

        Yields:
            StreamPacket:
                - reasoning : 主管思考摘要 token
                - output    : 主管文本输出 token(主管通常不输出正文)
                - completed : 每轮结束时的 CompletedPayload(包含 tool_calls 与 res_id)
                - error     : 异常提示
                此外,流式专家(Writer)的 packets 也会在 tool 执行期间转发出来。

        退出条件:
            1. LLM completed 包不携带 tool_calls(主管认为任务完成)
            2. 达到 max_turns(强制退出,通过 error 包提示)
            3. 抛出 LLMError 异常(向上传播)
        """
        effective_max_turns = max_turns if max_turns is not None else self.max_turns

        rendered_user = self._build_user_prompt(user_prompt, context)
        system_prompt = self._inject_project_meta(self._load_system_prompt())

        client = self._get_client()
        tools_schemas = self.tool_manager.filter_by_scope(self.tool_scope) or None

        # 首轮 input
        current_input: list[dict] = []
        if system_prompt and system_prompt.strip():
            current_input.append({"role": "system", "content": system_prompt.strip()})
        current_input.append({"role": "user", "content": rendered_user})

        current_res_id = previous_response_id
        total_tokens_accum = 0
        turn = 0

        logger.info(
            f"主管启动 {self.agent_name} (model={self.runtime.model}, "
            f"tools={len(tools_schemas or [])}, max_turns={effective_max_turns})"
        )

        try:
            for turn in range(1, effective_max_turns + 1):
                logger.debug(f"{self.agent_name} 第 {turn} 轮开始")

                last_completed: Optional[CompletedPayload] = None

                async for packet in client.stream_call(
                    agent_name=self.agent_name,
                    input_data=current_input,
                    tools=tools_schemas,
                    tool_choice="auto" if tools_schemas else None,
                    previous_response_id=current_res_id,
                    thinking=self.thinking,
                ):
                    yield packet
                    if packet.type == "completed" and isinstance(packet.content, CompletedPayload):
                        last_completed = packet.content
                        current_res_id = packet.content.res_id
                        total_tokens_accum += packet.content.total_tokens

                if last_completed is None:
                    logger.warning(f"{self.agent_name} 第 {turn} 轮未收到 completed 包,退出")
                    yield StreamPacket(
                        type="error",
                        content="未收到 completed 事件,可能是网络中断或 ARK 异常",
                    )
                    break

                if not last_completed.tool_calls:
                    logger.info(
                        f"{self.agent_name} 在第 {turn} 轮无工具调用,任务结束 "
                        f"(累计 tokens={total_tokens_accum})"
                    )
                    break

                # 工具派发(含流式专家/ManagerTool 流式透传)
                confirm_emitted = False
                async for relay in self._dispatch_tools(last_completed.tool_calls):
                    yield relay
                    if relay.type in ("confirm", "ask_user"):
                        confirm_emitted = True

                # 工具失败计数与熔断保护
                if not confirm_emitted and self._last_dispatch_outputs:
                    call_id_to_name = {
                        tc.call_id: tc.name for tc in last_completed.tool_calls
                    }
                    for output_item in self._last_dispatch_outputs:
                        call_id = output_item.get("call_id")
                        output_str = output_item.get("output", "")
                        tool_name = call_id_to_name.get(call_id, "unknown")

                        is_error = False
                        try:
                            parsed = json.loads(output_str)
                            if isinstance(parsed, dict) and "error" in parsed:
                                is_error = True
                        except (json.JSONDecodeError, TypeError):
                            pass

                        if is_error:
                            self._tool_failure_counts[tool_name] = (
                                self._tool_failure_counts.get(tool_name, 0) + 1
                            )
                            if self._tool_failure_counts[tool_name] >= self._max_tool_failures:
                                msg = (
                                    f"工具 '{tool_name}' 连续失败 {self._max_tool_failures} 次，"
                                    f"触发熔断保护，任务终止"
                                )
                                logger.error(f"{self.agent_name}: {msg}")
                                yield StreamPacket(type="error", content=msg)
                                return
                        else:
                            self._tool_failure_counts[tool_name] = 0

                if confirm_emitted:
                    # Director 发出阶段确认/用户询问请求,中止 ReAct 等待用户,保存状态供 Orchestrator 续会话
                    # 关键：必须先静默发送 function_call_output 完成本轮对话,
                    # 否则 previous_response_id 指向的响应包含未完成的 tool_calls,
                    # 续接时 ARK 会将其视为"未执行/失败",导致工具返回内容全部丢失。
                    if self._last_dispatch_outputs and current_res_id:
                        try:
                            silent_res_id = ""
                            async for _pkt in client.stream_call(
                                agent_name=self.agent_name,
                                input_data=self._last_dispatch_outputs,
                                previous_response_id=current_res_id,
                                thinking=self.thinking,
                                tools=None,
                                tool_choice=None,
                            ):
                                if (
                                    _pkt.type == "completed"
                                    and isinstance(_pkt.content, CompletedPayload)
                                ):
                                    silent_res_id = _pkt.content.res_id
                                    total_tokens_accum += _pkt.content.total_tokens
                            if silent_res_id:
                                current_res_id = silent_res_id
                        except Exception as e:
                            logger.warning(
                                f"{self.agent_name} confirm/ask_user 后静默补全对话失败: {e}，"
                                f"续接时可能丢失 tool output 上下文"
                            )
                    logger.info(
                        f"{self.agent_name} 收到 confirm/ask_user 包,暂停等待用户回复"
                    )
                    return

                # 第二轮起,input = function_call_output 列表 + previous_response_id
                current_input = self._last_dispatch_outputs

            else:
                logger.warning(
                    f"{self.agent_name} 达到 max_turns={effective_max_turns},强制退出"
                )
                yield StreamPacket(
                    type="error",
                    content=f"达到 max_turns={effective_max_turns},任务未完成强制退出",
                )

        finally:
            self.last_response_id = current_res_id or ""
            self.last_total_tokens = total_tokens_accum
            self.last_turns = turn

    # --------------------------------------------------------
    # 工具派发(含流式专家 sink 注入与并发转发)
    # --------------------------------------------------------
    async def _dispatch_tools(
        self, tool_calls: list[ToolCallData]
    ) -> AsyncIterator[StreamPacket]:
        """
        执行一批 tool_calls,期间把流式专家的 packets 实时转发出来。

        机制:
            1. 给所有"流式 ExpertTool"安装一个 asyncio.Queue 作为 sink。
            2. 把 `tool_manager.handle_tool_calls(tool_calls)` 包装成 task 并行执行。
            3. 主循环 0.05s 取一次 queue,有则 yield;task 完成且 queue 空则退出。
            4. 任务完成后保存 outputs 到 `_last_dispatch_outputs` 供 run() 构造下一轮 input。
            5. 解除 sink 注入,避免下一轮意外复用。
        """
        sink_queue: asyncio.Queue[StreamPacket] = asyncio.Queue()

        async def relay_sink(p: StreamPacket) -> None:
            await sink_queue.put(p)

        attached: list[BaseTool] = []
        for tc in tool_calls:
            tool = self.tool_manager.get(tc.name)
            # 鸭子类型:任何带 with_stream_sink 的工具(ExpertTool / ManagerTool / ConfirmStageAdvance)都注入 sink
            # ConfirmStageAdvance 不是流式工具,但需要 sink 来发送 confirm packet
            if (
                tool is not None
                and hasattr(tool, "with_stream_sink")
                and callable(getattr(tool, "with_stream_sink"))
            ):
                tool.with_stream_sink(relay_sink)
                attached.append(tool)

        task = asyncio.create_task(self.tool_manager.handle_tool_calls(tool_calls))

        try:
            while True:
                if task.done() and sink_queue.empty():
                    break
                try:
                    packet = await asyncio.wait_for(sink_queue.get(), timeout=0.05)
                    yield packet
                except asyncio.TimeoutError:
                    pass

            outputs = task.result()
        finally:
            # 解除 sink,避免主管复用工具实例时误转发
            for t in attached:
                t.with_stream_sink(None)

        self._last_dispatch_outputs = outputs

    # --------------------------------------------------------
    # 提示词装载
    # --------------------------------------------------------
    def _inject_project_meta(self, base_prompt: str) -> str:
        """
        从 project_meta.json 读取 genre/tone/core_idea 并注入 system prompt 顶部。
        让主管无需自己调用 read_project_meta 就能稳定知道项目题材。
        """
        try:
            pid = require_current_project_id()
            meta_path = Path("workspace") / pid / "project_meta.json"
            if meta_path.exists():
                text = meta_path.read_text(encoding="utf-8")
                meta = json.loads(text)
                genre = meta.get("genre", "")
                tone = meta.get("tone", "")
                core_idea = meta.get("core_idea", "")
                if genre or tone or core_idea:
                    header = "[项目信息]\n"
                    if genre:
                        header += f"题材: {genre}\n"
                    if tone:
                        header += f"基调: {tone}\n"
                    if core_idea:
                        header += f"核心创意: {core_idea}\n"
                    header += "\n"
                    return header + base_prompt
        except Exception:
            pass
        return base_prompt

    def _load_system_prompt(self) -> str:
        """读取 system prompt。允许文件不存在时退化为空 system 段(便于早期联调)。"""
        try:
            return _read_prompt_file(self.system_prompt_path)
        except FileNotFoundError as e:
            logger.warning(
                f"{self.agent_name} 的 system_prompt_path={self.system_prompt_path} "
                f"未找到,使用空 system 段。错误: {e}"
            )
            return ""

    def _build_user_prompt(self, user_prompt: Optional[str], context: dict) -> str:
        """
        决定本轮 user prompt:
            1. 显式传入 user_prompt → 直接使用
            2. 否则若声明了 user_prompt_template → 渲染模板
            3. 都没有 → 抛 ValueError
        """
        if user_prompt is not None:
            return user_prompt
        if self.user_prompt_template:
            try:
                template_text = _read_prompt_file(self.user_prompt_template)
                return _render_template(template_text, context)
            except FileNotFoundError as e:
                raise ValueError(
                    f"{self.agent_name} 的 user_prompt_template={self.user_prompt_template} "
                    f"未找到: {e}"
                )
        raise ValueError(
            f"{self.agent_name}.run() 既未传 user_prompt,也未声明 user_prompt_template"
        )

    # --------------------------------------------------------
    # 共享 LLMClient
    # --------------------------------------------------------
    @classmethod
    def _get_client(cls) -> LLMClient:
        if cls._shared_client is None:
            cls._shared_client = LLMClient()
        return cls._shared_client

