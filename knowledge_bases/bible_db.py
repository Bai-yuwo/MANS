"""
knowledge_bases/bible_db.py

世界观知识库（Bible），存储小说世界的全局设定和规则。

职责边界：
    - 存储世界规则（WorldRule），包括修炼体系、地理分布、势力关系、物理法则等。
    - 遵循"只增不减"原则：世界规则一旦确认并写入，只允许追加新规则，不允许修改或删除已有规则。
      这是为了防止后期写作中出现设定前后矛盾的问题。
    - 支持按分类筛选规则，便于 Injection Engine 按需检索。
    - 提供按内容精确匹配移除规则的能力（仅用于回滚场景产生的新规则）。

存储结构：
    workspace/{project_id}/bible/
    └── world_rules.json       # 世界规则列表

分类体系：
    - cultivation：修炼体系、境界划分、战力规则。
    - geography：地理环境、地图、势力分布。
    - social：社会关系、宗门结构、政治体系。
    - physics：物理法则、世界运行的底层规则。
    - special：特殊规则、尚未分类的条目。

典型用法：
    db = BibleDB(project_id="xxx")
    await db.append_rule(world_rule)
    rules = await db.get_rules(category="cultivation")
"""

from knowledge_bases.base_db import BaseDB
from core.schemas import WorldRule, WorldRuleCategory, WorldRuleImportance
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.bible_db')


class BibleDB(BaseDB):
    """
    世界观知识库（Bible）。

    Bible 是小说的"宪法"，所有情节展开都必须遵守其中定义的规则。
    一旦某条规则在正文中被明确（如"筑基期修士无法御剑飞行"），
    它就会被追加到 Bible 中，后续所有章节都必须遵循此约束。

    "只增不减"原则：
        这是维护设定一致性的核心机制。即使发现已有规则需要修正，
        也不应直接修改原规则，而是追加一条新规则进行补充或_override_说明。
        这保留了设定的完整演化历史，便于追溯矛盾来源。

    来源追踪：
        每条规则记录 source_chapter 字段，标明该规则首次在正文中明确的章节号，
        便于后续审查和知识库维护。
    """

    def __init__(self, project_id: str):
        super().__init__(project_id, "bible")

    async def append_rule(self, rule: WorldRule) -> bool:
        """
        追加世界规则。

        使用 BaseDB.append() 实现只追加写入，新规则自动添加到 world_rules 列表末尾。
        追加前不对规则内容进行校验，由调用方（UpdateExtractor 或 Generator）确保质量。

        Args:
            rule: 世界规则对象，必须包含 content、category、importance 字段。

        Returns:
            是否追加成功。
        """
        return await self.append("world_rules", rule.model_dump())

    async def get_rules(self, category: str = None) -> list[WorldRule]:
        """
        获取世界规则列表，支持按分类筛选。

        防御性处理：
            - 若规则数据中的 category 或 importance 不在有效枚举值范围内，
              自动降级为默认值（category="special"，importance="major"）。
            - 解析失败的单条规则会被跳过，不影响其他规则的返回。

        Args:
            category: 分类筛选条件，可选值为 WorldRuleCategory 的枚举值。
                      传入 None 返回所有分类的规则。

        Returns:
            WorldRule 对象列表，按存储顺序排列。
        """
        data = await self.load("world_rules") or {}
        items = data.get("items", [])

        valid_categories = {e.value for e in WorldRuleCategory}
        valid_importances = {e.value for e in WorldRuleImportance}

        rules = []
        for item in items:
            try:
                cat = item.get("category", "special")
                if cat not in valid_categories:
                    item = {**item, "category": "special"}
                imp = item.get("importance", "major")
                if imp not in valid_importances:
                    item = {**item, "importance": "major"}
                rule = WorldRule(**item)
                if category is None or rule.category == category:
                    rules.append(rule)
            except Exception as e:
                logger.error(f"解析世界规则失败: {e}")
                continue

        return rules

    async def remove_rule_by_content(self, content: str) -> bool:
        """
        根据内容精确移除世界规则。

        使用限制：
            此方法仅用于回滚场景产生的新规则，不应作为常规删除手段。
            因为 Bible 遵循"只增不减"原则，常规流程不应删除任何规则。

        匹配策略：
            采用精确字符串相等匹配（content == rule.content），
            避免模糊匹配误删不相关的历史规则。

        Args:
            content: 要移除的规则内容描述。

        Returns:
            是否成功移除。若找不到匹配内容返回 False。
        """
        try:
            data = await self.load("world_rules") or {}
            items = data.get("items", [])
            original_len = len(items)

            items = [
                item for item in items
                if item.get("content", "") != content
            ]

            if len(items) < original_len:
                await self.save("world_rules", {"items": items})
                return True
            return False
        except Exception as e:
            logger.error(f"移除世界规则失败: {e}")
            return False

    async def get_rule_by_id(self, rule_id: str) -> WorldRule | None:
        """
        根据 ID 获取单条世界规则。

        Args:
            rule_id: 规则的 id 字段值。

        Returns:
            WorldRule 对象，不存在返回 None。
        """
        rules = await self.get_rules()
        for rule in rules:
            if rule.id == rule_id:
                return rule
        return None
