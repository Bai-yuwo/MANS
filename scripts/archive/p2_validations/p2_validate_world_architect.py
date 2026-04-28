"""
scripts/p2_validate_world_architect.py

P2.0 垂直切片验证 — WorldArchitect → Geographer + RuleSmith → save_bible 端到端打通。

目的:在批量铺其余 4 个主管前,先用真实 LLM 跑通"主管 ReAct → 调专家 → 拿产物 → 写 KB"
这条核心 handshake,确保:
    1. agents/managers/world_architect.py 通过 BaseAgent 校验
    2. tool_scope 过滤后,WorldArchitect 看得到 call_geographer / call_rule_smith / save_bible /
       append_foreshadowing / read_* 但看不到其他主管的写工具
    3. ARK Responses API 能正确按 schema 走 ReAct 循环(reasoning + tool_calls + completed)
    4. ExpertTool 一次性 LLM 调用按 output_schema 返回结构化 JSON
    5. WorldArchitect 拿到草案后能正确传 save_bible 的 rules 参数(category/content/importance)
    6. KB 落盘成功 — workspace/{pid}/bible/world_rules.json 与 (可选)
       workspace/{pid}/foreshadowing/items.json 出现合法数据

运行:
    cd D:\\AI协作任务\\MANS
    python -X utf8 scripts/p2_validate_world_architect.py

环境要求:
    .env 中 ARK_API_KEY 已设置(或 DOUBAO_API_KEY 作 fallback)

退出码:
    0 = 验证通过
    1 = 验证失败(异常或 KB 未写入)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

# 让脚本可独立运行(无需 PYTHONPATH 配置)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 触发 .env 装载、日志初始化、AGENT_DEFINITIONS 注册
from core import (  # noqa: E402
    CompletedPayload,
    StreamPacket,
    get_config,
    project_context,
)
from core.logging_config import get_logger  # noqa: E402

# 注意:必须先 import tools 触发所有工具子类注册,
# 然后 import agents 触发 WorldArchitect 类注册
import tools  # noqa: E402, F401
import agents  # noqa: E402, F401
from agents.managers import WorldArchitect  # noqa: E402

logger = get_logger("scripts.p2_validate")

USER_PROMPT = """请为一部新作《青云志异》设计世界观。

题材:玄幻 + 修真 + 门派斗争
力量类型:修真(灵气、丹道、法宝)
风格关键词:东方、师门情谊、复仇、灰色道德
世界规模:一州七国(中央昆仑州 + 周边六小国)

请你按照工作流程:
1. 先调用 RuleSmith 设计核心规则(修炼境界、力量代价、独特设定)
2. 再调用 Geographer 设计地理与势力
3. 审阅整合后,调用 save_bible 把整合好的核心规则写入 bible
4. 如有世界级伏笔(如远古真相、沉睡神秘),调用 append_foreshadowing 写入(可选)

完成后即停止调用工具。
"""


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {text}")
    print("=" * 78)


async def run_validation() -> int:
    banner("P2.0 垂直切片验证 — WorldArchitect 主管")

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

    # 2. 实例化 WorldArchitect(_validate_class_attrs 会自动校验 AGENT_DEFINITIONS)
    try:
        agent = WorldArchitect()
    except Exception as e:
        print(f"[FAIL] WorldArchitect 实例化失败:{e}")
        return 1
    print(f"[OK] WorldArchitect 实例化 OK (model={agent.runtime.model}, "
          f"temp={agent.runtime.temperature}, max_turns={agent.max_turns})")

    # 3. 检查 tool_scope 过滤是否正确
    schemas = agent.tool_manager.filter_by_scope(agent.tool_scope)
    visible_names = {s["name"] for s in schemas}
    expected = {
        "read_project_meta", "read_bible", "read_foreshadowing", "vector_search",
        "save_bible", "append_foreshadowing",
        "call_geographer", "call_rule_smith",
    }
    missing = expected - visible_names
    extra = visible_names - expected
    if missing:
        print(f"[FAIL] tool_scope 过滤后缺失工具: {missing}")
        return 1
    if extra:
        print(f"[FAIL] tool_scope 过滤后混入了不该有的工具: {extra}")
        return 1
    print(f"[OK] tool_scope 过滤正确,主管可见 {len(visible_names)} 个工具:"
          f"{sorted(visible_names)}")

    # 4. 准备唯一 project_id 与工作目录
    project_id = f"p2-validate-{uuid.uuid4().hex[:8]}"
    workspace = Path(cfg.WORKSPACE_PATH) / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"[OK] 项目目录已建: {workspace}")

    # 5. 跑主管(在 ContextVar 内,确保 KB 写工具能拿到 pid)
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
                    # 思考流可能很长,只打印每 200 字一段
                    if counters["reasoning"] % 1 == 0 and len(packet.content) > 0:
                        snippet = packet.content[:80].replace("\n", " ")
                        # 节流到每 20 个 token 打一行
                        if counters["reasoning"] % 20 == 0:
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
                            preview = json.dumps(
                                args, ensure_ascii=False
                            )[:140]
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
    print(f"主管最终 res_id: {agent.last_response_id[:24]}…")
    print(f"主管输出(非工具调用)累计字符:{sum(len(c) for c in output_chunks)}")
    if output_chunks:
        joined = "".join(output_chunks).strip()
        if joined:
            print(f"  └─ 输出预览:{joined[:200]}…")

    # 6. 验证 KB 落盘
    banner("验证 KB 落盘")
    bible_file = workspace / "bible" / "world_rules.json"
    foreshadowing_file = workspace / "foreshadowing" / "items.json"

    success = True

    if bible_file.exists():
        data = json.loads(bible_file.read_text(encoding="utf-8"))
        rules = data.get("items") if isinstance(data, dict) else data
        if not rules:
            print(f"[FAIL] bible/world_rules.json 存在但内容为空: {data}")
            success = False
        else:
            print(f"[OK] bible/world_rules.json 落盘成功,{len(rules)} 条规则")
            for i, r in enumerate(rules[:3]):
                print(
                    f"  · #{i+1} category={r.get('category')} "
                    f"importance={r.get('importance')} "
                    f"content={str(r.get('content', ''))[:60]}…"
                )
            if len(rules) > 3:
                print(f"  · …… 另 {len(rules) - 3} 条")
    else:
        print(f"[FAIL] 未找到 {bible_file}")
        success = False

    if foreshadowing_file.exists():
        data = json.loads(foreshadowing_file.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if items:
            print(f"[OK] foreshadowing/items.json 落盘成功,{len(items)} 条伏笔(可选)")
            for i, f in enumerate(items[:3]):
                print(
                    f"  · #{i+1} type={f.get('type')} "
                    f"description={str(f.get('description', ''))[:60]}…"
                )
        else:
            print("[INFO] foreshadowing 文件存在但为空(可选,LLM 判定无世界级伏笔)")
    else:
        print("[INFO] foreshadowing 未落盘(可选,LLM 判定无世界级伏笔)")

    # 7. 验证工具调用顺序符合主管行为合约
    banner("ReAct 行为合约校验")
    expert_calls = [(t, n) for t, n, _ in tool_call_log
                    if n in {"call_geographer", "call_rule_smith"}]
    write_calls = [(t, n) for t, n, _ in tool_call_log
                   if n in {"save_bible", "append_foreshadowing"}]

    if not expert_calls:
        print("[FAIL] 主管没有调用任何专家")
        success = False
    else:
        names = [n for _, n in expert_calls]
        print(f"[OK] 专家被调用 {len(expert_calls)} 次:{names}")

    if not any(n == "save_bible" for _, n in write_calls):
        print("[FAIL] 主管没有调用 save_bible 落盘")
        success = False
    else:
        print(f"[OK] save_bible 已被调用")

    # 8. 总结
    banner("验证总结")
    if success:
        print("[PASS] P2.0 垂直切片验证通过 ✓")
        print("       handshake 「主管 ReAct → 调专家 → 拿产物 → 写 KB」 端到端打通")
        print("       可以推进 P2.1:批量铺其余 4 主管(Director / CastingDirector / "
              "PlotArchitect / SceneShowrunner)")
        return 0
    else:
        print("[FAIL] P2.0 垂直切片验证未通过,请检查上方失败项")
        return 1


def main() -> int:
    try:
        return asyncio.run(run_validation())
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
