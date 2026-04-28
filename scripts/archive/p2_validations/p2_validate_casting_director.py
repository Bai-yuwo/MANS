"""
scripts/p2_validate_casting_director.py

P2.1b 垂直切片验证 — CastingDirector → PortraitDesigner × N + RelationDesigner →
save_character + save_relationships 端到端打通。

目的:验证 CastingDirector 的「**主管循环调同一专家**」模式 handshake:
    1. 按角色名循环调用 PortraitDesigner(N=3)
    2. 每次拿到 CharacterCard 草案 → save_character 落盘
    3. 全员到齐后 → 调用 RelationDesigner → 拿到关系图
    4. save_relationships 落盘
    5. 不再调用工具,任务结束

与 P2.0 / P2.1a 的差异:
    - **同一专家被循环调用 N 次**(P2.0 / P2.1a 的专家各调一次)
    - 关系网设计依赖**全员塑型完成**(顺序契约)
    - 输出无 Pydantic 强约束,output_schema 为宽松内联(strict=False)

按用户决策:
    - 不依赖前置 KB(prompt 自含信息)
    - 3 角色:主角 + 对手 + 导师,验证最小有意义的关系网

运行:
    cd D:\\AI协作任务\\MANS
    python -X utf8 scripts/p2_validate_casting_director.py
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
from agents.managers import CastingDirector  # noqa: E402

logger = get_logger("scripts.p2_validate_casting")

USER_PROMPT = """请为一部新作《长歌行》设计 INIT 阶段的角色阵容。

世界观背景(可作为 PortraitDesigner 的 world_context 传入):
- 题材:东方武侠 + 复仇 + 江湖
- 力量体系:内力修为 + 招式套路 + 兵器(剑/刀/暗器)
- 风格基调:冷峻、压抑、江湖味道、留白
- 时代:虚构武林,有名门正派(天山、武当、少林)与魔教对峙

故事核心冲突(可作为 RelationDesigner 的 story_goal_hint 传入):
- 主角追查十年前师门血案的凶手
- 江湖三大高手中两人勾结魔教,最后一人立场未明
- 个人复仇 vs 武林大义 的二元拉扯

请你按工作流程为以下 3 个角色塑型并设计关系网:

1. **沈青衣**(主角):曾经的天山弟子,因师门血案沦为江湖弃子,化名行走人间。
   是十年前血案的幸存者,内心冷峻、报复欲强、但保留着对师恩的留念。
   role_brief="主角,前天山弟子,因师门血案沦为江湖弃子的复仇者"
   is_protagonist=true

2. **陆离**(对手):沈青衣昔日同门师弟,血案后归附魔教,成为追杀沈青衣的猎手之一。
   表面冷酷却内心矛盾,既背负魔教使命又对昔日情谊仍有挣扎。
   role_brief="对手,沈青衣昔日师弟,现已沦为追杀他的魔教猎手"

3. **白衣老人**(导师):隐居塞外的江湖老侠,曾是天山长老师叔,
   血案后看破纷争隐世,既知主角身世又掌握部分真相,会在关键时点出手指引。
   role_brief="导师,前天山长老师叔,血案后隐世的江湖前辈"

请你按以下步骤工作:
1. 调用 call_portrait_designer 三次(每次只塑一个角色),拿到草案后立刻 save_character 落盘
2. 三人都落盘后,调用 call_relation_designer(传 3 人的简表 + story_goal_hint),拿到关系网
3. 调用 save_relationships(data={"relationships": [...]}) 落盘
4. 完成后即停止调用工具。

注意:本次只设计这 3 个角色,后续配角由后续任务推进。
"""


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {text}")
    print("=" * 78)


async def run_validation() -> int:
    banner("P2.1b 垂直切片验证 — CastingDirector 主管")

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

    # 2. 实例化 CastingDirector
    try:
        agent = CastingDirector()
    except Exception as e:
        print(f"[FAIL] CastingDirector 实例化失败:{e}")
        return 1
    print(f"[OK] CastingDirector 实例化 OK (model={agent.runtime.model}, "
          f"temp={agent.runtime.temperature}, max_turns={agent.max_turns})")

    # 3. tool_scope 过滤校验
    schemas = agent.tool_manager.filter_by_scope(agent.tool_scope)
    visible_names = {s["name"] for s in schemas}
    expected = {
        # KB 共享读
        "read_project_meta", "read_bible",
        "read_character", "read_relationships",
        "vector_search",
        # 自身写组
        "save_character", "save_relationships",
        # 可调专家
        "call_portrait_designer", "call_relation_designer",
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
    project_id = f"p2-casting-{uuid.uuid4().hex[:8]}"
    workspace = Path(cfg.WORKSPACE_PATH) / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"[OK] 项目目录已建: {workspace}")

    # 5. 跑主管
    banner(f"启动 ReAct 循环 - project_id={project_id}")
    print(f"用户输入(片段):{USER_PROMPT.splitlines()[0]} ...")
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

    # 角色卡在 workspace/{pid}/characters/{name}.json
    char_dir = workspace / "characters"
    if char_dir.exists():
        # 排除 relationships.json 自身
        char_files = sorted(
            f for f in char_dir.glob("*.json") if f.name != "relationships.json"
        )
        if char_files:
            print(f"[OK] characters/ 落盘 {len(char_files)} 张角色卡")
            for cf in char_files:
                try:
                    data = json.loads(cf.read_text(encoding="utf-8"))
                    name = data.get("name", "(无 name)")
                    voice = data.get("voice_keywords") or []
                    pcore = data.get("personality_core", "(无)")
                    is_p = data.get("is_protagonist", False)
                    print(
                        f"  · {cf.name}  name={name} is_protagonist={is_p} "
                        f"voice={voice[:3]} core={str(pcore)[:30]}"
                    )
                except Exception as ex:
                    print(f"  · {cf.name} 解析失败:{ex}")
                    success = False
            if len(char_files) < 3:
                print(f"[FAIL] 期望 3 张角色卡,实际 {len(char_files)} 张")
                success = False
        else:
            print(f"[FAIL] characters/ 目录存在但无角色卡")
            success = False
    else:
        print(f"[FAIL] 未找到 characters/ 目录")
        success = False

    # 关系网在 workspace/{pid}/characters/relationships.json
    rel_file = char_dir / "relationships.json"
    if rel_file.exists():
        try:
            rel_data = json.loads(rel_file.read_text(encoding="utf-8"))
            relationships = rel_data.get("relationships") or []
            if relationships:
                print(f"[OK] characters/relationships.json 落盘成功,{len(relationships)} 条关系")
                # 打印前 4 条
                for i, r in enumerate(relationships[:4]):
                    target = r.get("target_name", "(无 target)")
                    rtype = r.get("relation_type", "(无 type)")
                    sent = r.get("current_sentiment", "(无 sentiment)")
                    print(f"  · #{i+1} → {target}  type={rtype}  sentiment={sent}")
                if len(relationships) > 4:
                    print(f"  · …… 另 {len(relationships) - 4} 条")
            else:
                print(f"[FAIL] relationships.json 存在但 relationships 数组为空")
                success = False
        except Exception as ex:
            print(f"[FAIL] 解析 relationships.json 失败:{ex}")
            success = False
    else:
        print(f"[FAIL] 未找到 {rel_file}")
        success = False

    # 7. 行为合约校验
    banner("ReAct 行为合约校验")
    portrait_calls = [n for _, n, _ in tool_call_log if n == "call_portrait_designer"]
    relation_calls = [n for _, n, _ in tool_call_log if n == "call_relation_designer"]
    save_char_calls = [n for _, n, _ in tool_call_log if n == "save_character"]
    save_rel_calls = [n for _, n, _ in tool_call_log if n == "save_relationships"]

    if len(portrait_calls) < 3:
        print(f"[FAIL] PortraitDesigner 期望被调用 ≥3 次,实际 {len(portrait_calls)} 次")
        success = False
    else:
        print(f"[OK] PortraitDesigner 被循环调用 {len(portrait_calls)} 次(目标 3)")

    if len(relation_calls) < 1:
        print(f"[FAIL] RelationDesigner 没有被调用")
        success = False
    else:
        print(f"[OK] RelationDesigner 被调用 {len(relation_calls)} 次")

    if len(save_char_calls) < 3:
        print(f"[FAIL] save_character 期望调用 ≥3 次,实际 {len(save_char_calls)} 次")
        success = False
    else:
        print(f"[OK] save_character 被调用 {len(save_char_calls)} 次")

    if len(save_rel_calls) < 1:
        print(f"[FAIL] save_relationships 没有被调用")
        success = False
    else:
        print(f"[OK] save_relationships 被调用 {len(save_rel_calls)} 次")

    # 验证顺序契约:RelationDesigner 必须在所有 PortraitDesigner 之后
    seq_names = [n for _, n, _ in tool_call_log
                 if n in {"call_portrait_designer", "call_relation_designer"}]
    if "call_relation_designer" in seq_names:
        rel_idx = seq_names.index("call_relation_designer")
        portrait_after_rel = seq_names[rel_idx + 1:].count("call_portrait_designer")
        portrait_before_rel = seq_names[:rel_idx].count("call_portrait_designer")
        if portrait_after_rel > 0:
            print(f"[WARN] RelationDesigner 之后又调了 {portrait_after_rel} 次 PortraitDesigner")
            print(f"       理想顺序是先全员塑型再设计关系。调用前/后比 = "
                  f"{portrait_before_rel}/{portrait_after_rel}")
        else:
            print(f"[OK] 顺序正确:{portrait_before_rel}× PortraitDesigner → "
                  f"RelationDesigner(全员到齐后再设关系网)")

    # 8. 总结
    banner("验证总结")
    if success:
        print("[PASS] P2.1b 垂直切片验证通过 ✓")
        print("       CastingDirector 「循环调同一专家」模式打通")
        print("       (3× PortraitDesigner + 1× RelationDesigner + 4× 写工具)")
        print(f"       项目目录(可保留供检查):{workspace}")
        return 0
    else:
        print("[FAIL] P2.1b 垂直切片验证未通过,请检查上方失败项")
        return 1


def main() -> int:
    try:
        return asyncio.run(run_validation())
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
