"""
tests/core/test_schemas.py — Pydantic Schema 验证层

验证所有核心模型的字段约束、默认值、序列化/反序列化。
无 I/O,无 mock,运行最快。
"""

import pytest
from pydantic import ValidationError

from core.schemas import (
    ActionBeat,
    CharacterCard,
    CharacterRosterEntry,
    ConflictResolution,
    CultivationNode,
    EmotionalBeat,
    Issue,
    IssueSeverity,
    IssueType,
    ProjectMeta,
    RewriteGuidance,
    SceneBeatsheet,
    SocialNode,
    TechNode,
)


# ============================================================
# CharacterCard
# ============================================================

class TestCharacterCard:
    def test_minimal_creation(self):
        """只传 name 也能创建,其余字段用默认值。"""
        c = CharacterCard(name="林默")
        assert c.name == "林默"
        assert c.is_full_profile is False
        assert c.importance == "support"
        assert c.appearance == ""
        assert c.personality_core == ""
        assert c.state_history == []
        assert len(c.id) > 0  # uuid 自动生成

    def test_full_profile_creation(self):
        c = CharacterCard(
            name="林默",
            appearance="黑衣青年",
            personality_core="冷静,果断",
            is_full_profile=True,
            importance="protagonist",
            is_protagonist=True,
        )
        assert c.is_full_profile is True
        assert c.importance == "protagonist"
        assert c.is_protagonist is True

    def test_importance_enum_values(self):
        """importance 只接受 4 个枚举值。"""
        for val in ["protagonist", "main", "support", "background"]:
            c = CharacterCard(name="X", importance=val)
            assert c.importance == val

    def test_importance_invalid_rejected(self):
        with pytest.raises(ValidationError):
            CharacterCard(name="X", importance="invalid")

    def test_personality_core_list_coercion(self):
        """LLM 可能把 personality_core 输出成列表,应自动拼接为字符串。"""
        c = CharacterCard(name="X", personality_core=["冷", "酷"])
        assert c.personality_core == "冷, 酷"

    def test_personality_core_none_becomes_empty(self):
        c = CharacterCard(name="X", personality_core=None)
        assert c.personality_core == ""

    def test_update_state_records_history(self):
        c = CharacterCard(name="X")
        c.update_state(chapter=1, updates={"current_emotion": "愤怒"})
        assert len(c.state_history) == 1
        assert c.state_history[0]["chapter"] == 1
        assert c.state_history[0]["updates"]["current_emotion"] == "愤怒"
        assert c.current_emotion == "愤怒"
        assert c.last_updated_chapter == 1


# ============================================================
# CharacterRosterEntry
# ============================================================

class TestCharacterRosterEntry:
    def test_required_fields(self):
        """char_id 和 name 必填。"""
        with pytest.raises(ValidationError):
            CharacterRosterEntry()

    def test_minimal_creation(self):
        e = CharacterRosterEntry(char_id="linmo", name="林默")
        assert e.char_id == "linmo"
        assert e.name == "林默"
        assert e.role_summary == ""
        assert e.faction_id is None
        assert e.importance == "support"

    def test_full_creation(self):
        e = CharacterRosterEntry(
            char_id="linmo",
            name="林默",
            faction_id="faction_01",
            role_summary="主角的师父",
            importance="main",
        )
        assert e.faction_id == "faction_01"
        assert e.role_summary == "主角的师父"
        assert e.importance == "main"


# ============================================================
# SceneBeatsheet
# ============================================================

class TestSceneBeatsheet:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            SceneBeatsheet()

    def test_minimal_creation(self):
        s = SceneBeatsheet(chapter_number=1, scene_index=0)
        assert s.chapter_number == 1
        assert s.scene_index == 0
        assert s.characters_present == []
        assert s.action_beats == []
        assert s.emotional_beats == []
        assert s.target_word_count == 1200
        assert s.sensory_requirements == {}

    def test_with_beats(self):
        s = SceneBeatsheet(
            chapter_number=1,
            scene_index=0,
            action_beats=[
                ActionBeat(subject="林默", action="拔剑", impact="剑气冲霄")
            ],
            emotional_beats=[
                EmotionalBeat(character="林默", emotion="愤怒", trigger="敌人挑衅")
            ],
            characters_present=["林默"],
        )
        assert len(s.action_beats) == 1
        assert s.action_beats[0].subject == "林默"
        assert len(s.emotional_beats) == 1


# ============================================================
# Issue + Severity/Type
# ============================================================

class TestIssue:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            Issue()

    def test_creation(self):
        i = Issue(
            type=IssueType.PACING,
            severity=IssueSeverity.HIGH,
            description="节奏拖沓",
            location="第二段",
            suggestion="删减环境描写",
            source_agent="Critic",
        )
        assert i.type == IssueType.PACING
        assert i.severity == IssueSeverity.HIGH
        assert i.location == "第二段"

    def test_severity_order(self):
        """severity 枚举顺序可通过自定义 order 比较。"""
        order = {
            IssueSeverity.LOW: 0,
            IssueSeverity.MEDIUM: 1,
            IssueSeverity.HIGH: 2,
            IssueSeverity.CRITICAL: 3,
        }
        assert order[IssueSeverity.CRITICAL] > order[IssueSeverity.HIGH]
        assert order[IssueSeverity.HIGH] > order[IssueSeverity.MEDIUM]
        assert order[IssueSeverity.MEDIUM] > order[IssueSeverity.LOW]


# ============================================================
# RewriteGuidance
# ============================================================

class TestRewriteGuidance:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            RewriteGuidance()

    def test_creation(self):
        g = RewriteGuidance(
            needs_rewrite=True,
            must_keep=["主角冷静的态度"],
            must_change=["增加环境描写"],
            rewrite_attempt=1,
        )
        assert g.needs_rewrite is True
        assert g.must_keep == ["主角冷静的态度"]
        assert g.must_change == ["增加环境描写"]
        assert g.rewrite_attempt == 1
        assert g.priority_issues == []
        assert g.style_hints == ""

    def test_no_rewrite(self):
        g = RewriteGuidance(needs_rewrite=False)
        assert g.needs_rewrite is False


# ============================================================
# 题材节点: Cultivation / Tech / Social
# ============================================================

class TestCultivationNode:
    def test_creation(self):
        n = CultivationNode(name="练气期", tier=1, node_type="realm")
        assert n.name == "练气期"
        assert n.tier == 1
        assert n.node_type == "realm"
        assert n.id.startswith("cul_")
        assert n.parent_id is None

    def test_chain(self):
        n2 = CultivationNode(
            name="筑基期", tier=2, parent_id="cul_001", next_ids=["cul_003"]
        )
        assert n2.parent_id == "cul_001"
        assert n2.next_ids == ["cul_003"]


class TestTechNode:
    def test_creation(self):
        n = TechNode(name="跃迁引擎", tier=3, node_type="tech")
        assert n.name == "跃迁引擎"
        assert n.id.startswith("tech_")
        assert n.research_cost is None


class TestSocialNode:
    def test_creation(self):
        n = SocialNode(name="九品中正制", node_type="institution")
        assert n.name == "九品中正制"
        assert n.node_type == "institution"
        assert n.id.startswith("soc_")


# ============================================================
# ProjectMeta (含新增可配置字段)
# ============================================================

class TestProjectMeta:
    def test_minimal_creation(self):
        p = ProjectMeta(name="测试", genre="玄幻")
        assert p.name == "测试"
        assert p.genre == "玄幻"
        # 新增可配置字段默认值
        assert p.auto_advance is False
        assert p.auto_rewrite is False
        assert p.max_rewrite_attempts == 2
        assert p.enable_consistency_check is True
        assert p.token_budget_per_scene == 0

    def test_config_fields_range(self):
        """max_rewrite_attempts 范围 0-3。"""
        from core.schemas import Genre
        p = ProjectMeta(name="X", genre=Genre.OTHER, max_rewrite_attempts=3)
        assert p.max_rewrite_attempts == 3

        with pytest.raises(ValidationError):
            ProjectMeta(name="X", genre=Genre.OTHER, max_rewrite_attempts=5)

    def test_serialization_roundtrip(self):
        p = ProjectMeta(
            name="测试",
            genre="仙侠",
            auto_advance=True,
            auto_rewrite=True,
            token_budget_per_scene=50000,
        )
        data = p.model_dump()
        p2 = ProjectMeta(**data)
        assert p2.auto_advance is True
        assert p2.token_budget_per_scene == 50000
