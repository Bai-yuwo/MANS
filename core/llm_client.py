"""
core/llm_client.py

LLM 统一调用封装（Ark SDK 版本：使用 OpenAI 兼容的 responses API）。

职责边界：
    - 为所有模块提供统一的 LLM 调用接口，外部不直接调用 API。
    - 当前仅支持 doubao（Ark 平台），通过 OpenAI 兼容的 responses.create() API 调用。
    - 支持非流式调用（call）和流式调用（stream）两种模式。
    - 实现速率限制（Token 桶算法）和并发控制（asyncio.Semaphore）。
    - 提供带重试机制的便捷方法（call_with_retry, stream_with_retry）。
    - 支持结构化输出（json_schema）和深度思考（thinking 模式）。
    - 统一异常体系，便于调用方按错误类型处理。

设计原则：
    1. 单一 Provider：当前仅支持 doubao，其他 Provider 留空待扩展。
    2. 角色化调用：根据角色（writer/generator/trim/extract）自动选择对应模型。
    3. 结构化输出：非 writer 角色统一使用 json_schema，writer 角色纯文本输出。
    4. 深度思考：所有模型默认开启 thinking（extra_body），不调节 reasoning 参数。
    5. 降级策略移除：不再使用模型降级，直接使用 .env 中配置的模型。
    6. JSON 清洗：多级防御策略清洗 LLM 输出中的 Markdown 代码块、think 标签等污染。

速率限制与并发控制：
    - RateLimiter: Token 桶算法控制每秒请求数，防止触发厂商限流。
    - ConcurrencyLimiter: 全局单例信号量限制同时进行的 LLM 调用数量，
      防止内存/连接池耗尽和网络拥塞。

异常体系：
    - LLMError: 基类异常，包含 provider 和 model 信息。
    - LLMAPIError: API 调用异常（网络错误、认证失败等），包含 status_code。
    - LLMTimeoutError: 调用超时异常。
    - LLMRateLimitError: 限流异常，包含 retry_after 字段指导重试等待时间。

典型用法：
    client = LLMClient()

    # 非流式调用（自动重试）
    response = await client.call_with_retry(
        role="writer",
        prompt="生成一段小说开头...",
        max_tokens=2000,
        max_retries=3
    )

    # 流式调用（逐 token 输出）
    async for token in client.stream(
        role="writer",
        prompt="生成一段小说开头..."
    ):
        print(token, end="")
"""

import asyncio
import inspect
import json
import re
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass

from openai import AsyncOpenAI, APIError, RateLimitError, APITimeoutError

from core.config import get_config, ProviderConfig
from core.logging_config import get_logger, log_exception

logger = get_logger('core.llm_client')
# Prompt 专用日志器：用于记录每次 LLM 调用的完整请求上下文
prompt_logger = get_logger('prompt')


def _get_caller_info():
    """
    从调用栈中提取调用者信息，用于日志标识请求来源。

    遍历调用栈，跳过 llm_client 内部的调用以及 asyncio/logging 等库调用，
    定位到真正发起 LLM 请求的业务代码位置。

    Returns:
        dict，包含 source_function、source_file、source_line、module、program、step。
    """
    stack = inspect.stack()
    for frame_info in stack[2:]:
        filename = frame_info.filename
        func_name = frame_info.function
        lineno = frame_info.lineno

        if 'llm_client.py' in filename:
            continue
        if any(skip in filename for skip in ['asyncio', 'logging', 'concurrent', 'threading']):
            continue

        module_name = filename.replace('\\', '/').split('/')[-1].replace('.py', '')

        program = "unknown"
        step = "unknown"

        if 'writer' in filename:
            program = "writer"
            if 'regenerate' in func_name:
                step = "scene_regeneration"
            elif 'rewrite' in func_name:
                step = "scene_rewrite"
            else:
                step = "scene_writing"
        elif 'bible_generator' in filename:
            program = "generator"
            step = "bible_generation"
        elif 'character_generator' in filename:
            program = "generator"
            step = "character_generation"
        elif 'outline_generator' in filename:
            program = "generator"
            step = "outline_generation"
        elif 'arc_planner' in filename:
            program = "generator"
            step = "arc_planning"
        elif 'chapter_planner' in filename:
            program = "generator"
            step = "chapter_planning"
        elif 'update_extractor' in filename:
            program = "update_extractor"
            step = "state_extraction"
        elif 'injection_engine' in filename:
            program = "injection_engine"
            step = "context_building"
        elif 'web_app' in filename:
            program = "web_app"
            step = "api_endpoint"
        elif 'generator' in filename:
            program = "generator"
            step = "generation"

        return {
            "source_function": func_name,
            "source_file": filename,
            "source_line": lineno,
            "module": module_name,
            "program": program,
            "step": step,
        }

    return {
        "source_function": "unknown",
        "source_file": "unknown",
        "source_line": 0,
        "module": "unknown",
        "program": "unknown",
        "step": "unknown",
    }


def _log_llm_call(role, model, max_tokens, temperature, system_prompt, prompt,
                  json_schema, caller_info, call_type="call"):
    """
    将单次 LLM 调用的请求上下文输出到控制台和 prompt.log。

    输出内容经过结构化处理，便于开发时快速审阅 prompt 内容和调用参数。
    控制台输出为紧凑的多行格式；prompt.log 为更详细的审计格式。

    Args:
        role: LLM 角色（writer/generator/trim/extract）。
        model: 实际调用的模型名称。
        max_tokens: 最大输出 token 数。
        temperature: 采样温度。
        system_prompt: 系统提示词（可能为空）。
        prompt: 用户提示词。
        json_schema: JSON Schema 定义（可能为 None）。
        caller_info: _get_caller_info() 返回的字典。
        call_type: 调用类型标识，"call" 或 "stream"。
    """
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    schema_status = f"是 (name={json_schema.get('name', 'unnamed')})" if json_schema else "否"
    sys_len = len(system_prompt) if system_prompt else 0
    usr_len = len(prompt) if prompt else 0
    call_type_label = "流式调用" if call_type == "stream" else "非流式调用"

    # ── 控制台输出（紧凑格式，便于开发时查看）──
    console_lines = [
        "",
        "╔" + "═" * 78 + "╗",
        f"║ LLM {call_type_label}  |  Role: {role:<12}  |  Model: {model:<20} ║",
        "╠" + "═" * 78 + "╣",
        f"║ Source   : {caller_info['source_function']:<30} ({caller_info['module']}.py:{caller_info['source_line']}) ║",
        f"║ Program  : {caller_info['program']:<15} | Step: {caller_info['step']:<25} ║",
        f"║ Time     : {now:<20} | Temp: {temperature:<6} | MaxTokens: {max_tokens:<6} ║",
        f"║ Schema   : {schema_status:<66} ║",
        "╠" + "═" * 78 + "╣",
    ]

    if system_prompt and system_prompt.strip():
        # 截断过长的 system prompt，控制台只显示前 200 字符
        sys_display = system_prompt.strip().replace('\n', ' ')
        if len(sys_display) > 200:
            sys_display = sys_display[:200] + " ..."
        console_lines.append(f"║ SYSTEM   ({sys_len} chars): {sys_display:<55} ║")
    else:
        console_lines.append(f"║ SYSTEM   : (无)                                                            ║")

    # 截断过长的 user prompt，控制台只显示前 300 字符
    usr_display = prompt.strip().replace('\n', ' ') if prompt else ""
    if len(usr_display) > 300:
        usr_display = usr_display[:300] + " ..."
    console_lines.append(f"║ USER     ({usr_len} chars): {usr_display:<55} ║")
    console_lines.append("╚" + "═" * 78 + "╝")

    console_msg = "\n".join(console_lines)
    logger.info(console_msg)

    # ── prompt.log 输出（详细审计格式）──
    prompt_lines = [
        "",
        "═══════════════════════════════════════════════════════════════════════════════",
        f"[CALL_TYPE]     {call_type_label}",
        f"[TIME]          {now}",
        f"[PROGRAM]       {caller_info['program']}",
        f"[STEP]          {caller_info['step']}",
        f"[SOURCE]        {caller_info['source_function']} @ {caller_info['source_file']}:{caller_info['source_line']}",
        f"[ROLE]          {role}",
        f"[MODEL]         {model}",
        f"[MAX_TOKENS]    {max_tokens}",
        f"[TEMPERATURE]   {temperature}",
        f"[JSON_SCHEMA]   {schema_status}",
        "───────────────────────────────────────────────────────────────────────────────",
    ]

    if json_schema:
        try:
            schema_json = json.dumps(json_schema, ensure_ascii=False, indent=2)
            prompt_lines.append("[SCHEMA DEFINITION]")
            prompt_lines.append(schema_json)
            prompt_lines.append("───────────────────────────────────────────────────────────────────────────────")
        except Exception:
            prompt_lines.append("[SCHEMA DEFINITION] (序列化失败)")

    if system_prompt and system_prompt.strip():
        prompt_lines.append(f"[SYSTEM PROMPT] ({sys_len} chars)")
        prompt_lines.append(system_prompt.strip())
        prompt_lines.append("───────────────────────────────────────────────────────────────────────────────")

    if prompt and prompt.strip():
        prompt_lines.append(f"[USER PROMPT] ({usr_len} chars)")
        prompt_lines.append(prompt.strip())
        prompt_lines.append("───────────────────────────────────────────────────────────────────────────────")

    prompt_lines.append("")

    prompt_msg = "\n".join(prompt_lines)
    prompt_logger.info(prompt_msg)


# ============================================================
# 速率限制器（Token 桶算法）
# ============================================================

class RateLimiter:
    """
    Token 桶速率限制器。

    用于控制 API 调用速率，防止短时间内过多请求触发厂商限流。
    令牌桶算法相比固定窗口更平滑，允许一定程度的突发请求。

    工作原理：
        - 桶容量 = max_requests，初始满。
        - 每 time_window 秒 refill max_requests 个令牌。
        - 每次 acquire() 消耗 1 个令牌，无令牌时计算等待时间。

    使用方式：
        async with limiter:
            await api_call()

    Attributes:
        max_requests: 时间窗口内最大请求数。
        time_window: 时间窗口长度（秒）。
    """

    def __init__(self, max_requests: int = 10, time_window: float = 1.0):
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens = max_requests
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """
        获取一个令牌。若无可用令牌，返回需要等待的时间（秒）。

        Returns:
            0 表示成功获取令牌；正数表示需要等待的秒数。
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(
                self.max_requests,
                self.tokens + elapsed * (self.max_requests / self.time_window)
            )
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.time_window / self.max_requests)
                logger.debug(f"速率限制：等待 {wait_time:.2f}s")
                return wait_time
            else:
                self.tokens -= 1
                return 0

    async def __aenter__(self):
        """异步上下文管理器入口：获取令牌，必要时等待。"""
        wait_time = await self.acquire()
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口：无需释放令牌（令牌桶已自动管理）。"""
        pass


# ============================================================
# 并发信号量（全局单例）
# ============================================================

class ConcurrencyLimiter:
    """
    全局并发限制器（单例模式）。

    限制同时进行的 LLM 调用数量，防止：
        1. 触发 API 厂商并发限制。
        2. 内存/连接池耗尽。
        3. 网络拥塞。

    单例实现：
        使用 __new__ 确保全局只有一个实例，即使在多模块导入时。
        首次初始化后 _initialized 标志防止重复初始化。

    使用方式：
        async with limiter:
            await api_call()

    Attributes:
        max_concurrent: 最大并发数。
        current_count: 当前并发数（只读属性）。
    """

    _instance = None
    _lock = asyncio.Lock()

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

        logger.info(f"并发限制器初始化：最大并发 {max_concurrent}")

    async def __aenter__(self):
        """获取信号量，进入并发保护区域。"""
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """释放信号量，退出并发保护区域。"""
        self.semaphore.release()

    @property
    def current_count(self) -> int:
        """当前正在进行的 LLM 调用数量。"""
        return self.max_concurrent - self.semaphore._value


# ============================================================
# 异常定义
# ============================================================

class LLMError(Exception):
    """
    LLM 调用基础异常。

    Attributes:
        provider: 发生异常的 Provider 名称。
        model: 发生异常的模型名称。
    """
    def __init__(self, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class LLMAPIError(LLMError):
    """
    API 调用异常（网络错误、认证失败、服务器错误等）。

    Attributes:
        status_code: HTTP 状态码（如 401/429/500）。
    """
    def __init__(self, message: str, status_code: int = 0, **kwargs):
        super().__init__(message, **kwargs)
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    """调用超时异常（连接超时或读取超时）。"""
    pass


class LLMRateLimitError(LLMError):
    """
    限流异常（API 返回 429 状态码）。

    Attributes:
        retry_after: 建议等待秒数后再重试。
    """
    def __init__(self, message: str, retry_after: int = 60, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


# ============================================================
# 响应模型
# ============================================================

@dataclass
class LLMResponse:
    """
    LLM 响应封装。

    统一所有 LLM 调用的返回格式，便于调用方统一处理。

    Attributes:
        content: 生成的文本内容（已清洗 JSON 污染）。
        model: 实际使用的模型名称。
        provider: 使用的 Provider 名称。
        usage: Token 使用统计字典（prompt_tokens/completion_tokens/total_tokens）。
        finish_reason: 生成结束原因（如"stop"/"length"，部分 Provider 可能为空）。
    """
    content: str                        # 生成的文本内容
    model: str                          # 实际使用的模型
    provider: str                       # 使用的 Provider
    usage: dict = None                  # Token 使用统计
    finish_reason: str = ""             # 结束原因


# ============================================================
# Token 计数器
# ============================================================

class TokenCounter:
    """
    Token 计数工具。

    基于 tiktoken 库实现，使用 cl100k_base 编码器（与 GPT-4/Claude 兼容）。
    注意：不同 Provider 的 tokenizer 可能不同，cl100k_base 只是近似估计。

    使用方式：
        counter = TokenCounter()
        token_count = counter.count("这是一段中文文本")
    """

    ENCODER_MAP = {
        "doubao": "cl100k_base",
        "qwen": "cl100k_base",
        "glm": "cl100k_base",
        "openai": "cl100k_base",
    }

    def __init__(self, provider: str = "doubao"):
        import tiktoken
        self.provider = provider
        encoder_name = self.ENCODER_MAP.get(provider, "cl100k_base")
        try:
            self.encoder = tiktoken.get_encoding(encoder_name)
        except Exception:
            self.encoder = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        """
        计算文本的 token 数量。

        Args:
            text: 输入文本。

        Returns:
            Token 数量整数。空文本返回 0。
        """
        if not text:
            return 0
        return len(self.encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        """
        计算消息列表的总 token 数量。

        每条消息额外加 4 个 token（OpenAI 的消息格式开销）。

        Args:
            messages: OpenAI 格式的消息列表（{"role": "...", "content": "..."}）。

        Returns:
            总 token 数量。
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count(content)
            total += 4
        return total


# ============================================================
# LLM 客户端
# ============================================================

class LLMClient:
    """
    LLM 统一调用客户端（Ark SDK 版本）。

    核心能力：
        1. 角色化模型选择：根据 role 自动从配置中选取对应模型。
        2. 速率限制：通过 RateLimiter 控制每秒请求数。
        3. 并发控制：通过 ConcurrencyLimiter 限制同时调用数。
        4. JSON 清洗：多级防御清洗 LLM 输出中的格式污染。
        5. 结构化输出：非 writer 角色自动使用 json_schema。
        6. 深度思考：所有调用默认开启 thinking 模式。

    Provider 支持：
        当前仅支持 doubao（Ark 平台），通过 OpenAI 兼容接口调用。
        初始化时检查 ACTIVE_PROVIDER，非 doubao 时抛出 NotImplementedError。

    使用方式：
        client = LLMClient()
        response = await client.call_with_retry(role="writer", prompt="...")
    """

    DEFAULT_CONCURRENT_LIMIT = 5
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0

    _rate_limiters: dict[str, RateLimiter] = {}

    @staticmethod
    def _clean_json_content(content: str) -> str:
        """
        JSON 清洗（多级防御）。

        清洗策略（按优先级）：
            1. 去除前后空白字符和 UTF-8 BOM。
            2. 去除 Markdown 代码块包裹（```json ... ```）。
            3. 去除推理模型 <think> 标签内容。
            4. 强力截取：寻找文本中第一个 { 或 [ 到最后一个 } 或 ]，提取 JSON 主体。
            5. 去除尾部可能污染 JSON 的逗号。

        无论底层是否使用结构化参数，解析前都建议过此清洗。

        Args:
            content: LLM 返回的原始文本。

        Returns:
            清洗后的字符串，更适合 json.loads() 解析。
        """
        text = content.strip()
        text = text.lstrip('\ufeff')

        pattern = r'^```(?:json)?\s*\n?(.*?)\n?```\s*$'
        match = re.match(pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        first_brace = text.find('{')
        first_bracket = text.find('[')

        if first_brace == -1 and first_bracket == -1:
            return text

        if first_brace == -1:
            start = first_bracket
        elif first_bracket == -1:
            start = first_brace
        else:
            start = min(first_brace, first_bracket)

        if text[start] == '{':
            end = text.rfind('}')
        else:
            end = text.rfind(']')

        if end != -1 and end > start:
            text = text[start:end + 1]

        text = text.rstrip().rstrip(',').rstrip()
        return text

    def __init__(self,
                 rate_limit: Optional[int] = None,
                 concurrent_limit: Optional[int] = None):
        """
        初始化 LLMClient。

        Args:
            rate_limit: 每秒最大请求数（覆盖 config 默认值）。
            concurrent_limit: 最大并发数（覆盖默认值 5）。

        Raises:
            NotImplementedError: 当 ACTIVE_PROVIDER 不是 doubao 时抛出。
        """
        self.config = get_config()
        self.token_counters: dict[str, TokenCounter] = {}

        provider = self.config.get_active_provider()

        # 初始化异步 OpenAI 客户端（兼容任意 OpenAI-compatible API）
        self.client = AsyncOpenAI(
            base_url=provider.base_url,
            api_key=provider.api_key,
        )

        self.rate_limit = rate_limit or self.config.RATE_LIMIT
        self.concurrency_limiter = ConcurrencyLimiter(
            concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT
        )

        logger.info(
            f"LLMClient 初始化 - Provider: {self.config.ACTIVE_PROVIDER}, "
            f"BaseURL: {provider.base_url}, "
            f"速率限制: {self.rate_limit}/s, "
            f"并发限制: {concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT}"
        )

    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        """
        获取指定 Provider 的 RateLimiter（懒加载）。

        每个 Provider 有独立的 RateLimiter，避免不同 Provider 的限流策略互相影响。
        """
        if provider_name not in self.__class__._rate_limiters:
            self.__class__._rate_limiters[provider_name] = RateLimiter(
                max_requests=self.rate_limit,
                time_window=1.0
            )
        return self.__class__._rate_limiters[provider_name]

    def _get_token_counter(self, provider: str) -> TokenCounter:
        """获取指定 Provider 的 TokenCounter（懒加载）。"""
        if provider not in self.token_counters:
            self.token_counters[provider] = TokenCounter(provider)
        return self.token_counters[provider]

    def _build_input(self, system_prompt: str, prompt: str) -> list[dict]:
        """构建 input 数组，所有模型都使用 system + user 角色。"""
        messages = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_call_kwargs(
        self,
        role: str,
        model: str,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        json_schema: Optional[dict] = None,
    ) -> dict:
        """
        构建 responses.create 的 kwargs 字典。

        核心逻辑：
            - 豆包 provider 开启 thinking 模式（extra_body={"thinking": {"type": "enabled"}}），
              其他 provider 不支持此参数，发送会导致 400 错误。
            - 非 writer 角色且传入 json_schema 时，启用结构化输出（text.format.type="json_schema"）。
            - writer 角色不使用结构化输出，返回纯文本。
        """
        kwargs = {
            "model": model,
            "input": self._build_input(system_prompt, prompt),
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }

        # 深度思考模式仅豆包 provider 支持，其他 provider 会返回 400
        active_provider = self.config.ACTIVE_PROVIDER
        if active_provider == "doubao":
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        # 非 writer 角色且传入了 schema，使用 json_schema 结构化输出
        if role != "writer" and json_schema:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": json_schema.get("name", "output"),
                    "strict": True,
                    "schema": json_schema.get("schema", {}),
                }
            }

        return kwargs

    async def call(
        self,
        role: str,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        response_format: Optional[str] = None,
        json_schema: Optional[dict] = None,
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600,
    ) -> LLMResponse:
        """
        非流式调用 LLM（带速率限制和并发控制）。

        调用流程：
            1. 根据 role 从配置获取对应模型。
            2. 若未显式传入 max_tokens 或 temperature，自动从 config 按角色获取默认值。
            3. 若 response_format="json_schema" 但没传 schema，忽略结构化输出。
            4. 获取 RateLimiter 和 ConcurrencyLimiter。
            5. 在双重保护下执行实际调用（_call_impl）。

        Args:
            role: 角色（writer/generator/trim/extract/embed）。
            prompt: 用户提示词。
            system_prompt: 系统提示词。
            max_tokens: 最大生成 token 数（None 则从 config 按角色获取默认值）。
            temperature: 温度参数（None 则从 config 按角色获取默认值）。
            response_format: 响应格式（保留参数，仅作为 json_schema 开关）。
            json_schema: JSON Schema 定义（非 writer 角色使用）。
            connect_timeout: 连接超时（保留参数，由 SDK 内部处理）。
            sock_read_timeout: 读取超时（保留参数，由 SDK 内部处理）。
            total_timeout: 总超时（保留参数，由 SDK 内部处理）。

        Returns:
            LLMResponse 对象，包含清洗后的 content。
        """
        model, provider = self.config.get_model_for_role(role)

        # 从 config 获取角色默认值
        effective_max_tokens = max_tokens if max_tokens is not None else self.config.get_max_tokens_for_role(role)
        effective_temperature = temperature if temperature is not None else self.config.get_temperature_for_role(role)

        # 如果调用方通过 response_format 声明 json_schema 但没传 schema，忽略
        if response_format == "json_schema" and not json_schema:
            json_schema = None

        rate_limiter = self._get_rate_limiter(provider.name)

        async with rate_limiter:
            async with self.concurrency_limiter:
                return await self._call_impl(
                    role=role,
                    model=model,
                    provider=provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=effective_max_tokens,
                    temperature=effective_temperature,
                    json_schema=json_schema,
                )

    async def _call_impl(
        self,
        role: str,
        model: str,
        provider: ProviderConfig,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        json_schema: Optional[dict] = None,
    ) -> LLMResponse:
        """
        实际的 LLM 调用实现（非流式）。

        直接调用 Ark SDK 的 responses.create() 方法，处理响应并清洗内容。
        所有异常被转换为 LLMError 子类，便于调用方统一处理。
        """
        kwargs = self._build_call_kwargs(
            role=role,
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )

        caller_info = _get_caller_info()
        _log_llm_call(
            role=role, model=model, max_tokens=max_tokens, temperature=temperature,
            system_prompt=system_prompt, prompt=prompt,
            json_schema=json_schema, caller_info=caller_info, call_type="call"
        )

        try:
            logger.info(
                f"调用模型: {model} (Provider: {provider.name}, Role: {role}, "
                f"并发: {self.concurrency_limiter.current_count}/{self.concurrency_limiter.max_concurrent})"
            )
            logger.debug(f"Prompt长度: {len(prompt)} 字符, max_tokens: {max_tokens}")

            start_time = time.time()

            response = await self.client.responses.create(**kwargs)

            elapsed = time.time() - start_time

            content = getattr(response, 'output_text', '') or ''

            usage = {}
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": getattr(response.usage, 'input_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'output_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }

            logger.info(
                f"模型调用成功 - 模型: {model}, "
                f"耗时: {elapsed:.2f}s, "
                f"输入Tokens: {usage.get('prompt_tokens', 'N/A')}, "
                f"输出Tokens: {usage.get('completion_tokens', 'N/A')}, "
                f"总Tokens: {usage.get('total_tokens', 'N/A')}"
            )

            cleaned_content = LLMClient._clean_json_content(content)

            return LLMResponse(
                content=cleaned_content,
                model=model,
                provider=provider.name,
                usage=usage,
                finish_reason="",
            )

        except RateLimitError as e:
            retry_after = 60
            if hasattr(e, 'headers') and e.headers:
                retry_after = int(e.headers.get('retry-after', 60))
            logger.warning(f"模型 {model} 触发限流，等待 {retry_after}s 后重试")
            raise LLMRateLimitError(
                str(e),
                retry_after=retry_after,
                provider=provider.name,
                model=model
            )
        except APITimeoutError as e:
            logger.error(f"模型调用超时 - 模型: {model}")
            raise LLMTimeoutError(
                str(e),
                provider=provider.name,
                model=model
            )
        except APIError as e:
            status_code = getattr(e, 'status_code', 0)
            logger.error(f"API调用失败 - 状态码: {status_code}, 模型: {model}, 错误: {str(e)}")
            raise LLMAPIError(
                str(e),
                status_code=status_code,
                provider=provider.name,
                model=model
            )
        except Exception as e:
            log_exception(logger, e, context=f"模型调用异常 - 模型: {model}, Role: {role}")
            raise

    async def stream(
        self,
        role: str,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_schema: Optional[dict] = None,
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600,
    ) -> AsyncIterator[str]:
        """
        流式调用 LLM。

        与 call() 的区别：
            - 通过 stream=True 启用 SSE 流式输出。
            - 逐 token yield 给调用方，便于实时展示生成进度。
            - 生成完成后不返回完整响应，仅 yield token。

        事件处理：
            只处理 type="response.output_text.delta" 的事件，提取 delta 字段 yield。
            其他事件（如 thinking 进度）被忽略。

        Args:
            role: 角色。
            prompt: 用户提示词。
            system_prompt: 系统提示词。
            max_tokens: 最大生成 token 数（None 则从 config 按角色获取默认值）。
            temperature: 温度参数（None 则从 config 按角色获取默认值）。
            json_schema: JSON Schema 定义（非 writer 角色使用结构化输出）。
            connect_timeout: 连接超时（保留参数）。
            sock_read_timeout: 读取超时（保留参数）。
            total_timeout: 总超时（保留参数）。

        Yields:
            生成的文本片段（token）。
        """
        model, provider = self.config.get_model_for_role(role)

        # 从 config 获取角色默认值
        effective_max_tokens = max_tokens if max_tokens is not None else self.config.get_max_tokens_for_role(role)
        effective_temperature = temperature if temperature is not None else self.config.get_temperature_for_role(role)

        rate_limiter = self._get_rate_limiter(provider.name)

        async with rate_limiter:
            async with self.concurrency_limiter:
                async for token in self._stream_impl(
                    role=role,
                    model=model,
                    provider=provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=effective_max_tokens,
                    temperature=effective_temperature,
                    json_schema=json_schema,
                ):
                    yield token

    async def _stream_impl(
        self,
        role: str,
        model: str,
        provider: ProviderConfig,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        json_schema: Optional[dict] = None,
    ) -> AsyncIterator[str]:
        """
        实际的流式 LLM 调用实现。

        通过 responses.create(stream=True) 获取事件流，
        逐事件处理并 yield text delta。
        """
        kwargs = self._build_call_kwargs(
            role=role,
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )
        kwargs["stream"] = True

        caller_info = _get_caller_info()
        _log_llm_call(
            role=role, model=model, max_tokens=max_tokens, temperature=temperature,
            system_prompt=system_prompt, prompt=prompt,
            json_schema=json_schema, caller_info=caller_info, call_type="stream"
        )

        try:
            logger.info(
                f"开始流式调用 - 模型: {model} (Provider: {provider.name}, Role: {role}, "
                f"并发: {self.concurrency_limiter.current_count}/{self.concurrency_limiter.max_concurrent})"
            )
            logger.debug(f"Prompt长度: {len(prompt)} 字符")

            start_time = time.time()
            token_count = 0
            first_token_received = False

            response = await self.client.responses.create(**kwargs)

            # 调试：检查响应对象类型
            response_type = type(response).__name__
            logger.info(f"流式响应对象类型: {response_type}")

            # 防御性检查：如果 provider 不支持流式，可能返回普通响应对象
            if not hasattr(response, '__aiter__'):
                logger.warning(
                    f"模型 {model} 不支持 responses.create 流式模式，"
                    f"返回类型为 {response_type}，尝试提取完整内容"
                )
                content = getattr(response, 'output_text', '') or ''
                if content:
                    yield content
                return

            async for event in response:
                if not first_token_received:
                    first_token_received = True
                    elapsed = time.time() - start_time
                    event_type = getattr(event, 'type', 'unknown')
                    logger.info(
                        f"收到首个事件 - 耗时: {elapsed:.2f}s, "
                        f"事件类型: {event_type}"
                    )

                # 记录所有事件类型用于调试兼容性
                event_type = getattr(event, 'type', 'unknown')
                logger.debug(f"SSE事件: type={event_type}")

                # 尝试从多种可能的事件类型中提取文本增量
                delta = None

                if event_type == "response.output_text.delta":
                    delta = getattr(event, 'delta', None)
                elif hasattr(event, 'delta') and event.delta:
                    # 兼容其他可能返回 delta 的事件
                    delta = event.delta

                if delta:
                    token_count += 1
                    yield delta

            elapsed = time.time() - start_time
            logger.info(
                f"流式生成完成 - 模型: {model}, "
                f"耗时: {elapsed:.2f}s, "
                f"生成Tokens: {token_count}"
            )

        except RateLimitError as e:
            retry_after = 60
            if hasattr(e, 'headers') and e.headers:
                retry_after = int(e.headers.get('retry-after', 60))
            logger.warning(f"流式调用触发限流，等待 {retry_after}s")
            raise LLMRateLimitError(
                str(e),
                retry_after=retry_after,
                provider=provider.name,
                model=model
            )
        except APITimeoutError as e:
            elapsed = time.time() - start_time
            if not first_token_received:
                logger.error(f"流式调用连接超时 - 模型: {model}, 已等待: {elapsed:.2f}s")
                raise LLMTimeoutError(
                    f"Connection timeout",
                    provider=provider.name,
                    model=model
                )
            else:
                logger.error(f"流式调用读取超时 - 模型: {model}, 已生成{token_count}个token, 已等待: {elapsed:.2f}s")
                raise LLMTimeoutError(
                    f"Read timeout (no data received)",
                    provider=provider.name,
                    model=model
                )
        except APIError as e:
            status_code = getattr(e, 'status_code', 0)
            logger.error(f"流式API调用失败 - 状态码: {status_code}, 模型: {model}, 错误: {str(e)}")
            raise LLMAPIError(
                str(e),
                status_code=status_code,
                provider=provider.name,
                model=model
            )
        except Exception as e:
            log_exception(logger, e, context=f"流式调用异常 - 模型: {model}, Role: {role}")
            raise

    async def call_with_retry(
        self,
        role: str,
        prompt: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        **kwargs
    ) -> LLMResponse:
        """
        带重试机制的 LLM 调用。

        重试策略：
            - LLMRateLimitError: 等待服务端建议的 retry_after 秒，然后重试。
            - LLMAPIError / LLMTimeoutError: 指数退避（retry_delay * 2^attempt），然后重试。
            - 其他异常: 不重试，直接抛出。

        Args:
            role: 角色。
            prompt: 提示词。
            max_retries: 最大重试次数（默认 3）。
            retry_delay: 基础重试间隔（默认 1.0 秒）。
            **kwargs: 其他参数传递给 call()。

        Returns:
            LLMResponse 对象。

        Raises:
            LLMError: 重试耗尽后抛出最后一次异常。
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"第 {attempt + 1}/{max_retries} 次重试调用")
                return await self.call(role, prompt, **kwargs)

            except LLMRateLimitError as e:
                wait_time = e.retry_after
                logger.warning(
                    f"限流错误，等待 {wait_time}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e

            except (LLMAPIError, LLMTimeoutError) as e:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"调用失败 ({type(e).__name__}), "
                    f"等待 {wait_time:.1f}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e

        logger.error(f"重试耗尽 ({max_retries} 次尝试后仍失败)")
        raise last_error

    async def stream_with_retry(
        self,
        role: str,
        prompt: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        带重试机制的流式 LLM 调用。

        重试策略与 call_with_retry() 相同，但针对流式调用优化：
            流式调用失败后，下一次重试从头开始生成，调用方需要处理重复内容。

        Args:
            role: 角色。
            prompt: 提示词。
            max_retries: 最大重试次数。
            retry_delay: 基础重试间隔。
            **kwargs: 其他参数传递给 stream()。

        Yields:
            生成的文本片段。
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"流式调用第 {attempt + 1}/{max_retries} 次重试")

                async for token in self.stream(role, prompt, **kwargs):
                    yield token
                return

            except LLMRateLimitError as e:
                wait_time = e.retry_after
                logger.warning(
                    f"流式调用限流错误，等待 {wait_time}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e

            except (LLMTimeoutError, LLMAPIError) as e:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"流式调用失败 ({type(e).__name__}), "
                    f"等待 {wait_time:.1f}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e

        logger.error(f"流式调用重试耗尽 ({max_retries} 次尝试后仍失败)")
        raise last_error

    def count_tokens(self, text: str, provider_name: Optional[str] = None) -> int:
        """
        计算文本的 token 数量。

        Args:
            text: 要计算的文本。
            provider_name: Provider 名称（为空则使用当前激活的 Provider）。

        Returns:
            Token 数量。
        """
        if provider_name is None:
            provider_name = self.config.ACTIVE_PROVIDER

        counter = self._get_token_counter(provider_name)
        return counter.count(text)


# ============================================================
# 便捷函数（带重试）
# ============================================================

async def quick_call(
    role: str,
    prompt: str,
    max_retries: int = 3,
    **kwargs
) -> str:
    """
    快速调用 LLM（带自动重试），返回文本内容。

    便捷函数，无需手动创建 LLMClient 实例，直接调用并返回 content 字符串。

    Args:
        role: 角色。
        prompt: 提示词。
        max_retries: 最大重试次数，默认 3。
        **kwargs: 其他参数传递给 call_with_retry()。

    Returns:
        生成的文本字符串。
    """
    client = LLMClient()
    response = await client.call_with_retry(role, prompt, max_retries=max_retries, **kwargs)
    return response.content


async def quick_stream(
    role: str,
    prompt: str,
    max_retries: int = 3,
    **kwargs
) -> AsyncIterator[str]:
    """
    快速流式调用 LLM（带自动重试）。

    便捷函数，无需手动创建 LLMClient 实例，直接调用并 yield token。

    Args:
        role: 角色。
        prompt: 提示词。
        max_retries: 最大重试次数，默认 3。
        **kwargs: 其他参数传递给 stream_with_retry()。

    Yields:
        生成的文本片段。
    """
    client = LLMClient()
    async for token in client.stream_with_retry(role, prompt, max_retries=max_retries, **kwargs):
        yield token
