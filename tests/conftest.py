"""
conftest.py — MANS 测试全局配置

Fixture 清单:
  - _clean_state (autouse): 每测试后重置所有 ClassVar 单例
  - tmp_workspace: 创建临时 workspace 目录
  - fake_llm: FakeLLMClient 实例
  - fake_tool_manager: FakeToolManager 实例
  - _ensure_prompts_path: 确保 prompts/ 目录存在
  - _load_config: session 级配置加载
  - mock_vector_store: 模拟向量存储
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 PYTHONPATH
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 1. 状态清理 (autouse — 每个测试后自动执行)
# ============================================================

@pytest.fixture(autouse=True)
def _clean_state():
    """
    重置所有可能泄漏的 ClassVar 单例状态。

    清理对象:
      - BaseAgent._shared_client
      - ExpertTool._shared_client
      - ManagerTool._manager_instances / _instance_access_ts
      - core.config._config
      - os.environ 变更
    """
    # 保存原始环境变量快照
    orig_env = dict(os.environ)

    yield

    # 测试后: 重置共享客户端
    from core.base_agent import BaseAgent
    from core.expert_tool import ExpertTool
    from core.manager_tool import ManagerTool

    BaseAgent._shared_client = None
    ExpertTool._shared_client = None
    ManagerTool.clear_cache()

    # 重置 Config 单例
    import core.config as _cfg_mod
    _cfg_mod._config = None

    # 恢复环境变量(删除测试期间新增的,恢复被修改的)
    for key in list(os.environ.keys()):
        if key not in orig_env:
            del os.environ[key]
    for key, val in orig_env.items():
        os.environ[key] = val


# ============================================================
# 2. 临时 Workspace
# ============================================================

@pytest.fixture
def tmp_workspace(tmp_path: Path):
    """
    创建临时 workspace 目录,返回 project_id。

    结构:
        {tmp_path}/workspace/{project_id}/
            project_meta.json
            bible.json
            characters/
            ...
    """
    project_id = "test_proj_001"
    ws = tmp_path / "workspace" / project_id
    ws.mkdir(parents=True)

    # 写入最小 project_meta
    meta = {
        "project_id": project_id,
        "name": "测试项目",
        "stage": "INIT",
        "status": "active",
        "genre": "玄幻",
        "tone": "热血",
        "core_idea": "测试核心创意",
        "created_at": "2024-01-01T00:00:00",
    }
    (ws / "project_meta.json").write_text(
        __import__("json").dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    # 预创建子目录
    for sub in ["characters", "arcs", "chapters/scene_beatsheets"]:
        (ws / sub).mkdir(parents=True, exist_ok=True)

    # Monkeypatch workspace 路径
    import core.config as _cfg_mod
    orig_config = _cfg_mod._config
    _cfg_mod._config = None

    with patch.object(_cfg_mod.Config, "WORKSPACE_PATH", str(tmp_path / "workspace")):
        yield project_id

    # 恢复
    _cfg_mod._config = orig_config


# ============================================================
# 3. Fake LLM Client
# ============================================================

@pytest.fixture
def fake_llm():
    """提供已重置的 FakeLLMClient 实例。"""
    from tests.fixtures.fake_llm_client import FakeLLMClient

    client = FakeLLMClient()
    yield client
    client.reset()


# ============================================================
# 4. Fake Tool Manager
# ============================================================

@pytest.fixture
def fake_tool_manager():
    """提供已重置的 FakeToolManager 实例。"""
    from tests.fixtures.fake_tool_manager import FakeToolManager

    tm = FakeToolManager()
    yield tm


# ============================================================
# 5. Prompts 路径检查
# ============================================================

@pytest.fixture(scope="session", autouse=True)
def _ensure_prompts_path():
    """确保 prompts/ 目录存在,否则 ExpertTool 加载模板会失败。"""
    prompts_dir = PROJECT_ROOT / "prompts"
    if not prompts_dir.exists():
        pytest.skip("prompts/ 目录不存在,跳过依赖 prompt 的测试")


# ============================================================
# 6. Config 加载 (session)
# ============================================================

@pytest.fixture(scope="session")
def _load_config():
    """预加载 Config,避免每个测试重复读取 .env。"""
    from core.config import get_config

    return get_config()


# ============================================================
# 7. 向量存储 Mock
# ============================================================

@pytest.fixture
def mock_vector_store():
    """模拟 ChromaDB 向量存储,避免测试时加载 torch/sentence-transformers。"""
    mock_client = MagicMock()
    mock_collection = MagicMock()
    mock_collection.query.return_value = {"ids": [[]], "distances": [[]], "metadatas": [[]]}
    mock_client.get_or_create_collection.return_value = mock_collection

    with patch("core.vector_store.chromadb.Client", return_value=mock_client):
        with patch("core.vector_store.SentenceTransformer") as mock_st:
            mock_encoder = MagicMock()
            mock_encoder.encode.return_value = [[0.1] * 768]
            mock_st.return_value = mock_encoder
            yield mock_client
