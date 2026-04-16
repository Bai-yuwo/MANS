"""
knowledge_bases/foreshadowing_db.py
伏笔知识库

设计原则：
1. 全生命周期：planted → hinted → triggered → resolved
2. 触发追踪：记录计划触发范围和实际触发章节
3. 优先级：urgency 字段控制注入优先级
"""

from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.schemas import ForeshadowingItem, ForeshadowingStatus
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.foreshadowing_db')


class ForeshadowingDB(BaseDB):
    """
    伏笔知识库
    
    存储伏笔清单、状态追踪、触发管理
    
    使用示例：
        db = ForeshadowingDB(project_id="xxx")
        active = db.get_active_for_chapter(current_chapter=5)
        await db.update_status(fs_id="xxx", new_status="triggered")
    """
    
    def __init__(self, project_id: str):
        super().__init__(project_id, "foreshadowing")
    
    async def get_all_items(self) -> list[ForeshadowingItem]:
        """
        获取所有伏笔（异步）
        
        Returns:
            ForeshadowingItem 列表
        """
        data = await self.load("items") or {}
        items_data = data.get("items", [])
        
        items = []
        for item_data in items_data:
            try:
                items.append(ForeshadowingItem(**item_data))
            except Exception as e:
                logger.error(f"解析伏笔失败: {e}")
                continue
        
        return items
    
    async def get_active_for_chapter(
        self,
        current_chapter: int,
        trigger_ids: list[str] = None
    ) -> list[ForeshadowingItem]:
        """
        获取当前章节需要处理的伏笔（异步）
        
        Args:
            current_chapter: 当前章节号
            trigger_ids: 本场景计划触发的伏笔 ID 列表
        
        Returns:
            需要处理的伏笔列表
        """
        items = await self.get_all_items()
        active = []
        
        for item in items:
            # 检查是否在本场景触发列表中
            if trigger_ids and item.id in trigger_ids:
                active.append(item)
                continue
            
            # 检查是否在触发范围内
            if item.can_trigger_in_chapter(current_chapter):
                active.append(item)
        
        # 按 urgency 排序
        urgency_order = {"high": 0, "medium": 1, "low": 2}
        active.sort(key=lambda x: urgency_order.get(x.urgency, 1))
        
        return active
    
    async def update_status(
        self,
        fs_id: str,
        new_status: str,
        notes: str = ""
    ) -> bool:
        """
        更新伏笔状态（异步）
        
        Args:
            fs_id: 伏笔 ID
            new_status: 新状态（hinted/triggered/resolved）
            notes: 变化说明
        
        Returns:
            是否更新成功
        """
        items = await self.get_all_items()
        
        for item in items:
            if item.id == fs_id:
                item.status = ForeshadowingStatus(new_status)
                
                # 记录实际触发章节（如果是 triggered 状态）
                if new_status == "triggered":
                    # 应该从调用方传入当前章节
                    pass
                
                # 保存所有伏笔
                return await self._save_all_items(items)
        
        logger.error(f"伏笔不存在: {fs_id}")
        return False
    
    async def add_item(self, item: ForeshadowingItem) -> bool:
        """
        添加新伏笔（异步）
        
        Args:
            item: 伏笔对象
        
        Returns:
            是否添加成功
        """
        return await self.append("items", item.model_dump())
    
    async def _save_all_items(self, items: list[ForeshadowingItem]) -> bool:
        """保存所有伏笔（异步内部方法）"""
        data = {
            "items": [item.model_dump() for item in items]
        }
        return await self.save("items", data)
    
    async def list_all_foreshadowing(self) -> list[dict]:
        """
        获取所有伏笔（异步，返回字典列表，供 API 使用）
        
        Returns:
            伏笔字典列表
        """
        items = await self.get_all_items()
        return [item.model_dump() for item in items]
    
    async def add_foreshadowing(
        self,
        fs_type: str,
        description: str,
        trigger_range: tuple = (1, 100),
        urgency: str = "medium"
    ) -> bool:
        """
        添加新伏笔（异步简化接口）
        
        Args:
            fs_type: 伏笔类型 (plot/character/world/emotional)
            description: 伏笔描述
            trigger_range: 触发章节范围 (start, end)
            urgency: 紧急程度 (low/medium/high)
        
        Returns:
            是否添加成功
        """
        import uuid
        
        # 映射urgency值到合法枚举
        urgency_map = {
            "critical": "high",
            "major": "high",
            "medium": "medium",
            "minor": "low",
            "low": "low",
            "high": "high"
        }
        normalized_urgency = urgency_map.get(urgency, "medium")
        
        item = ForeshadowingItem(
            id=f"fs_{uuid.uuid4().hex[:8]}",
            type=fs_type,  # 修复：使用type而不是fs_type
            description=description,
            planted_chapter=trigger_range[0],
            trigger_range=list(trigger_range),
            urgency=normalized_urgency,  # 修复：使用标准化后的值
            status=ForeshadowingStatus.PLANTED
        )
        return await self.append("items", item.model_dump())
