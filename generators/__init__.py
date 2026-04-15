"""
generators/__init__.py
初始化生成器包

所有生成器遵循统一模式：
1. 接收已有知识库内容作为输入
2. 调用主力大模型
3. 解析 JSON 输出
4. 验证数据完整性
5. 写入对应知识库
6. 触发向量化

生成顺序约束（必须顺序执行）：
bible_generator → character_generator → outline_generator → arc_planner → chapter_planner
"""

from generators.bible_generator import BibleGenerator
from generators.character_generator import CharacterGenerator
from generators.outline_generator import OutlineGenerator
from generators.arc_planner import ArcPlanner
from generators.chapter_planner import ChapterPlanner

__all__ = [
    "BibleGenerator",
    "CharacterGenerator",
    "OutlineGenerator",
    "ArcPlanner",
    "ChapterPlanner",
]
