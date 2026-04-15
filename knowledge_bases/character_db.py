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
    
    def get_character(self, name: str) -> Optional[CharacterCard]:
        """
        根据姓名获取人物卡
        
        Args:
            name: 人物姓名
        
        Returns:
            CharacterCard 对象，不存在则返回 None
        """
        data = self.load(name)
        if not data:
            return None
        
        try:
            return CharacterCard(**data)
        except Exception as e:
            logger.error(f"解析人物卡失败 {name}: {e}")
            return None
    
    def save_character(self, character: CharacterCard) -> bool:
        """
        保存人物卡
        
        Args:
            character: 人物卡对象
        
        Returns:
            是否保存成功
        """
        return self.save(character.name, character.model_dump())
    
    async def apply_update(self, update: CharacterStateUpdate) -> bool:
        """
        应用人物状态更新
        
        Args:
            update: 状态更新对象
        
        Returns:
            是否更新成功
        """
        char = self.get_character(update.character_name)
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
        
        return self.save_character(char)
    
    def list_characters(self) -> list[str]:
        """
        列出所有人物姓名
        
        Returns:
            人物姓名列表
        """
        return self.list_keys()
