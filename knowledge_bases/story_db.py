"""
knowledge_bases/story_db.py

故事知识库，管理小说的宏观结构数据。

职责边界：
    - 全局大纲（outline）：故事的整体架构，包括主线、支线、世界观框架。
    - 故事弧规划（arc）：将长篇故事划分为多个弧线，每个弧线包含连续的若干章节。
    - 章节规划（chapter plan）：单章的场景序列、目标、情绪走向。
    - 章节摘要（chapter summary）：已完稿章节的精炼摘要，用于后续章节的前情提要注入。
    - 章节草稿（chapter draft）：正在写作中的章节内容，包含各场景的文本。

存储结构：
    workspace/{project_id}/
    ├── outline.json              # 全局大纲
    ├── story/
    │   ├── chapter_{n}_plan.json   # 章节规划
    │   └── chapter_{n}_draft.json  # 章节草稿
    └── arcs/
        └── arc_{id}.json           # 弧线规划

原子性保证：
    草稿的场景更新使用 FileLockRegistry 全局锁，防止并发写入导致数据丢失。
    弧线规划保存使用临时文件 + os.replace 的原子写入模式。

典型用法：
    db = StoryDB(project_id="xxx")
    plan = await db.get_chapter_plan(5)
    await db.save_chapter_draft(5, draft_data)
"""

import json
import os
from pathlib import Path

import aiofiles

from knowledge_bases.base_db import BaseDB, FileLockRegistry
from core.schemas import ChapterPlan, ChapterFinal
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.story_db')


class StoryDB(BaseDB):
    """
    故事知识库。

    小说创作是一个层层递进的过程：
        1. 首先生成全局大纲（outline）。
        2. 根据大纲划分故事弧（arc），每个弧线聚焦一个子目标。
        3. 为每个章节制定规划（chapter plan），确定场景序列。
        4. 逐场景生成草稿（chapter draft）。
        5. 章节完稿后保存最终版本（chapter final）和摘要（summary）。

    StoryDB 负责管理上述所有层级数据的持久化，是连接 Generator 和 Writer 的桥梁。

    兼容处理：
        部分旧数据可能存储在 story/ 子目录中，get_arc_plan 等方法会自动回退查找。
    """

    def __init__(self, project_id: str):
        super().__init__(project_id, "story")

    async def get_chapter_summary(self, chapter_number: int) -> str:
        """
        获取指定章节的摘要文本。

        摘要来源：
            从 chapter_{n}_final.json 中读取 summary 字段。
            完稿时由 Generator 或用户手动生成，约 200 字，概括本章核心事件。

        Args:
            chapter_number: 章节号。

        Returns:
            章节摘要字符串。若章节尚未完稿或读取失败，返回空字符串。
        """
        final_path = (
            Path(self.base_path) / "chapters" /
            f"chapter_{chapter_number}_final.json"
        )

        if final_path.exists():
            try:
                async with aiofiles.open(final_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    return data.get("summary", "")
            except Exception as e:
                logger.error(f"读取章节摘要失败: {e}")

        return ""

    async def save_chapter_plan(self, chapter_number_or_plan, plan_data=None) -> bool:
        """
        保存章节规划。

        重载支持：
            - 传入 ChapterPlan 对象：自动提取 chapter_number 并序列化。
            - 传入 (chapter_number, plan_data) 元组：直接保存原始字典。

        Args:
            chapter_number_or_plan: 章节号（int）或 ChapterPlan 对象。
            plan_data: 当第一个参数为章节号时的规划数据字典。

        Returns:
            是否保存成功。
        """
        if isinstance(chapter_number_or_plan, ChapterPlan):
            key = f"chapter_{chapter_number_or_plan.chapter_number}_plan"
            return await self.save(key, chapter_number_or_plan.model_dump())
        else:
            key = f"chapter_{chapter_number_or_plan}_plan"
            return await self.save(key, plan_data)

    async def get_chapter_plan(self, chapter_number: int) -> ChapterPlan | None:
        """
        获取指定章节的规划。

        Args:
            chapter_number: 章节号。

        Returns:
            ChapterPlan 实例。若规划不存在或解析失败，返回 None。
        """
        key = f"chapter_{chapter_number}_plan"
        data = await self.load(key)

        if not data:
            return None

        try:
            return ChapterPlan(**data)
        except Exception as e:
            logger.error(f"解析章节规划失败: {e}")
            return None

    async def save_chapter_final(self, final: ChapterFinal) -> bool:
        """
        保存章节完稿数据。

        完稿数据包含：
            - full_text：完整的章节正文。
            - word_count：字数统计。
            - scene_texts：各场景文本列表。
            - summary：章节摘要（用于后续章节的前情提要）。

        存储位置为 chapters/ 子目录，与草稿（story/ 子目录）分离，
        避免写作过程中的临时文件污染完稿数据。

        Args:
            final: 章节完稿对象。

        Returns:
            是否保存成功。
        """
        chapters_dir = Path(self.base_path) / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)

        final_path = chapters_dir / f"chapter_{final.chapter_number}_final.json"

        try:
            async with aiofiles.open(final_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(final.model_dump(), ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            logger.error(f"保存章节完稿失败: {e}")
            return False

    async def save_outline(self, outline_data: dict) -> bool:
        """
        保存全局大纲。

        大纲是故事的最高层级规划，定义了主线走向、关键节点和结局方向。
        所有后续的弧线规划和章节规划都必须服从大纲的约束。

        Args:
            outline_data: 大纲数据字典，结构由 OutlineGenerator 定义。

        Returns:
            是否保存成功。
        """
        return await self.save("outline", outline_data)

    async def get_outline(self) -> dict | None:
        """
        获取全局大纲。

        Returns:
            大纲数据字典，若未生成则返回 None。
        """
        return await self.load("outline")

    async def save_arc_plan(self, arc_id: str, arc_data: dict) -> bool:
        """
        保存弧线规划到 arcs/ 目录。

        弧线（Arc）是将长篇小说划分为若干逻辑单元的故事结构。
        每个弧线包含：
            - arc_id / arc_number：弧线的唯一标识和序号。
            - arc_theme：弧线主题。
            - chapter_range：弧线覆盖的章节范围 [start, end]。
            - arc_goal：弧线对主线的推进目标。
            - is_placeholder：是否为占位弧线（尚未详细规划）。

        原子写入：
            使用临时文件 + os.replace() 确保写入的原子性，
            即使进程崩溃也不会留下半写文件。

        Args:
            arc_id: 弧线唯一标识。
            arc_data: 弧线规划数据字典。

        Returns:
            是否保存成功。
        """
        arcs_dir = Path(self.base_path) / "arcs"
        arcs_dir.mkdir(parents=True, exist_ok=True)
        file_path = arcs_dir / f"arc_{arc_id}.json"
        temp_path = file_path.with_suffix('.tmp')

        try:
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(arc_data, ensure_ascii=False, indent=2))
            os.replace(str(temp_path), str(file_path))
            return True
        except Exception as e:
            logger.error(f"保存弧线规划失败: {e}")
            if temp_path.exists():
                temp_path.unlink()
            return False

    async def get_arc_plan(self, arc_id: str) -> dict | None:
        """
        获取指定弧线的规划。

        兼容处理：
            优先从 arcs/ 目录读取。
            若不存在，回退到旧数据路径 story/arc_arc_{arc_id}.json。

        Args:
            arc_id: 弧线唯一标识。

        Returns:
            弧线规划数据字典，不存在或读取失败时返回 None。
        """
        file_path = Path(self.base_path) / "arcs" / f"arc_{arc_id}.json"
        if not file_path.exists():
            story_path = Path(self.base_path) / "story" / f"arc_arc_{arc_id}.json"
            if story_path.exists():
                file_path = story_path
            else:
                return None

        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"读取弧线规划失败: {e}")
            return None

    async def list_arc_plans(self) -> list[dict]:
        """
        列出所有已保存的弧线规划。

        Returns:
            弧线元信息列表，每项包含 arc_id、arc_number、title、chapter_range、
            description、is_placeholder 等字段。
            读取失败的文件会被静默跳过。
        """
        arcs = []
        arcs_dir = Path(self.base_path) / "arcs"
        if arcs_dir.exists():
            for arc_file in sorted(arcs_dir.glob("arc_*.json")):
                try:
                    async with aiofiles.open(arc_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                    arcs.append({
                        "arc_id": data.get("arc_id", arc_file.stem),
                        "arc_number": data.get("arc_number", 0),
                        "title": data.get("arc_theme", "未命名弧线"),
                        "chapter_range": data.get("chapter_range", [0, 0]),
                        "description": data.get("arc_goal", ""),
                        "is_placeholder": data.get("is_placeholder", False)
                    })
                except Exception:
                    continue
        return arcs

    async def get_arc_plan_for_chapter(self, chapter_number: int) -> dict | None:
        """
        根据章节号查找其所属的弧线规划。

        查找逻辑：
            遍历所有弧线规划，检查 chapter_number 是否落在该弧线的 chapter_range 范围内。
            优先从新路径 arcs/ 查找，回退到旧路径 story/。

        Args:
            chapter_number: 章节编号。

        Returns:
            包含该章节的弧线规划数据，未找到返回 None。
        """
        arcs_dir = Path(self.base_path) / "arcs"
        if arcs_dir.exists():
            for arc_file in arcs_dir.glob("arc_*.json"):
                try:
                    async with aiofiles.open(arc_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        arc_data = json.loads(content)
                    chapter_range = arc_data.get("chapter_range", [0, 0])
                    if chapter_range[0] <= chapter_number <= chapter_range[1]:
                        return arc_data
                except Exception:
                    continue

        story_dir = Path(self.base_path) / "story"
        if story_dir.exists():
            for f in story_dir.iterdir():
                if f.name.startswith("arc_") and f.suffix == ".json":
                    try:
                        async with aiofiles.open(f, 'r', encoding='utf-8') as fp:
                            content = await fp.read()
                            arc_data = json.loads(content)
                        chapter_range = arc_data.get("chapter_range", [0, 0])
                        if chapter_range[0] <= chapter_number <= chapter_range[1]:
                            return arc_data
                    except Exception:
                        continue

        return None

    async def get_chapter_final(self, chapter_number: int) -> dict | None:
        """
        获取章节完稿数据。

        Args:
            chapter_number: 章节号。

        Returns:
            完稿数据字典，不存在或读取失败时返回 None。
        """
        final_path = (
            Path(self.base_path) / "chapters" /
            f"chapter_{chapter_number}_final.json"
        )

        if final_path.exists():
            try:
                async with aiofiles.open(final_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    return json.loads(content)
            except Exception as e:
                logger.error(f"读取章节完稿失败: {e}")

        return None

    async def save_chapter_draft(self, chapter_number: int, draft_data: dict) -> bool:
        """
        保存章节草稿。

        草稿是写作过程中的临时数据，包含各场景的生成文本。
        完稿后应调用 save_chapter_final() 保存最终版本。

        Args:
            chapter_number: 章节号。
            draft_data: 草稿数据字典，通常包含 scenes 数组。

        Returns:
            是否保存成功。
        """
        key = f"chapter_{chapter_number}_draft"
        return await self.save(key, draft_data)

    async def get_chapter_draft(self, chapter_number: int) -> dict | None:
        """
        获取章节草稿。

        Args:
            chapter_number: 章节号。

        Returns:
            草稿数据字典，不存在时返回 None。
        """
        key = f"chapter_{chapter_number}_draft"
        return await self.load(key)

    async def update_scene_in_draft(
        self,
        chapter_number: int,
        scene_data: dict
    ) -> bool:
        """
        原子性更新草稿中的特定场景。

        并发安全：
            使用 FileLockRegistry 获取文件级全局锁，
            包裹"读取 → 修改 → 写入"的完整流程，
            防止多请求并发修改同一草稿导致数据丢失。

        更新逻辑：
            根据 scene_data["scene_index"] 查找草稿中已存在的场景：
            - 找到则替换该场景数据。
            - 未找到则追加到 scenes 列表末尾。

        Args:
            chapter_number: 章节号。
            scene_data: 单场景数据字典，必须包含 scene_index 字段。

        Returns:
            是否保存成功。
        """
        key = f"chapter_{chapter_number}_draft"
        file_path = self._get_file_path(key)
        file_lock = await FileLockRegistry.acquire(str(file_path))

        async with file_lock:
            chapter_draft = await self._load_no_lock(key) or {}
            scenes = chapter_draft.get("scenes", [])

            scene_index = scene_data.get("scene_index")
            updated = False
            for i, scene in enumerate(scenes):
                if scene.get("scene_index") == scene_index:
                    scenes[i] = scene_data
                    updated = True
                    break
            if not updated:
                scenes.append(scene_data)

            chapter_draft["scenes"] = scenes
            if "chapter_number" not in chapter_draft:
                chapter_draft["chapter_number"] = chapter_number

            return await self._save_no_lock(key, chapter_draft)

    # ── 向量同步 ──

    async def _after_save(self, key: str, data: dict) -> None:
        """保存后自动同步故事数据到向量库。"""
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)

            if key == "outline":
                text = self._outline_to_text(data)
                if text:
                    await vs.upsert("outlines", "outline", text, {})
                    logger.info("大纲向量同步完成")

            elif key.startswith("chapter_") and key.endswith("_plan"):
                chapter_num = data.get("chapter_number", 0)
                text = self._chapter_plan_to_text(data)
                if text:
                    await vs.upsert(
                        "chapter_scenes",
                        f"plan_{chapter_num}",
                        text,
                        {"chapter_number": chapter_num, "type": "plan"}
                    )
                    logger.info(f"章节规划向量同步: 第{chapter_num}章")

        except Exception as e:
            log_exception(logger, e, f"故事数据向量同步失败 {key}")

    @staticmethod
    def _outline_to_text(data: dict) -> str:
        parts = ["故事大纲"]
        if data.get("title"):
            parts.append(f"标题: {data['title']}")
        if data.get("summary"):
            parts.append(f"概要: {data['summary']}")
        if data.get("acts"):
            for act in data["acts"]:
                parts.append(f"幕: {act.get('name', '')} - {act.get('summary', '')}")
        return "\n".join(parts)

    @staticmethod
    def _chapter_plan_to_text(data: dict) -> str:
        parts = [f"第{data.get('chapter_number', 0)}章规划"]
        if data.get("title"):
            parts.append(f"标题: {data['title']}")
        if data.get("chapter_goal"):
            parts.append(f"目标: {data['chapter_goal']}")
        if data.get("emotional_arc"):
            parts.append(f"情绪: {data['emotional_arc']}")
        for scene in data.get("scenes", []):
            parts.append(f"场景: {scene.get('intent', '')}")
        return "\n".join(parts)
