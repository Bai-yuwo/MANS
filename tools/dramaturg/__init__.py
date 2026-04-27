"""
tools/dramaturg/

SceneShowrunner 主管的"剧作转译"产物落盘工具组。

包含:
    save_scene_beatsheet — 把 SceneDirector 专家产出的 SceneBeatsheet 写入
                           workspace/{pid}/chapters/scene_beatsheets/scene_{i}.json

为什么单独成组而不并入 writer/:
    SceneBeatsheet 是 SceneShowrunner 的中间产物(剧作层),与正文(writer/ 写组)
    在权限语义上属于不同物件;分组后未来若想给 SceneDirector 自己写权限也方便切。
"""

from .save_scene_beatsheet import SaveSceneBeatsheet

__all__ = ["SaveSceneBeatsheet"]
