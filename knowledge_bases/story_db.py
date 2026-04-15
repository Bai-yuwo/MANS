"""
knowledge_bases/story_db.py
故事知识库

设计原则：
1. 大纲管理：全局大纲、弧线规划、章节规划
2. 摘要追踪：已完成章节的摘要，用于后续注入
3. 版本控制：保留规划变更历史
"""

from pathlib import Path

from knowledge_bases.base_db import BaseDB
from core.schemas import ChapterPlan, ChapterFinal


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
    
    def get_chapter_summary(self, chapter_number: int) -> str:
        """
        获取章节摘要
        
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
                import json
                with open(final_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("summary", "")
            except Exception as e:
                print(f"读取章节摘要失败: {e}")
        
        return ""
    
    def save_chapter_plan(self, plan: ChapterPlan) -> bool:
        """
        保存章节规划
        
        Args:
            plan: 章节规划对象
        
        Returns:
            是否保存成功
        """
        key = f"chapter_{plan.chapter_number}_plan"
        return self.save(key, plan.model_dump())
    
    def get_chapter_plan(self, chapter_number: int) -> ChapterPlan | None:
        """
        获取章节规划
        
        Args:
            chapter_number: 章节号
        
        Returns:
            ChapterPlan 对象，不存在则返回 None
        """
        key = f"chapter_{chapter_number}_plan"
        data = self.load(key)
        
        if not data:
            return None
        
        try:
            return ChapterPlan(**data)
        except Exception as e:
            print(f"解析章节规划失败: {e}")
            return None
    
    def save_chapter_final(self, final: ChapterFinal) -> bool:
        """
        保存章节完稿
        
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
            import json
            with open(final_path, 'w', encoding='utf-8') as f:
                json.dump(final.model_dump(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存章节完稿失败: {e}")
            return False
