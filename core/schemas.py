"""
core/schemas.py

系统的单一数据源（Single Source of Truth）。

职责边界：
    - 定义 MANS 系统中所有模块的输入输出数据模型。
    - 所有 Pydantic 模型集中在此文件定义，任何新字段必须首先在此声明。
    - 提供类型安全的枚举定义，避免魔法字符串在代码中扩散。
    - 通过 Pydantic 的验证机制确保数据完整性和一致性。

设计原则：
    1. 每个模型都有明确的生命周期和所有权（谁创建、谁更新、谁消费）。
    2. 预留扩展字段（extra="allow"）应对未来需求，避免频繁修改 schema。
    3. 添加版本字段支持数据迁移（如 Bible 的 version 字段）。
    4. 所有时间戳使用 ISO 格式字符串，便于 JSON 序列化和跨语言兼容。
    5. 使用 AliasChoices 支持多种字段名别名，提高 LLM 输出兼容性。

模型分类：
    - 枚举类型：Genre, ProjectStatus, WorldRuleCategory 等。
    - 基础类型：CultivationLevel, Relationship。
    - 人物相关：CharacterCard, CharacterStateUpdate。
    - 世界观相关：WorldRule, CombatSystem。
    - 伏笔相关：ForeshadowingItem。
    - 故事结构：ScenePlan, ChapterPlan, ChapterFinal。
    - 注入上下文：InjectionContext。
    - 更新提取：ExtractedUpdates。
    - 项目元信息：ProjectMeta。
    - API 模型：CreateProjectRequest, GenerateResponse, StreamEvent。

修改约定：
    当需要添加新数据字段时，首先在此文件中定义，然后同步更新：
    - 前端表单和类型定义
    - 生成器的 JSON Schema
    - 知识库的读写方法
    - 向量存储的 metadata
"""

from pydantic import BaseModel, Field, ConfigDict, AliasChoices, field_validator
from typing import Literal, Optional, Any
from datetime import datetime
from enum import Enum
import uuid


# ============================================================
# 枚举类型定义（提供类型安全和代码提示，替代魔法字符串）
# ============================================================

class Genre(str, Enum):
    """
    小说类型枚举。

    用于 ProjectMeta.genre 字段，限制可选项并提供 IDE 自动补全。
    当前支持的类型覆盖中文网络小说主流题材。
    """
    FANTASY = "玄幻"
    XIANXIA = "仙侠"
    URBAN = "都市"
    SCIFI = "科幻"
    WUXIA = "武侠"
    HISTORICAL = "历史"
    OTHER = "其他"


class ProjectStatus(str, Enum):
    """
    项目状态枚举。

    项目生命周期：initializing（初始化中）→ writing（写作中）→ paused（暂停）→ completed（已完成）。
    状态转换通常由前端用户操作或初始化流程自动推进。
    """
    INITIALIZING = "initializing"
    WRITING = "writing"
    PAUSED = "paused"
    COMPLETED = "completed"


class WorldRuleCategory(str, Enum):
    """
    世界规则分类枚举。

    用于 WorldRule.category 字段，对规则进行主题归类，便于 InjectionEngine 按类别检索。
    例如：检索 category="cultivation" 的规则用于战斗场景。
    """
    CULTIVATION = "cultivation"
    GEOGRAPHY = "geography"
    SOCIAL = "social"
    PHYSICS = "physics"
    SPECIAL = "special"


class WorldRuleImportance(str, Enum):
    """
    世界规则重要性枚举。

    影响 InjectionEngine 的检索优先级和上下文注入排序：
        - critical: 核心规则（如战力体系），优先注入。
        - major: 重要规则（如主要势力关系），次优先注入。
        - minor: 次要规则（如背景设定），仅在 token 充裕时注入。
    """
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class ForeshadowingType(str, Enum):
    """
    伏笔类型枚举。

    对伏笔按叙事维度分类，便于 InjectionEngine 按类型检索和过滤。
    """
    PLOT = "plot"
    CHARACTER = "character"
    WORLD = "world"
    EMOTIONAL = "emotional"


class ForeshadowingStatus(str, Enum):
    """
    伏笔状态枚举。

    伏笔全生命周期状态机：
        PLANTED（已埋下）→ HINTED（已暗示）→ TRIGGERED（已触发）→ RESOLVED（已解决）。

    状态说明：
        - PLANTED: 伏笔已被埋下，读者可能尚未察觉。
        - HINTED: 通过细节对读者进行暗示，提高后续揭晓的合理性。
        - TRIGGERED: 伏笔在情节中被直接触发，悬念揭晓。
        - RESOLVED: 伏笔的影响已完全消化，不再需要在上下文中提醒。
    """
    PLANTED = "planted"
    HINTED = "hinted"
    TRIGGERED = "triggered"
    RESOLVED = "resolved"


class TargetLength(str, Enum):
    """
    目标篇幅枚举。

    对应不同的估算章节数（短篇约30章、中篇约100章、长篇约300章、超长篇约500章）。
    由 OutlineGenerator._estimate_chapter_count() 用于计算三幕结构的章节范围。
    """
    SHORT = "短篇(<10万)"
    MEDIUM = "中篇(10-50万)"
    LONG = "长篇(50-200万)"
    EPIC = "超长篇(200万+)"


# ============================================================
# 基础类型
# ============================================================

class CultivationLevel(BaseModel):
    """
    修炼境界模型。

    用于人物卡的 cultivation 字段和 Bible 的战力体系定义。
    包含大境界、小阶段和战力估算三个维度。

    扩展预留：
        通过 extra="allow" 支持未来添加境界特性、突破条件引用等字段，
        无需修改模型定义即可兼容新旧数据。
    """
    model_config = ConfigDict(extra="allow")

    realm: str                          # 大境界，如"筑基期"、"金丹期"
    stage: str                          # 小阶段，如"初期/中期/后期/圆满"
    combat_power_estimate: str          # 战力估算描述，如"可力敌百人"、"堪比元婴"


class Relationship(BaseModel):
    """
    人物关系条目模型。

    表示两个人物之间的有向关系（source → target）。
    关系历史只增不减（append-only），保留完整的变化轨迹，
    便于回溯人物关系演变。

    方法：
        add_history_note(): 添加带时间戳的关系变化记录。
    """
    model_config = ConfigDict(extra="allow")

    target_character_id: str            # 目标人物的 UUID
    target_name: str                    # 目标人物的姓名（冗余存储，便于展示）
    relation_type: str                  # 关系类型，如"师父/敌人/挚友/恋人"
    current_sentiment: str              # 当前态度，如"信任/敌对/复杂/暧昧"
    history_notes: list[str] = Field(default_factory=list)  # 关系变化记录，只增不减

    def add_history_note(self, note: str) -> None:
        """
        添加带时间戳的关系历史记录。

        格式：[ISO时间戳] 记录内容
        例如：[2026-04-19T10:30:00] 主角救下了被围攻的目标人物

        Args:
            note: 关系变化描述。
        """
        self.history_notes.append(f"[{datetime.now().isoformat()}] {note}")


# ============================================================
# 人物相关
# ============================================================

class CharacterCard(BaseModel):
    """
    人物卡模型 —— 单个人物的完整信息。

    分为固有属性（初始化后原则上不修改）和动态状态（每章可能更新）两类：
        - 固有属性：appearance（外貌）、personality_core（性格核心）、voice_keywords（声线关键词）、
          background（背景）。这些是角色的"DNA"，定义了角色是谁。
        - 动态状态：current_location（位置）、cultivation（修为）、current_emotion（情绪）、
          active_goals（目标）。这些随故事推进而变化，由 UpdateExtractor 每章更新。

    降级注入支持：
        appearance/personality_core/background 字段设有默认值（""），
        使 InjectionEngine 可以创建仅包含 name 和 personality_core 的降级卡片，
        用于配角在场景中的简略注入，节省 token。

    状态历史：
        state_history 列表记录每次状态变更的快照（chapter, scene_index, timestamp, updates），
        支持 Writer.regenerate_scene() 时的状态回滚。
    """
    model_config = ConfigDict(extra="allow")

    # 标识信息
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    aliases: list[str] = Field(default_factory=list)

    # 固有属性（初始化后原则上不修改，只追加）
    appearance: str = ""                # 外貌描述（降级注入时可为空）
    personality_core: str = ""          # 性格核心关键词（3-5个词，降级注入时可为空）
    voice_keywords: list[str] = Field(default_factory=list)  # 声线关键词，如["愤怒时沉默", "开心时话多"]
    background: str = ""                # 背景设定（降级注入时可为空）

    @field_validator("personality_core", mode="before")
    @classmethod
    def _coerce_personality_core(cls, v):
        """兼容 LLM 把 personality_core 输出成列表的情况。"""
        if v is None:
            return ""
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return v

    # 动态状态（每章可能更新）
    current_location: str = ""
    cultivation: Optional[CultivationLevel] = None
    current_emotion: str = ""           # 当前情绪状态
    active_goals: list[str] = Field(default_factory=list)  # 当前目标列表

    # 关系网
    relationships: list[Relationship] = Field(default_factory=list)

    # 元信息
    is_protagonist: bool = False        # 是否为主角（用于 InjectionEngine 和生成器区分主角/配角）
    first_appeared_chapter: int = 0     # 首次出场章节（0表示尚未在正文中出场）
    last_updated_chapter: int = 0       # 最后更新章节

    # 扩展：状态历史（用于追踪人物变化轨迹和支持回滚）
    state_history: list[dict] = Field(default_factory=list)

    def update_state(self, chapter: int, updates: dict[str, Any], scene_index: int = -1) -> None:
        """
        更新人物状态并记录历史快照。

        每次调用会：
            1. 创建包含当前时间戳、章节号、场景索引和变更内容的快照。
            2. 将快照追加到 state_history。
            3. 更新 last_updated_chapter。
            4. 应用 updates 中指定的字段变更（通过 setattr）。

        Args:
            chapter: 当前章节号。
            updates: 变更字典，key 为字段名，value 为新值。
            scene_index: 场景索引（-1 表示非特定场景更新）。
        """
        snapshot = {
            "chapter": chapter,
            "scene_index": scene_index,
            "timestamp": datetime.now().isoformat(),
            "updates": updates
        }
        self.state_history.append(snapshot)
        self.last_updated_chapter = chapter

        # 应用更新
        for key, value in updates.items():
            if hasattr(self, key):
                setattr(self, key, value)


class CharacterStateUpdate(BaseModel):
    """
    UpdateExtractor 提取出的人物状态变更模型。

    用于异步更新知识库时传递状态变更信息。
    通过 AliasChoices 支持多种字段名别名，兼容不同 LLM 的输出格式。

    支持别名：
        - character_id: "character_id" | "id" | "char_id" | "characterId"
        - character_name: "character_name" | "name" | "char_name" | "characterName"
        - location_change: "location_change" | "location" | "new_location" | "locationChange"
        - cultivation_change: "cultivation_change" | "cultivation" | "realm_change" | "cultivationChange"
        - emotion_change: "emotion_change" | "emotion" | "mood" | "feeling" | "emotionChange"
        - goal_updates: "goal_updates" | "goals" | "new_goals" | "goalUpdates"
        - relationship_updates: "relationship_updates" | "relationships" | "relation_updates" | "relationshipChanges"
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    character_id: str = Field(validation_alias=AliasChoices("character_id", "id", "char_id", "characterId"))
    character_name: str = Field(validation_alias=AliasChoices("character_name", "name", "char_name", "characterName"))
    location_change: Optional[str] = Field(default=None, validation_alias=AliasChoices("location_change", "location", "new_location", "locationChange"))
    cultivation_change: Optional[str] = Field(default=None, validation_alias=AliasChoices("cultivation_change", "cultivation", "realm_change", "cultivationChange"))
    emotion_change: Optional[str] = Field(default=None, validation_alias=AliasChoices("emotion_change", "emotion", "mood", "feeling", "emotionChange"))
    goal_updates: list[str] = Field(default_factory=list, validation_alias=AliasChoices("goal_updates", "goals", "new_goals", "goalUpdates"))
    relationship_updates: list[dict] = Field(default_factory=list, validation_alias=AliasChoices("relationship_updates", "relationships", "relation_updates", "relationshipChanges"))


# ============================================================
# 世界观相关
# ============================================================

class WorldRule(BaseModel):
    """
    单条世界规则模型。

    世界规则是 Bible 的组成部分，一旦确认后进入"仅追加"模式：
        - 不修改已有规则的内容（保证历史一致性）。
        - 可以追加新发现的规则（如主角探索到新区域时发现新法则）。

    字段说明：
        - category: 规则分类，影响 InjectionEngine 的检索策略。
        - content: 规则描述文本，是向量化存储和检索的主要内容。
        - source_chapter: 首次明确该规则的章节号，用于审计。
        - importance: 重要性，影响注入优先级（critical 优先注入）。
        - version: 版本号（当前固定为 1，预留未来升级）。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: WorldRuleCategory
    content: str                        # 规则描述，如"元婴期修士可神识外放百里"
    source_chapter: int                 # 首次明确该规则的章节号
    importance: WorldRuleImportance     # 重要性，影响注入优先级
    version: int = 1                    # 版本控制（预留）
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CombatSystem(BaseModel):
    """
    战力体系模型。

    全局定义，在项目初始化时由 BibleGenerator 生成，后续仅追加不修改。
    是小说战斗描写和人物修为设定的根本依据。

    字段说明：
        - name: 战力体系名称，如"灵气修炼体系"。
        - realms: 大境界列表（从低到高排序），如["练气", "筑基", "金丹", "元婴"]。
        - breakthrough_conditions: 各境界突破条件字典，key 为境界名，value 为条件描述。
        - special_abilities: 特殊能力类型列表，如["炼丹", "炼器", "阵法"]。
        - power_ceiling: 当前故事的战力上限说明。
    """
    model_config = ConfigDict(extra="allow")

    name: str                           # 体系名称，如"灵气修炼体系"
    realms: list[str]                   # 大境界列表（从低到高排序）
    breakthrough_conditions: dict[str, str]  # 各境界突破条件，如{"练气": "需打通十二经脉"}
    special_abilities: list[str] = Field(default_factory=list)
    power_ceiling: str                  # 当前故事战力上限说明


# ============================================================
# 节点式世界观数据（图结构）
# ============================================================
# 地理、势力、修为三类天然具有"向外/向上扩展"属性的数据，
# 使用节点+边的图结构存储，替代扁平数组。
# ============================================================

class GeoConnection(BaseModel):
    """地理节点之间的连接关系（空间邻接、通道、包含等）。"""
    model_config = ConfigDict(extra="allow")

    target_id: str
    relation_type: Literal["adjacent", "passage", "teleport", "border", "contains"] = "adjacent"
    distance: Optional[str] = None       # "300里", "半日路程"
    description: Optional[str] = None
    bidirectional: bool = True


class FactionPresence(BaseModel):
    """某势力在特定地理节点上的存在情况。"""
    model_config = ConfigDict(extra="allow")

    faction_id: str
    faction_name: str
    strength: Literal["dominant", "strong", "moderate", "weak", "hidden", "contested"] = "moderate"
    description: Optional[str] = None    # 如"总部所在地"、"秘密据点"


class GeoNode(BaseModel):
    """
    地理节点 —— 层级树 + 连接图。

    用 parent_id/child_ids 表达层级（大陆→区域→城邦→据点），
    用 connections 表达空间连接（相邻、通道、传送等）。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"geo_{str(uuid.uuid4())[:6]}")
    name: str
    node_type: Literal["continent", "region", "state", "city", "district", "site", "realm", "secret_realm"] = "site"
    parent_id: Optional[str] = None      # 上级区域 ID
    child_ids: list[str] = Field(default_factory=list)   # 直接下级 ID 列表
    connections: list[GeoConnection] = Field(default_factory=list)
    description: str = ""
    faction_presence: list[FactionPresence] = Field(default_factory=list)
    depth_level: int = 0                 # 层级深度（大陆=0, 区域=1, 城邦=2...）
    scale: str = ""                      # 大陆/区域/城邦/据点
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = None


class FactionRelation(BaseModel):
    """势力之间的关系边。"""
    model_config = ConfigDict(extra="allow")

    target_faction_id: str
    relation_type: Literal["rivalry", "alliance", "vassal", "hostile", "friendly", "neutral", "secret", "trade"] = "neutral"
    intensity: Literal["low", "medium", "high", "critical"] = "medium"
    description: Optional[str] = None
    since_chapter: int = 0


class FactionNode(BaseModel):
    """
    势力节点 —— 关系网。

    用 parent_faction_id/sub_faction_ids 表达层级（总盟→分舵），
    用 relations 表达与其他势力的关系边（敌对/同盟/隶属等）。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"fac_{str(uuid.uuid4())[:6]}")
    name: str
    node_type: Literal["sect", "dynasty", "guild", "clan", "secret_org", "alliance", "tribe", "council"] = "sect"
    stance: Literal["righteous", "neutral", "evil", "gray"] = "neutral"
    parent_faction_id: Optional[str] = None
    sub_faction_ids: list[str] = Field(default_factory=list)
    description: str = ""
    leader: Optional[str] = None
    relations: list[FactionRelation] = Field(default_factory=list)
    controlled_territories: list[str] = Field(default_factory=list)  # geo_node id 列表
    member_count_estimate: Optional[str] = None  # "数千", "未知"
    founded_chapter: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CultivationNode(BaseModel):
    """
    修为节点 —— 递进链 + 分支。

    用 parent_id/next_ids 表达境界递进（练气→筑基→金丹），
    用 branch_from 表达分支（如体修/法修从同一境界分出）。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"cul_{str(uuid.uuid4())[:6]}")
    name: str
    tier: int = 1                        # 层级序号（越小越低）
    node_type: Literal["realm", "stage", "breakthrough", "branch", "special"] = "realm"
    parent_id: Optional[str] = None      # 上级境界 ID
    next_ids: list[str] = Field(default_factory=list)    # 后续境界 ID 列表（可能有分支）
    branch_from: Optional[str] = None    # 从哪个节点分出的分支
    prerequisites: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    power_scale: Optional[int] = None    # 战力标尺（相对值）
    description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CultivationChain(BaseModel):
    """修为体系的整体定义（一根或多根链条的集合）。"""
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"chain_{str(uuid.uuid4())[:6]}")
    name: str                            # 如"龙血修炼体系"
    root_id: str                         # 根节点 ID
    branch_ids: list[str] = Field(default_factory=list)  # 主要分支入口
    description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# 伏笔相关
# ============================================================

class ForeshadowingItem(BaseModel):
    """
    伏笔条目模型 —— 追踪和管理小说中的伏笔全生命周期。

    伏笔状态机：
        PLANTED（已埋下）→ HINTED（已暗示）→ TRIGGERED（已触发）→ RESOLVED（已解决）。

    字段说明：
        - type: 伏笔类型（plot/character/world/emotional）。
        - planted_chapter: 埋下该伏笔的章节号。
        - description: 伏笔内容描述，是向量化存储的主要内容。
        - trigger_range: 计划触发章节范围（start, end），用于 InjectionEngine 自动检索。
        - status: 当前状态。
        - urgency: 紧急程度（low/medium/high），影响注入优先级。
        - actual_trigger_chapter: 实际触发章节（触发后记录，用于审计和回滚）。
        - actual_resolution_chapter: 实际解决章节。

    方法：
        can_trigger_in_chapter(): 检查是否可以在指定章节触发（状态+范围双重检查）。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ForeshadowingType
    planted_chapter: int                # 埋下伏笔的章节号
    description: str                    # 伏笔内容描述，如"主角母亲的玉佩有特殊纹路"
    trigger_range: tuple[int, int]      # 计划触发章节范围（start, end）
    status: ForeshadowingStatus = ForeshadowingStatus.PLANTED
    resolution_description: Optional[str] = None
    urgency: Literal["low", "medium", "high"] = "medium"

    # 扩展：实际触发信息（用于审计和回滚）
    actual_trigger_chapter: Optional[int] = None
    actual_resolution_chapter: Optional[int] = None

    def can_trigger_in_chapter(self, chapter: int) -> bool:
        """
        检查该伏笔是否可以在指定章节触发。

        触发条件（必须同时满足）：
            1. 当前章节在 trigger_range 范围内（start <= chapter <= end）。
            2. 当前状态为 PLANTED 或 HINTED（已触发或已解决的伏笔不能再次触发）。

        Args:
            chapter: 要检查的章节号。

        Returns:
            True 表示可以在该章节触发，False 表示不能。
        """
        start, end = self.trigger_range
        return start <= chapter <= end and self.status in [
            ForeshadowingStatus.PLANTED,
            ForeshadowingStatus.HINTED
        ]


# ============================================================
# 故事结构相关
# ============================================================

class ScenePlan(BaseModel):
    """
    单个场景规划模型 —— 写作的最小调度单元。

    Writer 每次只生成一个场景（Scene），ScenePlan 定义了这个场景的"指令集"：
        - 意图（intent）：这个场景要达成什么叙事目的。
        - 视角（pov_character）：谁的眼睛看这个场景。
        - 出场人物（present_characters）：谁在这个场景中出现。
        - 情绪基调（emotional_tone）：这个场景的整体情绪氛围。
        - 伏笔处理（foreshadowing_to_plant/trigger）：这个场景要埋下或触发哪些伏笔。
        - 字数目标（target_word_count）：这个场景应该写多长。
        - 特殊指示（special_instructions）：额外的写作要求。

    场景是章节（Chapter）的组成部分，一个章节通常包含 2-6 个场景。
    """
    model_config = ConfigDict(extra="allow")

    scene_index: int                    # 在本章内的序号（从0开始连续）
    intent: str                         # 场景意图（1-2句话），如"主角与师父告别，展示不舍之情"
    pov_character: str                  # 主视角人物姓名
    present_characters: list[str] = Field(default_factory=list)  # 出场人物列表（必须包含POV人物）
    emotional_tone: str = "中性"         # 情绪基调，如"压抑/热血/温情/紧张"
    foreshadowing_to_plant: list[str] = Field(default_factory=list)  # 要埋入的伏笔ID列表
    foreshadowing_to_trigger: list[str] = Field(default_factory=list)  # 要触发的伏笔ID列表
    target_word_count: int = 1200       # 目标字数（Writer 生成时的参考长度）
    special_instructions: str = ""      # 特殊写作指示，如"注意节奏控制，不要拖沓"


class ChapterPlan(BaseModel):
    """
    章节规划模型 —— 包含场景序列和本章目标。

    ChapterPlan 是 ChapterPlanner 的输出，也是 Writer 生成正文时的重要上下文来源。
    它定义了一个章节内所有场景的序列、每章的整体目标和情绪走向。

    字段说明：
        - chapter_number: 章节编号（从1开始）。
        - title: 章节标题。
        - arc_id: 所属弧线 ID，用于关联回弧线规划。
        - chapter_goal: 本章对主线的推进目标（一句话）。
        - emotional_arc: 本章的情绪走向描述，如"紧张期待 → 险象环生 → 意外惊喜"。
        - key_events: 本章的关键事件列表。
        - scenes: 场景序列（ScenePlan 列表）。
        - previous_chapter_summary: 上一章摘要（用于上下文衔接）。
    """
    model_config = ConfigDict(extra="allow")

    chapter_number: int
    title: str = ""
    arc_id: str = ""                    # 所属弧线 ID
    chapter_goal: str = ""              # 本章对主线的推进目标（一句话）
    emotional_arc: str = ""             # 情绪走向描述
    key_events: list[str] = Field(default_factory=list)
    scenes: list[ScenePlan] = Field(default_factory=list)
    previous_chapter_summary: str = ""


class ChapterFinal(BaseModel):
    """
    章节完稿模型 —— 包含完整文本和元数据。

    当一个章节的所有场景都生成完毕并经过人工确认后，合并为 ChapterFinal。
    这是章节的"最终形态"，包含完整文本、字数统计、摘要等信息。

    字段说明：
        - full_text: 章节完整文本（所有场景拼接）。
        - word_count: 总字数。
        - scene_texts: 各场景的文本列表（便于回溯和修改单场景）。
        - summary: 本章摘要（约200字），用于后续章节的上下文衔接。
        - state_snapshot: 本章结束时的世界状态快照。
        - issues_found: 发现的问题列表（人工审核时记录）。
    """
    model_config = ConfigDict(extra="allow")

    chapter_number: int
    title: str
    full_text: str = ""
    word_count: int = 0
    scene_texts: list[str] = Field(default_factory=list)
    summary: str = ""                   # 本章摘要（约200字）
    state_snapshot: dict = Field(default_factory=dict)
    issues_found: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# 注入上下文相关（核心数据结构）
# ============================================================

class InjectionContext(BaseModel):
    """
    InjectionEngine 组装完成的上下文 —— Writer 生成所需的完整数据包。

    这是 InjectionEngine 的输出和 Writer 的输入之间的"桥梁"数据结构。
    包含 Writer 生成场景正文所需的所有信息，按注入优先级分为三层：

    强制注入（规则层）—— 必须存在：
        - scene_plan: 当前场景规划（核心指令）。
        - chapter_goal: 本章对主线的推进目标。
        - previous_text: 上一场景末尾 300-500 字（上下文衔接）。
        - present_character_cards: 出场人物的人物卡（含降级卡片）。

    检索注入（向量层）—— 可能为空：
        - relevant_world_rules: 与当前场景相关的世界规则（向量检索）。
        - active_foreshadowing: 当前需要关注的活跃伏笔。
        - similar_scenes_reference: 相似历史场景的参考文本。
        - style_reference: 文风参考（按情绪基调匹配的风格示例）。

    预算信息（监控和调试）：
        - total_tokens_used: 已使用的 token 数。
        - token_budget_remaining: 剩余 token 预算。

    扩展字段：
        - next_scene_intent: 下一场景的意图（用于控制剧情节奏和悬念）。
        - injection_metadata: 注入过程的元信息（如检索来源、相似度分数等）。
    """
    model_config = ConfigDict(extra="allow")

    # 强制注入（规则层）- 必须存在
    scene_plan: ScenePlan
    chapter_goal: str
    previous_text: str = ""             # 上一场景末尾 300-500 字
    present_character_cards: list[CharacterCard] = Field(default_factory=list)

    # 检索注入（向量层）- 可能为空
    relevant_world_rules: list[WorldRule] = Field(default_factory=list)
    active_foreshadowing: list[ForeshadowingItem] = Field(default_factory=list)
    similar_scenes_reference: list[str] = Field(default_factory=list)
    style_reference: str = ""

    # 预算信息（用于监控和调试）
    total_tokens_used: int = 0
    token_budget_remaining: int = 0

    # 下一场景锚点（用于控制剧情节奏）
    next_scene_intent: str = ""

    # 扩展：注入元信息
    injection_metadata: dict = Field(default_factory=dict)

    def estimate_token_count(self) -> int:
        """
        估算当前上下文的 token 数量（粗略估计）。

        使用简单启发式：中文字符约占 1.5 tokens，英文约占 0.25 tokens。
        此处采用保守估计（每字符 0.5 token），实际值可能偏高，用于预算监控而非精确计费。

        Returns:
            估算的 token 数量整数。
        """
        text = self.model_dump_json()
        # 简单估算：每字符 0.5 token
        return len(text) // 2


# ============================================================
# 更新提取相关
# ============================================================

class ExtractedUpdates(BaseModel):
    """
    UpdateExtractor 从生成文本中提取的所有变更 —— 异步更新知识库的指令集合。

    这是 UpdateExtractor 的输出数据结构，包含从场景文本中提取出的所有状态变更。
    通过 AliasChoices 支持多种字段名别名，兼容不同 LLM 和版本的输出格式。

    变更类型：
        - character_updates: 人物状态变更（位置、修为、情绪、目标、关系）。
        - new_world_rules: 新发现或确认的世界规则。
        - foreshadowing_status_changes: 伏笔状态变更（如从 hinted 变为 triggered）。
        - new_foreshadowing: 新埋入的伏笔。
        - implicit_issues: 发现的潜在矛盾或问题（人工审核参考）。

    字段验证：
        implicit_issues 字段使用 field_validator 强制将标量包装为列表，
        应对 LLM 在只有一条记录时直接返回字符串而非字符串列表的情况。
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source_chapter: int
    source_scene_index: int
    character_updates: list[CharacterStateUpdate] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "character_updates", "characters", "characterUpdate",
            "character_updates_list", "updates"
        )
    )
    new_world_rules: list[WorldRule] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "new_world_rules", "world_rules", "rules",
            "newRules", "worldRules"
        )
    )
    foreshadowing_status_changes: list[dict] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "foreshadowing_status_changes", "foreshadowing_changes",
            "status_changes", "fs_changes", "foreshadowing_updates"
        )
    )
    new_foreshadowing: list[ForeshadowingItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "new_foreshadowing", "new_foreshadowing_items",
            "foreshadowing_items", "newFs", "fs_items"
        )
    )
    implicit_issues: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "implicit_issues", "issues", "problems",
            "potential_issues", "detected_issues"
        )
    )

    @field_validator("implicit_issues", mode="before")
    @classmethod
    def _coerce_implicit_issues(cls, v):
        """
        标量强制包装为列表。

        场景：LLM 在只有一条潜在问题时直接返回单个字符串而非字符串列表，
        导致 Pydantic 验证失败。此验证器在模式验证前执行，将标量自动包装为列表。

        Args:
            v: 原始值（可能是 str、list 或 None）。

        Returns:
            列表形式的值。
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v


# ============================================================
# 项目元信息
# ============================================================

class ProjectMeta(BaseModel):
    """
    项目元信息模型 —— 项目的顶级配置和状态。

    这是用户创建项目时填写的核心信息，贯穿整个项目生命周期：
        - 创作阶段：指导 BibleGenerator 和 CharacterGenerator 的生成方向。
        - 写作阶段：通过 tone 和 target_length 影响 Writer 的风格和节奏。
        - 归档阶段：作为项目的基本信息展示。

    字段说明：
        - core_idea: 用户填写的核心创意（一句话概括故事）。
        - protagonist_seed: 主角起点描述（如"一个被家族抛弃的废物少年"）。
        - target_length: 目标篇幅，影响大纲的章节数估算。
        - tone: 基调描述（如"热血爽文"、"虐心悲剧"），影响生成器的风格选择。
        - style_reference: 文风参考（可选，如"参考《斗破苍穹》的升级流写法"）。
        - forbidden_elements: 禁忌元素列表（可选，如"禁止后宫"、"禁止穿越"）。
        - current_chapter: 当前已完成章节数（写作进度跟踪）。
        - status: 项目状态（initializing/writing/paused/completed）。

    方法：
        mark_updated(): 更新 updated_at 时间戳。
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "未命名项目"
    genre: Genre = Genre.FANTASY
    core_idea: str = ""                 # 用户填写的核心创意
    protagonist_seed: str = ""          # 主角起点描述
    target_length: TargetLength = TargetLength.LONG
    tone: str = ""                      # 基调描述，如"热血爽文"、"虐心悲剧"
    style_reference: str = ""           # 文风参考
    forbidden_elements: list[str] = Field(default_factory=list)
    current_chapter: int = 0            # 当前已完成章节数
    status: ProjectStatus = ProjectStatus.INITIALIZING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def mark_updated(self) -> None:
        """标记项目已更新，刷新 updated_at 时间戳为当前时间。"""
        self.updated_at = datetime.now().isoformat()


# ============================================================
# API 请求/响应模型（为前端交互预留）
# ============================================================

class CreateProjectRequest(BaseModel):
    """
    创建项目请求模型。

    前端调用 POST /api/projects 时提交的数据结构。
    字段与 ProjectMeta 基本一致，但不含 id/status/current_chapter 等服务器端生成字段。
    """
    name: str
    genre: Genre
    core_idea: str
    protagonist_seed: str
    target_length: TargetLength = TargetLength.LONG
    tone: str = ""
    style_reference: str = ""
    forbidden_elements: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    """
    生成操作响应模型。

    统一生成类 API 的响应格式，包含成功/失败状态、任务 ID、消息和错误信息。
    """
    success: bool
    task_id: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


class StreamEvent(BaseModel):
    """
    SSE 流事件模型 —— 统一流式推送的数据格式。

    前端通过 EventSource 接收的每条消息都符合此格式，通过 type 字段区分事件类型：
        - token: 文本生成 token（实时流式输出）。
        - scene_start: 场景生成开始。
        - scene_complete: 场景生成完成，data 包含完整文本。
        - chapter_review: 章节审阅建议。
        - error: 错误事件。
        - done: 全部完成。
    """
    model_config = ConfigDict(extra="allow")

    type: Literal["token", "scene_start", "scene_complete", "chapter_review", "error", "done"]
    data: Any


# ============================================================
# 多 Agent 架构新增类型 (refactor 之后引入)
# ============================================================
#
# 这一段是 14-Agent 重构后新增的数据契约,与上面的「注入式管线」类型并存。
# 重构完成后,InjectionContext / ExtractedUpdates 等旧类型会随旧管线一起删除。
# 当前阶段:旧类型仍被 generators/* 与 writer/* 使用,新类型供 agents/* 与 tools/* 消费。

class IssueSeverity(str, Enum):
    """
    审查 Issue 严重等级。

    决定 Writer 是否被触发重写:
        LOW       — 风格层面建议,记录但不重写。
        MEDIUM    — 触发一次重写。
        HIGH      — 触发重写,最高优先级。
        CRITICAL  — 触发重写;若仍未解决会上升至 Director,可能要求人工介入。
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueType(str, Enum):
    """审查 Issue 类型,用于 ReviewManager 分组与去重。"""
    LITERARY = "literary"           # Critic 关注:文学性、节奏、人物刻画
    CONTINUITY = "continuity"       # ContinuityChecker 关注:设定连贯性
    FORESHADOWING = "foreshadowing" # 伏笔状态错位
    PACING = "pacing"               # 节奏(可由 Critic 或 ContinuityChecker 提出)
    TONE = "tone"                   # 基调一致性
    CHARACTER_VOICE = "character_voice"  # 人物声线/对话口吻
    OTHER = "other"


class Issue(BaseModel):
    """
    单条审查问题。

    Critic 与 ContinuityChecker 输出此类型;ReviewManager 在仲裁时合并、排序、去重。

    字段约定:
        type/severity 必填,location/suggestion 强烈建议填写以提高 Writer 重写命中率。
        source_agent 由产出方填入,例如 'Critic' 或 'ContinuityChecker'。
    """
    model_config = ConfigDict(extra="allow")

    type: IssueType
    severity: IssueSeverity
    description: str
    location: str = ""              # 文本内位置参考,如"第二段第3句"或"开头到'忽然'"
    suggestion: str = ""            # 修改建议(自然语言)
    source_agent: str = ""          # Issue 提出者


class ReviewIssues(BaseModel):
    """
    Critic 与 ContinuityChecker 并行审查的结果汇总。

    ReviewManager 接收本对象,执行去重 + 冲突化解 + 优先级排序,输出 RewriteGuidance。
    """
    model_config = ConfigDict(extra="allow")

    critic_issues: list[Issue] = Field(default_factory=list)
    continuity_issues: list[Issue] = Field(default_factory=list)

    @property
    def all_issues(self) -> list[Issue]:
        """合并两路 issues,顺序为 critic 在前。"""
        return self.critic_issues + self.continuity_issues

    @property
    def max_severity(self) -> Optional[IssueSeverity]:
        """
        所有 issues 中的最高严重级别。无 issues 时返回 None。

        Director 用此值决定是否进入 Writer 重写循环:
            None / LOW         -> 不重写,直接落稿。
            MEDIUM / HIGH / CRITICAL -> 触发 ReviewManager → Writer 重写。
        """
        if not self.all_issues:
            return None
        order = {
            IssueSeverity.LOW: 0,
            IssueSeverity.MEDIUM: 1,
            IssueSeverity.HIGH: 2,
            IssueSeverity.CRITICAL: 3,
        }
        return max(self.all_issues, key=lambda i: order[i.severity]).severity


class ConflictResolution(BaseModel):
    """
    ReviewManager 化解 Critic 与 ContinuityChecker 之间冲突意见的过程记录。

    场景:Critic 要求"加速节奏 → 删除冗长心理描写",
         ContinuityChecker 同时要求"补充上一章遗漏的回忆细节"。
         ReviewManager 必须做出取舍并解释。

    用途:
        1. 写入 RewriteGuidance.conflicts_resolved,Writer 看到化解结论。
        2. 落盘到日志,便于人工复盘 ReviewManager 的判断质量。
    """
    model_config = ConfigDict(extra="allow")

    conflicting_issues: list[Issue] = Field(default_factory=list)
    resolution: str = ""            # 化解结论(自然语言)
    chosen_direction: str = ""      # 最终选定的修改方向


class RewriteGuidance(BaseModel):
    """
    ReviewManager 仲裁后输出的统一《修改指导意见》。

    Writer 重写时不再回看原始 issues,而是通过 get_rewrite_guidance tool 拉取本对象。
    设计目标:让 Writer 接受到的指令是「一份」、「无矛盾」、「带优先级」的。

    needs_rewrite 为 False 时,本对象仅作为审查留档存在,不会触发 Writer 重写。
    """
    model_config = ConfigDict(extra="allow")

    needs_rewrite: bool
    priority_issues: list[Issue] = Field(default_factory=list)  # 排序后的核心问题(高优先级在前)
    must_keep: list[str] = Field(default_factory=list)          # 重写时不得丢失的元素(原文亮点)
    must_change: list[str] = Field(default_factory=list)        # 重写时必须修改的元素
    style_hints: str = ""           # 风格层面的提示(如"减少排比"、"加强感官细节")
    conflicts_resolved: list[ConflictResolution] = Field(default_factory=list)
    rewrite_attempt: int = 0        # 当前重写轮次(0 = 首稿,1 = 第一次重写,2 = 第二次重写)


# --- 剧作转译层(Dramaturg)产物 ---

class ActionBeat(BaseModel):
    """
    单个动作节拍。

    SceneDirector 把场景拆解成一系列「谁做了什么 → 带来什么后果」的节拍,
    Writer 按顺序把每个节拍展开成段落。
    """
    model_config = ConfigDict(extra="allow")

    subject: str                    # 动作发出者(人物名 / 群体 / 环境)
    action: str                     # 具体动作描述
    impact: str                     # 对场景或他人的直接影响


class EmotionalBeat(BaseModel):
    """
    单个情绪节拍。

    与 ActionBeat 平行,用于约束人物的内心节奏,避免 Writer 写出"工具人式"对话。
    emotion 可以是动态描述,如"恐惧逐渐转为决绝"。
    """
    model_config = ConfigDict(extra="allow")

    character: str                  # 情绪主体
    emotion: str                    # 情绪类型/演变
    trigger: str                    # 触发因素


class SceneBeatsheet(BaseModel):
    """
    剧作转译层(SceneDirector)的产物。

    Writer 严格只读这一份数据 + 上一场景尾段 + RewriteGuidance(若重写),
    禁止直接读 Bible / Character / Foreshadowing 等设定数据库。

    如此保证:
        1. Writer 看到的是已经"舞台化"的指令,而非干瘪的字典。
        2. 同一份 SceneBeatsheet 被 Writer 写两次时,产出可比较(便于 A/B)。
        3. 设定数据的呈现方式由 SceneDirector 控制,Writer 不需要做"翻译"工作。

    sensory_requirements 推荐键:
        sight / sound / smell / touch / taste / atmosphere / weather
    """
    model_config = ConfigDict(extra="allow")

    chapter_number: int
    scene_index: int

    # 视觉/感官要求 —— 强制 Writer 落实"五感体验"
    sensory_requirements: dict[str, str] = Field(default_factory=dict)

    # 节拍序列 —— Writer 按序号展开
    action_beats: list[ActionBeat] = Field(default_factory=list)
    emotional_beats: list[EmotionalBeat] = Field(default_factory=list)

    # 写作约束
    target_word_count: int = 1200
    pov_character: str = ""
    style_hints: str = ""           # SceneDirector 给 Writer 的风格层提示
    must_avoid: list[str] = Field(default_factory=list)  # 显式禁止的事项(如"不要心理描写")

    # 元信息
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    source_scene_plan_ref: str = "" # 关联的 ScenePlan 标识(chapter:scene_index)


# --- Agent 运行态记录 ---

class AgentRunRecord(BaseModel):
    """
    单次 Agent 调用的运行审计记录。

    Director / Orchestrator 在调度每个 Agent 后落盘一条,便于:
        1. 排查 ReAct 循环失控(turns 异常多)。
        2. 统计 token 消耗。
        3. 用 final_response_id 在 ARK 后台续接调试。
    """
    model_config = ConfigDict(extra="allow")

    agent_name: str
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str = ""
    turns: int = 0
    total_tokens: int = 0
    final_response_id: str = ""
    tool_calls_count: int = 0
    error: Optional[str] = None
