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
from pathlib import Path
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
# 结构化输出能力降级链（从高到低）
# ============================================================

FORMAT_LEVELS = ["json_schema", "tools", "json_object", "text"]


# ============================================================
# 模型能力动态注册表
# ============================================================

class ModelCapabilityRegistry:
    """
    模型能力动态注册表

    支持：
    1. 基线加载（从 capabilities.json）
    2. 运行时探测（调用失败自动降级）
    3. 降级缓存（内存 + 文件持久化）
    4. 程序退出时自动保存
    """

    # 代码内最小兜底基线（capabilities.json 缺失时使用）
    FALLBACK_BASELINE: dict[str, dict[str, bool]] = {
        "gpt-4o": {"json_schema": True, "tools": True, "json_object": True},
        "gpt-4o-mini": {"json_schema": True, "tools": True, "json_object": True},
        "doubao-pro-128k": {"json_schema": True, "tools": True, "json_object": True},
        "doubao-pro-32k": {"json_schema": True, "tools": True, "json_object": True},
        "doubao-lite-32k": {"json_schema": True, "tools": True, "json_object": True},
        "doubao-embedding": {"json_schema": False, "tools": False, "json_object": False},
        "qwen-max": {"json_schema": False, "tools": True, "json_object": True},
        "qwen-plus": {"json_schema": False, "tools": True, "json_object": True},
        "qwen-turbo": {"json_schema": False, "tools": True, "json_object": True},
        "text-embedding-v3": {"json_schema": False, "tools": False, "json_object": False},
        "glm-4": {"json_schema": False, "tools": True, "json_object": True},
        "glm-4-air": {"json_schema": False, "tools": True, "json_object": True},
        "glm-4-flash": {"json_schema": False, "tools": False, "json_object": False},
        "embedding-3": {"json_schema": False, "tools": False, "json_object": False},
    }

    def __init__(self, baseline_path: str = "capabilities.json"):
        self._capabilities: dict[str, dict[str, bool]] = {}
        self._dirty = False
        self._baseline_path = Path(baseline_path)
        self._load()

        import atexit
        atexit.register(self.save)

    def _load(self):
        """从文件加载基线，文件不存在时使用代码内兜底"""
        if self._baseline_path.exists():
            try:
                with open(self._baseline_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._capabilities = data.get("baseline", {})
                logger.info(
                    f"加载模型能力基线: {self._baseline_path} "
                    f"({len(self._capabilities)} 个模型)"
                )
            except Exception as e:
                logger.warning(f"加载能力基线文件失败，使用代码内兜底: {e}")
                self._capabilities = dict(self.FALLBACK_BASELINE)
        else:
            logger.info("能力基线文件不存在，使用代码内兜底")
            self._capabilities = dict(self.FALLBACK_BASELINE)
            self._dirty = True

    def get_level(self, model: str) -> str:
        """
        获取模型当前支持的最高能力级别。
        未知模型默认假设支持 json_schema（运行时探测）。
        """
        caps = self._capabilities.get(model, {})
        if not caps:
            return "json_schema"

        for level in FORMAT_LEVELS:
            if caps.get(level, False):
                return level
        return "text"

    def downgrade(self, model: str, from_level: str) -> str:
        """
        将模型能力从 from_level 降级到下一级别。
        返回降级后的级别。
        """
        from_idx = FORMAT_LEVELS.index(from_level)
        if from_idx >= len(FORMAT_LEVELS) - 1:
            return "text"

        to_level = FORMAT_LEVELS[from_idx + 1]

        if model not in self._capabilities:
            self._capabilities[model] = {}

        self._capabilities[model][from_level] = False
        if to_level != "text":
            self._capabilities[model][to_level] = True

        self._dirty = True
        logger.info(f"模型 {model} 能力降级: {from_level} -> {to_level}")
        return to_level

    def is_capability_error(self, error: LLMAPIError, level: str) -> bool:
        """判断错误是否由指定能力级别不支持导致"""
        if error.status_code not in (400, 422, 403):
            return False

        text = str(error).lower()

        level_keywords = {
            "json_schema": ["json_schema", "json schema", "schema"],
            "tools": ["tool", "function", "tools"],
            "json_object": ["json_object", "json object"],
        }
        for kw in level_keywords.get(level, []):
            if kw in text:
                return True

        generic = [
            "not supported", "not valid", "unsupported",
            "invalid", "response_format"
        ]
        for kw in generic:
            if kw in text:
                return True

        return False

    def save(self):
        """同步保存到文件"""
        if not self._dirty:
            return
        try:
            with open(self._baseline_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"baseline": self._capabilities},
                    f, ensure_ascii=False, indent=2
                )
            self._dirty = False
            logger.info(f"模型能力注册表已保存: {self._baseline_path}")
        except Exception as e:
            logger.warning(f"保存能力注册表失败: {e}")


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

    # 类级共享：所有 LLMClient 实例共用同一组 RateLimiter
    _rate_limiters: dict[str, RateLimiter] = {}

    # 降级时追加到 prompt 尾部的 JSON 约束指令
    JSON_CONSTRAINT_SUFFIX = (
        "\n\n【重要】请严格输出纯净的JSON字符串，"
        "不要输出任何Markdown代码块（如```json）、"
        "不要输出任何解释性文字或思考过程、"
        "不要添加任何寒暄语句，只返回纯JSON内容。"
    )

    def _apply_format_fallback(self, model: str, prompt: str, kwargs: dict) -> str:
        """
        根据模型能力注册表，确定实际使用的结构化输出级别。

        降级链：json_schema → tools → json_object → text + Prompt约束

        Returns:
            可能已被追加约束的 prompt
        """
        requested = kwargs.get("response_format")
        if not requested:
            return prompt

        level_map = {
            "json_schema": "json_schema",
            "tools": "tools",
            "json": "json_object",
            "json_object": "json_object",
        }
        requested_level = level_map.get(requested, "text")

        # 取请求级别与注册表级别的较低者
        registry_level = self.capability_registry.get_level(model)
        req_idx = FORMAT_LEVELS.index(requested_level)
        reg_idx = FORMAT_LEVELS.index(registry_level)
        effective_level = FORMAT_LEVELS[max(req_idx, reg_idx)]

        if effective_level == "json_schema":
            pass  # 保持原样
        elif effective_level == "tools":
            if not kwargs.get("json_schema"):
                # 无 schema 可包装为 tool，降级到 json_object
                kwargs["response_format"] = "json"
                kwargs.pop("json_schema", None)
            else:
                kwargs["response_format"] = "tools"
                # 保留 json_schema 用于 tool 参数构建
        elif effective_level == "json_object":
            kwargs["response_format"] = "json"
            kwargs.pop("json_schema", None)
        else:  # text
            kwargs.pop("response_format", None)
            kwargs.pop("json_schema", None)
            if self.JSON_CONSTRAINT_SUFFIX not in prompt:
                prompt = prompt.rstrip() + self.JSON_CONSTRAINT_SUFFIX

        return prompt

    @staticmethod
    def _clean_json_content(content: str) -> str:
        """
        终极 JSON 清洗（多级防御）

        清洗策略（按优先级）：
        1. 去除前后空白字符和 BOM
        2. 去除 Markdown 代码块包裹（```json ... ```）
        3. 【强力截取】寻找文本中第一个 { 或 [ 到最后一个 } 或 ]，
           提取最可能的 JSON 主体（应对模型输出寒暄前缀/后缀）
        4. 去除尾部可能的逗号等常见 JSON 语法污染

        无论底层是否使用结构化参数，解析前都必须过此清洗。
        """
        import re
        text = content.strip()
        text = text.lstrip('\ufeff')

        # 第1层：去除 Markdown 代码块
        pattern = r'^```(?:json)?\s*\n?(.*?)\n?```\s*$'
        match = re.match(pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        # 第2层：强力截取 JSON 主体
        first_brace = text.find('{')
        first_bracket = text.find('[')

        if first_brace == -1 and first_bracket == -1:
            # 文本中没有任何 JSON 标记，直接返回（让上层解析时报错）
            return text

        # 取最先出现的起始符号
        if first_brace == -1:
            start = first_bracket
        elif first_bracket == -1:
            start = first_brace
        else:
            start = min(first_brace, first_bracket)

        # 取最后出现的对应闭合符号
        if text[start] == '{':
            end = text.rfind('}')
        else:
            end = text.rfind(']')

        if end != -1 and end > start:
            text = text[start:end + 1]

        # 第3层：去除尾部可能污染 JSON 的逗号
        text = text.rstrip().rstrip(',').rstrip()

        return text

    @staticmethod
    def _extract_tool_call_json(result: dict) -> str:
        """
        从响应中提取 tool_calls 的 arguments JSON 字符串。

        OpenAI / 豆包 / Qwen 的 tool_calls 格式：
        choices[0].message.tool_calls[0].function.arguments
        """
        choices = result.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            args = tool_calls[0].get("function", {}).get("arguments", "")
            if args:
                return args

        return ""

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

        # 模型能力注册表（运行时探测 + 持久化）
        self.capability_registry = ModelCapabilityRegistry()

        # 速率限制配置（仅在创建新的 RateLimiter 时使用）
        self.rate_limit = rate_limit or self.DEFAULT_RATE_LIMIT

        # 并发限制器（全局单例）
        self.concurrency_limiter = ConcurrencyLimiter(
            concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT
        )

        logger.info(
            f"LLMClient 初始化 - 速率限制: {self.rate_limit}/s, "
            f"并发限制: {concurrent_limit or self.DEFAULT_CONCURRENT_LIMIT}"
        )

    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        """获取指定 Provider 的速率限制器（类级共享）"""
        if provider_name not in self.__class__._rate_limiters:
            self.__class__._rate_limiters[provider_name] = RateLimiter(
                max_requests=self.rate_limit,
                time_window=1.0
            )
        return self.__class__._rate_limiters[provider_name]
    
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
        if response_format == "tools" and json_schema:
            # 将 json_schema 包装为 Tool 定义（国内厂商广泛支持）
            payload["tools"] = [{
                "type": "function",
                "function": {
                    "name": "submit_extraction",
                    "description": "Submit the structured extraction result as JSON",
                    "parameters": json_schema.get("schema", {})
                }
            }]
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": "submit_extraction"}
            }
        elif response_format == "json_schema" and json_schema:
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
            # 使用 json_object 模式约束模型输出 JSON
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

        # 模型能力预检：若当前模型不支持请求的 response_format，预先降级
        format_kwargs = {
            "response_format": response_format,
            "json_schema": json_schema,
        }
        prompt = self._apply_format_fallback(model, prompt, format_kwargs)
        response_format = format_kwargs.get("response_format")
        json_schema = format_kwargs.get("json_schema")

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

                    # 优先提取 tool_calls 中的 JSON（tools 模式）
                    tool_call_json = self._extract_tool_call_json(result)
                    if tool_call_json:
                        content = tool_call_json
                    else:
                        content = choice["message"]["content"]

                    usage = result.get("usage", {})

                    logger.info(
                        f"模型调用成功 - 模型: {model}, "
                        f"耗时: {elapsed:.2f}s, "
                        f"输入Tokens: {usage.get('prompt_tokens', 'N/A')}, "
                        f"输出Tokens: {usage.get('completion_tokens', 'N/A')}, "
                        f"总Tokens: {usage.get('total_tokens', 'N/A')}"
                    )

                    # 自动清洗 JSON 输出（去除 Markdown 代码块包裹等）
                    cleaned_content = LLMClient._clean_json_content(content)

                    return LLMResponse(
                        content=cleaned_content,
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
    
    @staticmethod
    def _get_level_from_kwargs(kwargs: dict) -> str:
        """从 kwargs 中提取当前请求的格式级别"""
        rf = kwargs.get("response_format")
        if rf == "json_schema":
            return "json_schema"
        elif rf == "tools":
            return "tools"
        elif rf in ("json", "json_object"):
            return "json_object"
        return "text"

    def _rebuild_kwargs_for_level(self, kwargs: dict, level: str) -> dict:
        """为指定级别重建 kwargs"""
        updated = dict(kwargs)
        if level == "json_schema":
            updated["response_format"] = "json_schema"
        elif level == "tools":
            updated["response_format"] = "tools"
            # 保留 json_schema 用于 tool 参数
        elif level == "json_object":
            updated["response_format"] = "json"
            updated.pop("json_schema", None)
        else:  # text
            updated.pop("response_format", None)
            updated.pop("json_schema", None)
        return updated

    async def call_with_retry(
        self,
        role: str,
        prompt: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        **kwargs
    ) -> LLMResponse:
        """
        带重试机制的 LLM 调用（支持阶梯式能力降级）

        降级链：json_schema → tools → json_object → text + Prompt约束

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
        model, _ = self.config.get_model_for_role(role)

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

            except LLMAPIError as e:
                # 确定本次调用实际尝试的级别
                registry_level = self.capability_registry.get_level(model)
                requested_level = self._get_level_from_kwargs(kwargs)
                req_idx = FORMAT_LEVELS.index(requested_level)
                reg_idx = FORMAT_LEVELS.index(registry_level)
                effective_level = FORMAT_LEVELS[max(req_idx, reg_idx)]

                # 检查是否为能力不支持导致的错误，需要降级
                if (
                    e.status_code in (400, 422, 403)
                    and effective_level != "text"
                    and self.capability_registry.is_capability_error(e, effective_level)
                ):
                    next_level = self.capability_registry.downgrade(
                        model, effective_level
                    )
                    if next_level != effective_level:
                        kwargs = self._rebuild_kwargs_for_level(kwargs, next_level)
                        logger.info(
                            f"检测到能力错误，降级到 {next_level} 后重试 "
                            f"(尝试 {attempt + 1}/{max_retries})"
                        )
                        continue

                # 非能力错误，指数退避重试
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"调用失败 ({type(e).__name__}), "
                    f"等待 {wait_time:.1f}s 后重试 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait_time)
                last_error = e

            except LLMTimeoutError as e:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(
                    f"调用超时 ({type(e).__name__}), "
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
