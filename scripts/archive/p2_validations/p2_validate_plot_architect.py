"""
scripts/p2_validate_plot_architect.py

P2.1a 垂直切片验证 — PlotArchitect → ArcDesigner + ChapterDesigner → save_outline /
save_arc / save_chapter_plan 端到端打通。

目的:验证 PlotArchitect 三层产出 handshake:
    1. 主管自产 outline → save_outline 落盘
    2. 主管调用 ArcDesigner → 拿到草案 → save_arc 落盘
    3. 主管调用 ChapterDesigner → 拿到 ChapterPlan → save_chapter_plan 落盘
    4. 不再调用工具,任务结束

与 P2.0 的差异:
    - PlotArchitect 没有 OutlineDesigner 专家,outline 来自主管 reasoning(创新点)
    - 三层落盘 vs 两层(WorldArchitect 只有 bible + foreshadowing)
    - ChapterDesigner 输出受 Pydantic ChapterPlan schema 严格约束(已有)

按用户决策:
    - 不依赖前置 KB(prompt 自含信息)
    - 1 outline + 1 arc + 1 chapter,验证最小 handshake

运行:
    cd D:\\AI协作任务\\MANS
    python -X utf8 scripts/p2_validate_plot_architect.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import (  # noqa: E402
    CompletedPayload,
    StreamPacket,
    get_config,
    project_context,
)
from core.logging_config import get_logger  # noqa: E402

import tools  # noqa: E402, F401
import agents  # noqa: E402, F401
from agents.managers import PlotArchitect  # noqa: E402

logger = get_logger("scripts.p2_validate_plot")

USER_PROMPT = """请为一部新作《长歌行》设计 PLAN 阶段的剧情结构。

故事核心:
- 题材:东方武侠 + 复仇 + 江湖
- 主角:沈青衣,曾经的天山弟子,因师门血案沦为江湖弃子,化名行走人间
- 主线:沈青衣循着十年前师门血案的线索追凶,逐步揭开「江湖三大高手」中两人勾结魔教的真相
- 核心冲突:个人复仇 vs 武林大义。沈青衣可以在某一刻选择独自报仇了断,但这会牺牲整个武林
- 风格基调:冷峻、压抑、江湖味道、留白
- 目标:总长约 50 章,分 3 个 arc(童年回溯+初出江湖、复仇布局+真相揭露、最终决战+收束)
- 第一章场景目标:沈青衣在塞外小镇酒馆遇见昔日师弟陆离,得知师弟已成为追杀自己的猎手之一

请你按照工作流程:
1. 先在 reasoning 阶段思考一份 outline,然后调用 save_outline 落盘
   (outline 必须含 logline / main_thread / themes / style_tone / ending_direction / arcs_overview)
2. 调用 call_arc_designer 设计第一个 arc(arc_id="arc_1",约前 15 章),拿到草案后调用 save_arc 落盘
3. 调用 call_chapter_designer 设计第 1 章场景序列(chapter_number=1,基于 arc_1 计划),
   拿到 ChapterPlan 后调用 save_chapter_plan 落盘
4. 完成后即停止调用工具。

注意:本次只设计 1 个 arc + 1 章,后续 arc 与章节由后续任务推进。
"""


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {text}")
    print("=" * 78)


async def run_validation() -> int:
    banner("P2.1a 垂直切片验证 — PlotArchitect 主管")

    # 1. 配置自检
    cfg = get_config()
    errors = cfg.validate()
    if errors:
        print("[FAIL] 配置错误:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[OK] 配置 OK (provider={cfg.ark_provider.name}, "
          f"workspace={cfg.WORKSPACE_PATH})")

    # 2. 实例化 PlotArchitect
    try:
        agent = PlotArchitect()
    except Exception as e:
        print(f"[FAIL] PlotArchitect 实例化失败:{e}")
        return 1
    print(f"[OK] PlotArchitect 实例化 OK (model={agent.runtime.model}, "
          f"temp={agent.runtime.temperature}, max_turns={agent.max_turns})")

    # 3. tool_scope 过滤校验
    schemas = agent.tool_manager.filter_by_scope(agent.tool_scope)
    visible_names = {s["name"] for s in schemas}
    expected = {
        # KB 共享读
        "read_project_meta", "read_bible", "read_foreshadowing",
        "read_character", "read_relationships",
        "read_outline", "read_arc", "read_chapter_plan",
        "vector_search",
        # 自身写组
        "save_outline", "save_arc", "save_chapter_plan",
        # 可调专家
        "call_arc_designer", "call_chapter_designer",
    }
    missing = expected - visible_names
    extra = visible_names - expected
    if missing:
        print(f"[FAIL] tool_scope 过滤后缺失工具: {missing}")
        return 1
    if extra:
        print(f"[FAIL] tool_scope 过滤后混入了不该有的工具: {extra}")
        return 1
    print(f"[OK] tool_scope 过滤正确,主管可见 {len(visible_names)} 个工具")

    # 4. 准备唯一 project_id
    project_id = f"p2-plotarch-{uuid.uuid4().hex[:8]}"
    workspace = Path(cfg.WORKSPACE_PATH) / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"[OK] 项目目录已建: {workspace}")

    # 5. 跑主管
    banner(f"启动 ReAct 循环 - project_id={project_id}")
    print(f"用户输入(片段):{USER_PROMPT.splitlines()[2]} ...")
    print()

    counters = {"reasoning": 0, "output": 0, "completed": 0, "error": 0}
    completed_payloads: list[CompletedPayload] = []
    tool_call_log: list[tuple[int, str, str]] = []  # (turn, name, args_preview)
    output_chunks: list[str] = []
    t0 = time.time()
    turn_idx = 0

    try:
        with project_context(project_id):
            async for packet in agent.run(user_prompt=USER_PROMPT):
                counters[packet.type] = counters.get(packet.type, 0) + 1

                if packet.type == "reasoning" and isinstance(packet.content, str):
                    if counters["reasoning"] % 20 == 0 and len(packet.content) > 0:
                        snippet = packet.content[:80].replace("\n", " ")
                        print(f"  · [reasoning #{counters['reasoning']}] {snippet}")

                elif packet.type == "output" and isinstance(packet.content, str):
                    output_chunks.append(packet.content)

                elif packet.type == "completed" and isinstance(
                    packet.content, CompletedPayload
                ):
                    payload: CompletedPayload = packet.content
                    completed_payloads.append(payload)
                    turn_idx += 1
                    print(
                        f"  ◇ [turn {turn_idx} completed] tokens={payload.total_tokens} "
                        f"tool_calls={len(payload.tool_calls)} res_id={payload.res_id[:14]}…"
                    )
                    for tc in payload.tool_calls:
                        try:
                            args = json.loads(tc.arguments) if tc.arguments else {}
                            preview = json.dumps(args, ensure_ascii=False)[:140]
                        except Exception:
                            preview = tc.arguments[:140]
                        tool_call_log.append((turn_idx, tc.name, preview))
                        print(f"     ⤷ tool_call: {tc.name}({preview})")

                elif packet.type == "error":
                    print(f"  ⚠ [error] {packet.content}")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n[FAIL] 主管运行抛异常({elapsed:.1f}s): {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1

    elapsed = time.time() - t0
    banner("ReAct 循环结束")
    print(f"耗时:{elapsed:.1f}s  总轮数:{turn_idx}  累计 tokens:{agent.last_total_tokens}")
    print(f"包计数:{counters}")
    if output_chunks:
        joined = "".join(output_chunks).strip()
        if joined:
            print(f"主管输出(非工具)预览:{joined[:200]}…")

    # 6. 验证 KB 落盘
    banner("验证 KB 落盘")
    success = True

    # outline 在 workspace/{pid}/story/outline.json
    outline_file = workspace / "story" / "outline.json"
    if outline_file.exists():
        outline_data = json.loads(outline_file.read_text(encoding="utf-8"))
        # 对齐 PlotArchitect prompt 中建议结构
        keys = list(outline_data.keys()) if isinstance(outline_data, dict) else []
        print(f"[OK] story/outline.json 落盘成功,顶层字段:{keys[:8]}")
        if "logline" in outline_data:
            print(f"  · logline: {str(outline_data.get('logline'))[:80]}")
        if "arcs_overview" in outline_data:
            arcs_count = len(outline_data.get("arcs_overview") or [])
            print(f"  · arcs_overview: {arcs_count} 项")
    else:
        print(f"[FAIL] 未找到 {outline_file}")
        success = False

    # arc 在 workspace/{pid}/arcs/arc_*.json (一个或多个)
    arcs_dir = workspace / "arcs"
    if arcs_dir.exists():
        arc_files = sorted(arcs_dir.glob("*.json"))
        if arc_files:
            print(f"[OK] arcs/ 落盘 {len(arc_files)} 个 arc 文件")
            for af in arc_files[:3]:
                arc_data = json.loads(af.read_text(encoding="utf-8"))
                theme = arc_data.get("arc_theme", "(无 theme)")
                ce = len(arc_data.get("key_events") or [])
                print(f"  · {af.name}  theme={theme[:30]}  key_events={ce} 条")
        else:
            print(f"[FAIL] {arcs_dir} 目录存在但为空")
            success = False
    else:
        print(f"[FAIL] 未找到 arcs/ 目录")
        success = False

    # chapter_1_plan 在 workspace/{pid}/story/chapter_1_plan.json
    chapter_plan_file = workspace / "story" / "chapter_1_plan.json"
    if chapter_plan_file.exists():
        plan_data = json.loads(chapter_plan_file.read_text(encoding="utf-8"))
        scenes = plan_data.get("scenes") or []
        print(f"[OK] story/chapter_1_plan.json 落盘成功,{len(scenes)} 个场景")
        for i, s in enumerate(scenes[:4]):
            intent = str(s.get("intent", ""))[:50]
            pov = s.get("pov_character", "")
            print(f"  · scene #{s.get('scene_index', i)} pov={pov} intent={intent}…")
    else:
        print(f"[FAIL] 未找到 {chapter_plan_file}")
        success = False

    # 7. 行为合约校验
    banner("ReAct 行为合约校验")
    expert_calls = [n for _, n, _ in tool_call_log
                    if n in {"call_arc_designer", "call_chapter_designer"}]
    write_calls = [n for _, n, _ in tool_call_log
                   if n in {"save_outline", "save_arc", "save_chapter_plan"}]

    if "call_arc_designer" not in expert_calls:
        print("[FAIL] 主管没有调用 ArcDesigner")
        success = False
    else:
        print(f"[OK] ArcDesigner 被调用 {expert_calls.count('call_arc_designer')} 次")

    if "call_chapter_designer" not in expert_calls:
        print("[FAIL] 主管没有调用 ChapterDesigner")
        success = False
    else:
        print(f"[OK] ChapterDesigner 被调用 {expert_calls.count('call_chapter_designer')} 次")

    for w in ("save_outline", "save_arc", "save_chapter_plan"):
        if w not in write_calls:
            print(f"[FAIL] 主管没有调用 {w}")
            success = False
        else:
            print(f"[OK] {w} 被调用 {write_calls.count(w)} 次")

    # 验证三层产出顺序合理:save_outline 必须早于 save_arc 早于 save_chapter_plan
    seq = [(t, n) for t, n, _ in tool_call_log
           if n in {"save_outline", "save_arc", "save_chapter_plan"}]
    if seq:
        names_only = [n for _, n in seq]
        idx_outline = names_only.index("save_outline") if "save_outline" in names_only else -1
        idx_arc = names_only.index("save_arc") if "save_arc" in names_only else -1
        idx_chapter = (
            names_only.index("save_chapter_plan")
            if "save_chapter_plan" in names_only else -1
        )
        if -1 not in (idx_outline, idx_arc, idx_chapter):
            if idx_outline < idx_arc < idx_chapter:
                print("[OK] 三层产出顺序正确:save_outline → save_arc → save_chapter_plan")
            else:
                print(
                    f"[WARN] 写工具调用顺序意外:outline#{idx_outline} arc#{idx_arc} "
                    f"chapter#{idx_chapter}(不影响落盘成功)"
                )

    # 8. 总结
    banner("验证总结")
    if success:
        print("[PASS] P2.1a 垂直切片验证通过 ✓")
        print("       PlotArchitect 三层规划链路打通(outline 主管自产 + 2 专家 + 3 落盘)")
        print(f"       项目目录(可保留供检查):{workspace}")
        return 0
    else:
        print("[FAIL] P2.1a 垂直切片验证未通过,请检查上方失败项")
        return 1


def main() -> int:
    try:
        return asyncio.run(run_validation())
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
