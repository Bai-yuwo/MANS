"""
vector_store/
向量存储模块：

- embedding.py: Embedding 模型管理器（bge-m3 本地模型，单例 + LRU 缓存）
- store.py: 向量存储封装（ChromaDB + bge-m3）

快速开始：
    # 方式 1：直接使用 VectorStore
    from vector_store.store import VectorStore
    
    store = VectorStore(project_id="xxx")
    await store.upsert(collection="bible_rules", id="rule_1", text="世界设定")
    results = await store.search(collection="bible_rules", query="修炼体系", n_results=5)
    
    # 方式 2：通过 InjectionEngine 自动使用（推荐）
    from core.injection_engine import InjectionEngine
    
    engine = InjectionEngine(project_id="xxx")
    context = await engine.build_context(scene_plan, chapter_plan)
"""

from .embedding import EmbeddingManager, get_embedding_manager
from .store import VectorStore

__all__ = [
    "EmbeddingManager",
    "get_embedding_manager",
    "VectorStore",
]
