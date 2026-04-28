"""
core/expert_tool.py

专家工具基类 — 把"读 prompt 模板 → 调一次 LLM → 返回字符串"的样板工艺沉淀进基类,
12 个专家(Geographer / RuleSmith / ... / Writer / Critic / ...)都继承本类。

为什么把专家做成 Tool 而不是独立 Agent:
    1. 专家不需要 ReAct 循环——它的工作模式是"输入→ LLM →结构化输出",一次调用即可。
    2. 主管以 ExpertTool 形式持有专家,LLM 只看到一组工具,可在 reasoning 阶段决定
       何时调用、并行调用哪几个,具备主管自主编排能力。
    3. 专家不写 KB——返回数据由主管检阅后落盘,权限隔离更干净。

子类必须声明:
    expert_name           : 专家名(PascalCase),必须出现在 AGENT_DEFINITIONS 且 kind="expert"
    description           : 一句自然语言说明,LLM 决策时阅读
    input_schema          : OpenAI parameters JSON Schema(供 LLM 知道传哪些参数)
    system_prompt_path    : system prompt 文件路径(相对 prompts/ 根)
    user_prompt_template  : user prompt 模板路径(相对 prompts/ 根)

可选:
    output_schema         : 约束 LLM 输出的 JSON Schema(reviewer / generator 必填,creator 留空)
    streaming             : 是否流式专家(目前仅 Writer)

自动派生:
    name      : `call_<snake_case>` 形式,LLM 从 tools 列表中按此名调用
    schema    : 标准 OpenAI function 工具 schema

参考:`D:\\AI协作任务\\NovelAgent\\Tools\\base.py`(改造点:execute 由同步改为异步;
新增 prompt 模板渲染、json_schema 约束、专家与 agent_name 的强绑定)。
"""

import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional

from core.base_tool import BaseTool
from core.config import AGENT_DEFINITIONS, get_config
from core.context import require_current_project_id
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.performance_logger import log_token_audit
from core.stream_packet import StreamPacket

logger = get_logger("core.expert_tool")


# ============================================================
# Prompts 根目录解析(可被环境变量覆盖)
# ============================================================
import os

_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"
PROMPTS_ROOT = Path(os.getenv("MANS_PROMPTS_ROOT", str(_DEFAULT_PROMPTS_ROOT)))


def _read_prompt_file(rel_or_abs: str) -> str:
    """
    读取 prompt 文件。

    路径解析顺序:
        1. 绝对路径直接使用
        2. 相对路径在 PROMPTS_ROOT 下查找
        3. 找不到时抛 FileNotFoundError 并提示尝试过的路径

    缓存:同一文件在进程内只读一次,降低高频调用的 IO。生产中 prompt 文件
    几乎不会运行时变更;若改文件需要重启进程或调 `_clear_prompt_cache()`。
    """
    cache = _prompt_cache_get(rel_or_abs)
    if cache is not None:
        return cache

    p = Path(rel_or_abs)
    if not p.is_absolute():
        candidate = PROMPTS_ROOT / rel_or_abs
        if not candidate.exists():
            raise FileNotFoundError(
                f"找不到 prompt 文件 '{rel_or_abs}',已尝试: {candidate}"
            )
        p = candidate
    elif not p.exists():
        raise FileNotFoundError(f"找不到 prompt 文件: {p}")

    text = p.read_text(encoding="utf-8")
    _prompt_cache_set(rel_or_abs, text)
    return text


_prompt_cache: dict[str, str] = {}
_prompt_cache_lock = threading.Lock()


def _prompt_cache_get(key: str) -> Optional[str]:
    with _prompt_cache_lock:
        return _prompt_cache.get(key)


def _prompt_cache_set(key: str, value: str) -> None:
    with _prompt_cache_lock:
        _prompt_cache[key] = value


def _clear_prompt_cache() -> None:
    """清空 prompt 文件缓存(主要供测试或热更新使用)。"""
    with _prompt_cache_lock:
        _prompt_cache.clear()


# ============================================================
# Jinja2 渲染(惰性 import,不依赖时退化为 str.format)
# ============================================================
_jinja_env = None


def _render_template(template_text: str, context: dict[str, Any]) -> str:
    """
    用 Jinja2 渲染模板。Jinja2 不可用时退化为 `str.format(**context)`。

    Jinja2 是项目硬依赖,理论上一定能 import 成功。`format` 路径仅作健壮性兜底。
    """
    global _jinja_env
    try:
        if _jinja_env is None:
            from jinja2 import Environment, StrictUndefined

            _jinja_env = Environment(
                undefined=StrictUndefined,
                trim_blocks=True,
                lstrip_blocks=True,
                autoescape=False,
            )
        return _jinja_env.from_string(template_text).render(**context)
    except Exception as e:
        logger.warning(f"Jinja2 渲染失败,退化为 str.format: {e}")
        try:
            return template_text.format(**context)
        except Exception:
            return template_text


# ============================================================
# 命名工具
# ============================================================

def _to_snake_case(name: str) -> str:
    """PascalCase → snake_case。SceneDirector → scene_director。"""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# ============================================================
# 专家流式 sink(供 Writer 使用)
# ============================================================
StreamSink = Callable[[StreamPacket], Awaitable[None]]


# ============================================================
# ExpertTool 基类
# ============================================================

class ExpertTool(BaseTool):
    """
    专家工具基类。

    子类只需声明类属性 + 可选 override `execute()` 的入参校验/产出后处理。
    基类负责拼 prompt → 调 LLM → 返回字符串。

    重要约束:
        - `expert_name` 必须出现在 AGENT_DEFINITIONS 且 kind="expert"。
        - `output_schema` 在 reviewer / generator 档应当填写,creator 档(Writer)留空。
        - 子类可重写 `_postprocess(content, **kwargs)` 在返回前做校验或重写。

    流式专家:
        - `streaming = True` 仅供 Writer。
        - 主管在调用前可通过 `with_stream_sink(sink)` 注入回调,Writer 的每个 token
          会通过 sink 推给前端;execute 仍返回完整文本字符串以供主管落盘。
    """

    # 由子类覆盖
    expert_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}
    system_prompt_path: ClassVar[str] = ""
    user_prompt_template: ClassVar[str] = ""
    output_schema: ClassVar[Optional[dict]] = None
    streaming: ClassVar[bool] = False

    # 内部
    _shared_client: ClassVar[Optional[LLMClient]] = None

    def __init__(self):
        self._validate_class_attrs()
        self._stream_sink: Optional[StreamSink] = None
        self._last_response_id: str = ""
        self._last_usage: dict = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # --------------------------------------------------------
    # 类属性合法性校验(实例化时执行,避免启动后才发现配置错误)
    # --------------------------------------------------------
    def _validate_class_attrs(self) -> None:
        cls_name = type(self).__name__
        if not self.expert_name:
            raise ValueError(f"ExpertTool 子类 {cls_name} 未声明 expert_name")
        if self.expert_name not in AGENT_DEFINITIONS:
            raise ValueError(
                f"ExpertTool 子类 {cls_name} 的 expert_name='{self.expert_name}' "
                f"未注册到 AGENT_DEFINITIONS,可用专家:"
                f"{[n for n, s in AGENT_DEFINITIONS.items() if s['kind'] == 'expert']}"
            )
        if AGENT_DEFINITIONS[self.expert_name]["kind"] != "expert":
            raise ValueError(
                f"ExpertTool 子类 {cls_name} 的 expert_name='{self.expert_name}' "
                f"在 AGENT_DEFINITIONS 中是 manager,应使用 BaseAgent"
            )
        if not self.description:
            raise ValueError(f"ExpertTool 子类 {cls_name} 未声明 description")
        if not self.system_prompt_path:
            raise ValueError(f"ExpertTool 子类 {cls_name} 未声明 system_prompt_path")
        if not self.user_prompt_template:
            raise ValueError(f"ExpertTool 子类 {cls_name} 未声明 user_prompt_template")

    # --------------------------------------------------------
    # BaseTool 约定接口
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        """工具名 = `call_` + 专家名 snake_case 形式。例:Writer → call_writer。"""
        return f"call_{_to_snake_case(self.expert_name)}"

    @property
    def schema(self) -> dict:
        """
        OpenAI Responses Tools 标准 schema。

        parameters 直接挂 input_schema(子类声明)。description 由子类的 description 字段提供。
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    async def execute(self, **kwargs) -> str:
        """
        基础执行流程:加载 system → 渲染 user → 调 LLM → 返回字符串。

        子类可重写 `_postprocess()` 做产出校验/再加工,但**不要重写 execute**——
        重写会绕过日志、流式 sink、错误兜底、token 审计等基础设施。
        """
        start_time = time.time()
        self._last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        client = self._get_client()
        cfg = get_config()
        rt = cfg.get_for_agent(self.expert_name)

        try:
            system_prompt = _read_prompt_file(self.system_prompt_path)
        except FileNotFoundError as e:
            logger.error(f"{type(self).__name__}: {e}")
            raise

        try:
            user_template_text = _read_prompt_file(self.user_prompt_template)
        except FileNotFoundError as e:
            logger.error(f"{type(self).__name__}: {e}")
            raise

        user_prompt = _render_template(user_template_text, kwargs)

        logger.info(
            f"专家调用 {self.expert_name} (model={rt.model}, temp={rt.temperature}, "
            f"streaming={self.streaming})"
        )

        if self.streaming and self._stream_sink is not None:
            content = await self._streamed_call(
                client=client,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        else:
            resp = await client.call(
                agent_name=self.expert_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=self.output_schema,
            )
            self._last_response_id = resp.res_id
            self._last_usage = {
                "input_tokens": resp.usage.get("input_tokens", 0) if resp.usage else 0,
                "output_tokens": resp.usage.get("output_tokens", 0) if resp.usage else 0,
                "total_tokens": resp.usage.get("total_tokens", 0) if resp.usage else 0,
            }
            content = resp.content

        duration_ms = int((time.time() - start_time) * 1000)

        # Token 审计记录(非阻塞,失败不影响主流程)
        try:
            pid = require_current_project_id()
            await log_token_audit(
                project_id=pid,
                agent_name=self.expert_name,
                agent_kind="expert",
                chapter_number=kwargs.get("chapter_number", 0),
                scene_index=kwargs.get("scene_index", 0),
                duration_ms=duration_ms,
                input_tokens=self._last_usage.get("input_tokens", 0),
                output_tokens=self._last_usage.get("output_tokens", 0),
                total_tokens=self._last_usage.get("total_tokens", 0),
            )
        except Exception as e:
            logger.debug(f"专家 token 审计记录失败(非阻塞): {e}")

        return await self._postprocess(content, **kwargs)

    # --------------------------------------------------------
    # 流式专家:把 token 通过 sink 推出去,同时聚合最终文本
    # --------------------------------------------------------
    async def _streamed_call(
        self,
        *,
        client: LLMClient,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        流式专家(Writer)的内部调用路径。

        - sink 收到的 reasoning/output/completed 包由调用方(主管或 Orchestrator)转发给前端。
        - 本方法返回完整 output 文本字符串,主管拿这个字符串去落盘。
        """
        from core.stream_packet import CompletedPayload  # 避免循环

        input_data = []
        if system_prompt and system_prompt.strip():
            input_data.append({"role": "system", "content": system_prompt.strip()})
        input_data.append({"role": "user", "content": user_prompt})

        full_text: list[str] = []
        async for packet in client.stream_call(
            agent_name=self.expert_name,
            input_data=input_data,
            json_schema=self.output_schema,
            tools=None,
            tool_choice=None,
            previous_response_id=None,
        ):
            if self._stream_sink is not None:
                try:
                    await self._stream_sink(packet)
                except Exception as e:
                    logger.warning(f"stream_sink 推送失败(忽略,继续聚合): {e}")
            if packet.type == "output" and isinstance(packet.content, str):
                full_text.append(packet.content)
            elif packet.type == "completed" and isinstance(packet.content, CompletedPayload):
                self._last_response_id = packet.content.res_id
                self._last_usage = {
                    "input_tokens": packet.content.input_tokens,
                    "output_tokens": packet.content.output_tokens,
                    "total_tokens": packet.content.total_tokens,
                }
        return "".join(full_text)

    # --------------------------------------------------------
    # 子类可重写
    # --------------------------------------------------------
    async def _postprocess(self, content: str, **kwargs) -> str:
        """
        默认透传 LLM 返回值。子类可重写做格式校验/字段重命名/兜底默认。

        不要在此抛异常——抛了会被 ToolManager 捕获并包装成 error JSON 返回给 LLM,
        LLM 看到错误只会重试或换工具,而不能解决根因。
        校验失败时返回带 _warning 字段的 JSON 字符串,让主管在 reasoning 中察觉。
        """
        return content

    # --------------------------------------------------------
    # 流式 sink 注入(用于 Writer)
    # --------------------------------------------------------
    def with_stream_sink(self, sink: Optional[StreamSink]) -> "ExpertTool":
        """
        设置流式 sink(主管在调用 Writer 前调用)。返回 self 便于链式书写。

        示例:
            writer_tool.with_stream_sink(orchestrator.relay_to_frontend)
            await writer_tool.execute(beatsheet=..., prev_tail=...)
        """
        if sink is not None and not self.streaming:
            logger.warning(
                f"{type(self).__name__} 不是 streaming 专家,设置 stream_sink 不会生效"
            )
        self._stream_sink = sink
        return self

    @property
    def last_response_id(self) -> str:
        """最近一次调用的 ARK response_id,主管可用于 ReAct 续接调试。"""
        return self._last_response_id

    # --------------------------------------------------------
    # 共享 LLMClient 单例(避免每个 ExpertTool 实例都建一个)
    # --------------------------------------------------------
    @classmethod
    def _get_client(cls) -> LLMClient:
        if cls._shared_client is None:
            cls._shared_client = LLMClient()
        return cls._shared_client
