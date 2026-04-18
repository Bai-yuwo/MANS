"""
core/schemas.py
系统的单一数据源（Single Source of Truth）
所有模块的输入输出都必须符合此文件定义的 Pydantic 模型

设计原则：
1. 每个模型都有明确的生命周期和所有权
2. 预留扩展字段（extra="allow"）应对未来需求
3. 添加版本字段支持数据迁移
4. 所有时间戳使用 ISO 格式字符串，便于序列化
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, Optional, Any
from datetime import datetime
from enum import Enum
import uuid


# ============================================================
# 枚举类型定义（提供类型安全和代码提示）
# ============================================================

class Genre(str, Enum):
    """小说类型枚举"""
    FANTASY = "玄幻"
    XIANXIA = "仙侠"
    URBAN = "都市"
    SCIFI = "科幻"
    WUXIA = "武侠"
    HISTORICAL = "历史"
    OTHER = "其他"


class ProjectStatus(str, Enum):
    """项目状态枚举"""
    INITIALIZING = "initializing"
    WRITING = "writing"
    PAUSED = "paused"
    COMPLETED = "completed"


class WorldRuleCategory(str, Enum):
    """世界规则分类"""
    CULTIVATION = "cultivation"
    GEOGRAPHY = "geography"
    SOCIAL = "social"
    PHYSICS = "physics"
    SPECIAL = "special"


class WorldRuleImportance(str, Enum):
    """世界规则重要性"""
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class ForeshadowingType(str, Enum):
    """伏笔类型"""
    PLOT = "plot"
    CHARACTER = "character"
    WORLD = "world"
    EMOTIONAL = "emotional"


class ForeshadowingStatus(str, Enum):
    """伏笔状态"""
    PLANTED = "planted"
    HINTED = "hinted"
    TRIGGERED = "triggered"
    RESOLVED = "resolved"


class TargetLength(str, Enum):
    """目标篇幅"""
    SHORT = "短篇(<10万)"
    MEDIUM = "中篇(10-50万)"
    LONG = "长篇(50-200万)"
    EPIC = "超长篇(200万+)"


# ============================================================
# 基础类型
# ============================================================

class CultivationLevel(BaseModel):
    """
    修炼境界
    用于人物卡和战力体系
    """
    model_config = ConfigDict(extra="allow")
    
    realm: str                          # 大境界，如"筑基期"
    stage: str                          # 小阶段，如"初期/中期/后期/圆满"
    combat_power_estimate: str          # 战力估算描述
    # 扩展：未来可添加境界特性、突破条件引用等


class Relationship(BaseModel):
    """
    人物关系条目
    关系历史只增不减，保留完整轨迹
    """
    model_config = ConfigDict(extra="allow")
    
    target_character_id: str
    target_name: str
    relation_type: str                  # 如"师父/敌人/挚友"
    current_sentiment: str              # 如"信任/敌对/复杂"
    history_notes: list[str] = Field(default_factory=list)  # 关系变化记录，只增不减
    
    def add_history_note(self, note: str) -> None:
        """添加关系历史记录"""
        self.history_notes.append(f"[{datetime.now().isoformat()}] {note}")


# ============================================================
# 人物相关
# ============================================================

class CharacterCard(BaseModel):
    """
    人物卡：单个人物的完整信息
    分为固有属性（只追加）和动态状态（每章更新）
    """
    model_config = ConfigDict(extra="allow")
    
    # 标识
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    aliases: list[str] = Field(default_factory=list)
    
    # 固有属性（只追加，不修改）
    appearance: str                     # 外貌描述
    personality_core: str               # 性格核心关键词（3-5个词）
    voice_keywords: list[str] = Field(default_factory=list)  # 声线关键词
    background: str                     # 背景设定
    
    # 动态状态（每章可能更新）
    current_location: str = ""
    cultivation: Optional[CultivationLevel] = None
    current_emotion: str = ""           # 当前情绪状态
    active_goals: list[str] = Field(default_factory=list)  # 当前目标
    
    # 关系网
    relationships: list[Relationship] = Field(default_factory=list)
    
    # 元信息
    first_appeared_chapter: int = 0
    last_updated_chapter: int = 0
    
    # 扩展：状态历史（用于追踪人物变化轨迹）
    state_history: list[dict] = Field(default_factory=list)
    
    def update_state(self, chapter: int, updates: dict[str, Any]) -> None:
        """更新人物状态并记录历史"""
        snapshot = {
            "chapter": chapter,
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
    Update Extractor 提取出的人物状态变更
    用于异步更新知识库
    """
    model_config = ConfigDict(extra="allow")
    
    character_id: str
    character_name: str
    location_change: Optional[str] = None
    cultivation_change: Optional[str] = None
    emotion_change: Optional[str] = None
    goal_updates: list[str] = Field(default_factory=list)
    relationship_updates: list[dict] = Field(default_factory=list)


# ============================================================
# 世界观相关
# ============================================================

class WorldRule(BaseModel):
    """
    单条世界规则
    一旦确认只追加，不修改
    """
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: WorldRuleCategory
    content: str                        # 规则描述
    source_chapter: int                 # 首次明确的章节
    importance: WorldRuleImportance     # 重要性，影响注入优先级
    # 扩展：版本控制
    version: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CombatSystem(BaseModel):
    """
    战力体系
    全局定义，项目初始化时生成
    """
    model_config = ConfigDict(extra="allow")
    
    name: str                           # 体系名称
    realms: list[str]                   # 大境界列表（从低到高）
    breakthrough_conditions: dict[str, str]  # 各境界突破条件
    special_abilities: list[str] = Field(default_factory=list)
    power_ceiling: str                  # 当前故事战力上限说明


# ============================================================
# 伏笔相关
# ============================================================

class ForeshadowingItem(BaseModel):
    """
    伏笔条目
    全生命周期管理：planted → hinted → triggered → resolved
    """
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ForeshadowingType
    planted_chapter: int
    description: str                    # 伏笔内容描述
    trigger_range: tuple[int, int]      # 计划触发章节范围（start, end）
    status: ForeshadowingStatus = ForeshadowingStatus.PLANTED
    resolution_description: Optional[str] = None
    urgency: Literal["low", "medium", "high"] = "medium"
    
    # 扩展：实际触发信息
    actual_trigger_chapter: Optional[int] = None
    actual_resolution_chapter: Optional[int] = None
    
    def can_trigger_in_chapter(self, chapter: int) -> bool:
        """检查是否可以在指定章节触发"""
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
    单个场景的规划
    写作的最小调度单元
    """
    model_config = ConfigDict(extra="allow")
    
    scene_index: int                    # 在本章内的序号
    intent: str                         # 场景意图（1-2句话）
    pov_character: str                  # 主视角人物名
    present_characters: list[str] = Field(default_factory=list)
    emotional_tone: str = "中性"         # 情绪基调
    foreshadowing_to_plant: list[str] = Field(default_factory=list)
    foreshadowing_to_trigger: list[str] = Field(default_factory=list)
    target_word_count: int = 1200
    special_instructions: str = ""


class ChapterPlan(BaseModel):
    """
    章节规划
    包含场景序列和本章目标
    """
    model_config = ConfigDict(extra="allow")
    
    chapter_number: int
    title: str = ""
    arc_id: str = ""                    # 所属弧线 ID
    chapter_goal: str = ""              # 本章对主线的推进目标
    emotional_arc: str = ""             # 情绪走向
    key_events: list[str] = Field(default_factory=list)
    scenes: list[ScenePlan] = Field(default_factory=list)
    previous_chapter_summary: str = ""


class ChapterFinal(BaseModel):
    """
    章节完稿
    包含完整文本和元数据
    """
    model_config = ConfigDict(extra="allow")
    
    chapter_number: int
    title: str
    full_text: str = ""
    word_count: int = 0
    scene_texts: list[str] = Field(default_factory=list)
    summary: str = ""                   # 本章摘要（200字）
    state_snapshot: dict = Field(default_factory=dict)
    issues_found: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# 注入上下文相关（核心数据结构）
# ============================================================

class InjectionContext(BaseModel):
    """
    Injection Engine 组装完成的上下文
    直接传入 Writer 的完整数据包
    
    设计原则：
    - 包含 Writer 生成所需的所有信息
    - 预算信息帮助监控 token 使用
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
        """估算当前上下文的 token 数量（粗略估计）"""
        # 中文字符约占 1.5 tokens，英文约占 0.25 tokens
        text = self.model_dump_json()
        # 简单估算：每字符 0.5 token
        return len(text) // 2


# ============================================================
# 更新提取相关
# ============================================================

class ExtractedUpdates(BaseModel):
    """
    Update Extractor 从生成文本中提取的所有变更
    异步更新知识库的指令集合
    """
    model_config = ConfigDict(extra="allow")
    
    source_chapter: int
    source_scene_index: int
    character_updates: list[CharacterStateUpdate] = Field(default_factory=list)
    new_world_rules: list[WorldRule] = Field(default_factory=list)
    foreshadowing_status_changes: list[dict] = Field(default_factory=list)
    new_foreshadowing: list[ForeshadowingItem] = Field(default_factory=list)
    implicit_issues: list[str] = Field(default_factory=list)


# ============================================================
# 项目元信息
# ============================================================

class ProjectMeta(BaseModel):
    """
    项目元信息
    项目的顶级配置和状态
    """
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "未命名项目"
    genre: Genre = Genre.FANTASY
    core_idea: str = ""                 # 用户填写的核心 idea
    protagonist_seed: str = ""          # 主角起点描述
    target_length: TargetLength = TargetLength.LONG
    tone: str = ""                      # 基调描述
    style_reference: str = ""           # 文风参考
    forbidden_elements: list[str] = Field(default_factory=list)
    current_chapter: int = 0            # 当前已完成章节数
    status: ProjectStatus = ProjectStatus.INITIALIZING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    
    def mark_updated(self) -> None:
        """标记项目已更新"""
        self.updated_at = datetime.now().isoformat()


# ============================================================
# API 请求/响应模型（为前端交互预留）
# ============================================================

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str
    genre: Genre
    core_idea: str
    protagonist_seed: str
    target_length: TargetLength = TargetLength.LONG
    tone: str = ""
    style_reference: str = ""
    forbidden_elements: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    """生成操作响应"""
    success: bool
    task_id: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


class StreamEvent(BaseModel):
    """
    SSE 流事件
    统一流式推送的数据格式
    """
    model_config = ConfigDict(extra="allow")
    
    type: Literal["token", "scene_start", "scene_complete", "chapter_review", "error", "done"]
    data: Any
