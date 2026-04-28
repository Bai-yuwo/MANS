"""
core/performance_logger.py

Token 与执行时长审计日志。

职责:
    - 在 ExpertTool / ManagerTool 执行出口处写入 performance_log.jsonl(追加)。
    - 提供查询接口供前端展示单场景/章节/项目级聚合。

数据结构(TokenAuditEntry):
    {
      "timestamp": "2026-04-28T12:34:56",
      "agent_name": "SceneDirector",
      "agent_kind": "expert" | "manager",
      "project_id": "uuid",
      "chapter_number": 1,
      "scene_index": 0,
      "duration_ms": 3456,
      "input_tokens": 1200,
      "output_tokens": 800,
      "total_tokens": 2000,
      "cached_tokens": 500
    }

写入策略:
    - 单条 JSON 一行追加到 workspace/{pid}/performance_log.jsonl。
    - 使用 aiofiles 直接追加写入(单次 append 由 OS 保证原子性,无需 tmp+rename)。
    - 并发安全:不同协程追加同一文件,底层 OS 保证追加原子性。

查询策略:
    - 前端按 project_id + chapter_number + scene_index 过滤聚合。
    - 后台遍历 jsonl 按条件筛选(文件通常 < 1MB,内存过滤足够快)。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles

from core.logging_config import get_logger

logger = get_logger("core.performance_logger")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_log_path(project_id: str, workspace_root: str | Path = "workspace") -> Path:
    p = Path(workspace_root) / project_id / "performance_log.jsonl"
    return p


async def log_token_audit(
    project_id: str,
    *,
    agent_name: str,
    agent_kind: str,  # "expert" | "manager"
    chapter_number: int = 0,
    scene_index: int = 0,
    duration_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cached_tokens: int = 0,
    workspace_root: str | Path = "workspace",
) -> None:
    """写入单条 TokenAuditEntry 到 performance_log.jsonl(追加)。"""
    entry = {
        "timestamp": _now_iso(),
        "agent_name": agent_name,
        "agent_kind": agent_kind,
        "project_id": project_id,
        "chapter_number": chapter_number,
        "scene_index": scene_index,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
    }

    log_path = _get_log_path(project_id, workspace_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"performance_log 写入失败(非阻塞): {e}")


async def query_token_audit(
    project_id: str,
    *,
    chapter_number: Optional[int] = None,
    scene_index: Optional[int] = None,
    agent_name: Optional[str] = None,
    workspace_root: str | Path = "workspace",
) -> list[dict]:
    """查询 performance_log.jsonl,按条件过滤返回条目列表。"""
    log_path = _get_log_path(project_id, workspace_root)
    if not log_path.exists():
        return []

    results = []
    try:
        async with aiofiles.open(log_path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chapter_number is not None and entry.get("chapter_number") != chapter_number:
                    continue
                if scene_index is not None and entry.get("scene_index") != scene_index:
                    continue
                if agent_name is not None and entry.get("agent_name") != agent_name:
                    continue
                results.append(entry)
    except Exception as e:
        logger.warning(f"performance_log 查询失败: {e}")

    return results


async def aggregate_token_audit(
    project_id: str,
    *,
    chapter_number: Optional[int] = None,
    scene_index: Optional[int] = None,
    workspace_root: str | Path = "workspace",
) -> dict:
    """
    聚合查询结果,返回统计摘要。

    返回:
        {
          "entries": [...],  # 原始条目
          "total_duration_ms": 12345,
          "total_input_tokens": 5000,
          "total_output_tokens": 3000,
          "total_tokens": 8000,
          "agent_breakdown": {
            "SceneDirector": {"count": 1, "tokens": 2000, "duration_ms": 3000},
            "Writer": {"count": 2, "tokens": 4000, "duration_ms": 6000},
            ...
          }
        }
    """
    entries = await query_token_audit(
        project_id,
        chapter_number=chapter_number,
        scene_index=scene_index,
        workspace_root=workspace_root,
    )

    total_duration = 0
    total_input = 0
    total_output = 0
    total_tokens = 0
    total_cached = 0
    breakdown: dict[str, dict] = {}

    for entry in entries:
        dur = entry.get("duration_ms", 0)
        inp = entry.get("input_tokens", 0)
        out = entry.get("output_tokens", 0)
        tot = entry.get("total_tokens", 0)
        cached = entry.get("cached_tokens", 0)
        agent = entry.get("agent_name", "unknown")

        total_duration += dur
        total_input += inp
        total_output += out
        total_tokens += tot
        total_cached += cached

        if agent not in breakdown:
            breakdown[agent] = {"count": 0, "tokens": 0, "duration_ms": 0, "cached_tokens": 0}
        breakdown[agent]["count"] += 1
        breakdown[agent]["tokens"] += tot
        breakdown[agent]["duration_ms"] += dur
        breakdown[agent]["cached_tokens"] = breakdown[agent].get("cached_tokens", 0) + cached

    cache_hit_ratio = round(total_cached / max(total_input, 1), 2)

    return {
        "entries": entries,
        "total_duration_ms": total_duration,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_tokens,
        "total_cached_tokens": total_cached,
        "cache_hit_ratio": cache_hit_ratio,
        "agent_breakdown": breakdown,
    }
