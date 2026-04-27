"""
tools/writer/

SceneShowrunner 主管的正文落盘工具组(注意:**正文生成由 Writer 专家完成**,
本组只负责把 Writer 返回的字符串写入 KB)。

包含:
    save_scene_draft  — 写入 chapters/chapter_{n}_draft.json 的某个 scene
    save_scene_final  — 把整章终稿写入 chapters/chapter_{n}_final.json
"""

from .save_scene_draft import SaveSceneDraft
from .save_scene_final import SaveSceneFinal

__all__ = ["SaveSceneDraft", "SaveSceneFinal"]
