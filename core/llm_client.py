"""
core/llm_client.py
LLM 统一调用封装（增强版：支持自动重试、Token 速率限制、并发控制）

设计原则：
1. 统一接口：所有 LLM 调用通过此类，外部不直接调用 API
2. 多 Provider 支持：根据配置自动切换豆包/Qwen/GLM/OpenAI
3. 流式输出：支持 SSE 流式返回，实时推送生成内容
4. 错误处理：统一异常体系，自动重试机制
5. 角色化调用：根据角色(writer/generator/trim/extract)自动选择对应模型
6. 速率限制：Token 桶算法控制并发和速率
7. 智能重试：指数退避 + 限流等待
"""

import asyncio
import json
import time
from typing import AsyncIterator, Optional, Literal, Any
from dataclasses import dataclass
from enum import Enum
import aiohttp
import tiktoken

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
        """
        初始化速率限制器
        
        Args:
            max_requests: 时间窗口内最大请求数
            time_window: 时间窗口（秒）
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens = max_requests  # 当前可用令牌数
        self.last_update = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """
        获取一个令牌，如果没有可用令牌则等待
        
        Returns:
            等待时间（秒）
        """
        async with self._lock:
            now = time.time()
            # 计算自上次更新以来新增的令牌
            elapsed = now - self.last_update
            self.tokens = min(
                self.max_requests,
                self.tokens + elapsed * (self.max_requests / self.time_window)
            )
            self.last_update = now
            
            if self.tokens < 1:
                # 需要等待
                wait_time = (1 - self.tokens) * (self.time_window / self.max_requests)
                logger.debug(f"速率限制：等待 {wait_time:.2f}s")
                return wait_time
            else:
                self.tokens -= 1
                return 0
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        wait_time = await self.acquire()
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
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
    
    使用示例：
        limiter = ConcurrencyLimiter(max_concurrent=5)
        async with limiter:
            await llm_call()
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
        """获取信号量"""
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """释放信号量"""
        self.semaphore.release()
    
    @property
    def current_count(self) -> int:
        """获取当前正在进行的调用数"""
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
    
    # 各 Provider 的编码器映射
    ENCODER_MAP = {
        "doubao": "cl100k_base",        # 豆包使用 cl100k_base
        "qwen": "cl100k_base",          # Qwen 使用 cl100k_base
        "glm": "cl100k_base",           # GLM 使用 cl100k_base
        "openai": "cl100k_base",        # OpenAI 使用 cl100k_base
    }
    
    def __init__(self, provider: str = "doubao"):
        self.provider = provider
        encoder_name = self.ENCODER_MAP.get(provider, "cl100k_base")
        try:
            self.encoder = tiktoken.get_encoding(encoder_name)
        except Exception:
            # 如果获取失败，使用默认编码器
            self.encoder = tiktoken.get_encoding("cl100k_base")
    
    def count(self, text: str) -> int:
        """计算文本的 token 数量"""
        if not text:
            return 0
        return len(self.encoder.encode(text))
    
    def count_messages(self, messages: list[dict]) -> int:
        """计算消息列表的 token 数量"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count(content)
            # 加上消息格式的开销（约 4 tokens）
            total += 4
        return total


# ============================================================
# LLM 客户端
# ============================================================

class LLMClient:
    """
    LLM 统一调用客户端（增强版：支持速率限制和并发控制）
    
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
    
    # 默认配置
    DEFAULT_RATE_LIMIT = 10          # 每秒最大请求数
    DEFAULT_CONCURRENT_LIMIT = 5     # 最大并发请求数
    DEFAULT_MAX_RETRIES = 3          # 默认重试次数
    DEFAULT_RETRY_DELAY = 1.0        # 默认重试间隔（秒）
    
    def __init__(self, 
                 rate_limit: Optional[int] = None,
                 concurrent_limit: Optional[int] = None):
        """
        初始化 LLM 客户端
        
        Args:
            rate_limit: 速率限制（请求/秒），默认 10
            concurrent_limit: 并发限制，默认 5
        """
        self.config = get_config()
        self.token_counters: dict[str, TokenCounter] = {}
        
        # 速率限制器（每个 provider 独立）
        self.rate_limit = rate_limit or self.DEFAULT_RATE_LIMIT
        self._rate_limiters: dict[str, RateLimiter] = {}
        
        # 并发限制器（全局单例）
        self.concurrency_limiter = ConcurrencyLimiter(
            concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT
        )
        
        logger.info(
            f"LLMClient 初始化 - 速率限制: {self.rate_limit}/s, "
            f"并发限制: {concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT}"
        )
    
    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        """获取指定 Provider 的速率限制器"""
        if provider_name not in self._rate_limiters:
            self._rate_limiters[provider_name] = RateLimiter(
                max_requests=self.rate_limit,
                time_window=1.0
            )
        return self._rate_limiters[provider_name]
    
    def _get_token_counter(self, provider: str) -> TokenCounter:
        """获取指定 Provider 的 Token 计数器"""
        if provider not in self.token_counters:
            self.token_counters[provider] = TokenCounter(provider)
        return self.token_counters[provider]
    
    def _build_headers(self, provider: ProviderConfig) -> dict:
        """构建 API 请求头"""
        return {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
    
    def _build_payload(
        self,
        provider: ProviderConfig,
        model: str,
        messages: list[dict],
        max_tokens: int = 2000,
        temperature: float = 0.7,
        stream: bool = False,
        response_format: Optional[str] = None,
        json_schema: Optional[dict] = None
    ) -> dict:
        """
        构建 API 请求体
        
        Args:
            response_format: 响应格式类型（"json" 或 "json_schema"）
            json_schema: JSON Schema 定义（当 response_format="json_schema" 时使用）
        """
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        
        # JSON 格式输出处理
        if response_format == "json_schema" and json_schema:
            # 使用 json_schema 模式（豆包官方推荐）
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name", "output"),
                    "strict": True,
                    "schema": json_schema.get("schema", {})
                }
            }
        elif response_format == "json":
            # 兼容旧版 json_object 模式（仅非豆包模型）
            if "doubao" not in provider.name.lower():
                payload["response_format"] = {"type": "json_object"}
        
        return payload
    
    def _parse_stream_line(self, line: str, provider_name: str) -> Optional[str]:
        """解析流式响应的一行数据"""
        line = line.strip()
        if not line or line == "data: [DONE]":
            return None
        
        if line.startswith("data: "):
            line = line[6:]
        
        try:
            data = json.loads(line)
            
            # OpenAI/豆包格式
            if "choices" in data and len(data["choices"]) > 0:
                delta = data["choices"][0].get("delta", {})
                content = delta.get("content", "")
                return content
            
            # 其他格式兼容
            return data.get("content", "")
            
        except json.JSONDecodeError:
            return None
    
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
        total_timeout: int = 600
    ) -> LLMResponse:
        """
        非流式调用 LLM（带速率限制和并发控制）
        
        Args:
            role: 角色（writer/generator/trim/extract/embed）
            prompt: 用户提示词
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            response_format: 响应格式（None/json/json_schema）
            json_schema: JSON Schema 定义（当 response_format="json_schema" 时使用）
            connect_timeout: 连接超时（秒）
            sock_read_timeout: 读取超时（秒）
            total_timeout: 总超时（秒）
        
        Returns:
            LLMResponse 对象
        """
        model, provider = self.config.get_model_for_role(role)
        
        # 获取速率限制器和并发限制器
        rate_limiter = self._get_rate_limiter(provider.name)
        
        # 应用速率限制和并发控制
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
                    response_format=response_format,
                    json_schema=json_schema,
                    connect_timeout=connect_timeout,
                    sock_read_timeout=sock_read_timeout,
                    total_timeout=total_timeout
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
        response_format: Optional[str] = None,
        json_schema: Optional[dict] = None,
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600
    ) -> LLMResponse:
        """
        实际的 LLM 调用实现（内部方法）
        """
        # 构建消息
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # 构建请求
        headers = self._build_headers(provider)
        payload = self._build_payload(
            provider=provider,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            response_format=response_format,
            json_schema=json_schema
        )
        
        # 发送请求
        async with aiohttp.ClientSession() as session:
            try:
                logger.info(
                    f"调用模型: {model} (Provider: {provider.name}, Role: {role}, "
                    f"并发: {self.concurrency_limiter.current_count}/{self.concurrency_limiter.max_concurrent})"
                )
                logger.debug(f"Prompt长度: {len(prompt)} 字符, max_tokens: {max_tokens}")
                
                start_time = time.time()
                
                # 使用分离的超时策略
                timeout_config = aiohttp.ClientTimeout(
                    total=total_timeout,
                    connect=connect_timeout,
                    sock_read=sock_read_timeout
                )
                
                async with session.post(
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=timeout_config
                ) as response:
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        logger.warning(f"模型 {model} 触发限流，等待 {retry_after}s 后重试")
                        raise LLMRateLimitError(
                            "Rate limit exceeded",
                            retry_after=retry_after,
                            provider=provider.name,
                            model=model
                        )
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"API调用失败 - 状态码: {response.status}, 模型: {model}")
                        logger.error(f"错误信息: {error_text}")
                        raise LLMAPIError(
                            f"API error: {error_text}",
                            status_code=response.status,
                            provider=provider.name,
                            model=model
                        )
                    
                    result = await response.json()
                    elapsed = time.time() - start_time
                    
                    # 解析响应
                    choice = result["choices"][0]
                    content = choice["message"]["content"]
                    usage = result.get("usage", {})
                    
                    logger.info(
                        f"模型调用成功 - 模型: {model}, "
                        f"耗时: {elapsed:.2f}s, "
                        f"输入Tokens: {usage.get('prompt_tokens', 'N/A')}, "
                        f"输出Tokens: {usage.get('completion_tokens', 'N/A')}, "
                        f"总Tokens: {usage.get('total_tokens', 'N/A')}"
                    )
                    
                    return LLMResponse(
                        content=content,
                        model=model,
                        provider=provider.name,
                        usage=usage,
                        finish_reason=choice.get("finish_reason", "")
                    )
                    
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                logger.error(f"模型调用超时 - 模型: {model}, 已等待: {elapsed:.2f}s, 超时设置: total={total_timeout}s, connect={connect_timeout}s, read={sock_read_timeout}s")
                raise LLMTimeoutError(
                    f"Request timeout after {total_timeout}s",
                    provider=provider.name,
                    model=model
                )
            except aiohttp.ClientError as e:
                logger.error(f"模型调用网络错误 - 模型: {model}, 错误: {str(e)}")
                raise LLMAPIError(
                    f"Network error: {str(e)}",
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
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600
    ) -> AsyncIterator[str]:
        """
        流式调用 LLM
        
        Args:
            role: 角色（writer/generator/trim/extract/embed）
            prompt: 用户提示词
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            connect_timeout: 连接超时（秒）- 建立TCP连接+收到HTTP状态码
            sock_read_timeout: 读取超时（秒）- 两个token之间的最大间隔
            total_timeout: 总超时（秒）- 整个请求的最大时长
        
        Yields:
            生成的文本片段
        """
        model, provider = self.config.get_model_for_role(role)
        
        # 获取速率限制器和并发限制器
        rate_limiter = self._get_rate_limiter(provider.name)
        
        # 应用速率限制和并发控制
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
                    connect_timeout=connect_timeout,
                    sock_read_timeout=sock_read_timeout,
                    total_timeout=total_timeout
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
        connect_timeout: int = 30,
        sock_read_timeout: int = 60,
        total_timeout: int = 600
    ) -> AsyncIterator[str]:
        """
        实际的流式 LLM 调用实现（内部方法）
        """
        # 构建消息
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # 构建请求
        headers = self._build_headers(provider)
        payload = self._build_payload(
            provider=provider,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True
        )
        
        # 发送请求
        async with aiohttp.ClientSession() as session:
            try:
                logger.info(
                    f"开始流式调用 - 模型: {model} (Provider: {provider.name}, Role: {role}, "
                    f"并发: {self.concurrency_limiter.current_count}/{self.concurrency_limiter.max_concurrent})"
                )
                logger.debug(f"Prompt长度: {len(prompt)} 字符")
                
                start_time = time.time()
                token_count = 0
                first_token_received = False
                
                # 使用分离的超时策略：
                # - connect: 30s（建立TCP连接+TLS握手+收到HTTP状态码）
                # - sock_read: 60s（两个token之间的最大间隔，防止卡死）
                # - total: 600s（整个请求的最大时长，支持长生成）
                timeout_config = aiohttp.ClientTimeout(
                    total=total_timeout,
                    connect=connect_timeout,
                    sock_read=sock_read_timeout
                )
                
                async with session.post(
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=timeout_config
                ) as response:
                    # 收到HTTP状态码即判定连接成功
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        logger.warning(f"流式调用触发限流，等待 {retry_after}s")
                        raise LLMRateLimitError(
                            "Rate limit exceeded",
                            retry_after=retry_after,
                            provider=provider.name,
                            model=model
                        )
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"流式API调用失败 - 状态码: {response.status}, 模型: {model}")
                        logger.error(f"错误信息: {error_text}")
                        raise LLMAPIError(
                            f"API error: {error_text}",
                            status_code=response.status,
                            provider=provider.name,
                            model=model
                        )
                    
                    logger.info(f"流式连接成功 - 状态码: {response.status}")
                    
                    # 读取流式响应
                    async for line in response.content:
                        line = line.decode("utf-8").strip()
                        if not line:
                            continue
                        
                        # 收到第一个字节/行时记录
                        if not first_token_received:
                            first_token_received = True
                            elapsed = time.time() - start_time
                            logger.info(f"收到首个token - 耗时: {elapsed:.2f}s")
                        
                        content = self._parse_stream_line(line, provider.name)
                        if content:
                            token_count += 1
                            yield content
                    
                    elapsed = time.time() - start_time
                    logger.info(
                        f"流式生成完成 - 模型: {model}, "
                        f"耗时: {elapsed:.2f}s, "
                        f"生成Tokens: {token_count}"
                    )
                            
            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                if not first_token_received:
                    logger.error(f"流式调用连接超时 - 模型: {model}, 已等待: {elapsed:.2f}s")
                    raise LLMTimeoutError(
                        f"Connection timeout after {connect_timeout}s",
                        provider=provider.name,
                        model=model
                    )
                else:
                    logger.error(f"流式调用读取超时 - 模型: {model}, 已生成{token_count}个token, 已等待: {elapsed:.2f}s")
                    raise LLMTimeoutError(
                        f"Read timeout after {sock_read_timeout}s (no data received)",
                        provider=provider.name,
                        model=model
                    )
            except aiohttp.ClientError as e:
                logger.error(f"流式调用网络错误 - 模型: {model}, 错误: {str(e)}")
                raise LLMAPIError(
                    f"Network error: {str(e)}",
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
                # 限流错误，等待指定时间后重试
                wait_time = e.retry_after
                logger.warning(
                    f"限流错误，等待 {wait_time}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
            except (LLMTimeoutError, LLMAPIError) as e:
                # 网络/超时错误，指数退避重试
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"调用失败 ({type(e).__name__}), "
                    f"等待 {wait_time:.1f}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
        
        # 重试耗尽，抛出最后一个错误
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
                return  # 成功完成，退出重试循环
                
            except LLMRateLimitError as e:
                # 限流错误，等待指定时间后重试
                wait_time = e.retry_after
                logger.warning(
                    f"流式调用限流错误，等待 {wait_time}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
                
            except (LLMTimeoutError, LLMAPIError) as e:
                # 网络/超时错误，指数退避重试
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"流式调用失败 ({type(e).__name__}), "
                    f"等待 {wait_time:.1f}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e
        
        # 重试耗尽，抛出最后一个错误
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
