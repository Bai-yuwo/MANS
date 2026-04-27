"""
tools/style/

风格示例库共享只读工具组(写权限暂未开放,style 示例由人工/工程脚本预填)。

包含:
    search_style_examples — 按情绪基调或题材关键词检索风格示例段落

被多个 Agent 共用:Writer 引用作为示例段、SceneDirector/ChapterDesigner
也会在规划时引用风格倾向。
"""

from .search_style_examples import SearchStyleExamples

__all__ = ["SearchStyleExamples"]
