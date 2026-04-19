"""
knowledge_bases/bible_db.py
世界观知识库

设计原则：
1. 只增不减：世界规则一旦确认，只允许追加
2. 分类存储：按 cultivation/geography/social/physics/special 分类
3. 来源追踪：记录每条规则的首次明确章节
"""

from knowledge_bases.base_db import BaseDB
from core.schemas import WorldRule, WorldRuleCategory, WorldRuleImportance
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.bible_db')


class BibleDB(BaseDB):
    """
    世界观知识库（Bible）
    
    存储世界规则、战力体系、地理势力等全局设定
    
    使用示例：
        db = BibleDB(project_id="xxx")
        await db.append_rule(world_rule)
    """
    
    def __init__(self, project_id: str):
        super().__init__(project_id, "bible")
    
    async def append_rule(self, rule: WorldRule) -> bool:
        """
        追加世界规则（只增不减原则）
        
        Args:
            rule: 世界规则对象
        
        Returns:
            是否追加成功
        """
        return await self.append("world_rules", rule.model_dump())
    
    async def get_rules(self, category: str = None) -> list[WorldRule]:
        """
        获取世界规则列表（异步）
        
        Args:
            category: 分类筛选（可选）
        
        Returns:
            WorldRule 对象列表
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
        根据内容移除世界规则（用于回滚场景产生的规则）

        Args:
            content: 规则内容描述

        Returns:
            是否成功移除
        """
        try:
            data = await self.load("world_rules") or {}
            items = data.get("items", [])
            original_len = len(items)

            # 精确匹配内容（避免模糊匹配误删不相关规则）
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
        根据 ID 获取世界规则（异步）
        
        Args:
            rule_id: 规则 ID
        
        Returns:
            WorldRule 对象，不存在则返回 None
        """
        rules = await self.get_rules()
        for rule in rules:
            if rule.id == rule_id:
                return rule
        return None
