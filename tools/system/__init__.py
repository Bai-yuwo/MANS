"""
tools/system/

通用系统工具组(跨主管使用)。

包含:
    apply_kb_diff          — SceneShowrunner 接收 Scribe 产物后的 KB 增量应用
    log_run_record         — 主管/专家执行 trace 落盘到 workspace/{pid}/runs/
    confirm_stage_advance  — Director 阶段切换确认(向前端推送 confirm packet)
    write_project_meta     — Director 更新项目元信息(stage / status / current_chapter)
"""

from .apply_kb_diff import ApplyKBDiff
from .confirm_stage_advance import ConfirmStageAdvance
from .log_run_record import LogRunRecord
from .write_project_meta import WriteProjectMeta

__all__ = ["ApplyKBDiff", "ConfirmStageAdvance", "LogRunRecord", "WriteProjectMeta"]
