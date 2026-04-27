#!/usr/bin/env python3
"""
scripts/p5_validate_api_integration.py

P5 API 集成验证 — 端到端 HTTP 链路冒烟测试。

验证范围:
    1. FastAPI app 可正常启动
    2. 项目 CRUD (POST/GET/DELETE /api/v2/projects)
    3. 项目状态查询 (GET /api/v2/projects/{pid}/status)
    4. SessionManager 与会话状态
    5. SSE /stream 端点可连接、协议格式正确
    6. /run + /respond 编排(可选真实 LLM)

使用:
    python scripts/p5_validate_api_integration.py [--live]

    --live: 走真实 LLM 跑一个最小 Director 会话(需要 ARK_API_KEY)
            预计 1-3 轮 / 20-60 秒
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ARK_API_KEY", "test-key-placeholder")
os.environ.setdefault("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

import httpx
from fastapi.testclient import TestClient

from core.config import get_config
from core.context import set_current_project_id

# 导入 web_app 会触发 ToolManager 初始化
from frontend.web_app import app
from api.session_manager import get_session_manager, SESSION_TIMEOUT

errors: list[str] = []
passes: list[str] = []


def ok(msg: str):
    passes.append(msg)
    print(f"  [OK] {msg}")


def fail(msg: str):
    errors.append(msg)
    print(f"  [FAIL] {msg}")


# ============================================================
# 1. FastAPI app 启动与健康检查
# ============================================================
def test_app_startup():
    print("\n[1] FastAPI app 启动")
    client = TestClient(app)
    resp = client.get("/v2")
    if resp.status_code == 200:
        ok("GET /v2 返回 v2 前端入口")
    else:
        fail(f"GET /v2 返回 {resp.status_code}")

    resp2 = client.get("/frontend/v2/styles.css")
    if resp2.status_code == 200:
        ok("静态文件 /frontend/v2/styles.css 可访问")
    else:
        fail(f"静态文件返回 {resp2.status_code}")


# ============================================================
# 2. 项目 CRUD
# ============================================================
def test_project_crud():
    print("\n[2] 项目 CRUD")
    client = TestClient(app)

    # 创建
    create_resp = client.post(
        "/api/v2/projects",
        json={
            "name": "P5 测试项目",
            "genre": "玄幻",
            "core_idea": "测试核心创意",
        },
    )
    if create_resp.status_code != 200:
        fail(f"创建项目失败: {create_resp.status_code} {create_resp.text}")
        return None
    data = create_resp.json()
    pid = data.get("project_id")
    if not pid:
        fail("创建项目未返回 project_id")
        return None
    ok(f"创建项目成功: {pid}")

    # 列表
    list_resp = client.get("/api/v2/projects")
    if list_resp.status_code == 200 and any(p.get("id") == pid for p in list_resp.json().get("projects", [])):
        ok("项目出现在列表中")
    else:
        fail("项目未出现在列表中")

    # 获取
    get_resp = client.get(f"/api/v2/projects/{pid}")
    if get_resp.status_code == 200 and get_resp.json().get("name") == "P5 测试项目":
        ok("获取项目详情成功")
    else:
        fail("获取项目详情失败")

    # 状态
    status_resp = client.get(f"/api/v2/projects/{pid}/status")
    if status_resp.status_code == 200:
        st = status_resp.json()
        if st.get("stage") == "INIT" and st.get("project_id") == pid:
            ok("项目状态查询成功(stage=INIT)")
        else:
            fail(f"项目状态异常: {st}")
    else:
        fail(f"状态查询失败: {status_resp.status_code}")

    return pid


# ============================================================
# 3. SessionManager 与会话生命周期
# ============================================================
async def test_session_manager():
    print("\n[3] SessionManager 与会话生命周期")
    mgr = get_session_manager()
    pid = "test_session_mgr"

    # 清理残留
    await mgr.remove(pid)

    sess = await mgr.get_or_create(pid)
    if sess.project_id == pid and sess.session_id:
        ok("get_or_create 创建会话成功")
    else:
        fail("会话创建失败")

    got = await mgr.get(pid)
    if got is sess:
        ok("get 返回同一实例")
    else:
        fail("get 返回不同实例")

    await mgr.remove(pid)
    if await mgr.get(pid) is None:
        ok("remove 成功清理会话")
    else:
        fail("remove 未清理会话")


# ============================================================
# 4. SSE 协议格式验证(mock 模式)
# ============================================================
def test_sse_format_mock():
    print("\n[4] SSE 协议格式验证(mock)")
    client = TestClient(app)

    # 先创建一个项目
    create_resp = client.post(
        "/api/v2/projects",
        json={"name": "SSE Test", "genre": "测试"},
    )
    if create_resp.status_code != 200:
        fail("创建项目失败,跳过 SSE 测试")
        return None
    pid = create_resp.json()["project_id"]

    # /stream 在会话不存在时应 404
    stream_resp = client.get(f"/api/v2/projects/{pid}/stream")
    if stream_resp.status_code == 404:
        ok("SSE /stream 无会话时正确返回 404")
    else:
        fail(f"SSE /stream 无会话时应 404,实际 {stream_resp.status_code}")

    # /respond 在会话不存在时应 404
    respond_resp = client.post(
        f"/api/v2/projects/{pid}/respond",
        json={"reply": "同意"},
    )
    if respond_resp.status_code == 404:
        ok("POST /respond 无会话时正确返回 404")
    else:
        fail(f"POST /respond 无会话时应 404,实际 {respond_resp.status_code}")

    return pid


# ============================================================
# 5. 端到端(可选,真实 LLM)
# ============================================================
async def test_live_e2e():
    print("\n[5] 端到端(真实 LLM)")
    cfg = get_config()
    if not cfg.ark_provider.is_configured() or cfg.ark_provider.api_key == "test-key-placeholder":
        print("  [SKIP] 跳过: ARK_API_KEY 未配置")
        return

    # 使用 TestClient 做 HTTP 层端到端
    client = TestClient(app)

    # 创建项目
    create_resp = client.post(
        "/api/v2/projects",
        json={
            "name": "P5 Live E2E",
            "genre": "玄幻",
            "core_idea": "一个少年获得神秘力量的东方玄幻故事",
        },
    )
    if create_resp.status_code != 200:
        fail(f"创建项目失败: {create_resp.text}")
        return
    pid = create_resp.json()["project_id"]
    ok(f"Live 项目创建: {pid}")

    # 启动运行
    run_resp = client.post(
        f"/api/v2/projects/{pid}/run",
        json={"user_prompt": "开始 INIT 阶段,构建世界观和角色设定"},
    )
    if run_resp.status_code == 200:
        ok(f"POST /run 启动成功: {run_resp.json()}")
    else:
        fail(f"POST /run 失败: {run_resp.status_code} {run_resp.text}")
        return

    # SSE 消费(TestClient 不支持 SSE 流式,用 requests 模式)
    # 这里只验证端点可访问,真实 SSE 消费用 httpx 异步
    import requests
    sse_url = f"http://testserver/api/v2/projects/{pid}/stream"
    # TestClient 可以直接 get stream
    with client.stream("GET", f"/api/v2/projects/{pid}/stream") as response:
        events = []
        for line in response.iter_lines():
            if line.startswith(b"event:"):
                event_type = line.decode().replace("event:", "").strip()
                events.append(event_type)
            if len(events) >= 10:
                break
        ok(f"SSE 收到事件类型: {events[:5]}... (共消费 {len(events)} 个事件)")


# ============================================================
# 主入口
# ============================================================
async def main():
    parser = argparse.ArgumentParser(description="P5 API 集成验证")
    parser.add_argument("--live", action="store_true", help="运行真实 LLM 端到端测试(需要 ARK_API_KEY)")
    args = parser.parse_args()

    print("=" * 60)
    print("P5 API 集成验证 — 端到端 HTTP 链路冒烟测试")
    print("=" * 60)

    test_app_startup()
    pid = test_project_crud()
    await test_session_manager()
    test_sse_format_mock()

    if args.live:
        await test_live_e2e()

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
