"""
core/project_config.py

项目配置读取辅助。

职责:
    为工具层提供异步读取 project_meta.json 配置的统一入口。
    所有工具(ConfirmStageAdvance / AskUser 等)通过本模块读取配置，
    避免重复实现文件 IO 和错误处理。

设计:
    - 失败时返回全默认值，保证工具层不因配置读取失败而崩溃。
    - 使用 aiofiles 异步读取，不阻塞事件循环。
    - 不缓存：project_meta.json 可能在运行时通过 API 修改，每次读取取最新值。
"""

import json
from pathlib import Path
from typing import Any

import aiofiles

from core.logging_config import get_logger

logger = get_logger("core.project_config")

# 配置默认值（与 schemas.ProjectMeta 一致）
_DEFAULT_CONFIG: dict[str, Any] = {
    "auto_advance": False,
    "auto_rewrite": False,
    "max_rewrite_attempts": 2,
    "enable_consistency_check": True,
    "token_budget_per_scene": 0,
    "max_scenes_per_batch": 1,
    "auto_continue_batch": False,
    "scenes_generated_in_batch": 0,
}


async def get_project_config(project_id: str, workspace_root: str | Path = "workspace") -> dict[str, Any]:
    """
    异步读取 project_meta.json，返回配置字段字典。

    失败时（文件不存在 / JSON 解析错误 / IO 异常）返回全默认值，
    保证调用方无需处理异常。

    Args:
        project_id: 项目 ID。
        workspace_root: workspace 根目录路径。

    Returns:
        配置字典，键与 schemas.ProjectMeta 的运行时配置字段一致。
    """
    meta_path = Path(workspace_root) / project_id / "project_meta.json"
    if not meta_path.exists():
        return dict(_DEFAULT_CONFIG)

    try:
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            text = await f.read()
        meta = json.loads(text)
    except Exception as e:
        logger.warning(f"读取 project_meta.json 失败 {project_id}: {e}，使用默认值")
        return dict(_DEFAULT_CONFIG)

    result = dict(_DEFAULT_CONFIG)
    for key in _DEFAULT_CONFIG:
        if key in meta:
            result[key] = meta[key]
    return result
