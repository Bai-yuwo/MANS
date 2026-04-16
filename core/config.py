"""
core/config.py
全局配置管理

设计原则：
1. 单一配置入口，所有模块通过此模块获取配置
2. 支持多 LLM Provider（豆包为主，预留 Qwen/GLM 等扩展）
3. 根据 ACTIVE_PROVIDER 自动选择对应 Provider 的模型配置
4. 环境变量优先，提供合理的默认值
5. 配置验证，启动时检查必要配置
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv


# 加载 .env 文件（如果存在）
load_dotenv()


# ============================================================
# Provider 配置类
# ============================================================

@dataclass
class ProviderConfig:
    """
    LLM Provider 配置
    包含 API 信息和模型分配
    """
    name: str                           # Provider 名称
    api_key: str = ""                   # API Key
    base_url: str = ""                  # API 端点
    
    # 模型分配（各角色使用的模型）
    writer_model: str = ""              # 正文生成
    generator_model: str = ""           # 初始化生成
    trim_model: str = ""                # 上下文裁剪
    extract_model: str = ""             # 状态提取
    embed_model: str = ""               # 文本向量化
    
    def is_configured(self) -> bool:
        """检查此 Provider 是否已配置（有 API Key）"""
        return bool(self.api_key)


# ============================================================
# 主配置类
# ============================================================

@dataclass
class Config:
    """
    全局配置类
    所有配置项集中管理
    """
    
    # --------------------------------------------------------
    # 环境配置
    # --------------------------------------------------------
    ENV: str = "development"
    DEBUG: bool = True
    
    # --------------------------------------------------------
    # 服务配置
    # --------------------------------------------------------
    HOST: str = "127.0.0.1"
    PORT: int = 666
    
    # --------------------------------------------------------
    # 存储路径
    # --------------------------------------------------------
    WORKSPACE_PATH: str = "workspace"
    VECTOR_STORE_TYPE: str = "chromadb"
    
    # --------------------------------------------------------
    # Token 预算配置
    # --------------------------------------------------------
    INJECTION_TOKEN_BUDGET: int = 3500      # Injection Engine 总预算
    INJECTION_MAX_CHARACTERS: int = 3       # 最大出场人物数
    INJECTION_MAX_FORESHADOWING: int = 2    # 最大激活伏笔数
    WRITER_MAX_TOKENS: int = 3000           # Writer 单次最大生成 token
    
    # --------------------------------------------------------
    # 当前激活的 Provider
    # --------------------------------------------------------
    ACTIVE_PROVIDER: str = "doubao"
    
    # --------------------------------------------------------
    # Provider 配置字典
    # --------------------------------------------------------
    PROVIDERS: dict[str, ProviderConfig] = field(default_factory=dict)
    
    # --------------------------------------------------------
    # 功能开关
    # --------------------------------------------------------
    ENABLE_STREAMING: bool = True
    ENABLE_ASYNC_UPDATE: bool = True
    ENABLE_VECTOR_SEARCH: bool = True
    
    # --------------------------------------------------------
    # 向量存储配置
    # --------------------------------------------------------
    # 向量模型来源：local（本地 bge-m3）/ cloud（API 调用）
    VECTOR_MODEL_SOURCE: str = "local"
    # 本地向量模型：bge-m3 / m3e-base / text2vec-base
    LOCAL_EMBED_MODEL: str = "bge-m3"
    # 本地模型缓存目录
    LOCAL_EMBED_CACHE_DIR: Optional[str] = None
    
    # --------------------------------------------------------
    # 日志配置
    # --------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None
    
    def __post_init__(self):
        """初始化后处理：从环境变量加载所有配置"""
        self._load_basic_config()
        self._load_providers_config()
    
    def _load_basic_config(self):
        """加载基础配置"""
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
        """加载所有 Provider 配置"""
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
    
    # ============================================================
    # 便捷方法
    # ============================================================
    
    def get_active_provider(self) -> ProviderConfig:
        """
        获取当前激活的 Provider 配置
        
        Returns:
            ProviderConfig 对象
        """
        if self.ACTIVE_PROVIDER not in self.PROVIDERS:
            raise ValueError(
                f"未知的 Provider: {self.ACTIVE_PROVIDER}，"
                f"可用选项: {list(self.PROVIDERS.keys())}"
            )
        return self.PROVIDERS[self.ACTIVE_PROVIDER]
    
    def get_model_for_role(self, role: str) -> tuple[str, ProviderConfig]:
        """
        获取指定角色使用的模型和 Provider 配置
        
        Args:
            role: 角色名称（writer/generator/trim/extract/embed）
        
        Returns:
            (model_id, provider_config) 元组
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
        验证配置完整性
        
        Returns:
            错误信息列表（为空表示验证通过）
        """
        errors = []
        
        # 检查当前激活的 Provider 是否配置了 API Key
        try:
            active = self.get_active_provider()
            if not active.is_configured():
                errors.append(
                    f"当前激活的 Provider '{self.ACTIVE_PROVIDER}' 未配置 API Key，"
                    f"请设置 {self.ACTIVE_PROVIDER.upper()}_API_KEY"
                )
        except ValueError as e:
            errors.append(str(e))
        
        # 检查工作目录是否存在或可创建
        workspace = Path(self.WORKSPACE_PATH)
        if not workspace.exists():
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"无法创建工作目录 '{self.WORKSPACE_PATH}': {e}")
        
        return errors
    
    def is_production(self) -> bool:
        """检查是否为生产环境"""
        return self.ENV.lower() == "production"
    
    def to_dict(self) -> dict:
        """导出配置为字典（用于调试，隐藏 API Key）"""
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
            "PROVIDERS_STATUS": {
                name: "已配置" if cfg.is_configured() else "未配置"
                for name, cfg in self.PROVIDERS.items()
            },
        }


# ============================================================
# 全局配置实例
# ============================================================

_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局配置实例（单例模式）
    
    Returns:
        Config 实例
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """
    重新加载配置（用于热更新）
    
    Returns:
        新的 Config 实例
    """
    global _config
    _config = Config()
    return _config


# ============================================================
# 便捷导出
# ============================================================

def __getattr__(name: str):
    """动态导出配置项"""
    cfg = get_config()
    if hasattr(cfg, name):
        return getattr(cfg, name)
    raise AttributeError(f"module 'core.config' has no attribute '{name}'")
