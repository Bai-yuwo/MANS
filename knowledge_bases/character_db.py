"""
knowledge_bases/character_db.py

人物知识库，负责人物卡的持久化存储和状态更新。

职责边界：
    - 存储每个人物的完整 CharacterCard（固有属性 + 动态状态）。
    - 管理人物状态历史，记录每次状态变更的时间、章节和具体内容。
    - 维护人物关系网，支持关系的创建、更新和历史追踪。
    - 提供 UpdateExtractor 所需的人物状态应用接口（apply_update）。

存储结构：
    workspace/{project_id}/characters/
    ├── {name}.json          # 单个人物卡
    └── relationships.json   # 关系网络（预留扩展）

状态更新流程：
    1. UpdateExtractor 从生成文本中提取 CharacterStateUpdate。
    2. 调用 apply_update() 将变更应用到对应人物卡。
    3. apply_update() 自动记录状态历史快照（update_state）。
    4. 更新后的人物卡通过 save_character() 持久化到磁盘。

典型用法：
    db = CharacterDB(project_id="xxx")
    char = await db.get_character("张三")
    await db.apply_update(update, chapter=5, scene_index=2)
"""

import re
from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.schemas import CharacterCard, CharacterStateUpdate
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.character_db')


class CharacterDB(BaseDB):
    """
    人物知识库。

    人物是故事的核心。CharacterDB 不仅存储人物的静态设定（外貌、性格、背景），
    还追踪人物的动态状态（位置、修为、情绪、目标）随故事发展的变化轨迹。
    通过 state_history 字段，可以回溯任意时刻人物的状态，支持场景回滚。

    存储约定：
        每个人物以姓名为文件名（{name}.json）存储在 characters/ 子目录下。
        文件名即人物的唯一标识（Display Name），确保与 ScenePlan.present_characters 列表一致。

    延迟初始化：
        继承 BaseDB 的延迟初始化机制，首次访问时自动创建目录结构。
    """

    def __init__(self, project_id: str):
        super().__init__(project_id, "characters")

    async def get_character(self, name: str) -> Optional[CharacterCard]:
        """
        根据姓名获取人物卡，支持精确匹配、别名匹配和规范化匹配。

        查找逻辑（按优先级）：
            1. 精确匹配：以 name 为 key 加载对应的 JSON 文件。
            2. 别名匹配：遍历所有人物，检查 name 是否等于某人物的 name
               或出现在该人物的 aliases 列表中。
            3. 规范化匹配：去除 name 中的括号注释（如 "刘禅（现代）" → "刘禅"），
               再次尝试精确匹配和别名匹配。
            4. 若全部失败，返回 None 并记录警告日志。

        Args:
            name: 人物姓名（可能与文件名不完全一致，如带括号注释）。

        Returns:
            CharacterCard 实例，不存在或解析失败时返回 None。
        """
        # 1. 精确匹配
        data = await self.load(name)
        if data:
            try:
                return CharacterCard(**data)
            except Exception as e:
                logger.error(f"解析人物卡失败 {name}: {e}")
                return None

        # 2. 别名匹配
        all_names = await self.list_keys()
        for key in all_names:
            char_data = await self.load(key)
            if not char_data:
                continue
            if char_data.get("name") == name or name in char_data.get("aliases", []):
                try:
                    return CharacterCard(**char_data)
                except Exception as e:
                    logger.error(f"解析人物卡失败 {key}: {e}")
                    continue

        # 3. 规范化匹配（去除括号及其中内容，如 "刘禅（现代）" → "刘禅"）
        normalized = re.sub(r"[（(].*?[）)]", "", name).strip()
        if normalized and normalized != name:
            data = await self.load(normalized)
            if data:
                try:
                    return CharacterCard(**data)
                except Exception as e:
                    logger.error(f"解析人物卡失败 {normalized}: {e}")
                    return None

            for key in all_names:
                char_data = await self.load(key)
                if not char_data:
                    continue
                if char_data.get("name") == normalized or normalized in char_data.get("aliases", []):
                    try:
                        return CharacterCard(**char_data)
                    except Exception as e:
                        logger.error(f"解析人物卡失败 {key}: {e}")
                        continue

        return None

    async def save_character(self, character: CharacterCard) -> bool:
        """
        保存人物卡到磁盘。

        使用 BaseDB.save() 的原子写入机制（临时文件 + os.replace），
        确保即使写入过程中发生崩溃，也不会损坏已有数据。

        Args:
            character: 要保存的人物卡对象。

        Returns:
            是否保存成功。
        """
        return await self.save(character.name, character.model_dump())

    async def apply_update(
        self,
        update: CharacterStateUpdate,
        chapter: int = 0,
        scene_index: int = -1
    ) -> bool:
        """
        将 UpdateExtractor 提取的状态更新应用到人物卡。

        更新字段映射：
            - location_change → current_location
            - cultivation_change → cultivation.realm
            - emotion_change → current_emotion
            - goal_updates → active_goals（追加模式，不覆盖已有目标）
            - relationship_updates → relationships（更新现有关系或新建关系）

        每次成功更新后，会自动调用 update_state() 记录历史快照，
        快照包含章节号、场景序号、时间戳和变更内容字典。

        Args:
            update: UpdateExtractor 生成的状态更新对象。
            chapter: 当前章节号，用于历史记录。
            scene_index: 当前场景序号，用于历史记录。

        Returns:
            是否更新成功。若人物不存在则返回 False 并记录错误。
        """
        char = await self.get_character(update.character_name)
        if not char:
            logger.error(f"人物不存在: {update.character_name}")
            return False

        updates = {}

        if update.location_change:
            char.current_location = update.location_change
            updates['location'] = update.location_change

        if update.cultivation_change:
            from core.schemas import CultivationLevel
            if char.cultivation is None:
                char.cultivation = CultivationLevel(
                    realm=update.cultivation_change,
                    stage="",
                    combat_power_estimate="未知"
                )
            else:
                char.cultivation.realm = update.cultivation_change
            updates['cultivation'] = update.cultivation_change

        if update.emotion_change:
            char.current_emotion = update.emotion_change
            updates['emotion'] = update.emotion_change

        if update.goal_updates:
            for goal in update.goal_updates:
                if goal not in char.active_goals:
                    char.active_goals.append(goal)
            updates['goals'] = update.goal_updates

        if update.relationship_updates:
            from core.schemas import Relationship
            for rel_update in update.relationship_updates:
                if not isinstance(rel_update, dict):
                    continue
                target_name = rel_update.get("target", "")
                change_desc = rel_update.get("change", "")
                if not target_name or not change_desc:
                    continue

                existing_rel = None
                for rel in char.relationships:
                    if rel.target_name == target_name:
                        existing_rel = rel
                        break

                if existing_rel:
                    existing_rel.current_sentiment = change_desc
                    existing_rel.add_history_note(
                        f"第{chapter}章: {change_desc}"
                    )
                else:
                    new_rel = Relationship(
                        target_character_id="",
                        target_name=target_name,
                        relation_type="关联",
                        current_sentiment=change_desc
                    )
                    new_rel.add_history_note(
                        f"第{chapter}章: {change_desc}"
                    )
                    char.relationships.append(new_rel)

        if updates:
            char.update_state(
                chapter=chapter,
                updates=updates,
                scene_index=scene_index
            )

        return await self.save_character(char)

    async def list_characters(self) -> list[str]:
        """
        列出所有已保存的人物姓名。

        Returns:
            人物姓名列表（按字母顺序排序）。
        """
        return await self.list_keys()

    async def list_all_characters(self) -> list[dict]:
        """
        获取所有人物的原始数据字典。

        用途：
            主要用于 API 响应，前端展示人物列表时直接返回原始字典，
            避免 Pydantic 序列化的额外开销。

        Returns:
            人物数据字典列表，解析失败的条目会被跳过。
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
        根据 ID 查找人物。

        注意：
            CharacterDB 以姓名为文件名，此方法需要遍历所有人物文件进行匹配。
            性能上适用于人物数量不多的场景（通常 < 100 人）。

        Args:
            char_id: 人物卡中的 id 字段值。

        Returns:
            匹配的人物数据字典，未找到返回 None。
        """
        all_chars = await self.list_all_characters()
        for char in all_chars:
            if char.get("id") == char_id:
                return char
        return None

    async def add_relationship(self, character_id: str, relationship) -> bool:
        """
        为指定人物添加关系条目。

        实现逻辑：
            遍历所有人物文件，找到 id 匹配的人物后，
            在其 relationships 列表中追加新关系。

        Args:
            character_id: 人物的 id 字段值。
            relationship: Relationship 对象或兼容字典。

        Returns:
            是否添加成功。若找不到对应人物返回 False。
        """
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

    # ── 向量同步 ──

    async def _after_save(self, key: str, data: dict) -> None:
        """保存后自动同步人物卡到向量库。"""
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)

            name = data.get("name", key)
            char_id = data.get("id", key)

            parts = [f"角色: {name}"]
            if data.get("aliases"):
                parts.append(f"别名: {', '.join(data['aliases'])}")
            if data.get("appearance"):
                parts.append(f"外貌: {data['appearance']}")
            if data.get("personality_core"):
                parts.append(f"性格: {data['personality_core']}")
            if data.get("background"):
                parts.append(f"背景: {data['background']}")
            if data.get("voice_keywords"):
                parts.append(f"声线: {', '.join(data['voice_keywords'])}")
            if data.get("cultivation"):
                cul = data["cultivation"]
                parts.append(f"修为: {cul.get('realm', '')} {cul.get('stage', '')}")

            text = "\n".join(parts)
            await vs.upsert("character_cards", char_id, text, {
                "name": name,
                "is_protagonist": data.get("is_protagonist", False),
                "current_location": data.get("current_location", ""),
                "current_emotion": data.get("current_emotion", ""),
                "_content_hash": self._compute_hash(data),
            })
            logger.info(f"人物卡向量同步: {name}")
        except Exception as e:
            log_exception(logger, e, f"人物卡向量同步失败 {key}")

    async def _after_delete(self, key: str) -> None:
        """删除人物卡后从向量库清理。"""
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)
            # key 即角色名；若 JSON 中 id 与 name 不同，此处可能残留，但概率极低
            await vs.delete("character_cards", key)
            logger.info(f"人物卡向量删除: {key}")
        except Exception as e:
            logger.warning(f"人物卡向量删除失败 {key}: {e}")
