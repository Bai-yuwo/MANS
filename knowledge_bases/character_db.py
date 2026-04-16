"""
knowledge_bases/character_db.py
人物知识库

设计原则：
1. 单文件存储：每个人物一个 JSON 文件
2. 状态历史：保留人物变化轨迹
3. 关系网：独立存储关系信息
"""

from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.schemas import CharacterCard, CharacterStateUpdate
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.character_db')


class CharacterDB(BaseDB):
    """
    人物知识库
    
    存储人物卡、状态历史、关系网
    
    使用示例：
        db = CharacterDB(project_id="xxx")
        char = db.get_character("张三")
        await db.apply_update(update)
    """
    
    def __init__(self, project_id: str):
        super().__init__(project_id, "characters")
    
    async def get_character(self, name: str) -> Optional[CharacterCard]:
        """
        根据姓名获取人物卡（异步）
        
        Args:
            name: 人物姓名
        
        Returns:
            CharacterCard 对象，不存在则返回 None
        """
        data = await self.load(name)
        if not data:
            return None
        
        try:
            return CharacterCard(**data)
        except Exception as e:
            logger.error(f"解析人物卡失败 {name}: {e}")
            return None
    
    async def save_character(self, character: CharacterCard) -> bool:
        """
        保存人物卡（异步）
        
        Args:
            character: 人物卡对象
        
        Returns:
            是否保存成功
        """
        return await self.save(character.name, character.model_dump())
    
    async def apply_update(self, update: CharacterStateUpdate) -> bool:
        """
        应用人物状态更新（异步）
        
        Args:
            update: 状态更新对象
        
        Returns:
            是否更新成功
        """
        char = await self.get_character(update.character_name)
        if not char:
            logger.error(f"人物不存在: {update.character_name}")
            return False
        
        # 应用更新
        updates = {}
        
        if update.location_change:
            char.current_location = update.location_change
            updates['location'] = update.location_change
        
        if update.cultivation_change:
            # 简化处理，实际应该解析修为变化
            updates['cultivation'] = update.cultivation_change
        
        if update.emotion_change:
            char.current_emotion = update.emotion_change
            updates['emotion'] = update.emotion_change
        
        if update.goal_updates:
            for goal in update.goal_updates:
                if goal not in char.active_goals:
                    char.active_goals.append(goal)
            updates['goals'] = update.goal_updates
        
        # 记录状态历史
        if updates:
            char.update_state(
                chapter=0,  # 应该从更新中提取章节号
                updates=updates
            )
        
        return await self.save_character(char)
    
    async def list_characters(self) -> list[str]:
        """
        列出所有人物姓名（异步）
        
        Returns:
            人物姓名列表
        """
        return await self.list_keys()
    
    async def list_all_characters(self) -> list[dict]:
        """
        获取所有人物信息（异步，返回字典列表，供 API 使用）
        
        Returns:
            人物字典列表
        """
        names = await self.list_keys()
        characters = []
        for name in names:
            data = await self.load(name)
            if data:
                characters.append(data)
        return characters
    
    async def get_character_by_id(self, char_id: str) -> Optional[dict]:
        """
        根据 ID 获取人物信息（异步）
        
        Args:
            char_id: 人物ID
        
        Returns:
            人物数据字典，不存在则返回 None
        """
        all_chars = await self.list_all_characters()
        for char in all_chars:
            if char.get("id") == char_id:
                return char
        return None
    
    async def add_relationship(self, character_id: str, relationship) -> bool:
        """
        添加人物关系（异步）
        
        Args:
            character_id: 人物ID
            relationship: Relationship 对象或字典
        
        Returns:
            是否添加成功
        """
        # 遍历所有人物找到匹配的
        names = await self.list_keys()
        for name in names:
            data = await self.load(name)
            if data and data.get("id") == character_id:
                if "relationships" not in data:
                    data["relationships"] = []
                
                rel_data = relationship.model_dump() if hasattr(relationship, 'model_dump') else relationship
                data["relationships"].append(rel_data)
                return await self.save(name, data)
        
        logger.error(f"人物不存在: {character_id}")
        return False
