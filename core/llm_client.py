"""
core/llm_client.py

LLM 调用封装 — ARK Responses API(OpenAI 兼容)。

主管-专家二级架构下的两种调用模式:
    1. **主管 ReAct 流式**:`stream_call(agent_name, input, tools, previous_response_id)`
       → 异步 yield `StreamPacket`(reasoning / output / completed / error)。
       `BaseAgent` 用它驱动 ReAct 循环,服务端通过 `previous_response_id` 续接历史。
    2. **专家一次性同步**:`call(agent_name, system_prompt, user_prompt, json_schema)`
       → 返回 `LLMResponse`,内部仍走 stream(为了 ARK 平台稳定性)然后聚合。
       `ExpertTool.execute()` 用它,等同于 OpenAI Chat Completions 的非流式调用语义。

核心设计:
    - **agent_name 取代 role**:`get_for_agent(name)` 拿到 model/temperature/max_tokens/kind,
      调用方不再传 role 字符串。BaseAgent 与 ExpertTool 内部都只持有 agent_name。
    - **思考模式默认 enabled**:ARK 三家底模(doubao / deepseek / glm)在 ARK 平台上都支持
      `extra_body.thinking.type`。reasoning 摘要 token 通过 reasoning 包推到前端。
    - **JSON Schema 强制**:专家与所有非 Writer 调用都必须传 `json_schema`(由 prompts 工程
      自然约定)。Writer 是创作档,纯文本输出。
    - **previous_response_id**:主管的 ReAct 第二轮起,只发 tool_outputs,服务端用
      `previous_response_id` 续接全部前文,显著省 token。

向后兼容:
    旧代码(generators/ writer/ injection_engine/ update_extractor)仍调用
    `call(role="writer", ...)` / `stream(role="writer", ...)`。这两个签名通过
    LEGACY_ROLE_TO_AGENT 把 role 翻译成 agent_name 走新路径,**调用结果与旧实现等价**。

异常体系(沿用旧版,保持调用方不需改 except):
    - LLMError(基类) — provider/model 标记
    - LLMAPIError(status_code) — 4xx/5xx
    - LLMTimeoutError — 连接/读取超时
    - LLMRateLimitError(retry_after) — 429
"""

import asyncio
import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Union

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from core.config import LEGACY_ROLE_TO_AGENT, ARKProvider, get_config
from core.logging_config import get_logger, log_exception
from core.stream_packet import CompletedPayload, StreamPacket, ToolCallData

logger = get_logger("core.llm_client")
prompt_logger = get_logger("prompt")


# ============================================================
# 异常体系
# ============================================================

class LLMError(Exception):
    """LLM 调用基础异常,带 provider/model 标记便于排查。"""

    def __init__(self, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class LLMAPIError(LLMError):
    """ARK 返回非 2xx 状态码(认证、参数错误、服务端错误等)。"""

    def __init__(self, message: str, status_code: int = 0, **kwargs):
        super().__init__(message, **kwargs)
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    """连接超时或读取超时(SSE 长时间无 token)。"""


class LLMRateLimitError(LLMError):
    """ARK 返回 429,retry_after 字段告诉调用方建议等待秒数。"""

    def __init__(self, message: str, retry_after: int = 60, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


# ============================================================
# 响应封装
# ============================================================

@dataclass
class LLMResponse:
    """
    一次性同步调用的返回值。

    Attributes:
        content: LLM 文本输出(已清洗 JSON 污染)。
        model: 实际调用的模型 ID。
        provider: Provider 名(永远是 "ark")。
        usage: token 用量,键 input_tokens/output_tokens/total_tokens。
        res_id: ARK 服务端响应 ID,可作为下一轮 previous_response_id(专家一次性调用通常不用)。
        finish_reason: ARK 当前不稳定提供该字段,保留空字符串占位。
    """
    content: str
    model: str
    provider: str = "ark"
    usage: dict = None
    res_id: str = ""
    finish_reason: str = ""


# ============================================================
# 速率限制器(令牌桶)
# ============================================================

class RateLimiter:
    """
    令牌桶速率限制器。

    每秒补充 max_requests 个令牌,acquire 消耗 1 个;桶空时返回需等待秒数。
    主要为防止短时间突发请求触发 ARK 平台限流。
    """

    def __init__(self, max_requests: int = 10, time_window: float = 1.0):
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens: float = max_requests
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(
                self.max_requests,
                self.tokens + elapsed * (self.max_requests / self.time_window),
            )
            self.last_update = now
            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.time_window / self.max_requests)
                return wait_time
            self.tokens -= 1
            return 0.0

    async def __aenter__(self):
        wait_time = await self.acquire()
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


# ============================================================
# 全局并发信号量(单例)
# ============================================================

class ConcurrencyLimiter:
    """
    全局并发限制器(单例)。

    防止内存/连接池/网络拥塞与 ARK 并发上限触发。整个进程共用一个信号量,即使
    多次 LLMClient() 实例化也只有一个底层 limiter。
    """

    _instance = None

    def __new__(cls, max_concurrent: int = 5):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_concurrent: int = 5):
        if self._initialized:
            return
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._initialized = True
        logger.info(f"并发限制器初始化:最大并发 {max_concurrent}")

    async def __aenter__(self):
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()

    @property
    def current_count(self) -> int:
        return self.max_concurrent - self.semaphore._value


# ============================================================
# Token 计数(粗略,基于 cl100k_base)
# ============================================================

class TokenCounter:
    """
    Token 计数工具。基于 tiktoken 的 cl100k_base 编码器,与 GPT-4/Claude 兼容,
    对 ARK 上的 doubao/deepseek/glm 是粗略估计,主要用于上下文预算判断而非精确计费。
    """

    def __init__(self):
        import tiktoken
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.encoder = None

    def count(self, text: str) -> int:
        if not text or self.encoder is None:
            return 0
        return len(self.encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            total += self.count(content) + 4
        return total


# ============================================================
# 调用日志辅助
# ============================================================

def _get_caller_info() -> dict:
    """
    从调用栈提取真实调用方信息,跳过 llm_client / asyncio / logging 等中间帧。
    用于 prompt.log 标注哪个 agent / 哪个文件发起的调用。
    """
    stack = inspect.stack()
    for frame_info in stack[2:]:
        filename = frame_info.filename
        func_name = frame_info.function
        if "llm_client.py" in filename:
            continue
        if any(skip in filename for skip in ["asyncio", "logging", "concurrent", "threading"]):
            continue
        module_name = filename.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "")
        return {
            "source_function": func_name,
            "source_file": filename,
            "source_line": frame_info.lineno,
            "module": module_name,
        }
    return {"source_function": "unknown", "source_file": "unknown", "source_line": 0, "module": "unknown"}


def _log_llm_call(
    agent_name: str,
    model: str,
    temperature: float,
    max_tokens: int,
    system_prompt: str,
    user_prompt: str,
    json_schema: Optional[dict],
    call_type: str,
):
    """单次 LLM 调用的请求上下文写到 prompt.log,便于审计与提示词工程迭代。"""
    caller = _get_caller_info()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    sys_len = len(system_prompt) if system_prompt else 0
    usr_len = len(user_prompt) if user_prompt else 0
    schema_status = f"是 (name={json_schema.get('name', 'unnamed')})" if json_schema else "否"

    lines = [
        "",
        "═" * 79,
        f"[CALL_TYPE]   {call_type}",
        f"[TIME]        {now}",
        f"[AGENT]       {agent_name}",
        f"[MODEL]       {model}",
        f"[TEMP/MAX]    {temperature} / {max_tokens}",
        f"[CALLER]      {caller['source_function']} @ {caller['module']}.py:{caller['source_line']}",
        f"[JSON_SCHEMA] {schema_status}",
        "─" * 79,
    ]
    if json_schema:
        try:
            lines.append("[SCHEMA]")
            lines.append(json.dumps(json_schema, ensure_ascii=False, indent=2))
            lines.append("─" * 79)
        except Exception:
            lines.append("[SCHEMA] (序列化失败)")
    if system_prompt and system_prompt.strip():
        lines.append(f"[SYSTEM] ({sys_len} chars)")
        lines.append(system_prompt.strip())
        lines.append("─" * 79)
    if user_prompt and user_prompt.strip():
        lines.append(f"[USER] ({usr_len} chars)")
        lines.append(user_prompt.strip())
        lines.append("─" * 79)
    lines.append("")
    prompt_logger.info("\n".join(lines))


# ============================================================
# JSON 清洗
# ============================================================

def _clean_json_content(content: str) -> str:
    """
    清洗 LLM 输出中的 JSON 污染。

    步骤:
      1. 去 BOM、首尾空白
      2. 剥离 Markdown 代码块包裹(```json ... ```)
      3. 去 <think>...</think> 推理痕迹(部分模型输出会带)
      4. 强力截取首个 { 或 [ 到对应末尾,防止前后掺杂自然语言
      5. 去尾部多余逗号
    """
    if not content:
        return ""
    text = content.strip().lstrip("﻿")

    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    first_brace = text.find("{")
    first_bracket = text.find("[")
    if first_brace == -1 and first_bracket == -1:
        return text
    if first_brace == -1:
        start = first_bracket
    elif first_bracket == -1:
        start = first_brace
    else:
        start = min(first_brace, first_bracket)
    end = text.rfind("}") if text[start] == "{" else text.rfind("]")
    if end != -1 and end > start:
        text = text[start : end + 1]

    return text.rstrip().rstrip(",").rstrip()


# ============================================================
# LLMClient
# ============================================================

class LLMClient:
    """
    LLM 统一调用客户端(ARK Responses API)。

    主要 API:
        - stream_call(agent_name, input, tools, previous_response_id, ...) — 主管 ReAct
        - call(agent_name, system_prompt, user_prompt, json_schema) — 专家一次性
        - call_with_retry / stream_call_with_retry — 重试包装
        - call(role=...) / stream(role=...) — 旧 role-based shim,内部翻译到新 API

    线程模型:
        - AsyncOpenAI 客户端实例可以被多个协程共享。
        - RateLimiter / ConcurrencyLimiter 是异步安全的(asyncio Lock/Semaphore)。
    """

    DEFAULT_CONCURRENT_LIMIT = 5
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0

    _rate_limiter: Optional[RateLimiter] = None
    _token_counter: Optional[TokenCounter] = None

    @staticmethod
    def _clean_json_content(content: str) -> str:
        """暴露为静态方法,供调用方需要时手动清洗(如重试前重新解析)。"""
        return _clean_json_content(content)

    def __init__(
        self,
        rate_limit: Optional[int] = None,
        concurrent_limit: Optional[int] = None,
    ):
        self.config = get_config()
        self.provider: ARKProvider = self.config.ark_provider

        self.client = AsyncOpenAI(
            base_url=self.provider.base_url,
            api_key=self.provider.api_key,
        )

        self.rate_limit = rate_limit or self.config.RATE_LIMIT
        self.concurrency_limiter = ConcurrencyLimiter(
            concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT
        )

        if LLMClient._rate_limiter is None:
            LLMClient._rate_limiter = RateLimiter(
                max_requests=self.rate_limit, time_window=1.0
            )

        logger.info(
            f"LLMClient 初始化 - Provider: ark, BaseURL: {self.provider.base_url}, "
            f"速率限制: {self.rate_limit}/s, 并发限制: {concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT}"
        )

    # --------------------------------------------------------
    # Token 计数
    # --------------------------------------------------------
    def count_tokens(self, text: str) -> int:
        if LLMClient._token_counter is None:
            LLMClient._token_counter = TokenCounter()
        return LLMClient._token_counter.count(text)

    def count_messages_tokens(self, messages: list[dict]) -> int:
        if LLMClient._token_counter is None:
            LLMClient._token_counter = TokenCounter()
        return LLMClient._token_counter.count_messages(messages)

    # --------------------------------------------------------
    # 内部:统一构造 ARK responses.create 的 kwargs
    # --------------------------------------------------------
    def _build_kwargs(
        self,
        *,
        agent_name: str,
        input_data: Union[list[dict], str],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_schema: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Union[str, dict]] = "auto",
        previous_response_id: Optional[str] = None,
        thinking: str = "enabled",
        stream: bool = False,
    ) -> dict:
        """
        构造 responses.create 的 kwargs。

        关键:
            - agent_name 决定 model/temperature/max_tokens(可被参数覆盖)
            - thinking 默认 enabled,reasoning 摘要由 _process_stream 转成 reasoning 包
            - tools 仅在非空时注入,避免空数组触发 ARK 校验
            - json_schema 通过 text.format 注入,name/strict/schema 三字段必备
            - previous_response_id 用于 ReAct 续接,首轮为 None
        """
        rt = self.config.get_for_agent(agent_name)
        effective_max_tokens = max_tokens if max_tokens is not None else rt.max_tokens
        effective_temperature = temperature if temperature is not None else rt.temperature

        kwargs: dict = {
            "model": rt.model,
            "input": input_data,
            "max_output_tokens": effective_max_tokens,
            "temperature": effective_temperature,
            "stream": stream,
            "extra_body": {"thinking": {"type": thinking}},
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if json_schema:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": json_schema.get("name", "output"),
                    "strict": json_schema.get("strict", True),
                    "schema": json_schema.get("schema", {}),
                }
            }
        return kwargs

    # --------------------------------------------------------
    # 内部:把 ARK SSE 事件流归并为 StreamPacket
    # --------------------------------------------------------
    @staticmethod
    async def _process_stream(response) -> AsyncIterator[StreamPacket]:
        """
        把 ARK 17 种 SSE 事件归并成 StreamPacket 四态:
            - reasoning : response.reasoning_summary_text.delta
            - output    : response.output_text.delta
            - completed : response.completed(携带 res_id / total_tokens / tool_calls)
            - error     : 异常(网络断开、SDK 抛错)

        额外行为:
            - 当 reasoning 段切换到非 reasoning 时,补一个 reasoning="\\n" 让前端段落分隔
            - 当 output 段切换到非 output 时,同上
            - tool_calls 从 completed.response.output 中过滤 type=function_call 的项构造
        """
        try:
            last_type = None
            async for chunk in response:
                chunk_type = getattr(chunk, "type", None)

                if last_type == "response.reasoning_summary_text.delta" and chunk_type != "response.reasoning_summary_text.delta":
                    yield StreamPacket(type="reasoning", content="\n")
                elif last_type == "response.output_text.delta" and chunk_type != "response.output_text.delta":
                    yield StreamPacket(type="output", content="\n")

                if chunk_type == "response.reasoning_summary_text.delta":
                    yield StreamPacket(type="reasoning", content=getattr(chunk, "delta", "") or "")
                elif chunk_type == "response.output_text.delta":
                    yield StreamPacket(type="output", content=getattr(chunk, "delta", "") or "")
                elif chunk_type == "response.completed":
                    resp = getattr(chunk, "response", None)
                    if resp is None:
                        continue
                    extracted = []
                    output_types = []
                    for item in getattr(resp, "output", []) or []:
                        item_type = getattr(item, "type", "")
                        if item_type and item_type not in output_types:
                            output_types.append(item_type)
                        if item_type == "function_call":
                            extracted.append(
                                ToolCallData(
                                    call_id=getattr(item, "call_id", "") or "",
                                    name=getattr(item, "name", "") or "",
                                    arguments=getattr(item, "arguments", "{}") or "{}",
                                )
                            )
                    usage = getattr(resp, "usage", None)
                    total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
                    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                    yield StreamPacket(
                        type="completed",
                        content=CompletedPayload(
                            res_id=getattr(resp, "id", "") or "",
                            total_tokens=total_tokens,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            tool_calls=extracted,
                            output_types=output_types,
                        ),
                    )

                last_type = chunk_type

        except (RateLimitError, APITimeoutError, APIError):
            raise
        except Exception as e:
            yield StreamPacket(type="error", content=f"流处理异常:{type(e).__name__}: {e}")

    # ============================================================
    # 主管 ReAct 流式调用
    # ============================================================
    async def stream_call(
        self,
        *,
        agent_name: str,
        input_data: Union[list[dict], str],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Union[str, dict]] = "auto",
        previous_response_id: Optional[str] = None,
        thinking: str = "enabled",
        json_schema: Optional[dict] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[StreamPacket]:
        """
        主管 ReAct 流式调用。

        典型用法(在 BaseAgent 内部):
            input_data 第一轮:[{"role": "system", ...}, {"role": "user", ...}]
            input_data 后续轮:[{"type": "function_call_output", "call_id": ..., "output": ...}, ...]
                              + previous_response_id

        Yields:
            StreamPacket 流。BaseAgent 收集 reasoning / output 推送给前端,
            遇到 completed 包就检查 tool_calls,有就执行 tools 后再发一轮,无则退出。
        """
        kwargs = self._build_kwargs(
            agent_name=agent_name,
            input_data=input_data,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
            tools=tools,
            tool_choice=tool_choice,
            previous_response_id=previous_response_id,
            thinking=thinking,
            stream=True,
        )

        rt = self.config.get_for_agent(agent_name)
        _log_llm_call(
            agent_name=agent_name,
            model=rt.model,
            temperature=kwargs["temperature"],
            max_tokens=kwargs["max_output_tokens"],
            system_prompt=_extract_system(input_data),
            user_prompt=_extract_user(input_data),
            json_schema=json_schema,
            call_type="stream_call",
        )

        async with self._rate_limiter:
            async with self.concurrency_limiter:
                try:
                    logger.info(
                        f"流式调用 - agent: {agent_name}, model: {rt.model}, "
                        f"prev_res_id: {bool(previous_response_id)}, tools: {len(tools or [])}"
                    )
                    response = await self.client.responses.create(**kwargs)

                    if not hasattr(response, "__aiter__"):
                        # 防御性兜底:某些代理层可能把 stream 退化成同步对象
                        content = getattr(response, "output_text", "") or ""
                        if content:
                            yield StreamPacket(type="output", content=content, agent_name=agent_name)
                        yield StreamPacket(
                            type="completed",
                            content=CompletedPayload(
                                res_id=getattr(response, "id", "") or "",
                                total_tokens=0,
                                tool_calls=[],
                            ),
                            agent_name=agent_name,
                        )
                        return

                    async for packet in self._process_stream(response):
                        packet.agent_name = agent_name
                        yield packet

                except RateLimitError as e:
                    retry_after = 60
                    if hasattr(e, "headers") and e.headers:
                        retry_after = int(e.headers.get("retry-after", 60))
                    raise LLMRateLimitError(
                        str(e), retry_after=retry_after, provider="ark", model=rt.model
                    )
                except APITimeoutError as e:
                    raise LLMTimeoutError(str(e), provider="ark", model=rt.model)
                except APIError as e:
                    raise LLMAPIError(
                        str(e),
                        status_code=getattr(e, "status_code", 0),
                        provider="ark",
                        model=rt.model,
                    )
                except Exception as e:
                    log_exception(
                        logger, e, context=f"stream_call 异常 - agent: {agent_name}, model: {rt.model}"
                    )
                    raise

    # ============================================================
    # 专家一次性调用(同步语义)
    # ============================================================
    async def call(
        self,
        *,
        agent_name: Optional[str] = None,
        system_prompt: str = "",
        user_prompt: str = "",
        json_schema: Optional[dict] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        # ── backward-compat ──
        role: Optional[str] = None,
        prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> LLMResponse:
        """
        非流式一次性调用(底层仍走 stream + 聚合,以适配 ARK 的稳定形态)。

        新签名(关键参数全部 keyword-only):
            agent_name + system_prompt + user_prompt + json_schema

        旧签名兼容(LEGACY_ROLE_TO_AGENT 翻译):
            call(role="writer", prompt="...", system_prompt="...")
            call(role="extract", prompt="...", json_schema=...)

        Returns:
            LLMResponse(content 已清洗,content_raw 字段在 res_id 后,res_id 可用作续接)。
        """
        if agent_name is None:
            if role is None:
                raise ValueError("call() 需要 agent_name 或 role 参数二选一")
            agent_name = LEGACY_ROLE_TO_AGENT.get(role.lower())
            if agent_name is None:
                raise ValueError(
                    f"未知的 legacy role '{role}',可用:{list(LEGACY_ROLE_TO_AGENT.keys())}"
                )

        if not user_prompt and prompt:
            user_prompt = prompt
        if response_format == "json_schema" and not json_schema:
            json_schema = None

        # Writer(creator)默认不结构化输出
        rt = self.config.get_for_agent(agent_name)
        if rt.role == "creator":
            json_schema = None

        input_data = _build_input(system_prompt, user_prompt)

        # 走流式 + 聚合,得到完整 output_text + res_id
        full_text = []
        res_id = ""
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        try:
            async for packet in self.stream_call(
                agent_name=agent_name,
                input_data=input_data,
                json_schema=json_schema,
                max_tokens=max_tokens,
                temperature=temperature,
                # 一次性调用不需要 tools
                tools=None,
                tool_choice=None,
                previous_response_id=None,
            ):
                if packet.type == "output":
                    full_text.append(packet.content if isinstance(packet.content, str) else "")
                elif packet.type == "completed" and isinstance(packet.content, CompletedPayload):
                    res_id = packet.content.res_id
                    total_tokens = packet.content.total_tokens
                    input_tokens = packet.content.input_tokens
                    output_tokens = packet.content.output_tokens
                elif packet.type == "error":
                    raise LLMError(
                        packet.content if isinstance(packet.content, str) else "未知流错误",
                        provider="ark",
                        model=rt.model,
                    )
        except (LLMError, LLMAPIError, LLMTimeoutError, LLMRateLimitError):
            raise

        raw = "".join(full_text)
        cleaned = _clean_json_content(raw) if json_schema else raw
        return LLMResponse(
            content=cleaned,
            model=rt.model,
            provider="ark",
            usage={
                "total_tokens": total_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            res_id=res_id,
        )

    # ============================================================
    # 重试包装
    # ============================================================
    async def call_with_retry(
        self,
        *args,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **kwargs,
    ) -> LLMResponse:
        """带重试的 call()。RateLimit 等待 retry_after,其他错误指数退避。"""
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"重试 call {attempt + 1}/{max_retries}")
                return await self.call(*args, **kwargs)
            except LLMRateLimitError as e:
                logger.warning(f"call 限流,等待 {e.retry_after}s 后重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(e.retry_after)
                last_error = e
            except (LLMTimeoutError, LLMAPIError) as e:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"call 失败({type(e).__name__}),等待 {wait_time:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
        raise last_error  # type: ignore

    async def stream_call_with_retry(
        self,
        *args,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **kwargs,
    ) -> AsyncIterator[StreamPacket]:
        """
        带重试的 stream_call()。

        注意:流式重试会从头开始,**调用方负责处理重复的 reasoning/output 片段**
        (例如重置前端缓冲)。这是 SSE 重试的本质代价。
        """
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"重试 stream_call {attempt + 1}/{max_retries}")
                async for packet in self.stream_call(*args, **kwargs):
                    yield packet
                return
            except LLMRateLimitError as e:
                logger.warning(
                    f"stream_call 限流,等待 {e.retry_after}s 后重试 ({attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(e.retry_after)
                last_error = e
            except (LLMTimeoutError, LLMAPIError) as e:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"stream_call 失败({type(e).__name__}),等待 {wait_time:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
        raise last_error  # type: ignore

    # ============================================================
    # 旧 role-based API(backward-compat shim)
    # ============================================================
    async def stream(
        self,
        role: str,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_schema: Optional[dict] = None,
        **_ignored,
    ) -> AsyncIterator[str]:
        """
        旧 role-based 流式接口。调用方只关心 token 流(纯文本),不关心 reasoning。

        新代码请改用 stream_call() 直接消费 StreamPacket。
        """
        agent_name = LEGACY_ROLE_TO_AGENT.get(role.lower())
        if agent_name is None:
            raise ValueError(
                f"未知的 legacy role '{role}',可用:{list(LEGACY_ROLE_TO_AGENT.keys())}"
            )
        input_data = _build_input(system_prompt, prompt)
        async for packet in self.stream_call(
            agent_name=agent_name,
            input_data=input_data,
            json_schema=json_schema,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            if packet.type == "output" and isinstance(packet.content, str):
                yield packet.content
            elif packet.type == "error":
                raise LLMError(
                    packet.content if isinstance(packet.content, str) else "stream error",
                    provider="ark",
                )

    async def stream_with_retry(
        self,
        role: str,
        prompt: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **kwargs,
    ) -> AsyncIterator[str]:
        """旧 role-based 流式 + 重试。"""
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"stream(role) 重试 {attempt + 1}/{max_retries}")
                async for token in self.stream(role, prompt, **kwargs):
                    yield token
                return
            except LLMRateLimitError as e:
                await asyncio.sleep(e.retry_after)
                last_error = e
            except (LLMTimeoutError, LLMAPIError) as e:
                await asyncio.sleep(retry_delay * (2 ** attempt))
                last_error = e
        raise last_error  # type: ignore


# ============================================================
# 模块级辅助:input 构造 / 提取
# ============================================================

def _build_input(system_prompt: str, user_prompt: str) -> list[dict]:
    """构造 [system, user] 输入数组,system 为空时省略。"""
    msgs = []
    if system_prompt and system_prompt.strip():
        msgs.append({"role": "system", "content": system_prompt.strip()})
    msgs.append({"role": "user", "content": user_prompt})
    return msgs


def _extract_system(input_data: Union[list[dict], str]) -> str:
    """从 input_data 中提取 system 段,用于 prompt.log。"""
    if isinstance(input_data, str):
        return ""
    for msg in input_data:
        if isinstance(msg, dict) and msg.get("role") == "system":
            return msg.get("content", "") or ""
    return ""


def _extract_user(input_data: Union[list[dict], str]) -> str:
    """从 input_data 中提取 user 段(只取最后一个),用于 prompt.log。"""
    if isinstance(input_data, str):
        return input_data
    for msg in reversed(input_data):
        if isinstance(msg, dict):
            if msg.get("role") == "user":
                return msg.get("content", "") or ""
            if msg.get("type") == "function_call_output":
                return f"[tool_output:{msg.get('call_id', '')}] {msg.get('output', '')}"
    return ""


# ============================================================
# 便捷函数(带重试)
# ============================================================

async def quick_call(
    role_or_agent: str,
    prompt: str,
    *,
    system_prompt: str = "",
    json_schema: Optional[dict] = None,
    max_retries: int = LLMClient.DEFAULT_MAX_RETRIES,
) -> str:
    """
    便捷的一次性调用,返回纯文本内容。

    role_or_agent 自动判断:在 LEGACY_ROLE_TO_AGENT 中视作 role,否则视作 agent_name。
    """
    client = LLMClient()
    if role_or_agent.lower() in LEGACY_ROLE_TO_AGENT:
        resp = await client.call_with_retry(
            role=role_or_agent,
            prompt=prompt,
            system_prompt=system_prompt,
            json_schema=json_schema,
            max_retries=max_retries,
        )
    else:
        resp = await client.call_with_retry(
            agent_name=role_or_agent,
            user_prompt=prompt,
            system_prompt=system_prompt,
            json_schema=json_schema,
            max_retries=max_retries,
        )
    return resp.content


async def quick_stream(
    role: str,
    prompt: str,
    *,
    max_retries: int = LLMClient.DEFAULT_MAX_RETRIES,
    **kwargs,
) -> AsyncIterator[str]:
    """便捷的旧 role-based 流式调用,yield 纯 token 文本。新代码请用 LLMClient.stream_call。"""
    client = LLMClient()
    async for token in client.stream_with_retry(role, prompt, max_retries=max_retries, **kwargs):
        yield token
