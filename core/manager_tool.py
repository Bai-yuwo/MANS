"""
core/manager_tool.py

ManagerTool 基类 — 把子主管(BaseAgent)包装成一个可调用工具,供 Director 统一编排。

设计动机:
    Director 作为全局编排主管,需要调用 WorldArchitect / PlotArchitect / CastingDirector /
    SceneShowrunner 四个业务主管。每个业务主管都是 BaseAgent(ReAct 循环),它们产出的
    StreamPacket(包括 Writer 流式 tokens)必须实时向上透传到 Director 的 yield 流,
    最终到达前端 SSE。

因此 ManagerTool 不是"封掉子主管、只给摘要",而是:
    1. Director 的 LLM 通过 tool_call 发起 call_world_architect(user_prompt)
    2. ManagerTool 实例化 WorldArchitect → 跑完完整 ReAct 循环
    3. 循环期间所有 packets(包括 Writer 流式 token)通过 sink_queue 注入 Director 的 run()
    4. 业务主管跑完后,ManagerTool.execute() 返回一句简洁的 JSON 摘要(成果列表+token 统计)
       作为 function_call_output 反馈给 Director LLM
    5. Director LLM 决定下一步:继续确认阶段切换、或调用下一个业务主管

与 ExpertTool 的区别:
    - ExpertTool: 一次性 LLM 调用,无 ReAct 循环,内部不 yield packets
    - ManagerTool: 驱动完整子主管 ReAct,透传全部 packets,自己最后返回文本摘要

自动命名:
    子类声明 target_manager_class = SomeManager 后,name 自动生成 call_some_manager。
"""

import asyncio
import time
from typing import Awaitable, Callable, ClassVar, Optional, Type

from core.base_tool import BaseTool
from core.base_agent import BaseAgent
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.performance_logger import log_token_audit
from core.stream_packet import CompletedPayload, StreamPacket
from core.expert_tool import _to_snake_case

logger = get_logger("core.manager_tool")

StreamSink = Callable[[StreamPacket], Awaitable[None]]


class ManagerTool(BaseTool):
    """
    ManagerTool 基类。子类只需声明 target_manager_class 即可获得完整工具形态。

    子类必须声明:
        target_manager_class : 被调子主管的类(必须是 BaseAgent 子类)
        description          : 供 LLM 阅读的自然语言说明
    """

    target_manager_class: ClassVar[Optional[Type[BaseAgent]]] = None
    description: ClassVar[str] = ""
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "user_prompt": {
                "type": "string",
                "description": "给子主管的 user prompt。应简洁描述当前阶段目标(如'构建世界观与地理设定')。",
            },
        },
        "required": ["user_prompt"],
        "additionalProperties": False,
    }

    # 固定为 True:子主管可能内含 Writer 流式专家,token 必须透传
    streaming: ClassVar[bool] = True

    # 类级实例缓存: {cache_key: BaseAgent}
    # 按 project_id 缓存子主管实例,同一项目内重复调用时复用,
    # 保留对话状态(last_response_id / last_total_tokens / last_turns)。
    _manager_instances: ClassVar[dict[str, BaseAgent]] = {}
    _instance_access_ts: ClassVar[dict[str, float]] = {}
    _MAX_CACHED_INSTANCES: ClassVar[int] = 20

    def __init__(self):
        self._validate_class_attrs()
        self._stream_sink: Optional[StreamSink] = None

    def _validate_class_attrs(self) -> None:
        cls_name = type(self).__name__
        if self.target_manager_class is None:
            raise ValueError(
                f"ManagerTool 子类 {cls_name} 未声明 target_manager_class"
            )
        if not issubclass(self.target_manager_class, BaseAgent):
            raise ValueError(
                f"ManagerTool 子类 {cls_name} 的 target_manager_class 必须是 BaseAgent 子类"
            )
        if not self.description:
            raise ValueError(f"ManagerTool 子类 {cls_name} 未声明 description")

    @property
    def name(self) -> str:
        cls = self.target_manager_class
        manager_name = getattr(cls, "agent_name", cls.__name__)
        return f"call_{_to_snake_case(manager_name)}"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def with_stream_sink(self, sink: Optional[StreamSink]) -> "ManagerTool":
        """设置流式 sink(Director 的 _dispatch_tools 调用前注入)。返回 self 便于链式。"""
        self._stream_sink = sink
        return self

    async def execute(self, *, user_prompt: str, **kwargs) -> str:
        """
        驱动子主管完整 ReAct 循环,实时透传 packets,最终返回文本摘要。

        实例缓存:
            按 project_id 复用子主管实例,保留对话状态供 ReAct 续接。
            缓存键: "{tool_name}:{project_id}"。
            LRU 淘汰:超过 _MAX_CACHED_INSTANCES 时移除最久未访问实例。

        Args:
            user_prompt: 给子主管的 user prompt
            **kwargs: 子主管 run() 的额外参数(如 project_id context)

        Returns:
            JSON 字符串摘要:turns / tokens / last_response_id / summary
        """
        import json

        # 获取 project_id 以构建缓存键
        try:
            pid = require_current_project_id()
        except Exception:
            pid = "_unknown_"
        cache_key = f"{self.name}:{pid}"

        # LRU 淘汰检查
        if len(self._manager_instances) >= self._MAX_CACHED_INSTANCES:
            # 移除最久未访问的实例
            if self._instance_access_ts:
                oldest_key = min(
                    self._instance_access_ts, key=self._instance_access_ts.get
                )
                self._manager_instances.pop(oldest_key, None)
                self._instance_access_ts.pop(oldest_key, None)
                logger.info(f"ManagerTool 缓存淘汰: {oldest_key}")

        # 复用或创建实例
        manager = self._manager_instances.get(cache_key)
        if manager is not None:
            logger.info(
                f"ManagerTool 复用子主管实例: {cache_key} "
                f"(res_id={'有' if manager.last_response_id else '无'}, turns={manager.last_turns})"
            )
        else:
            manager = self.target_manager_class()
            self._manager_instances[cache_key] = manager
            logger.info(f"ManagerTool 创建子主管实例: {cache_key}")

        # 更新访问时间戳
        self._instance_access_ts[cache_key] = time.time()

        start_time = time.time()
        turns = 0
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        last_res_id = ""
        text_fragments: list[str] = []
        confirm_payload = None
        has_error = False

        async def relay_sink(p: StreamPacket) -> None:
            if self._stream_sink is not None:
                await self._stream_sink(p)

        try:
            async for packet in manager.run(user_prompt=user_prompt, **kwargs):
                # confirm 属于阶段切换确认，由 Director 控制，子主管不应越权 → 吞掉
                if packet.type == "confirm":
                    confirm_payload = packet.content
                    continue
                # ask_user 是子主管内部需要人类决策的场景 → 透传到 Director yield 流
                if packet.type == "error":
                    has_error = True
                await relay_sink(packet)

                if packet.type == "completed" and isinstance(packet.content, CompletedPayload):
                    total_tokens += packet.content.total_tokens
                    input_tokens += packet.content.input_tokens
                    output_tokens += packet.content.output_tokens
                    last_res_id = packet.content.res_id
                elif packet.type == "output" and isinstance(packet.content, str):
                    text_fragments.append(packet.content)
        except Exception as e:
            logger.exception(
                f"ManagerTool {self.name} 驱动子主管 {manager.agent_name} 异常"
            )
            return json.dumps(
                {
                    "status": "error",
                    "manager": manager.agent_name,
                    "error": f"{type(e).__name__}: {e}",
                },
                ensure_ascii=False,
            )

        duration_ms = int((time.time() - start_time) * 1000)
        turns = manager.last_turns
        summary = "".join(text_fragments)[-500:]  # 取最后 500 字作为摘要

        # Token 审计记录(非阻塞)
        try:
            await log_token_audit(
                project_id=pid,
                agent_name=manager.agent_name,
                agent_kind="manager",
                chapter_number=kwargs.get("chapter_number", 0),
                scene_index=kwargs.get("scene_index", 0),
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        except Exception as e:
            logger.debug(f"ManagerTool token 审计记录失败(非阻塞): {e}")

        result = {
            "status": "incomplete" if has_error else "ok",
            "manager": manager.agent_name,
            "turns": turns,
            "tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
            "last_response_id": last_res_id,
            "summary": summary,
        }
        if has_error:
            result["error_hint"] = "子主管运行过程中出现错误(可能达到 max_turns 或异常退出),任务可能未完成。请评估是否重试、跳过，或向用户报告。"
        if confirm_payload is not None:
            # 如果子主管本身发出确认请求(理论上不会,但留个钩子),在摘要里标注
            result["sub_confirm"] = {
                "from_stage": getattr(confirm_payload, "from_stage", ""),
                "to_stage": getattr(confirm_payload, "to_stage", ""),
            }

        return json.dumps(result, ensure_ascii=False)

    # --------------------------------------------------------
    # 缓存管理
    # --------------------------------------------------------
    @classmethod
    def clear_cache(cls, project_id: Optional[str] = None) -> None:
        """
        清理子主管实例缓存。

        Args:
            project_id: 若指定,只清理该项目的缓存;若为 None,清理全部缓存。
        """
        if project_id is None:
            cls._manager_instances.clear()
            cls._instance_access_ts.clear()
            logger.info("ManagerTool 全部缓存已清理")
            return

        keys_to_remove = [
            k for k in cls._manager_instances.keys() if k.endswith(f":{project_id}")
        ]
        for k in keys_to_remove:
            cls._manager_instances.pop(k, None)
            cls._instance_access_ts.pop(k, None)
        logger.info(f"ManagerTool 项目缓存已清理: {project_id} ({len(keys_to_remove)} 个实例)")
