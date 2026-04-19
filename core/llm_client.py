"""
core/llm_client.py
LLM 统一调用封装（Ark SDK 版本：使用 OpenAI 兼容的 responses API）

设计原则：
1. 统一接口：所有 LLM 调用通过此类，外部不直接调用 API
2. 单一 Provider：当前仅支持 doubao（Ark 平台），其他 Provider 留空
3. 流式输出：支持 SSE 流式返回，实时推送生成内容
4. 错误处理：统一异常体系，自动重试机制
5. 角色化调用：根据角色(writer/generator/trim/extract)自动选择对应模型
6. 速率限制：Token 桶算法控制并发和速率
7. 结构化输出：非 writer 角色统一使用 json_schema，writer 角色纯文本输出
8. 深度思考：所有模型默认开启 thinking（extra_body），不调节 reasoning
"""

import asyncio
import json
import re
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass

from openai import AsyncOpenAI, APIError, RateLimitError, APITimeoutError

from core.config import get_config, ProviderConfig
from core.logging_config import get_logger, log_exception

logger = get_logger('core.llm_client')


# ============================================================
# 速率限制器（Token 桶算法）
# ============================================================

class RateLimiter:
    """
    Token 桶速率限制器

    用于控制 API 调用速率，防止触发厂商限流

    使用示例：
        limiter = RateLimiter(max_requests=10, time_window=1.0)  # 每秒10请求
        async with limiter:
            await api_call()
    """

    def __init__(self, max_requests: int = 10, time_window: float = 1.0):
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens = max_requests
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self):
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
        wait_time = await self.acquire()
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# ============================================================
# 并发信号量（全局单例）
# ============================================================

class ConcurrencyLimiter:
    """
    全局并发限制器（单例模式）

    限制同时进行的 LLM 调用数量，防止：
    1. 触发 API 厂商并发限制
    2. 内存/连接池耗尽
    3. 网络拥塞
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
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()

    @property
    def current_count(self) -> int:
        return self.max_concurrent - self.semaphore._value


# ============================================================
# 异常定义
# ============================================================

class LLMError(Exception):
    """LLM 调用基础异常"""
    def __init__(self, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class LLMAPIError(LLMError):
    """API 调用异常（网络/认证/限流等）"""
    def __init__(self, message: str, status_code: int = 0, **kwargs):
        super().__init__(message, **kwargs)
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    """调用超时异常"""
    pass


class LLMRateLimitError(LLMError):
    """限流异常"""
    def __init__(self, message: str, retry_after: int = 60, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


# ============================================================
# 响应模型
# ============================================================

@dataclass
class LLMResponse:
    """LLM 响应封装"""
    content: str                        # 生成的文本内容
    model: str                          # 实际使用的模型
    provider: str                       # 使用的 Provider
    usage: dict = None                  # Token 使用统计
    finish_reason: str = ""             # 结束原因


# ============================================================
# Token 计数器
# ============================================================

class TokenCounter:
    """Token 计数工具"""

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
        if not text:
            return 0
        return len(self.encoder.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
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
    LLM 统一调用客户端（Ark SDK 版本）

    使用示例：
        client = LLMClient()

        # 非流式调用（自动重试）
        response = await client.call_with_retry(
            role="writer",
            prompt="生成一段小说开头...",
            max_tokens=2000,
            max_retries=3
        )

        # 流式调用（自动重试）
        async for token in client.stream_with_retry(
            role="writer",
            prompt="生成一段小说开头..."
        ):
            print(token, end="")
    """

    DEFAULT_RATE_LIMIT = 10
    DEFAULT_CONCURRENT_LIMIT = 5
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0

    _rate_limiters: dict[str, RateLimiter] = {}

    @staticmethod
    def _clean_json_content(content: str) -> str:
        """
        JSON 清洗（多级防御）

        清洗策略：
        1. 去除前后空白字符和 BOM
        2. 去除 Markdown 代码块包裹
        3. 去除推理模型 <think> 标签
        4. 强力截取 JSON 主体
        5. 去除尾部可能污染 JSON 的逗号
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
        self.config = get_config()
        self.token_counters: dict[str, TokenCounter] = {}

        # Provider 检查：当前仅支持 doubao
        if self.config.ACTIVE_PROVIDER != "doubao":
            raise NotImplementedError(
                f"当前仅支持 doubao provider，当前激活: {self.config.ACTIVE_PROVIDER}"
            )

        provider = self.config.get_active_provider()

        # 初始化异步 OpenAI 客户端（Ark 兼容模式）
        self.client = AsyncOpenAI(
            base_url=provider.base_url,
            api_key=provider.api_key,
        )

        self.rate_limit = rate_limit or self.DEFAULT_RATE_LIMIT
        self.concurrency_limiter = ConcurrencyLimiter(
            concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT
        )

        logger.info(
            f"LLMClient 初始化 - Provider: {self.config.ACTIVE_PROVIDER}, "
            f"速率限制: {self.rate_limit}/s, "
            f"并发限制: {concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT}"
        )

    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        if provider_name not in self.__class__._rate_limiters:
            self.__class__._rate_limiters[provider_name] = RateLimiter(
                max_requests=self.rate_limit,
                time_window=1.0
            )
        return self.__class__._rate_limiters[provider_name]

    def _get_token_counter(self, provider: str) -> TokenCounter:
        if provider not in self.token_counters:
            self.token_counters[provider] = TokenCounter(provider)
        return self.token_counters[provider]

    def _build_input(self, system_prompt: str, prompt: str) -> list[dict]:
        """构建 input 数组，所有模型都使用 system 角色"""
        messages = [{"role": "system", "content": system_prompt}]
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
        """构建 responses.create 的 kwargs"""
        kwargs = {
            "model": model,
            "input": self._build_input(system_prompt, prompt),
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "extra_body": {"thinking": {"type": "enabled"}},
        }

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
        max_tokens: int = 2000,
        temperature: float = 0.7,
        response_format: Optional[str] = None,
        json_schema: Optional[dict] = None,
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600,
    ) -> LLMResponse:
        """
        非流式调用 LLM（带速率限制和并发控制）

        Args:
            role: 角色（writer/generator/trim/extract/embed）
            prompt: 用户提示词
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            response_format: 响应格式（保留参数但不再用于降级，仅作为 json_schema 开关）
            json_schema: JSON Schema 定义
            connect_timeout: 连接超时（保留参数，由 SDK 内部处理）
            sock_read_timeout: 读取超时（保留参数，由 SDK 内部处理）
            total_timeout: 总超时（保留参数，由 SDK 内部处理）

        Returns:
            LLMResponse 对象
        """
        model, provider = self.config.get_model_for_role(role)

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
                    max_tokens=max_tokens,
                    temperature=temperature,
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
        """实际的 LLM 调用实现（非流式）"""
        kwargs = self._build_call_kwargs(
            role=role,
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
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
        max_tokens: int = 2000,
        temperature: float = 0.7,
        json_schema: Optional[dict] = None,
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600,
    ) -> AsyncIterator[str]:
        """
        流式调用 LLM

        Args:
            role: 角色（writer/generator/trim/extract/embed）
            prompt: 用户提示词
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            json_schema: JSON Schema 定义（非 writer 角色使用结构化输出）
            connect_timeout: 连接超时（保留参数）
            sock_read_timeout: 读取超时（保留参数）
            total_timeout: 总超时（保留参数）

        Yields:
            生成的文本片段
        """
        model, provider = self.config.get_model_for_role(role)

        rate_limiter = self._get_rate_limiter(provider.name)

        async with rate_limiter:
            async with self.concurrency_limiter:
                async for token in self._stream_impl(
                    role=role,
                    model=model,
                    provider=provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
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
        """实际的流式 LLM 调用实现"""
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

            async for event in response:
                if not first_token_received:
                    first_token_received = True
                    elapsed = time.time() - start_time
                    logger.info(f"收到首个token - 耗时: {elapsed:.2f}s")

                if event.type == "response.output_text.delta":
                    token_count += 1
                    yield event.delta

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
        带重试机制的 LLM 调用

        Args:
            role: 角色
            prompt: 提示词
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）
            **kwargs: 其他参数传递给 call()

        Returns:
            LLMResponse 对象
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
        带重试机制的流式 LLM 调用

        Args:
            role: 角色
            prompt: 提示词
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）
            **kwargs: 其他参数传递给 stream()

        Yields:
            生成的文本片段
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
        计算文本的 token 数量

        Args:
            text: 要计算的文本
            provider_name: Provider 名称（为空则使用当前激活的 Provider）

        Returns:
            Token 数量
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
    快速调用 LLM（带自动重试），返回文本内容

    Args:
        role: 角色
        prompt: 提示词
        max_retries: 最大重试次数，默认 3
        **kwargs: 其他参数

    Returns:
        生成的文本
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
    快速流式调用 LLM（带自动重试）

    Args:
        role: 角色
        prompt: 提示词
        max_retries: 最大重试次数，默认 3
        **kwargs: 其他参数

    Yields:
        生成的文本片段
    """
    client = LLMClient()
    async for token in client.stream_with_retry(role, prompt, max_retries=max_retries, **kwargs):
        yield token
