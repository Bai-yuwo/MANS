"""
tools/system/

通用系统工具组(跨主管使用)。

包含:
    apply_kb_diff          — SceneShowrunner 接收 Scribe 产物后的 KB 增量应用
    log_run_record         — 主管/专家执行 trace 落盘到 workspace/{pid}/runs/
    confirm_stage_advance  — Director 阶段切换确认(向前端推送 confirm packet)
    ask_user               — Director 遇到模糊信息时向用户发起通用询问
    write_project_meta     — Director 更新项目元信息(stage / status / current_chapter)
"""

from .apply_kb_diff import ApplyKBDiff
from .checkpoint_tools import ClearCheckpoint, ReadCheckpoint
from .confirm_stage_advance import ConfirmStageAdvance
from .ask_user import AskUser
from .log_run_record import LogRunRecord
from .write_project_meta import WriteProjectMeta

__all__ = ["ApplyKBDiff", "ClearCheckpoint", "ConfirmStageAdvance", "AskUser", "LogRunRecord", "ReadCheckpoint", "WriteProjectMeta"]
