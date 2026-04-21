"""
core/config.py

全局配置管理模块，提供 MANS 系统运行所需的全部配置项。

职责边界：
    - 作为系统唯一的配置入口，禁止任何模块直接读取 os.environ。
    - 支持多 LLM Provider 的模型映射，当前仅激活 doubao，其余预留扩展。
    - 环境变量优先于代码默认值，便于不同部署环境（开发/测试/生产）切换。
    - 启动时执行完整性校验，缺失必要配置立即报错，避免运行时才发现。

典型用法：
    from core.config import get_config
    cfg = get_config()
    provider = cfg.get_active_provider()
    model, provider_cfg = cfg.get_model_for_role("writer")
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv


# 加载项目根目录下的 .env 文件，将环境变量注入到当前进程。
# 这一步必须在 Config 类定义之前执行，确保 __post_init__ 能读取到环境变量。
load_dotenv()


@dataclass
class ProviderConfig:
    """
    单个 LLM Provider 的完整配置。

    MANS 为不同任务角色分配不同模型，以在成本与质量之间取得平衡：
        - writer：正文生成，对创意和文笔要求最高，分配最强模型。
        - generator：项目初始化（Bible、人物、大纲等），对结构化输出要求高。
        - trim：上下文裁剪，需要快速响应，分配轻量模型。
        - extract：从文本中提取状态变更，需要稳定的结构化输出能力。
        - embed：文本向量化，通常由专门的 Embedding 模型承担。

    Attributes:
        name: Provider 的显示名称（如"豆包"），仅用于日志输出。
        api_key: 访问 Provider API 所需的密钥。
        base_url: Provider 的 API 基础地址。
        writer_model: 正文生成角色使用的模型 ID。
        generator_model: 初始化生成角色使用的模型 ID。
        trim_model: 上下文裁剪角色使用的模型 ID。
        extract_model: 状态提取角色使用的模型 ID。
        embed_model: 向量化角色使用的模型 ID。
    """

    name: str = ""
    api_key: str = ""
    base_url: str = ""
    writer_model: str = ""
    generator_model: str = ""
    trim_model: str = ""
    extract_model: str = ""
    embed_model: str = ""

    def is_configured(self) -> bool:
        """
        判断当前 Provider 是否已配置 API Key。

        未配置 API Key 的 Provider 不会被使用，但会保留在 PROVIDERS 字典中，
        方便用户切换 Provider 时无需重新启动服务。
        """
        return bool(self.api_key)


@dataclass
class Config:
    """
    MANS 全局配置类，通过 dataclass 统一管理所有配置项。

    配置加载顺序（优先级从高到低）：
        1. 环境变量（通过 os.getenv 读取）
        2. 代码中的默认值（dataclass field default）
        3. 运行时动态修改（直接修改实例属性）

    配置分组说明：
        - 环境配置：区分开发/生产环境，控制调试模式开关。
        - 服务配置：HTTP 服务的监听地址和端口。
        - 存储配置：项目数据、向量库的持久化路径。
        - Token 预算配置：Injection Engine 和 Writer 的 token 上限，防止上下文爆炸。
        - Provider 配置：多 LLM Provider 的模型映射表。
        - 功能开关：控制流式输出、异步更新、向量检索等特性的启用状态。
        - 向量模型配置：本地 Embedding 模型的选择和缓存路径。
        - 日志配置：日志级别和输出文件路径。

    Attributes:
        ENV: 运行环境标识，取值 "development" 或 "production"。
        DEBUG: 调试模式开关，开启后会输出更详细的日志。
        HOST: HTTP 服务监听地址，默认仅监听本地回环。
        PORT: HTTP 服务监听端口。
        WORKSPACE_PATH: 项目数据持久化的根目录，每个项目在该目录下拥有独立子目录。
        VECTOR_STORE_TYPE: 向量数据库类型，当前仅支持 "chromadb"。
        INJECTION_TOKEN_BUDGET: Injection Engine 组装上下文时的总 token 预算上限。
            超过此预算会触发裁剪层，调用 trim 模型进行智能裁剪。
        INJECTION_MAX_CHARACTERS: 单场景最大出场人物数，超过此数量的角色会被忽略。
        INJECTION_MAX_FORESHADOWING: 单场景最大激活伏笔数，超出部分不会注入上下文。
        WRITER_MAX_TOKENS: Writer 单次生成的最大 token 数，控制单次输出的篇幅上限。
        ACTIVE_PROVIDER: 当前激活的 LLM Provider 名称，必须在 PROVIDERS 中存在。
        PROVIDERS: 所有支持的 Provider 配置字典，键为 Provider 标识名。
        ENABLE_STREAMING: 是否启用 SSE 流式输出，关闭则所有生成以同步方式返回。
        ENABLE_ASYNC_UPDATE: 是否启用异步知识库更新，关闭则 UpdateExtractor 同步执行。
        ENABLE_VECTOR_SEARCH: 是否启用向量语义检索，关闭则 Injection Engine 的检索层跳过。
        VECTOR_MODEL_SOURCE: 向量模型来源，"local" 使用本地 bge-m3，"cloud" 调用 API。
        LOCAL_EMBED_MODEL: 本地 Embedding 模型名称，当前固定为 "bge-m3"。
        LOCAL_EMBED_CACHE_DIR: 本地模型缓存目录，None 表示使用 HuggingFace 默认缓存路径。
        LOG_LEVEL: 全局日志级别，取值 DEBUG/INFO/WARNING/ERROR/CRITICAL。
        LOG_FILE: 日志文件路径，None 表示仅输出到控制台。
    """

    ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 666
    WORKSPACE_PATH: str = "workspace"
    VECTOR_STORE_TYPE: str = "chromadb"
    INJECTION_TOKEN_BUDGET: int = 8000
    INJECTION_MAX_CHARACTERS: int = 3
    INJECTION_MAX_FORESHADOWING: int = 2
    WRITER_MAX_TOKENS: int = 3000
    GENERATOR_MAX_TOKENS: int = 8000
    TRIM_MAX_TOKENS: int = 2000
    EXTRACT_MAX_TOKENS: int = 2000
    RATE_LIMIT: int = 2
    WRITER_TEMPERATURE: float = 0.75
    GENERATOR_TEMPERATURE: float = 0.3
    TRIM_TEMPERATURE: float = 0.1
    EXTRACT_TEMPERATURE: float = 0.3
    ACTIVE_PROVIDER: str = "doubao"
    PROVIDERS: dict[str, ProviderConfig] = field(default_factory=dict)
    ENABLE_STREAMING: bool = True
    ENABLE_ASYNC_UPDATE: bool = True
    ENABLE_VECTOR_SEARCH: bool = True
    VECTOR_MODEL_SOURCE: str = "local"
    LOCAL_EMBED_MODEL: str = "bge-m3"
    LOCAL_EMBED_CACHE_DIR: Optional[str] = None
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None

    def __post_init__(self):
        """
        dataclass 初始化后的回调方法。

        负责从环境变量加载配置并填充 PROVIDERS 字典。
        此方法在 dataclass 的 __init__ 执行完毕后自动调用。
        """
        self._load_basic_config()
        self._load_providers_config()

    def _load_basic_config(self):
        """
        从环境变量加载基础配置项。

        读取规则：若环境变量存在则覆盖默认值，否则保留默认值。
        布尔类型通过字符串比较 "true"/"false" 转换，兼容不同大小写写法。
        """
        self.ENV = os.getenv("ENV", self.ENV)
        self.DEBUG = os.getenv("DEBUG", str(self.DEBUG)).lower() == "true"
        self.HOST = os.getenv("HOST", self.HOST)
        self.PORT = int(os.getenv("PORT", self.PORT))
        self.WORKSPACE_PATH = os.getenv("WORKSPACE_PATH", self.WORKSPACE_PATH)
        self.VECTOR_STORE_TYPE = os.getenv("VECTOR_STORE_TYPE", self.VECTOR_STORE_TYPE)
        self.INJECTION_TOKEN_BUDGET = int(os.getenv("INJECTION_TOKEN_BUDGET", self.INJECTION_TOKEN_BUDGET))
        self.INJECTION_MAX_CHARACTERS = int(os.getenv("INJECTION_MAX_CHARACTERS", self.INJECTION_MAX_CHARACTERS))
        self.INJECTION_MAX_FORESHADOWING = int(os.getenv("INJECTION_MAX_FORESHADOWING", self.INJECTION_MAX_FORESHADOWING))
        self.WRITER_MAX_TOKENS = int(os.getenv("WRITER_MAX_TOKENS", self.WRITER_MAX_TOKENS))
        self.GENERATOR_MAX_TOKENS = int(os.getenv("GENERATOR_MAX_TOKENS", self.GENERATOR_MAX_TOKENS))
        self.TRIM_MAX_TOKENS = int(os.getenv("TRIM_MAX_TOKENS", self.TRIM_MAX_TOKENS))
        self.EXTRACT_MAX_TOKENS = int(os.getenv("EXTRACT_MAX_TOKENS", self.EXTRACT_MAX_TOKENS))
        self.RATE_LIMIT = int(os.getenv("RATE_LIMIT", self.RATE_LIMIT))
        self.WRITER_TEMPERATURE = float(os.getenv("WRITER_TEMPERATURE", self.WRITER_TEMPERATURE))
        self.GENERATOR_TEMPERATURE = float(os.getenv("GENERATOR_TEMPERATURE", self.GENERATOR_TEMPERATURE))
        self.TRIM_TEMPERATURE = float(os.getenv("TRIM_TEMPERATURE", self.TRIM_TEMPERATURE))
        self.EXTRACT_TEMPERATURE = float(os.getenv("EXTRACT_TEMPERATURE", self.EXTRACT_TEMPERATURE))
        self.ACTIVE_PROVIDER = os.getenv("ACTIVE_PROVIDER", self.ACTIVE_PROVIDER)
        self.ENABLE_STREAMING = os.getenv("ENABLE_STREAMING", str(self.ENABLE_STREAMING)).lower() == "true"
        self.ENABLE_ASYNC_UPDATE = os.getenv("ENABLE_ASYNC_UPDATE", str(self.ENABLE_ASYNC_UPDATE)).lower() == "true"
        self.ENABLE_VECTOR_SEARCH = os.getenv("ENABLE_VECTOR_SEARCH", str(self.ENABLE_VECTOR_SEARCH)).lower() == "true"
        self.VECTOR_MODEL_SOURCE = os.getenv("VECTOR_MODEL_SOURCE", self.VECTOR_MODEL_SOURCE)
        self.LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", self.LOCAL_EMBED_MODEL)
        self.LOCAL_EMBED_CACHE_DIR = os.getenv("LOCAL_EMBED_CACHE_DIR", self.LOCAL_EMBED_CACHE_DIR)
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", self.LOG_LEVEL)
        self.LOG_FILE = os.getenv("LOG_FILE", self.LOG_FILE)

    def _load_providers_config(self):
        """
        从环境变量加载所有 Provider 的配置。

        为每个支持的 Provider 读取对应的 API Key、Base URL 和各角色模型。
        环境变量命名规范：{PROVIDER}_{SETTING}，如 DOUBAO_API_KEY、QWEN_WRITER_MODEL。

        即使某个 Provider 未配置 API Key，也会保留其完整结构，方便运行时切换。
        """
        self.PROVIDERS = {
            "doubao": ProviderConfig(
                name="豆包",
                api_key=os.getenv("DOUBAO_API_KEY", ""),
                base_url=os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
                writer_model=os.getenv("DOUBAO_WRITER_MODEL", "doubao-pro-128k"),
                generator_model=os.getenv("DOUBAO_GENERATOR_MODEL", "doubao-pro-128k"),
                trim_model=os.getenv("DOUBAO_TRIM_MODEL", "doubao-lite-32k"),
                extract_model=os.getenv("DOUBAO_EXTRACT_MODEL", "doubao-pro-32k"),
                embed_model=os.getenv("DOUBAO_EMBED_MODEL", "doubao-embedding"),
            ),
            "qwen": ProviderConfig(
                name="通义千问",
                api_key=os.getenv("QWEN_API_KEY", ""),
                base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"),
                writer_model=os.getenv("QWEN_WRITER_MODEL", "qwen-max"),
                generator_model=os.getenv("QWEN_GENERATOR_MODEL", "qwen-max"),
                trim_model=os.getenv("QWEN_TRIM_MODEL", "qwen-turbo"),
                extract_model=os.getenv("QWEN_EXTRACT_MODEL", "qwen-plus"),
                embed_model=os.getenv("QWEN_EMBED_MODEL", "text-embedding-v3"),
            ),
            "glm": ProviderConfig(
                name="智谱 GLM",
                api_key=os.getenv("GLM_API_KEY", ""),
                base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
                writer_model=os.getenv("GLM_WRITER_MODEL", "glm-4"),
                generator_model=os.getenv("GLM_GENERATOR_MODEL", "glm-4"),
                trim_model=os.getenv("GLM_TRIM_MODEL", "glm-4-flash"),
                extract_model=os.getenv("GLM_EXTRACT_MODEL", "glm-4-air"),
                embed_model=os.getenv("GLM_EMBED_MODEL", "embedding-3"),
            ),
            "openai": ProviderConfig(
                name="OpenAI",
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_BASE_URL", ""),
                writer_model=os.getenv("OPENAI_WRITER_MODEL", "gpt-4o"),
                generator_model=os.getenv("OPENAI_GENERATOR_MODEL", "gpt-4o"),
                trim_model=os.getenv("OPENAI_TRIM_MODEL", "gpt-4o-mini"),
                extract_model=os.getenv("OPENAI_EXTRACT_MODEL", "gpt-4o"),
                embed_model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
            ),
        }

    def get_active_provider(self) -> ProviderConfig:
        """
        获取当前激活的 Provider 配置。

        Returns:
            ProviderConfig 实例。

        Raises:
            ValueError: 当 ACTIVE_PROVIDER 不在 PROVIDERS 中时抛出。
        """
        if self.ACTIVE_PROVIDER not in self.PROVIDERS:
            raise ValueError(
                f"未知的 Provider: {self.ACTIVE_PROVIDER}，"
                f"可用选项: {list(self.PROVIDERS.keys())}"
            )
        return self.PROVIDERS[self.ACTIVE_PROVIDER]

    def get_temperature_for_role(self, role: str) -> float:
        """
        根据角色获取默认温度。

        各角色的温度设计逻辑：
            - writer (0.75): 正文生成需要一定创意，允许较高温度。
            - generator (0.3): 结构化输出需要稳定性，温度较低。
            - trim (0.1): 上下文裁剪需要确定性，温度最低。
            - extract (0.3): 状态提取需要一致性，温度较低。

        Args:
            role: 任务角色标识。

        Returns:
            该角色的默认温度值。
        """
        role = role.lower()
        temp_map = {
            "writer": self.WRITER_TEMPERATURE,
            "generator": self.GENERATOR_TEMPERATURE,
            "trim": self.TRIM_TEMPERATURE,
            "extract": self.EXTRACT_TEMPERATURE,
        }
        return temp_map.get(role, 0.7)

    def get_max_tokens_for_role(self, role: str) -> int:
        """
        根据角色获取默认最大输出 token 数。

        各角色的 token 预算设计逻辑：
            - writer (3000): 单次场景正文，约 1500-2000 中文字符。
            - generator (8000): Bible/大纲等结构化数据生成，内容量大。
            - trim (2000): 上下文裁剪输出，通常较短。
            - extract (2000): 状态提取输出，结构化 JSON，不需要太长。

        Args:
            role: 任务角色标识。

        Returns:
            该角色的默认 max_tokens 值。
        """
        role = role.lower()
        tokens_map = {
            "writer": self.WRITER_MAX_TOKENS,
            "generator": self.GENERATOR_MAX_TOKENS,
            "trim": self.TRIM_MAX_TOKENS,
            "extract": self.EXTRACT_MAX_TOKENS,
        }
        return tokens_map.get(role, 4000)

    def get_model_for_role(self, role: str) -> tuple[str, ProviderConfig]:
        """
        根据任务角色获取对应的模型 ID 和 Provider 配置。

        MANS 采用角色化模型分配策略，不同任务使用不同模型，
        以在生成质量、响应速度和成本之间取得平衡。

        Args:
            role: 任务角色标识，取值范围为：
                - "writer"：正文生成，对创意和文笔要求最高
                - "generator"：项目初始化生成，需要强结构化输出能力
                - "trim"：上下文裁剪，需要快速响应
                - "extract"：状态提取，需要稳定的 JSON 输出
                - "embed"：文本向量化

        Returns:
            (model_id, provider_config) 元组，model_id 为模型标识字符串。

        Raises:
            ValueError: 当 role 不在支持的列表中时抛出。
        """
        provider = self.get_active_provider()
        role = role.lower()

        model_map = {
            "writer": provider.writer_model,
            "generator": provider.generator_model,
            "trim": provider.trim_model,
            "extract": provider.extract_model,
            "embed": provider.embed_model,
        }

        if role not in model_map:
            raise ValueError(
                f"未知的角色: {role}，"
                f"可用选项: {list(model_map.keys())}"
            )

        return model_map[role], provider

    def validate(self) -> list[str]:
        """
        校验配置完整性，返回所有发现的问题。

        校验项：
            1. 当前激活的 Provider 是否已配置 API Key。
            2. 工作目录是否存在或可创建。

        Returns:
            错误信息列表。空列表表示所有校验通过，配置可用。
        """
        errors = []

        try:
            active = self.get_active_provider()
            if not active.is_configured():
                errors.append(
                    f"当前激活的 Provider '{self.ACTIVE_PROVIDER}' 未配置 API Key，"
                    f"请设置 {self.ACTIVE_PROVIDER.upper()}_API_KEY"
                )
        except ValueError as e:
            errors.append(str(e))

        workspace = Path(self.WORKSPACE_PATH)
        if not workspace.exists():
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"无法创建工作目录 '{self.WORKSPACE_PATH}': {e}")

        return errors

    def is_production(self) -> bool:
        """
        判断当前是否为生产环境。

        生产环境下会关闭调试输出，减少日志量，提升性能。
        """
        return self.ENV.lower() == "production"

    def to_dict(self) -> dict:
        """
        将配置导出为字典，用于调试接口或日志记录。

        安全说明：
            导出结果中隐藏了 API Key，避免敏感信息泄露到日志或前端。

        Returns:
            包含当前配置状态的字典，API Key 被替换为"已配置"/"未配置"状态。
        """
        active = self.get_active_provider()
        return {
            "ENV": self.ENV,
            "DEBUG": self.DEBUG,
            "HOST": self.HOST,
            "PORT": self.PORT,
            "ACTIVE_PROVIDER": self.ACTIVE_PROVIDER,
            "ACTIVE_MODELS": {
                "writer": active.writer_model,
                "generator": active.generator_model,
                "trim": active.trim_model,
                "extract": active.extract_model,
                "embed": active.embed_model,
            },
            "WORKSPACE_PATH": self.WORKSPACE_PATH,
            "INJECTION_TOKEN_BUDGET": self.INJECTION_TOKEN_BUDGET,
            "WRITER_MAX_TOKENS": self.WRITER_MAX_TOKENS,
            "GENERATOR_MAX_TOKENS": self.GENERATOR_MAX_TOKENS,
            "TRIM_MAX_TOKENS": self.TRIM_MAX_TOKENS,
            "EXTRACT_MAX_TOKENS": self.EXTRACT_MAX_TOKENS,
            "RATE_LIMIT": self.RATE_LIMIT,
            "TEMPERATURES": {
                "writer": self.WRITER_TEMPERATURE,
                "generator": self.GENERATOR_TEMPERATURE,
                "trim": self.TRIM_TEMPERATURE,
                "extract": self.EXTRACT_TEMPERATURE,
            },
            "PROVIDERS_STATUS": {
                name: "已配置" if cfg.is_configured() else "未配置"
                for name, cfg in self.PROVIDERS.items()
            },
        }


# 模块级别的全局配置实例缓存。
# 使用单例模式避免重复解析环境变量，提升性能。
# 若需要热重载配置，调用 reload_config() 重置此缓存。
_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局配置单例。

    首次调用时创建 Config 实例并缓存，后续调用直接返回缓存实例。
    这是整个系统获取配置的唯一推荐入口。

    Returns:
        Config 全局配置实例。
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """
    重新加载配置，丢弃缓存并创建新的 Config 实例。

    适用场景：
        - 修改了 .env 文件后需要热更新配置。
        - 测试用例中需要切换不同的配置环境。

    Returns:
        重新创建后的 Config 实例。
    """
    global _config
    _config = Config()
    return _config


def __getattr__(name: str):
    """
    动态属性代理，允许通过 core.config.XXX 直接访问 Config 实例的属性。

    例如：
        from core.config import INJECTION_TOKEN_BUDGET

    等效于：
        from core.config import get_config
        get_config().INJECTION_TOKEN_BUDGET

    Raises:
        AttributeError: 当访问的属性不存在于 Config 中时抛出。
    """
    cfg = get_config()
    if hasattr(cfg, name):
        return getattr(cfg, name)
    raise AttributeError(f"module 'core.config' has no attribute '{name}'")
