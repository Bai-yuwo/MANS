"""
core/config.py

MANS 全局配置(主管-专家二级架构重构后)。

核心变更(相比旧的 role-based 设计):
    - 旧设计:writer / generator / trim / extract / embed 5 个 role,每个 provider 各配一组模型
    - 新设计:17 个 agent(5 主管 + 12 专家)→ 三档 role 模板(creator / generator / reviewer),
      Provider 单一化为 ARK,每个 agent 还带 kind 字段(manager | expert)区分调用模式

为什么这样设计:
    1. **单一 Provider**:.env 实际只有 ARK(火山引擎)的 key,旧代码里的 qwen/glm/openai 4 个槽位
       从未启用,徒增配置噪音;ARK 已经能跑 doubao / deepseek / glm 三家模型,统一一个 base_url 即可。
    2. **Agent 优先于 Role**:同样是"reviewer",Director 和 Critic 的提示词长度、推理深度差异巨大,
       未来很可能针对单个 agent 调温度/换模型;角色化映射(role→model)无法表达这种细粒度。
    3. **三档 Role 默认值**:仍保留 role 概念作为"默认值的快速分组",新增 agent 时只需指定它属于哪档。

环境变量优先级(高→低):
    1. `{AGENT_NAME_UPPER}_MODEL` / `_TEMP` / `_MAX_TOKENS`(单 agent 精细覆盖,如 `WRITER_TEMP=0.8`)
    2. ROLE_DEFAULTS(代码内三档默认)

ARK 凭据 fallback 链:
    api_key  : ARK_API_KEY → DOUBAO_API_KEY(.env 历史变量名,保持兼容)
    base_url : ARK_BASE_URL → DOUBAO_BASE_URL → 默认 https://ark.cn-beijing.volces.com/api/v3

向后兼容:
    旧代码(generators/ writer/ web_app)仍调用 get_model_for_role("writer") 等接口。
    新 Config 提供 LEGACY_ROLE_TO_AGENT 映射 + get_model_for_role shim,把 role 翻译成 agent
    再走新路径,保证旧调用方不需改动也能跑。

典型用法(新代码):
    from core.config import get_config
    cfg = get_config()
    rt = cfg.get_for_agent("Writer")
    # rt.model / rt.temperature / rt.max_tokens / rt.role
    # cfg.ark_provider.api_key / .base_url
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# 在类定义之前注入 .env 到进程环境,确保 dataclass 默认值与 __post_init__ 都能读到。
load_dotenv()


# ============================================================
# Agent 与 Role 映射表(系统真相源,不要在其他文件重复)
# ============================================================

# 三档 Role 默认值。新增 agent 时挑一档即可,不需要每个 agent 单独配模型。
ROLE_DEFAULTS: dict[str, dict] = {
    # 创作档:Writer 唯一占用,温度高、上下文留给场景文笔
    "creator": {
        "model": "glm-4-7-251222",
        "temperature": 0.7,
        "max_tokens": 3000,
    },
    # 生成档:世界观、大纲、角色、场景节拍表等结构化产物,需要"花火"但不能太散
    "generator": {
        "model": "deepseek-v3-2-251201",
        "temperature": 0.4,
        "max_tokens": 8000,
    },
    # 审查/调度档:Critic / Continuity / ReviewManager / Director / Scribe,要求确定性
    "reviewer": {
        "model": "doubao-seed-2-0-pro-260215",
        "temperature": 0.15,
        "max_tokens": 4000,
    },
}

# 17 个 Agent 的 role 与 kind 归属。键值对必须与 CLAUDE.md 中的 Agent 表保持一致。
#
# kind 取值:
#   - "manager":主管。跑 ReAct 循环,持 tool_scope,负责编排专家与 KB 写入。
#   - "expert" :专家。被主管以 tool 形式调用,内部一次 LLM 调用拿结果就返回,不跑 ReAct,不写 KB。
#
# Writer 在专家集合里是唯一的"流式专家"——它的 output token 必须实时推送给前端;
# 其他专家同步返回 JSON 字符串。这个流式属性不放在 AGENT_DEFINITIONS,而由 ExpertTool 子类
# 通过 streaming = True 显式声明,避免配置层污染框架职责。
AGENT_DEFINITIONS: dict[str, dict] = {
    # ── 5 主管 ──
    "Director":          {"role": "reviewer",  "kind": "manager"},
    "WorldArchitect":    {"role": "generator", "kind": "manager"},
    "CastingDirector":   {"role": "generator", "kind": "manager"},
    "PlotArchitect":     {"role": "generator", "kind": "manager"},
    "SceneShowrunner":   {"role": "reviewer",  "kind": "manager"},

    # ── 12 专家 ──
    # INIT 阶段(归 WorldArchitect / CastingDirector)
    "Geographer":        {"role": "generator", "kind": "expert"},
    "RuleSmith":         {"role": "generator", "kind": "expert"},
    "PortraitDesigner":  {"role": "generator", "kind": "expert"},
    "RelationDesigner":  {"role": "generator", "kind": "expert"},
    # PLAN 阶段(归 PlotArchitect)
    "ArcDesigner":       {"role": "generator", "kind": "expert"},
    "ChapterDesigner":   {"role": "generator", "kind": "expert"},
    # WRITE 阶段(归 SceneShowrunner)
    "SceneDirector":     {"role": "generator", "kind": "expert"},
    "Writer":            {"role": "creator",   "kind": "expert"},   # 唯一流式专家
    "Critic":            {"role": "reviewer",  "kind": "expert"},
    "ContinuityChecker": {"role": "reviewer",  "kind": "expert"},
    "Scribe":            {"role": "reviewer",  "kind": "expert"},
    "ReviewManager":     {"role": "reviewer",  "kind": "expert"},
}

# 旧 role 名 → 新 agent 名的兼容映射。仅用于 get_model_for_role / get_temperature_for_role
# 等 backward-compat shim,不应在新代码中使用。
#
# 选择代表 agent 的依据:挑一个 role 档位匹配且功能上接近 legacy 用途的。
#   writer    → Writer        创作档,唯一选择
#   generator → ArcDesigner   generator 档代表(Outliner 已并入 PlotArchitect 主管)
#   trim      → ContinuityChecker  reviewer 档,旧 trim 是裁剪上下文,语义最近
#   extract   → Scribe        reviewer 档,旧 extract 就是状态抽取
LEGACY_ROLE_TO_AGENT: dict[str, str] = {
    "writer":    "Writer",
    "generator": "ArcDesigner",
    "trim":      "ContinuityChecker",
    "extract":   "Scribe",
}


# ============================================================
# 数据类:运行时所需的配置切片
# ============================================================

@dataclass
class AgentRuntime:
    """
    单个 agent 的运行时配置切片。

    通过 `Config.get_for_agent(name)` 构造,屏蔽"agent → role → model"的查找细节。
    主管在自己的 ReAct 循环里只读这个对象,专家在 ExpertTool 基类里读它来调 LLM。

    Attributes:
        agent_name: agent 的 PascalCase 名称(如 "Writer"、"SceneShowrunner")。
        role: agent 在三档分类中的归属(creator / generator / reviewer),决定默认模型与温度。
        kind: 调用模式分类:
            - "manager":主管,跑 ReAct,持 tool_scope。
            - "expert" :专家,内部一次 LLM 调用,不跑 ReAct,不写 KB。
        model: 实际调用的模型 ID。
        temperature: 采样温度。
        max_tokens: 单次输出上限。
    """
    agent_name: str
    role: str
    kind: str
    model: str
    temperature: float
    max_tokens: int


@dataclass
class ARKProvider:
    """
    ARK 平台凭据(火山引擎,OpenAI 兼容 responses API)。

    单一 Provider:旧 4-provider 设计(doubao/qwen/glm/openai)在 MANS 中从未真正启用,
    所有模型(glm-4-7 / deepseek-v3-2 / doubao-seed-2-0-pro)都跑在 ARK 上,改成单一
    Provider 配置噪音少 90%。

    name 字段保留是为了 logging/调试时能区分将来可能加入的其他 Provider,
    但当前实现里它永远是 "ark"。

    embed_model 单独留字段:Embedding 通常走本地 bge-m3,而非 ARK,但保留 ARK 端的
    embed_model 配置作为 fallback。
    """
    name: str = "ark"
    api_key: str = ""
    base_url: str = ""
    embed_model: str = ""

    def is_configured(self) -> bool:
        """检查 ARK 凭据是否齐全。`base_url` 有内置默认值,所以只判 api_key 即可。"""
        return bool(self.api_key)


# ============================================================
# 全局 Config
# ============================================================

@dataclass
class Config:
    """
    MANS 全局配置(单例,通过 `get_config()` 获取)。

    职责边界:
        - 只读环境变量与代码默认值,不持有任何运行时状态。
        - 不维护 LLM client / agent 实例(那是 LLMClient / Orchestrator 的事)。
        - 提供 `get_for_agent(name)` 作为新代码的主入口。
        - 提供 `get_model_for_role(role)` 等 shim 兼容旧代码。

    重要的"非 agent" 字段保留原因:
        - INJECTION_TOKEN_BUDGET / WRITER_MAX_TOKENS:旧 InjectionEngine 与 web_app 仍引用,
          重构期间不能直接删,新代码不要再读这些。
        - RATE_LIMIT / ENABLE_STREAMING:LLMClient 直接消费,与 agent 无关。
        - VECTOR_STORE_TYPE / LOCAL_EMBED_MODEL:KB 与向量层使用,生命周期独立于 LLM。
    """

    # ── 环境与服务 ──
    ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 666

    # ── 存储 ──
    WORKSPACE_PATH: str = "workspace"
    VECTOR_STORE_TYPE: str = "chromadb"

    # ── LLM 调用控制 ──
    RATE_LIMIT: int = 2
    ENABLE_STREAMING: bool = True

    # ── 旧字段(backward-compat,新代码不要直接读)──
    INJECTION_TOKEN_BUDGET: int = 8000
    INJECTION_MAX_CHARACTERS: int = 3
    INJECTION_MAX_FORESHADOWING: int = 2
    WRITER_MAX_TOKENS: int = 3000
    GENERATOR_MAX_TOKENS: int = 8000
    TRIM_MAX_TOKENS: int = 2000
    EXTRACT_MAX_TOKENS: int = 2000

    # ── 功能开关 ──
    ENABLE_ASYNC_UPDATE: bool = True
    ENABLE_VECTOR_SEARCH: bool = True

    # ── 向量模型 ──
    VECTOR_MODEL_SOURCE: str = "local"
    LOCAL_EMBED_MODEL: str = "bge-m3"
    LOCAL_EMBED_CACHE_DIR: Optional[str] = None

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None

    # ── Provider(单一)──
    ark_provider: ARKProvider = field(default_factory=ARKProvider)

    # ── Agent 运行时缓存(__post_init__ 填充)──
    _agent_runtimes: dict[str, AgentRuntime] = field(default_factory=dict)

    # ============================================================
    # 初始化
    # ============================================================
    def __post_init__(self):
        """从环境变量加载所有字段,并预生成 14 个 AgentRuntime 缓存。"""
        self._load_basic_config()
        self._load_ark_provider()
        self._load_agent_runtimes()

    def _load_basic_config(self):
        """从环境变量覆盖基础配置。布尔值通过字符串 'true'/'false' 比较,大小写不敏感。"""
        self.ENV = os.getenv("ENV", self.ENV)
        self.DEBUG = os.getenv("DEBUG", str(self.DEBUG)).lower() == "true"
        self.HOST = os.getenv("HOST", self.HOST)
        self.PORT = int(os.getenv("PORT", self.PORT))
        self.WORKSPACE_PATH = os.getenv("WORKSPACE_PATH", self.WORKSPACE_PATH)
        self.VECTOR_STORE_TYPE = os.getenv("VECTOR_STORE_TYPE", self.VECTOR_STORE_TYPE)
        self.RATE_LIMIT = int(os.getenv("RATE_LIMIT", self.RATE_LIMIT))
        self.ENABLE_STREAMING = os.getenv("ENABLE_STREAMING", str(self.ENABLE_STREAMING)).lower() == "true"

        self.INJECTION_TOKEN_BUDGET = int(os.getenv("INJECTION_TOKEN_BUDGET", self.INJECTION_TOKEN_BUDGET))
        self.INJECTION_MAX_CHARACTERS = int(os.getenv("INJECTION_MAX_CHARACTERS", self.INJECTION_MAX_CHARACTERS))
        self.INJECTION_MAX_FORESHADOWING = int(os.getenv("INJECTION_MAX_FORESHADOWING", self.INJECTION_MAX_FORESHADOWING))
        self.WRITER_MAX_TOKENS = int(os.getenv("WRITER_MAX_TOKENS", self.WRITER_MAX_TOKENS))
        self.GENERATOR_MAX_TOKENS = int(os.getenv("GENERATOR_MAX_TOKENS", self.GENERATOR_MAX_TOKENS))
        self.TRIM_MAX_TOKENS = int(os.getenv("TRIM_MAX_TOKENS", self.TRIM_MAX_TOKENS))
        self.EXTRACT_MAX_TOKENS = int(os.getenv("EXTRACT_MAX_TOKENS", self.EXTRACT_MAX_TOKENS))

        self.ENABLE_ASYNC_UPDATE = os.getenv("ENABLE_ASYNC_UPDATE", str(self.ENABLE_ASYNC_UPDATE)).lower() == "true"
        self.ENABLE_VECTOR_SEARCH = os.getenv("ENABLE_VECTOR_SEARCH", str(self.ENABLE_VECTOR_SEARCH)).lower() == "true"

        self.VECTOR_MODEL_SOURCE = os.getenv("VECTOR_MODEL_SOURCE", self.VECTOR_MODEL_SOURCE)
        self.LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", self.LOCAL_EMBED_MODEL)
        self.LOCAL_EMBED_CACHE_DIR = os.getenv("LOCAL_EMBED_CACHE_DIR", self.LOCAL_EMBED_CACHE_DIR)

        self.LOG_LEVEL = os.getenv("LOG_LEVEL", self.LOG_LEVEL)
        self.LOG_FILE = os.getenv("LOG_FILE", self.LOG_FILE)

    def _load_ark_provider(self):
        """
        加载 ARK 凭据。fallback 链 ARK_* → DOUBAO_*,允许用户保留 .env 中的旧变量名。
        base_url 提供内置默认值,即使两个变量都没设也能跑(虽然没 key 还是会校验失败)。
        """
        api_key = os.getenv("ARK_API_KEY") or os.getenv("DOUBAO_API_KEY", "")
        base_url = (
            os.getenv("ARK_BASE_URL")
            or os.getenv("DOUBAO_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        )
        embed_model = os.getenv("ARK_EMBED_MODEL") or os.getenv("DOUBAO_EMBED_MODEL", "")

        self.ark_provider = ARKProvider(
            name="ark",
            api_key=api_key,
            base_url=base_url,
            embed_model=embed_model,
        )

    def _load_agent_runtimes(self):
        """
        为 17 个 agent 预生成 AgentRuntime。

        每个 agent 走两层覆盖:
            1. ROLE_DEFAULTS[role]   — 三档默认(由 AGENT_DEFINITIONS 中的 role 决定)
            2. ENV `{AGENT_UPPER}_MODEL` / `_TEMP` / `_MAX_TOKENS` — 单 agent 覆盖

        kind 字段直接从 AGENT_DEFINITIONS 透传到 AgentRuntime,不可被环境变量覆盖
        (kind 是架构事实,不应运行时切换)。

        预生成而非懒加载是因为启动时一次性把全部 17 项算出来,后续 get_for_agent O(1)
        且方便 to_dict 调试输出全貌。
        """
        for agent_name, spec in AGENT_DEFINITIONS.items():
            role = spec["role"]
            kind = spec["kind"]
            defaults = ROLE_DEFAULTS[role]
            upper = agent_name.upper()
            def _env_or_default(key: str, default):
                val = os.getenv(key)
                return default if val is None or val.strip() == "" else val

            model = _env_or_default(f"{upper}_MODEL", defaults["model"])
            temperature = float(_env_or_default(f"{upper}_TEMP", defaults["temperature"]))
            max_tokens = int(_env_or_default(f"{upper}_MAX_TOKENS", defaults["max_tokens"]))

            self._agent_runtimes[agent_name] = AgentRuntime(
                agent_name=agent_name,
                role=role,
                kind=kind,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    # ============================================================
    # 主接口(新代码使用)
    # ============================================================
    def get_for_agent(self, agent_name: str) -> AgentRuntime:
        """
        获取指定 agent 的运行时配置。

        Args:
            agent_name: agent 的 PascalCase 名称,必须在 AGENT_DEFINITIONS 中。

        Returns:
            AgentRuntime 切片(只读)。

        Raises:
            ValueError: agent_name 未注册时抛出。
        """
        if agent_name not in self._agent_runtimes:
            raise ValueError(
                f"未注册的 agent '{agent_name}',"
                f"已注册:{list(self._agent_runtimes.keys())}"
            )
        return self._agent_runtimes[agent_name]

    def list_agents(self) -> list[str]:
        """列出所有已注册 agent 名,供 CLI/debug 使用。"""
        return list(self._agent_runtimes.keys())

    def list_managers(self) -> list[str]:
        """列出所有主管(kind == 'manager')。Director / 4 业务主管。"""
        return [
            name for name, rt in self._agent_runtimes.items() if rt.kind == "manager"
        ]

    def list_experts(self) -> list[str]:
        """列出所有专家(kind == 'expert')。12 个,被主管以 ExpertTool 形式调用。"""
        return [
            name for name, rt in self._agent_runtimes.items() if rt.kind == "expert"
        ]

    # ============================================================
    # Backward-compat(旧 role-based 调用方仍可工作)
    # ============================================================
    def get_active_provider(self) -> ARKProvider:
        """旧接口:返回当前 Provider。新代码请直接读 cfg.ark_provider。"""
        return self.ark_provider

    def get_model_for_role(self, role: str) -> tuple[str, ARKProvider]:
        """
        旧接口:role → (model, provider)。

        新代码请改用 `get_for_agent`。本方法仅作 backward-compat shim,通过
        LEGACY_ROLE_TO_AGENT 把 role 翻译成 agent 后查 _agent_runtimes。
        """
        role = role.lower()
        if role == "embed":
            return self.ark_provider.embed_model, self.ark_provider

        agent_name = LEGACY_ROLE_TO_AGENT.get(role)
        if agent_name is None:
            raise ValueError(
                f"未知的 legacy role '{role}',"
                f"可用 legacy role:{list(LEGACY_ROLE_TO_AGENT.keys())} + 'embed'"
            )
        return self._agent_runtimes[agent_name].model, self.ark_provider

    def get_temperature_for_role(self, role: str) -> float:
        """旧接口:role → temperature。"""
        role = role.lower()
        agent_name = LEGACY_ROLE_TO_AGENT.get(role)
        if agent_name is None:
            return 0.7
        return self._agent_runtimes[agent_name].temperature

    def get_max_tokens_for_role(self, role: str) -> int:
        """旧接口:role → max_tokens。"""
        role = role.lower()
        agent_name = LEGACY_ROLE_TO_AGENT.get(role)
        if agent_name is None:
            return 4000
        return self._agent_runtimes[agent_name].max_tokens

    @property
    def ACTIVE_PROVIDER(self) -> str:
        """旧字段:当前 Provider 名(单一化后永远是 'ark')。"""
        return self.ark_provider.name

    @property
    def PROVIDERS(self) -> dict[str, ARKProvider]:
        """旧字段:Provider 字典。重构后只剩一个 ARK,但保留 dict 形态兼容旧调用。"""
        return {"ark": self.ark_provider, "doubao": self.ark_provider}

    # 旧的温度/max_tokens 属性,某些旧文件直接 `cfg.WRITER_TEMPERATURE` 读取
    @property
    def WRITER_TEMPERATURE(self) -> float:
        return self._agent_runtimes["Writer"].temperature

    @property
    def GENERATOR_TEMPERATURE(self) -> float:
        return self._agent_runtimes["ArcDesigner"].temperature

    @property
    def TRIM_TEMPERATURE(self) -> float:
        return self._agent_runtimes["ContinuityChecker"].temperature

    @property
    def EXTRACT_TEMPERATURE(self) -> float:
        return self._agent_runtimes["Scribe"].temperature

    # ============================================================
    # 校验与导出
    # ============================================================
    def validate(self) -> list[str]:
        """
        启动时调用,检查必要配置完备性。

        Returns:
            错误信息列表。空表示全通过。

        校验项:
            1. ARK API key 是否设置
            2. workspace 目录是否可访问/创建
            3. 14 个 agent runtime 是否齐备(防止 AGENT_DEFINITIONS 写残)
        """
        errors = []

        if not self.ark_provider.is_configured():
            errors.append(
                "ARK API key 未设置,请在 .env 中设置 ARK_API_KEY 或 DOUBAO_API_KEY"
            )

        workspace = Path(self.WORKSPACE_PATH)
        if not workspace.exists():
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"无法创建工作目录 '{self.WORKSPACE_PATH}': {e}")

        missing_agents = set(AGENT_DEFINITIONS) - set(self._agent_runtimes)
        if missing_agents:
            errors.append(f"AgentRuntime 缺失:{sorted(missing_agents)}")

        return errors

    def is_production(self) -> bool:
        return self.ENV.lower() == "production"

    def to_dict(self) -> dict:
        """
        导出当前配置快照,API key 被隐藏(仅显示是否已配置),用于日志或调试接口。
        """
        return {
            "ENV": self.ENV,
            "DEBUG": self.DEBUG,
            "HOST": self.HOST,
            "PORT": self.PORT,
            "WORKSPACE_PATH": self.WORKSPACE_PATH,
            "RATE_LIMIT": self.RATE_LIMIT,
            "ENABLE_STREAMING": self.ENABLE_STREAMING,
            "ARK": {
                "name": self.ark_provider.name,
                "base_url": self.ark_provider.base_url,
                "api_key": "已配置" if self.ark_provider.is_configured() else "未配置",
                "embed_model": self.ark_provider.embed_model or "(未配置,使用本地)",
            },
            "AGENTS": {
                name: {
                    "role": rt.role,
                    "kind": rt.kind,
                    "model": rt.model,
                    "temperature": rt.temperature,
                    "max_tokens": rt.max_tokens,
                }
                for name, rt in self._agent_runtimes.items()
            },
            "VECTOR": {
                "store": self.VECTOR_STORE_TYPE,
                "model_source": self.VECTOR_MODEL_SOURCE,
                "local_embed_model": self.LOCAL_EMBED_MODEL,
            },
        }


# ============================================================
# 单例与代理
# ============================================================

_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局 Config 单例。首次调用时初始化(读 .env、构建 14 AgentRuntime),后续返回缓存。
    所有模块**必须**通过此函数获取配置,禁止直接 `os.getenv`。
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """
    丢弃缓存,强制重新读取 .env 与环境变量。

    适用场景:
        - 修改了 .env 后想热更新
        - 测试用例切换不同环境

    注意:已经持有旧 Config 引用的对象不会自动更新,需要重新调用 get_config()。
    """
    global _config
    _config = Config()
    return _config


def __getattr__(name: str):
    """
    模块级动态属性代理:`from core.config import HOST` 等价于 `get_config().HOST`。

    覆盖范围:Config 实例上**所有**字段与属性(包括 backward-compat 的 PROVIDERS、
    ACTIVE_PROVIDER、WRITER_TEMPERATURE 等),以及 AGENT_DEFINITIONS / ROLE_DEFAULTS
    这些模块级常量(直接由 Python import 机制处理,不进入 __getattr__)。
    """
    cfg = get_config()
    if hasattr(cfg, name):
        return getattr(cfg, name)
    raise AttributeError(f"module 'core.config' has no attribute '{name}'")
