"""
scripts/p2_validate_scene_showrunner.py

P2.1c 垂直切片验证 — SceneShowrunner 主管单场景流水线端到端。

目标:验证 WRITE 阶段的「6 专家 + 5 写工具 + ≤1 轮重写」编排合约:
    1. SceneDirector → SceneBeatsheet → save_scene_beatsheet
    2. Writer(★ 流式)→ 草稿正文 → save_scene_draft(rewrite_attempt=0)
    3. **同一轮**:Critic + ContinuityChecker → 两组 issues
    4. save_review_issues(critic + continuity 合并)
    5. ReviewManager → RewriteGuidance → save_rewrite_guidance(attempt=0)
    6. 若 needs_rewrite=true 且 rewrite_attempt < 1:
       Writer 重写 → save_scene_draft(attempt=1) → 回到 3
    7. Scribe → KB diff → apply_kb_diff(characters / foreshadowing / bible 落盘)

与前序 P2 验证的差异:
    - 这是**最复杂**的主管流水线(6 个专家 + 5 个写工具 + 重写循环)
    - **唯一流式专家** Writer 的 token 转发由 BaseAgent 自动完成
    - 需要前置 KB(seed):chapter_plan + characters + 最小 bible
    - 重写循环由主管自身判定(needs_rewrite + rewrite_attempt < 1)

用户决策(确认):
    - 1 scene + 1 轮 rewrite 上限(快验通路)
    - 复用现有 P2 产出:p2-plotarch-717d6dff(outline/arc/chapter_1_plan)
                       + p2-casting-b1674e67(沈青衣/陆离/白衣老人 + relationships)
    - 由于 P2 没有产出武侠 bible,本脚本手工注入 5 条最小 WorldRule

运行:
    cd D:\\AI协作任务\\MANS
    python -X utf8 scripts/p2_validate_scene_showrunner.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
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
from agents.managers import SceneShowrunner  # noqa: E402

logger = get_logger("scripts.p2_validate_scene_showrunner")

# === 种子工程位置 ===
SEED_PLOT = "p2-plotarch-717d6dff"   # 提供 outline / arcs / chapters/{n}_plan
SEED_CAST = "p2-casting-b1674e67"    # 提供 characters/* + relationships

# === 手工注入的最小武侠 bible(P2 plot/cast 阶段未产出武侠 bible)===
MINIMAL_BIBLE_RULES = [
    {
        "category": "cultivation",
        "content": (
            "本世界武学体系按从低到高分为:外功(炼体)→ 内功(炼气)→ 真气(化罡)→ "
            "宗师(意境)→ 大宗师(天人)。普通江湖好手处于外功-内功之间;门派长老多为真气;"
            "宗师级别在武林屈指可数。修为境界不影响生死,但极大影响出招速度、内劲深浅与意境压制。"
        ),
        "source_chapter": 0,
        "importance": "critical",
    },
    {
        "category": "social",
        "content": (
            "江湖三大正派为天山、武当、少林;魔教(黑风寨为外围分舵之一)与正派长期对峙。"
            "三年前发生『天山血案』,天山一夜倾覆,主角沈青衣是仅存弟子,被江湖讹传为内鬼。"
        ),
        "source_chapter": 0,
        "importance": "critical",
    },
    {
        "category": "geography",
        "content": (
            "塞外『风沙渡』是江湖避世者与商队歇脚的边陲小镇,镇上唯一的酒馆 "
            "『落雁酒馆』是消息流通最快的所在,常有黑风寨眼线混入其间。"
        ),
        "source_chapter": 0,
        "importance": "major",
    },
    {
        "category": "physics",
        "content": (
            "本世界无御剑飞行/隔空取物等仙家手段,所有伤害必须通过物理接触(招式、暗器、毒、内劲外吐)。"
            "高手的『气劲』可在三尺内伤人,但不能远程发射。"
        ),
        "source_chapter": 0,
        "importance": "critical",
    },
    {
        "category": "special",
        "content": (
            "兵器对江湖人是身份标记:沈青衣的青锋长剑(粗布缠裹)是天山遗物;"
            "陆离的弯刀刻有黑风寨的『风』字纹;长辈级人物多用拂尘或不持兵器。"
        ),
        "source_chapter": 0,
        "importance": "minor",
    },
]


USER_PROMPT = """请为《长歌行》的第 1 章第 0 场(scene_index=0)走完整 WRITE 流水线。

# 场景定位
- chapter_number = 1
- scene_index = 0(本场景规划见 chapters/{n}_plan.json)
- 场景核心:边陲『风沙渡』夜雨中,沈青衣化名青衣客在落雁酒馆独饮,旧伤隐痛,内心压抑孤寂

# 任务约束(P2.1c 验证范围)
- 本次只产出本一场的最终稿,不要触碰 scene_index=1 / 2
- **重写最多 1 轮**:即首稿审查后若 needs_rewrite=true 触发一次重写;再次审查后**不论结果都接受**
- prev_tail = ""(本场是第 1 章首场,无承接)

# 工作步骤(严格遵守)
1. 读 KB:read_chapter_plan(1)、read_character(三个名字)、read_bible(可选)、read_foreshadowing(可选)
2. call_scene_director(scene_plan + character_briefs + world_context + tone_hint)
3. save_scene_beatsheet(beatsheet)
4. call_writer(beatsheet, prev_tail="")  ← 首稿(rewrite_attempt=0)
5. save_scene_draft(chapter_number=1, scene={scene_index:0, text:..., rewrite_attempt:0, status:"draft"})
6. **同一轮 tool_calls** 中调 call_critic + call_continuity_checker
7. save_review_issues(chapter_number=1, scene_index=0, data={critic_issues:[...], continuity_issues:[...]})
8. call_review_manager(rewrite_attempt=0, ...)
9. save_rewrite_guidance(chapter_number=1, scene_index=0, guidance={...rewrite_attempt:0...})
10. 若 guidance.needs_rewrite=true:
    - call_writer(beatsheet, current_draft=上次草稿, rewrite_guidance=guidance)
    - save_scene_draft(rewrite_attempt=1, status:"draft")
    - 再走一次步骤 6-9(rewrite_attempt=1 给 ReviewManager;guidance 落盘 attempt_1)
    - 此后**不再重写**(本次验证上限是 1)
11. 最终接受当前草稿:save_scene_draft(rewrite_attempt=最后一次值, status:"accepted")
12. call_scribe(scene_text=终稿, current_character_states=[沈青衣等的当前简表], active_foreshadowing=[])
13. apply_kb_diff(diff=Scribe 产物)
14. 不要调 save_scene_final(那是整章 3 场全部完成后才用)
15. 任务结束

# 注意
- 流式专家 Writer 的 token 会自动推到前端,你只需要拿 call_writer 的字符串返回值用于落盘
- 任何专家产物都先 save_*** 落盘,主管不要把产物缓存在脑子里跨多轮
- 工具失败立即停止,把错误回报给我(不要硬续)
"""


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {text}")
    print("=" * 78)


def seed_workspace(src_workspace: Path, dst_pid: str) -> Path:
    """
    把 P2 plot + cast 的产出复制到新工程,并注入最小武侠 bible。

    新工程结构:
        workspace/{dst_pid}/
        ├── arcs/arc_arc_1.json                  ← 复制自 SEED_PLOT
        ├── characters/{沈青衣,陆离,白衣老人}.json  ← 复制自 SEED_CAST
        ├── characters/relationships.json         ← 复制自 SEED_CAST
        ├── story/outline.json                    ← 复制自 SEED_PLOT
        ├── story/chapter_1_plan.json             ← 复制自 SEED_PLOT
        └── bible/world_rules.json                ← 本脚本手工注入
    """
    dst_workspace = src_workspace / dst_pid
    dst_workspace.mkdir(parents=True, exist_ok=True)

    plot_dir = src_workspace / SEED_PLOT
    cast_dir = src_workspace / SEED_CAST

    if not plot_dir.exists():
        raise FileNotFoundError(f"种子项目不存在:{plot_dir}")
    if not cast_dir.exists():
        raise FileNotFoundError(f"种子项目不存在:{cast_dir}")

    # 1. arcs/
    src_arcs = plot_dir / "arcs"
    dst_arcs = dst_workspace / "arcs"
    if src_arcs.exists():
        shutil.copytree(src_arcs, dst_arcs, dirs_exist_ok=True)

    # 2. story/
    src_story = plot_dir / "story"
    dst_story = dst_workspace / "story"
    if src_story.exists():
        shutil.copytree(src_story, dst_story, dirs_exist_ok=True)

    # 3. characters/
    src_chars = cast_dir / "characters"
    dst_chars = dst_workspace / "characters"
    if src_chars.exists():
        shutil.copytree(src_chars, dst_chars, dirs_exist_ok=True)

    # 4. bible/world_rules.json(BaseDB.append 格式:{"items": [...]})
    bible_dir = dst_workspace / "bible"
    bible_dir.mkdir(exist_ok=True)
    bible_data = {"items": MINIMAL_BIBLE_RULES}
    (bible_dir / "world_rules.json").write_text(
        json.dumps(bible_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 5. project_meta.json(给 read_project_meta 兜底)
    meta = {
        "project_id": dst_pid,
        "title": "长歌行",
        "genre": "武侠",
        "stage": "WRITE",
        "_seeded_from": [SEED_PLOT, SEED_CAST],
        "_seed_purpose": "P2.1c SceneShowrunner 单场景验证",
    }
    (dst_workspace / "project_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return dst_workspace


async def run_validation() -> int:
    banner("P2.1c 垂直切片验证 — SceneShowrunner 主管(WRITE 单场景流水线)")

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

    # 2. 实例化 SceneShowrunner
    try:
        agent = SceneShowrunner()
    except Exception as e:
        print(f"[FAIL] SceneShowrunner 实例化失败:{e}")
        import traceback
        traceback.print_exc()
        return 1
    print(f"[OK] SceneShowrunner 实例化 OK (model={agent.runtime.model}, "
          f"temp={agent.runtime.temperature}, max_turns={agent.max_turns})")

    # 3. tool_scope 过滤校验
    schemas = agent.tool_manager.filter_by_scope(agent.tool_scope)
    visible_names = {s["name"] for s in schemas}
    expected = {
        # KB 共享读
        "read_project_meta", "read_bible", "read_foreshadowing",
        "read_character", "read_relationships", "read_outline",
        "read_arc", "read_chapter_plan", "read_scene_beatsheet",
        "list_scenes", "vector_search",
        # 自身写组
        "save_scene_beatsheet", "save_scene_draft", "save_scene_final",
        "save_review_issues", "save_rewrite_guidance", "apply_kb_diff",
        # 6 个 WRITE 专家
        "call_scene_director", "call_writer", "call_critic",
        "call_continuity_checker", "call_review_manager", "call_scribe",
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

    # 4. 准备工程目录(种子复制 + bible 注入)
    src_workspace = Path(cfg.WORKSPACE_PATH)
    project_id = f"p2-scene-{uuid.uuid4().hex[:8]}"
    try:
        workspace = seed_workspace(src_workspace, project_id)
    except FileNotFoundError as e:
        print(f"[FAIL] 种子准备失败:{e}")
        print(f"       请先运行 P2.1a + P2.1b 验证脚本生成种子工程")
        return 1
    except Exception as e:
        print(f"[FAIL] 种子复制失败:{e}")
        import traceback
        traceback.print_exc()
        return 1
    print(f"[OK] 种子工程已建:{workspace}")
    print(f"     ↳ arcs / story / characters 来自 {SEED_PLOT} + {SEED_CAST}")
    print(f"     ↳ bible/world_rules.json 注入 {len(MINIMAL_BIBLE_RULES)} 条武侠规则")

    # 5. 跑主管
    banner(f"启动 ReAct 循环 - project_id={project_id}")
    print(f"用户输入(片段):{USER_PROMPT.splitlines()[0]} ...")
    print()

    counters = {"reasoning": 0, "output": 0, "completed": 0, "error": 0}
    completed_payloads: list[CompletedPayload] = []
    tool_call_log: list[tuple[int, str, str]] = []  # (turn, name, args_preview)
    output_chunks_per_turn: dict[int, list[str]] = {}  # 按轮分桶,Writer token 流单独可见
    t0 = time.time()
    turn_idx = 0
    writer_streamed_chars = 0  # Writer 流式 token 累计

    try:
        with project_context(project_id):
            async for packet in agent.run(user_prompt=USER_PROMPT):
                counters[packet.type] = counters.get(packet.type, 0) + 1

                if packet.type == "reasoning" and isinstance(packet.content, str):
                    if counters["reasoning"] % 30 == 0 and len(packet.content) > 0:
                        snippet = packet.content[:80].replace("\n", " ")
                        print(f"  · [reasoning #{counters['reasoning']}] {snippet}")

                elif packet.type == "output" and isinstance(packet.content, str):
                    # output 包不一定来自主管;Writer 流式 token 也走这里
                    bucket = output_chunks_per_turn.setdefault(turn_idx + 1, [])
                    bucket.append(packet.content)
                    # 区分 Writer token 流(短包高频)与主管 final output
                    if len(packet.content) <= 8:
                        writer_streamed_chars += len(packet.content)

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
    print(f"Writer 流式 token 累计字符数:{writer_streamed_chars}")

    # 6. 验证 KB 落盘
    banner("验证 KB 落盘")
    success = True

    # 6.1 SceneBeatsheet
    sb_dir = workspace / "chapters" / "scene_beatsheets"
    sb_file = sb_dir / "scene_0.json"
    if sb_file.exists():
        try:
            sb = json.loads(sb_file.read_text(encoding="utf-8"))
            sensory = sb.get("sensory_requirements") or {}
            actions = sb.get("action_beats") or []
            emos = sb.get("emotional_beats") or []
            print(f"[OK] scene_beatsheets/scene_0.json 落盘成功")
            print(
                f"     感官 {sum(1 for v in sensory.values() if v)} 类 / "
                f"action_beats {len(actions)} 拍 / emotional_beats {len(emos)} 拍"
            )
            if not actions or not emos:
                print(f"[FAIL] beatsheet 的 action_beats / emotional_beats 不能为空")
                success = False
            sense_count = sum(
                1 for k in ("sight", "sound", "smell", "touch", "taste")
                if sensory.get(k)
            )
            if sense_count < 3:
                print(f"[FAIL] sensory_requirements 至少 3 种感官,实际 {sense_count}")
                success = False
        except Exception as ex:
            print(f"[FAIL] 解析 scene_0.json 失败:{ex}")
            success = False
    else:
        print(f"[FAIL] 未找到 {sb_file}")
        success = False

    # 6.2 chapter_1_draft.json
    draft_file = workspace / "story" / "chapter_1_draft.json"
    final_text = ""
    final_attempt = -1
    if draft_file.exists():
        try:
            draft = json.loads(draft_file.read_text(encoding="utf-8"))
            scenes = draft.get("scenes") or []
            scene0 = next((s for s in scenes if s.get("scene_index") == 0), None)
            if scene0 is None:
                print(f"[FAIL] chapter_1_draft.json 中缺 scene_index=0")
                success = False
            else:
                final_text = scene0.get("text") or ""
                final_attempt = scene0.get("rewrite_attempt", 0)
                wc = len(final_text)
                status = scene0.get("status", "(无)")
                print(
                    f"[OK] chapter_1_draft.json scene_0 落盘成功"
                    f"(rewrite_attempt={final_attempt}, status={status}, 字符数={wc})"
                )
                if wc < 200:
                    print(f"[FAIL] 草稿文本字符数 {wc} 太少,不像是真正的场景正文")
                    success = False
                if final_attempt > 1:
                    print(f"[FAIL] 重写次数 {final_attempt} 超过 P2.1c 上限 1")
                    success = False
                # 预览
                preview = final_text[:120].replace("\n", " ")
                print(f"     正文预览:{preview}…")
        except Exception as ex:
            print(f"[FAIL] 解析 chapter_1_draft.json 失败:{ex}")
            success = False
    else:
        print(f"[FAIL] 未找到 {draft_file}")
        success = False

    # 6.3 review/issues
    review_dir = workspace / "review"
    issues_file = review_dir / "chapter_1_scene_0_issues.json"
    if issues_file.exists():
        try:
            issues_data = json.loads(issues_file.read_text(encoding="utf-8"))
            critic_issues = issues_data.get("critic_issues") or []
            cont_issues = issues_data.get("continuity_issues") or []
            print(
                f"[OK] review/chapter_1_scene_0_issues.json 落盘"
                f"(Critic {len(critic_issues)} 条 / Continuity {len(cont_issues)} 条)"
            )
            for i, isu in enumerate(critic_issues[:2]):
                print(
                    f"     · Critic#{i} {isu.get('type','?')}/"
                    f"{isu.get('severity','?')}: {str(isu.get('description',''))[:60]}"
                )
            for i, isu in enumerate(cont_issues[:2]):
                print(
                    f"     · Continuity#{i} {isu.get('type','?')}/"
                    f"{isu.get('severity','?')}: {str(isu.get('description',''))[:60]}"
                )
        except Exception as ex:
            print(f"[FAIL] 解析 issues.json 失败:{ex}")
            success = False
    else:
        print(f"[FAIL] 未找到 {issues_file}")
        success = False

    # 6.4 review/guidance(至少 attempt_0;若 needs_rewrite=true 则还有 attempt_1)
    guidance_files = sorted(review_dir.glob("chapter_1_scene_0_guidance_attempt_*.json"))
    if guidance_files:
        print(f"[OK] review/guidance 落盘 {len(guidance_files)} 份")
        first_needs_rewrite = None
        for gf in guidance_files:
            try:
                g = json.loads(gf.read_text(encoding="utf-8"))
                attempt = g.get("rewrite_attempt", "?")
                needs = g.get("needs_rewrite", "?")
                pri = len(g.get("priority_issues") or [])
                mk = len(g.get("must_keep") or [])
                mc = len(g.get("must_change") or [])
                print(
                    f"     · {gf.name}: attempt={attempt} needs_rewrite={needs} "
                    f"priority_issues={pri} must_keep={mk} must_change={mc}"
                )
                if first_needs_rewrite is None:
                    first_needs_rewrite = needs
            except Exception as ex:
                print(f"     · {gf.name} 解析失败:{ex}")
                success = False
        # 重写一致性检查:若 attempt_0.needs_rewrite=true,应该有 attempt_1 落盘
        if first_needs_rewrite is True and len(guidance_files) < 2:
            print(
                f"[WARN] guidance_attempt_0.needs_rewrite=true 但未找到 attempt_1,"
                f"可能 Writer 重写后 ReviewManager 未再次仲裁"
            )
        if first_needs_rewrite is False and final_attempt > 0:
            print(
                f"[WARN] attempt_0.needs_rewrite=false 但 draft.rewrite_attempt={final_attempt},"
                f"逻辑不一致"
            )
    else:
        print(f"[FAIL] 未找到 review/guidance_attempt_*.json")
        success = False

    # 6.5 KB diff 应用结果(角色 last_updated_chapter / current_emotion 等应有变化)
    char_dir = workspace / "characters"
    updated_chars = []
    for cf in char_dir.glob("*.json"):
        if cf.name == "relationships.json":
            continue
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            name = data.get("name", cf.stem)
            last_ch = data.get("last_updated_chapter", 0)
            emo = data.get("current_emotion", "")
            loc = data.get("current_location", "")
            if last_ch >= 1 or emo or loc:
                updated_chars.append((name, last_ch, emo[:25], loc[:20]))
        except Exception as ex:
            print(f"     [WARN] 角色卡 {cf.name} 解析失败:{ex}")

    if updated_chars:
        print(f"[OK] Scribe KB diff 已应用:{len(updated_chars)} 张角色卡有变化")
        for name, lc, emo, loc in updated_chars:
            print(f"     · {name} last_ch={lc} emotion='{emo}' location='{loc}'")
    else:
        print(
            f"[WARN] Scribe KB diff 似乎未对角色卡产生可见变化 "
            f"(last_updated_chapter / current_emotion / current_location 均空)"
        )
        # 不致命:Scribe 可能确实判定本场无角色状态变化(沈青衣还在原地饮酒)

    # 7. 行为合约校验
    banner("ReAct 行为合约校验")
    counts: dict[str, int] = {}
    for _, name, _ in tool_call_log:
        counts[name] = counts.get(name, 0) + 1

    expected_calls = {
        "call_scene_director": 1,
        "call_writer": 1,           # 至少一次首稿,可能多一次重写
        "call_critic": 1,
        "call_continuity_checker": 1,
        "call_review_manager": 1,
        "call_scribe": 1,
        "save_scene_beatsheet": 1,
        "save_scene_draft": 1,
        "save_review_issues": 1,
        "save_rewrite_guidance": 1,
        "apply_kb_diff": 1,
    }
    for name, min_count in expected_calls.items():
        actual = counts.get(name, 0)
        if actual < min_count:
            print(f"[FAIL] {name} 期望 ≥{min_count} 次,实际 {actual} 次")
            success = False
        else:
            print(f"[OK] {name} 调用 {actual} 次")

    # save_scene_final 不应被调用(单场景模式不写整章 final)
    if counts.get("save_scene_final", 0) > 0:
        print(
            f"[WARN] save_scene_final 被调用 {counts['save_scene_final']} 次,"
            f"P2.1c 单场景模式不应触发"
        )

    # 重写次数检查
    writer_calls = counts.get("call_writer", 0)
    if writer_calls > 2:
        print(f"[FAIL] call_writer {writer_calls} 次超过 P2.1c 上限 2(首稿 + 1 轮重写)")
        success = False
    else:
        print(
            f"[OK] call_writer {writer_calls} 次"
            f"({'仅首稿' if writer_calls == 1 else '首稿 + ' + str(writer_calls - 1) + ' 轮重写'})"
        )

    # 并行调用检查:Critic 与 ContinuityChecker 是否在同一轮
    critic_turns = [t for t, n, _ in tool_call_log if n == "call_critic"]
    cont_turns = [t for t, n, _ in tool_call_log if n == "call_continuity_checker"]
    if critic_turns and cont_turns:
        common_turns = set(critic_turns) & set(cont_turns)
        if common_turns:
            print(
                f"[OK] Critic 与 ContinuityChecker 在同一轮 tool_calls 中并行调用"
                f"(轮 {sorted(common_turns)})"
            )
        else:
            print(
                f"[WARN] Critic 在轮 {critic_turns},Continuity 在轮 {cont_turns},"
                f"未实现并行(应同一轮 tool_calls 中两条)"
            )

    # Writer 流式验证
    if writer_streamed_chars > 200:
        print(f"[OK] Writer 流式 token 累计 {writer_streamed_chars} 字符,流式生效")
    else:
        print(
            f"[WARN] Writer 流式 token 仅 {writer_streamed_chars} 字符,"
            f"可能流式未生效或返回值未走 sink"
        )

    # 8. 总结
    banner("验证总结")
    if success:
        print("[PASS] P2.1c 垂直切片验证通过 ✓")
        print("       SceneShowrunner WRITE 单场景流水线打通")
        print(f"       (6 专家 + ≥5 写工具 + 重写循环 ≤1 轮 + Writer 流式)")
        print(f"       项目目录(可保留供检查):{workspace}")
        return 0
    else:
        print("[FAIL] P2.1c 垂直切片验证未通过,请检查上方失败项")
        print(f"       项目目录(保留供调试):{workspace}")
        return 1


def main() -> int:
    try:
        return asyncio.run(run_validation())
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
