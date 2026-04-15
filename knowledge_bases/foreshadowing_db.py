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
    
    def get_all_items(self) -> list[ForeshadowingItem]:
        """
        获取所有伏笔
        
        Returns:
            ForeshadowingItem 列表
        """
        data = self.load("items") or {}
        items_data = data.get("items", [])
        
        items = []
        for item_data in items_data:
            try:
                items.append(ForeshadowingItem(**item_data))
            except Exception as e:
                print(f"解析伏笔失败: {e}")
                continue
        
        return items
    
    def get_active_for_chapter(
        self,
        current_chapter: int,
        trigger_ids: list[str] = None
    ) -> list[ForeshadowingItem]:
        """
        获取当前章节需要处理的伏笔
        
        Args:
            current_chapter: 当前章节号
            trigger_ids: 本场景计划触发的伏笔 ID 列表
        
        Returns:
            需要处理的伏笔列表
        """
        items = self.get_all_items()
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
        更新伏笔状态
        
        Args:
            fs_id: 伏笔 ID
            new_status: 新状态（hinted/triggered/resolved）
            notes: 变化说明
        
        Returns:
            是否更新成功
        """
        items = self.get_all_items()
        
        for item in items:
            if item.id == fs_id:
                item.status = ForeshadowingStatus(new_status)
                
                # 记录实际触发章节（如果是 triggered 状态）
                if new_status == "triggered":
                    # 应该从调用方传入当前章节
                    pass
                
                # 保存所有伏笔
                return self._save_all_items(items)
        
        print(f"伏笔不存在: {fs_id}")
        return False
    
    async def add_item(self, item: ForeshadowingItem) -> bool:
        """
        添加新伏笔
        
        Args:
            item: 伏笔对象
        
        Returns:
            是否添加成功
        """
        return self.append("items", item.model_dump())
    
    def _save_all_items(self, items: list[ForeshadowingItem]) -> bool:
        """保存所有伏笔（内部方法）"""
        data = {
            "items": [item.model_dump() for item in items]
        }
        return self.save("items", data)
