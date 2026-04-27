"""
tools/review/

SceneShowrunner 主管内部的审查产物落盘工具组。

包含:
    save_review_issues       — 落 Critic + ContinuityChecker 合并后的 issues 清单
    save_rewrite_guidance    — 落 ReviewManager 产出的重写指南(供 Writer 重写时读取)

设计意图:
    issues 与 guidance 不进入正式 KB,只是 SceneShowrunner 在一次场景循环内的工作暂存,
    便于事后审计(回看为什么这段被改/没改)。落盘到 workspace/{pid}/review/ 子目录。
"""

from .save_review_issues import SaveReviewIssues
from .save_rewrite_guidance import SaveRewriteGuidance

__all__ = ["SaveReviewIssues", "SaveRewriteGuidance"]
