"""
knowledge_bases/story_db.py
故事知识库（异步版本）

设计原则：
1. 大纲管理：全局大纲、弧线规划、章节规划
2. 摘要追踪：已完成章节的摘要，用于后续注入
3. 版本控制：保留规划变更历史
4. 异步安全：所有文件操作使用 aiofiles
"""

import json
from pathlib import Path

import aiofiles

from knowledge_bases.base_db import BaseDB, FileLockRegistry
from core.schemas import ChapterPlan, ChapterFinal
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.story_db')


class StoryDB(BaseDB):
    """
    故事知识库
    
    存储大纲、章节规划、章节摘要
    
    使用示例：
        db = StoryDB(project_id="xxx")
        summary = db.get_chapter_summary(5)
    """
    
    def __init__(self, project_id: str):
        super().__init__(project_id, "story")
    
    async def get_chapter_summary(self, chapter_number: int) -> str:
        """
        获取章节摘要（异步）
        
        Args:
            chapter_number: 章节号
        
        Returns:
            章节摘要，不存在则返回空字符串
        """
        # 从 chapter_final 文件中读取
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
        保存章节规划（异步）
        
        Args:
            chapter_number_or_plan: 章节号或 ChapterPlan 对象
            plan_data: 章节规划数据字典（当第一个参数为章节号时使用）
        
        Returns:
            是否保存成功
        """
        if isinstance(chapter_number_or_plan, ChapterPlan):
            key = f"chapter_{chapter_number_or_plan.chapter_number}_plan"
            return await self.save(key, chapter_number_or_plan.model_dump())
        else:
            key = f"chapter_{chapter_number_or_plan}_plan"
            return await self.save(key, plan_data)
    
    async def get_chapter_plan(self, chapter_number: int) -> ChapterPlan | None:
        """
        获取章节规划（异步）
        
        Args:
            chapter_number: 章节号
        
        Returns:
            ChapterPlan 对象，不存在则返回 None
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
        保存章节完稿（异步）
        
        Args:
            final: 章节完稿对象
        
        Returns:
            是否保存成功
        """
        # 保存到 chapters 目录
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
        保存全局大纲（异步）
        
        Args:
            outline_data: 大纲数据字典
        
        Returns:
            是否保存成功
        """
        return await self.save("outline", outline_data)
    
    async def get_outline(self) -> dict | None:
        """
        获取全局大纲（异步）
        
        Returns:
            大纲数据字典，不存在则返回 None
        """
        return await self.load("outline")
    
    async def save_arc_plan(self, arc_id: str, arc_data: dict) -> bool:
        """
        保存弧线规划到 arcs/ 目录（异步）

        Args:
            arc_id: 弧线ID
            arc_data: 弧线规划数据

        Returns:
            是否保存成功
        """
        arcs_dir = Path(self.base_path) / "arcs"
        arcs_dir.mkdir(parents=True, exist_ok=True)
        file_path = arcs_dir / f"arc_{arc_id}.json"
        temp_path = file_path.with_suffix('.tmp')

        try:
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(arc_data, ensure_ascii=False, indent=2))
            import shutil
            shutil.move(str(temp_path), str(file_path))
            return True
        except Exception as e:
            logger.error(f"保存弧线规划失败: {e}")
            if temp_path.exists():
                temp_path.unlink()
            return False

    async def get_arc_plan(self, arc_id: str) -> dict | None:
        """
        获取弧线规划（异步）

        Args:
            arc_id: 弧线ID

        Returns:
            弧线规划数据，不存在则返回 None
        """
        file_path = Path(self.base_path) / "arcs" / f"arc_{arc_id}.json"
        if not file_path.exists():
            # 兼容旧数据：尝试从 story 目录查找
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
        列出所有已保存的弧线规划（异步）

        Returns:
            弧线规划元信息列表
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
        根据章节号查找对应的弧线规划（异步）

        Args:
            chapter_number: 章节编号

        Returns:
            包含该章节的弧线规划数据，未找到则返回 None
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

        # 兼容旧数据：尝试从 story 目录查找
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
        获取章节完稿（异步）
        
        Args:
            chapter_number: 章节号
        
        Returns:
            章节完稿数据，不存在则返回 None
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
        保存章节草稿（异步）
        
        Args:
            chapter_number: 章节号
            draft_data: 草稿数据
        
        Returns:
            是否保存成功
        """
        key = f"chapter_{chapter_number}_draft"
        return await self.save(key, draft_data)
    
    async def get_chapter_draft(self, chapter_number: int) -> dict | None:
        """
        获取章节草稿（异步）

        Args:
            chapter_number: 章节号

        Returns:
            草稿数据，不存在则返回 None
        """
        key = f"chapter_{chapter_number}_draft"
        return await self.load(key)

    async def update_scene_in_draft(
        self,
        chapter_number: int,
        scene_data: dict
    ) -> bool:
        """
        原子性更新草稿中的特定场景（读取->修改->写入 全程加锁）

        使用 FileLockRegistry 全局锁防止并发写入导致的数据丢失。

        Args:
            chapter_number: 章节号
            scene_data: 单场景草稿数据（必须包含 scene_index）

        Returns:
            是否保存成功
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
