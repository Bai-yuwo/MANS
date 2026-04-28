#!/usr/bin/env python3
"""
scripts/p2_validate_director.py

P2.2 验证脚本 — Director + Orchestrator 架构合龙。

验证范围:
    1. 核心类型:ConfirmPayload / StreamPacket(type="confirm") 序列化反序列化
    2. ManagerTool:4 个子类 schema 正确、name 自动生成、target_manager_class 指向正确
    3. Director:类属性完整、tool_scope 7 项、AGENT_DEFINITIONS 注册正确
    4. ConfirmStageAdvance:sink 注入 + execute 推送 confirm packet + consume_pending
    5. WriteProjectMeta:schema 与执行(增量更新 project_meta.json)
    6. ToolManager 自动发现:46 tools、无 ManagerTool 基类误注册
    7. Orchestrator:状态机(run→confirm→approve→resume)
    8. 端到端(可选):真实 LLM 跑 Director 一个最小 ReAct 轮次

使用:
    python scripts/p2_validate_director.py [--live]

    --live: 走真实 LLM(需要 ARK_API_KEY),跑一个简化的 Director 会话
            (会调 read_project_meta + write_project_meta + confirm_stage_advance)
            预计 1-3 轮 / 约 2000 tokens / 20-60 秒
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# --------------------------------------------------------
# 路径与初始化
# --------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 避免加载旧 .env 中不存在的 key 导致校验失败
os.environ.setdefault("ARK_API_KEY", "test-key-placeholder")
os.environ.setdefault("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

import tools  # noqa: E402 触发 ToolManager 自动发现
from core import (
    BaseAgent,
    ConfirmPayload,
    ExpertTool,
    ManagerTool,
    StreamPacket,
    ToolManager,
    get_tool_manager,
    reset_tool_manager,
)
from core.config import AGENT_DEFINITIONS, get_config
from core.context import set_current_project_id
from core.stream_packet import CompletedPayload
from core.tool_manager import get_tool_manager

from agents.managers.director import Director
from agents.orchestrator import Orchestrator

from tools.managers import (
    CallCastingDirector,
    CallPlotArchitect,
    CallSceneShowrunner,
    CallWorldArchitect,
)
from tools.system.confirm_stage_advance import ConfirmStageAdvance
from tools.system.write_project_meta import WriteProjectMeta

# 日志静默
from core.logging_config import get_logger

logger = get_logger("p2_validate_director")

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

errors: list[str] = []
passes: list[str] = []


def ok(msg: str):
    passes.append(msg)
    print(f"  [OK] {msg}")


def fail(msg: str):
    errors.append(msg)
    print(f"  [FAIL] {msg}")


# ============================================================
# 1. 核心类型序列化
# ============================================================
def test_stream_packet_types():
    print("\n[1] 核心类型序列化")
    # confirm packet
    cp = ConfirmPayload(
        from_stage="INIT",
        to_stage="PLAN",
        summary="世界观与角色已就绪",
        prompt="是否确认进入 PLAN 阶段?",
        previous_response_id="res_123",
        pending_outputs=[{"type": "function_call_output"}],
    )
    pkt = StreamPacket(type="confirm", content=cp)
    raw = pkt.model_dump_json()
    data = json.loads(raw)
    assert data["type"] == "confirm"
    assert data["content"]["from_stage"] == "INIT"
    assert data["content"]["to_stage"] == "PLAN"
    ok("ConfirmPayload + StreamPacket 序列化/反序列化正确")

    # completed packet 仍兼容
    comp = CompletedPayload(res_id="res_456", total_tokens=100, tool_calls=[])
    pkt2 = StreamPacket(type="completed", content=comp)
    assert "completed" == pkt2.type
    ok("CompletedPayload 兼容性 OK")


# ============================================================
# 2. ManagerTool 子类验证
# ============================================================
def test_manager_tools():
    print("\n[2] ManagerTool 子类验证")
    mapping = [
        (CallWorldArchitect, "WorldArchitect", "call_world_architect"),
        (CallPlotArchitect, "PlotArchitect", "call_plot_architect"),
        (CallCastingDirector, "CastingDirector", "call_casting_director"),
        (CallSceneShowrunner, "SceneShowrunner", "call_scene_showrunner"),
    ]
    for cls, mgr_name, expected_tool_name in mapping:
        inst = cls()
        assert inst.streaming is True, f"{cls.__name__} streaming 应为 True"
        assert inst.name == expected_tool_name, f"{cls.__name__} name 应为 {expected_tool_name},实际 {inst.name}"
        assert inst.target_manager_class is not None
        assert inst.target_manager_class.agent_name == mgr_name
        assert "user_prompt" in inst.schema["parameters"]["properties"]
        ok(f"{cls.__name__} → {mgr_name} (streaming=True, name={inst.name})")


# ============================================================
# 3. Director 类属性验证
# ============================================================
def test_director_class():
    print("\n[3] Director 主管验证")
    d = Director()
    assert d.agent_name == "Director"
    assert d.max_turns == 15
    assert AGENT_DEFINITIONS["Director"]["kind"] == "manager"
    assert AGENT_DEFINITIONS["Director"]["role"] == "reviewer"
    assert "read_project_meta" in d.tool_scope
    assert "write_project_meta" in d.tool_scope
    assert "confirm_stage_advance" in d.tool_scope
    assert "call_world_architect" in d.tool_scope
    assert "call_plot_architect" in d.tool_scope
    assert "call_casting_director" in d.tool_scope
    assert "call_scene_showrunner" in d.tool_scope
    ok("Director 类属性、AGENT_DEFINITIONS 注册、tool_scope 7 项全部正确")


# ============================================================
# 4. ConfirmStageAdvance sink 机制
# ============================================================
async def test_confirm_stage_advance():
    print("\n[4] ConfirmStageAdvance sink 机制")
    tool = ConfirmStageAdvance()
    captured = []

    async def sink(pkt: StreamPacket) -> None:
        captured.append(pkt)

    tool.with_stream_sink(sink)
    result = await tool.execute(
        from_stage="INIT",
        to_stage="PLAN",
        summary="测试摘要",
        prompt="测试问句",
    )
    tool.with_stream_sink(None)

    # 验证 packet
    assert len(captured) == 1
    assert captured[0].type == "confirm"
    payload = captured[0].content
    assert isinstance(payload, ConfirmPayload)
    assert payload.from_stage == "INIT"
    assert payload.to_stage == "PLAN"
    ok("confirm packet 通过 sink 正确推送")

    # 验证 consume_pending
    pending = tool.consume_pending()
    assert pending is not None
    assert pending.from_stage == "INIT"
    assert tool.consume_pending() is None
    ok("consume_pending 正确工作")

    # 验证 function_call_output 字符串
    data = json.loads(result)
    assert data["status"] == "confirmation_emitted"
    assert "INIT" == data["from_stage"]
    ok("execute 返回 JSON 正确")


# ============================================================
# 5. WriteProjectMeta 执行
# ============================================================
async def test_write_project_meta():
    print("\n[5] WriteProjectMeta 执行")
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = get_config()
        orig_workspace = cfg.WORKSPACE_PATH
        cfg.WORKSPACE_PATH = tmpdir
        try:
            pid = "test_proj_director"
            set_current_project_id(pid)
            proj_dir = Path(tmpdir) / pid
            proj_dir.mkdir(parents=True, exist_ok=True)
            meta_path = proj_dir / "project_meta.json"
            meta_path.write_text(
                json.dumps({"name": "测试项目", "stage": "INIT"}, ensure_ascii=False),
                encoding="utf-8",
            )

            tool = WriteProjectMeta()
            result = await tool.execute(stage="PLAN", current_chapter=1)
            data = json.loads(result)
            assert data["status"] == "ok"
            assert "stage" in data["updated_fields"]
            assert "current_chapter" in data["updated_fields"]
            ok("WriteProjectMeta 增量更新返回正确")

            # 验证文件内容
            content = json.loads(meta_path.read_text(encoding="utf-8"))
            assert content["stage"] == "PLAN"
            assert content["current_chapter"] == 1
            assert content["name"] == "测试项目"  # 旧字段保留
            ok("project_meta.json 增量写入正确")
        finally:
            cfg.WORKSPACE_PATH = orig_workspace
            reset_tool_manager()


# ============================================================
# 6. ToolManager 自动发现
# ============================================================
def test_tool_discovery():
    print("\n[6] ToolManager 自动发现")
    reset_tool_manager()
    import tools  # re-import after reset  # noqa: F401
    tm = get_tool_manager()

    # 46 个工具(40 P1 + 4 ManagerTool + confirm_stage_advance + write_project_meta)
    expected = 46
    actual = len(tm.all_names)
    if actual == expected:
        ok(f"ToolManager 发现 {actual} 个工具(预期 {expected})")
    else:
        fail(f"ToolManager 发现 {actual} 个工具,预期 {expected}")
        print(f"    实际列表: {sorted(tm.all_names)}")

    # 无基类误注册
    assert not tm.has("manager_tool"), "ManagerTool 基类不应被注册"
    assert not tm.has("expert_tool"), "ExpertTool 基类不应被注册"
    assert not tm.has("base_tool"), "BaseTool 不应被注册"
    ok("基类未被误注册")

    # Director scope
    d = Director()
    schemas = tm.filter_by_scope(d.tool_scope)
    if len(schemas) == len(d.tool_scope):
        ok(f"Director tool_scope 7/7 全部发现")
    else:
        fail(f"Director tool_scope 仅发现 {len(schemas)}/{len(d.tool_scope)}")
        found_names = {s["name"] for s in schemas}
        missing = set(d.tool_scope) - found_names
        print(f"    缺失: {missing}")


# ============================================================
# 7. Orchestrator 状态机
# ============================================================
async def test_orchestrator_state_machine():
    print("\n[7] Orchestrator 状态机")
    orch = Orchestrator(project_id="test_orchestrator")
    assert not orch.is_waiting_confirm
    assert orch.confirm_payload is None
    ok("Orchestrator 初始状态正确")

    # 手动模拟 confirm 到达后的状态
    cp = ConfirmPayload(
        from_stage="INIT", to_stage="PLAN", summary="s", prompt="p"
    )
    orch._confirm_payload = cp
    orch._last_response_id = "test_res_id"
    assert orch.is_waiting_confirm
    assert orch.confirm_payload is cp
    assert orch.last_response_id == "test_res_id"
    ok("Orchestrator confirm 状态捕获正确")


# ============================================================
# 8. 端到端(可选,真实 LLM)
# ============================================================
async def test_live_director_minimal():
    print("\n[8] 端到端(真实 LLM) — 最小 Director 会话")
    cfg = get_config()
    if not cfg.ark_provider.is_configured() or cfg.ark_provider.api_key == "test-key-placeholder":
        print(f"  [SKIP] 跳过:ARK_API_KEY 未配置,无法运行真实 LLM 测试")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg.WORKSPACE_PATH = tmpdir
        pid = "p2_director_live"
        set_current_project_id(pid)
        proj_dir = Path(tmpdir) / pid
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project_meta.json").write_text(
            json.dumps(
                {
                    "id": pid,
                    "name": "P2.2 Director 验证",
                    "genre": "玄幻",
                    "stage": "INIT",
                    "status": "active",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        orch = Orchestrator(project_id=pid)
        packets = []
        turn_count = 0

        try:
            async for pkt in orch.run("开始 INIT 阶段,构建一个东方玄幻世界观和3个主要角色"):
                packets.append(pkt)
                if pkt.type == "completed":
                    turn_count += 1
                if pkt.type == "confirm":
                    break
        except Exception as e:
            fail(f"Director live 运行异常: {e}")
            traceback.print_exc()
            return

        # 基础断言
        if not packets:
            fail("Director live 未产出任何 packet")
            return

        # 检查 confirm 包或正常完成
        confirm_pkts = [p for p in packets if p.type == "confirm"]
        completed_pkts = [p for p in packets if p.type == "completed"]

        if confirm_pkts:
            cp = confirm_pkts[0].content
            ok(
                f"Director live 产出 confirm 包: {cp.from_stage}→{cp.to_stage} "
                f"(turns={turn_count}, packets={len(packets)})"
            )
        else:
            ok(
                f"Director live 完成无 confirm(可能直接结束): "
                f"turns={turn_count}, packets={len(packets)}"
            )

        # 尝试续接(如果存在 confirm)
        if confirm_pkts and orch.is_waiting_confirm:
            print(f"  -> 测试续接 approve...")
            resume_packets = []
            try:
                async for pkt in orch.approve("同意,继续推进"):
                    resume_packets.append(pkt)
                    if pkt.type == "confirm":
                        break
            except Exception as e:
                fail(f"Director approve 续接异常: {e}")
                return

            ok(
                f"Director approve 续接成功: packets={len(resume_packets)}"
            )


# ============================================================
# 主入口
# ============================================================
async def main():
    parser = argparse.ArgumentParser(description="P2.2 Director + Orchestrator 验证")
    parser.add_argument("--live", action="store_true", help="运行真实 LLM 端到端测试(需要 ARK_API_KEY)")
    args = parser.parse_args()

    print("=" * 60)
    print("P2.2 Director + Orchestrator 架构合龙验证")
    print("=" * 60)

    test_stream_packet_types()
    test_manager_tools()
    test_director_class()
    await test_confirm_stage_advance()
    await test_write_project_meta()
    test_tool_discovery()
    await test_orchestrator_state_machine()

    if args.live:
        await test_live_director_minimal()

    print("\n" + "=" * 60)
    print(f"结果: {len(passes)} 通过, {len(errors)} 失败")
    if errors:
        print(f"\n失败项:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"\n全部通过 [OK]")


if __name__ == "__main__":
    asyncio.run(main())
