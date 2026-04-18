"""
vector_store/store.py
向量存储封装（基于 ChromaDB + bge-m3 本地模型）：

设计原则：
1. 统一接口：屏蔽底层 ChromaDB/FAISS 差异
2. 项目隔离：每个项目独立 ChromaDB collection
3. 异步操作：所有操作支持异步
4. 本地模型：使用 bge-m3，完全离线零 API 成本
5. 持久化存储：ChromaDB 数据存储在 workspace/{project_id}/vector_store/

硬件要求：
- RTX 3060 Laptop：✅ 流畅运行（~2GB 显存）
- bge-m3 生成速度：~100 条/秒
- 单次检索延迟：< 50ms

使用示例：
    store = VectorStore(project_id="xxx")
    
    # 存储向量
    await store.upsert(
        collection="bible_rules",
        id="rule_0",
        text="修炼体系：淬体 → 炼气 → 结丹 → 元婴 → 化神",
        metadata={"type": "combat_system", "chapter": 1}
    )
    
    # 语义检索
    results = await store.search(
        collection="bible_rules",
        query="主角的修炼境界",
        n_results=5
    )
    
    # 批量存储
    await store.upsert_batch(
        collection="character_cards",
        items=[
            {"id": "char_1", "text": "主角外貌描述", "metadata": {...}},
            {"id": "char_2", "text": "配角外貌描述", "metadata": {...}},
        ]
    )

集合说明：
- bible_rules：世界规则（战力体系、地理、势力等）
- character_cards：人物卡（主角、配角）
- chapter_scenes：章节场景（用于检索相似历史场景）
- style_examples：文风范例
- foreshadowing：伏笔设定
"""

import os
import asyncio
from pathlib import Path
from typing import Optional

from core.config import get_config
from core.logging_config import get_logger, log_exception

from .embedding import get_embedding_manager, EmbeddingManager

logger = get_logger('vector_store.store')


class VectorStore:
    """
    向量存储封装（ChromaDB + bge-m3）
    
    提供语义检索和向量存储能力
    
    使用示例：
        store = VectorStore(project_id="xxx")
        results = await store.search(
            collection="bible_rules",
            query="修炼体系",
            n_results=5
        )
    """
    
    def __init__(
        self,
        project_id: str,
        embedding_manager: Optional[EmbeddingManager] = None,
    ):
        """
        初始化向量存储
        
        Args:
            project_id: 项目 ID（用于隔离不同项目的数据）
            embedding_manager: 可选，注入 EmbeddingManager 实例（用于测试）
        """
        self.project_id = project_id
        self.config = get_config()
        
        # Embedding 管理器（单例）
        self._embedding_manager = embedding_manager
        
        # ChromaDB 客户端和集合（延迟初始化）
        self._client = None
        self._collections: dict[str, object] = {}
    
    @property
    def embedding_manager(self) -> EmbeddingManager:
        """延迟获取 Embedding 管理器"""
        if self._embedding_manager is None:
            self._embedding_manager = get_embedding_manager()
        return self._embedding_manager
    
    def _get_db_path(self) -> Path:
        """获取 ChromaDB 持久化路径"""
        workspace = Path(self.config.WORKSPACE_PATH) / self.project_id
        db_path = workspace / "vector_store"
        db_path.mkdir(parents=True, exist_ok=True)
        return db_path
    
    def _get_collection_name(self, collection: str) -> str:
        """
        获取带项目前缀的 collection 名称
        
        ChromaDB collection 名称要求：
        - 只能是字母、数字、下划线
        - 长度 3-63 字符
        - 不能以数字开头
        """
        # 使用项目 ID 前 8 位 + collection 名
        prefix = self.project_id[:8].replace("-", "_")
        safe_name = collection.replace("-", "_").replace(" ", "_")
        return f"{prefix}_{safe_name}"
    
    def _get_client(self):
        """延迟初始化 ChromaDB 客户端"""
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings
                
                db_path = self._get_db_path()
                self._client = chromadb.PersistentClient(
                    path=str(db_path),
                    settings=Settings(
                        anonymized_telemetry=False,  # 禁用匿名遥测
                    )
                )
                logger.info(f"ChromaDB 客户端初始化: {db_path}")
                
            except ImportError:
                raise ImportError(
                    "ChromaDB 未安装。请运行：pip install chromadb"
                )
        
        return self._client
    
    async def _get_or_create_collection(self, collection: str):
        """获取或创建 collection（异步包装，避免阻塞事件循环）"""
        if collection not in self._collections:
            client = self._get_client()
            collection_name = self._get_collection_name(collection)

            # get_or_create_collection 会触发 sqlite 磁盘 I/O，用线程池封装
            self._collections[collection] = await asyncio.to_thread(
                client.get_or_create_collection,
                name=collection_name,
                metadata={"project_id": self.project_id, "source": collection}
            )
            logger.debug(f"Collection 获取/创建: {collection_name}")

        return self._collections[collection]
    
    async def search(
        self,
        collection: str,
        query: str,
        n_results: int = 5,
        filters: Optional[dict] = None,
        include: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        语义检索
        
        Args:
            collection: collection 名称
            query: 查询文本
            n_results: 返回结果数量
            filters: 过滤条件（可选），例如 {"type": "protagonist"}
            include: 返回字段，默认 ["documents", "metadatas", "distances"]
        
        Returns:
            检索结果列表，每项包含：
            - id: 文档 ID
            - text / document: 文档内容
            - metadata: 元数据
            - distance: 与查询向量的距离（越小越相似）
        """
        try:
            # 生成查询向量
            query_vector = await asyncio.to_thread(
                self.embedding_manager.encode,
                query
            )
            
            # 执行检索
            coll = await self._get_or_create_collection(collection)

            include = include or ["documents", "metadatas", "distances"]

            results = await asyncio.to_thread(
                coll.query,
                query_embeddings=[query_vector],
                n_results=n_results,
                where=filters,
                include=include,
            )
            
            # 格式化结果
            formatted = []
            if results and results["ids"]:
                ids = results["ids"][0]
                documents = results.get("documents", [[]])[0]
                metadatas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0]
                
                for i, doc_id in enumerate(ids):
                    item = {
                        "id": doc_id,
                        "distance": distances[i] if i < len(distances) else None,
                    }
                    if i < len(documents):
                        item["text"] = documents[i]
                    if metadatas and i < len(metadatas) and metadatas[i]:
                        item["metadata"] = metadatas[i]
                    
                    formatted.append(item)
            
            logger.info(
                f"向量检索成功: {collection}, query='{query[:30]}...', "
                f"results={len(formatted)}"
            )
            return formatted
            
        except Exception as e:
            log_exception(logger, e, "向量检索失败")
            logger.warning(f"向量检索失败，返回空结果: {e}")
            return []
    
    async def upsert(
        self,
        collection: str,
        id: str,
        text: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        插入或更新向量
        
        Args:
            collection: collection 名称
            id: 唯一标识
            text: 要向量化的文本
            metadata: 元数据（可选）
        
        Returns:
            是否操作成功
        """
        return await self.upsert_batch(
            collection=collection,
            items=[{"id": id, "text": text, "metadata": metadata}]
        )
    
    async def upsert_batch(
        self,
        collection: str,
        items: list[dict],
    ) -> bool:
        """
        批量插入或更新向量（推荐使用，更高效）
        
        Args:
            collection: collection 名称
            items: 列表，每项包含 id, text, metadata
        
        Returns:
            是否操作成功
        """
        if not items:
            return True
        
        try:
            # 批量向量化
            texts = [item["text"] for item in items]
            vectors = await asyncio.to_thread(
                self.embedding_manager.encode_batch,
                texts
            )
            
            # 准备数据
            ids = [item["id"] for item in items]
            metadatas = [item.get("metadata") for item in items]
            
            # 写入 ChromaDB
            coll = await self._get_or_create_collection(collection)

            await asyncio.to_thread(
                coll.upsert,
                ids=ids,
                embeddings=vectors,
                documents=texts,
                metadatas=metadatas,
            )
            
            logger.info(
                f"批量向量存储成功: {collection}, count={len(items)}, "
                f"ids={ids[:3]}{'...' if len(ids) > 3 else ''}"
            )
            return True
            
        except Exception as e:
            log_exception(logger, e, "批量向量存储失败")
            return False
    
    async def delete(
        self,
        collection: str,
        id: str,
    ) -> bool:
        """
        删除向量
        
        Args:
            collection: collection 名称
            id: 唯一标识
        
        Returns:
            是否删除成功
        """
        try:
            coll = await self._get_or_create_collection(collection)
            await asyncio.to_thread(coll.delete, ids=[id])
            logger.info(f"向量删除成功: {collection}, id={id}")
            return True
            
        except Exception as e:
            log_exception(logger, e, "向量删除失败")
            return False
    
    async def count(self, collection: str) -> int:
        """
        获取 collection 中的向量数量

        Args:
            collection: collection 名称

        Returns:
            向量数量
        """
        try:
            coll = await self._get_or_create_collection(collection)
            count = await asyncio.to_thread(coll.count)
            return count
        except Exception as e:
            logger.warning(f"获取 collection 数量失败: {e}")
            return 0
    
    async def get_collection_info(self, collection: str) -> dict:
        """
        获取 collection 信息
        
        Args:
            collection: collection 名称
        
        Returns:
            包含 collection 信息的字典
        """
        try:
            coll = self._get_or_create_collection(collection)
            count = await asyncio.to_thread(coll.count)
            return {
                "collection": collection,
                "count": count,
                "project_id": self.project_id,
            }
        except Exception as e:
            logger.warning(f"获取 collection 信息失败: {e}")
            return {"collection": collection, "count": 0, "error": str(e)}
    
    def get_stats(self) -> dict:
        """
        获取向量存储统计信息
        
        Returns:
            包含存储统计的字典
        """
        return {
            "embedding": self.embedding_manager.get_stats(),
            "db_path": str(self._get_db_path()),
            "collections": list(self._collections.keys()),
            "project_id": self.project_id,
        }
