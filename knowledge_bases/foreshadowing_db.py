"""
knowledge_bases/foreshadowing_db.py

伏笔知识库，追踪和管理小说中的伏笔全生命周期。

职责边界：
    - 存储所有伏笔条目（ForeshadowingItem），记录其类型、描述、触发范围、紧急程度和当前状态。
    - 管理伏笔状态转换：planted（埋下）→ hinted（暗示）→ triggered（触发）→ resolved（解决）。
    - 根据当前章节号自动筛选"需要关注"的伏笔，供 Injection Engine 注入上下文。
    - 支持伏笔状态回滚（用于场景重写后的撤销）。
    - 支持按描述精确移除伏笔（用于回滚场景产生的新伏笔）。

存储结构：
    workspace/{project_id}/foreshadowing/
    └── items.json              # 伏笔列表

状态机说明：
    PLANTED：伏笔已被埋下，读者可能尚未察觉。
    HINTED：通过细节对读者进行暗示，提高后续揭晓时的合理性。
    TRIGGERED：伏笔在情节中被直接触发，悬念揭晓。
    RESOLVED：伏笔的影响已完全消化，不再需要在上下文中提醒。

典型用法：
    db = ForeshadowingDB(project_id="xxx")
    active = await db.get_active_for_chapter(current_chapter=5)
    await db.update_status(fs_id="xxx", new_status="triggered", triggered_chapter=5)
"""

from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.schemas import ForeshadowingItem, ForeshadowingStatus, ForeshadowingType
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.foreshadowing_db')


class ForeshadowingDB(BaseDB):
    """
    伏笔知识库。

    伏笔是小说叙事的核心技巧之一。好的伏笔应该：
        - 埋下时轻描淡写，不引起读者过度注意。
        - 触发时自然融入情节，不显得生硬突兀。
        - 解决后读者回想时恍然大悟。

    ForeshadowingDB 通过状态追踪和触发范围管理，确保：
        1. 伏笔不会被遗忘（系统会在触发范围内主动提醒）。
        2. 伏笔不会被过早触发（状态检查 + 触发范围双重约束）。
        3. 已解决的伏笔不会继续占用上下文空间（resolved 状态的伏笔被过滤）。

    紧急程度（urgency）：
        - high：必须在触发范围内尽快处理，优先注入上下文。
        - medium：常规优先级，按触发范围正常处理。
        - low：可选处理，仅在 token 充裕时注入。
    """

    def __init__(self, project_id: str):
        super().__init__(project_id, "foreshadowing")

    async def get_all_items(self) -> list[ForeshadowingItem]:
        """
        获取所有伏笔条目。

        防御性处理：
            - 若 type 或 status 字段值不在有效枚举范围内，自动降级为默认值。
            - 解析失败的单条伏笔会被跳过并记录错误日志，不影响其他条目的返回。

        Returns:
            ForeshadowingItem 对象列表，按存储顺序排列。
        """
        data = await self.load("items") or {}
        items_data = data.get("items", [])

        valid_types = {e.value for e in ForeshadowingType}
        valid_statuses = {e.value for e in ForeshadowingStatus}

        items = []
        for item_data in items_data:
            try:
                fs_type = item_data.get("type", "plot")
                if fs_type not in valid_types:
                    item_data = {**item_data, "type": "plot"}
                status = item_data.get("status", "planted")
                if status not in valid_statuses:
                    item_data = {**item_data, "status": "planted"}
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
        获取当前章节需要处理的伏笔。

        激活条件（满足其一即可）：
            1. 该伏笔 ID 显式出现在 trigger_ids 列表中（由场景规划指定）。
            2. 伏笔处于 planted 或 hinted 状态，且 current_chapter 落在其 trigger_range 内。

        过滤条件：
            - resolved 状态的伏笔不再激活。
            - 超出 trigger_range 的 planted/hinted 伏笔暂不激活。

        排序规则：
            按 urgency 排序，high → medium → low，确保高优先级伏笔优先注入上下文。

        Args:
            current_chapter: 当前章节号。
            trigger_ids: 本场景计划显式触发或埋入的伏笔 ID 列表。

        Returns:
            需要处理的伏笔列表，已按 urgency 排序。
        """
        items = await self.get_all_items()
        active = []

        for item in items:
            if item.status == ForeshadowingStatus.RESOLVED:
                continue

            if trigger_ids and item.id in trigger_ids:
                active.append(item)
                continue

            if item.status in (ForeshadowingStatus.PLANTED, ForeshadowingStatus.HINTED) \
                    and item.can_trigger_in_chapter(current_chapter):
                active.append(item)

        urgency_order = {"high": 0, "medium": 1, "low": 2}
        active.sort(key=lambda x: urgency_order.get(x.urgency, 1))

        return active

    async def update_status(
        self,
        fs_id: str,
        new_status: str,
        notes: str = "",
        triggered_chapter: int = 0
    ) -> bool:
        """
        更新伏笔状态。

        状态转换校验：
            new_status 必须是有效的 ForeshadowingStatus 枚举值。
            非法状态会被拒绝并记录错误日志。

        触发记录：
            若 new_status 为 "triggered"，自动记录 actual_trigger_chapter 字段，
            便于后续审计和回滚。

        Args:
            fs_id: 伏笔唯一标识。
            new_status: 新状态（hinted / triggered / resolved）。
            notes: 状态变化的说明文字。
            triggered_chapter: 实际触发的章节号（仅在 triggered 状态时有效）。

        Returns:
            是否更新成功。伏笔不存在或状态无效时返回 False。
        """
        items = await self.get_all_items()

        valid_statuses = {e.value for e in ForeshadowingStatus}
        if new_status not in valid_statuses:
            logger.error(f"无效的伏笔状态: {new_status}")
            return False

        for item in items:
            if item.id == fs_id:
                item.status = ForeshadowingStatus(new_status)

                if new_status == "triggered":
                    item.actual_trigger_chapter = triggered_chapter

                return await self._save_all_items(items)

        logger.error(f"伏笔不存在: {fs_id}")
        return False

    async def add_item(self, item: ForeshadowingItem) -> bool:
        """
        添加新伏笔。

        使用 BaseDB.append() 实现只追加写入，新伏笔自动获得唯一 ID。

        Args:
            item: 完整的 ForeshadowingItem 对象。

        Returns:
            是否添加成功。
        """
        return await self.append("items", item.model_dump())

    async def _save_all_items(self, items: list[ForeshadowingItem]) -> bool:
        """
        内部方法：保存全部伏笔列表。

        使用 BaseDB.save() 的原子写入机制，确保数据一致性。
        此方法由 update_status、revert_status 等修改操作内部调用。

        Args:
            items: 完整的伏笔对象列表。

        Returns:
            是否保存成功。
        """
        data = {
            "items": [item.model_dump() for item in items]
        }
        return await self.save("items", data)

    async def list_all_foreshadowing(self) -> list[dict]:
        """
        获取所有伏笔的原始字典列表。

        用途：
            主要用于 API 响应，前端展示伏笔列表时直接返回原始字典。

        Returns:
            伏笔字典列表。
        """
        items = await self.get_all_items()
        return [item.model_dump() for item in items]

    async def revert_status(self, fs_id: str) -> bool:
        """
        回滚伏笔状态到上一状态。

        回退顺序（不可逆）：
            resolved → triggered → hinted → planted

        使用场景：
            场景重写后，需要将已触发或已解决的伏笔恢复到之前的状态，
            避免重写后的文本与伏笔状态不一致。

        Args:
            fs_id: 伏笔唯一标识。

        Returns:
            是否回滚成功。已处于 planted 状态或伏笔不存在时返回 False。
        """
        items = await self.get_all_items()

        status_order = [
            ForeshadowingStatus.PLANTED,
            ForeshadowingStatus.HINTED,
            ForeshadowingStatus.TRIGGERED,
            ForeshadowingStatus.RESOLVED
        ]

        for item in items:
            if item.id == fs_id:
                try:
                    current_idx = status_order.index(item.status)
                    if current_idx > 0:
                        item.status = status_order[current_idx - 1]
                        return await self._save_all_items(items)
                except ValueError:
                    pass
                return False

        logger.warning(f"回滚伏笔状态失败，伏笔不存在: {fs_id}")
        return False

    async def remove_by_description(self, description: str, chapter_number: int = 0) -> bool:
        """
        根据描述精确移除伏笔。

        使用限制：
            仅用于回滚场景产生的新伏笔，不应作为常规删除手段。

        匹配策略：
            采用精确字符串相等匹配（description == item.description），
            避免模糊匹配误删其他伏笔。

        Args:
            description: 伏笔描述文本（精确匹配）。
            chapter_number: 章节号（预留参数，当前未参与匹配逻辑）。

        Returns:
            是否成功移除。
        """
        try:
            items = await self.get_all_items()
            original_len = len(items)

            items = [
                item for item in items
                if item.description != description
            ]

            if len(items) < original_len:
                return await self._save_all_items(items)
            return False
        except Exception as e:
            logger.error(f"移除伏笔失败: {e}")
            return False

    async def add_foreshadowing(
        self,
        fs_type: str,
        description: str,
        trigger_range: tuple = (1, 100),
        urgency: str = "medium"
    ) -> bool:
        """
        添加新伏笔的简化接口。

        参数归一化：
            - fs_type 若不在有效枚举值中，自动降级为 "plot"。
            - urgency 支持多种输入（critical/major/minor 等），自动映射到 high/medium/low。

        Args:
            fs_type: 伏笔类型（plot / character / world / emotional）。
            description: 伏笔内容描述。
            trigger_range: 计划触发章节范围 (start, end)。
            urgency: 紧急程度（low / medium / high）。

        Returns:
            是否添加成功。
        """
        import uuid

        valid_types = {e.value for e in ForeshadowingType}
        if fs_type not in valid_types:
            fs_type = "plot"

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
            type=fs_type,
            description=description,
            planted_chapter=trigger_range[0],
            trigger_range=list(trigger_range),
            urgency=normalized_urgency,
            status=ForeshadowingStatus.PLANTED
        )
        return await self.append("items", item.model_dump())
