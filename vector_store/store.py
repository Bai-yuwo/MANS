"""
vector_store/store.py

向量存储封装，提供基于语义相似性的长期记忆检索能力。

职责边界：
    - 封装 ChromaDB 的底层细节，对外提供统一的异步接口。
    - 实现项目隔离：每个项目拥有独立的 ChromaDB collection，避免数据混叠。
    - 所有操作支持异步，避免阻塞主事件循环（ChromaDB 的 SQLite 操作通过 asyncio.to_thread 卸载到线程池）。
    - 支持向量的增删改查（upsert、delete、search、count）。

底层架构：
    - 向量数据库：ChromaDB（PersistentClient，数据持久化到本地文件系统）。
    - Embedding 模型：bge-m3（本地运行，通过 sentence-transformers 加载）。
    - 向量维度：1024（bge-m3 的输出维度）。
    - 相似度度量：余弦相似度（EmbeddingManager 已对向量做 L2 归一化）。

硬件要求：
    - RTX 3060 Laptop：流畅运行（Embedding 模型约占用 2GB 显存）。
    - 纯 CPU 环境：可运行，但速度较慢（约 10-20 条/秒）。

集合（Collection）说明：
    - bible_rules：世界规则（战力体系、地理、势力等）。
    - character_cards：人物卡（主角、配角的关键特征描述）。
    - chapter_scenes：章节场景文本（用于检索相似历史场景）。
    - style_examples：文风范例（按情绪基调分类）。
    - foreshadowing：伏笔设定描述。

典型用法：
    store = VectorStore(project_id="xxx")
    await store.upsert(collection="bible_rules", id="rule_1", text="淬体→炼气→结丹", metadata={"chapter": 1})
    results = await store.search(collection="bible_rules", query="主角的境界", n_results=5)
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
    向量存储封装（ChromaDB + bge-m3）。

    核心能力：
        将文本转换为高维向量并存储，支持基于语义相似度的检索。
        这突破了传统关键词检索的局限，能够理解同义词、近义表达和上下文语义。

    项目隔离机制：
        每个项目在 ChromaDB 中拥有独立的 collection，collection 名称格式为：
            {project_id_prefix}_{collection_name}
        其中 project_id_prefix 取项目 ID 的前 8 位（并将连字符替换为下划线）。
        这种命名策略既保证了项目隔离，又满足 ChromaDB 对 collection 名称的格式要求
       （只能包含字母、数字、下划线，长度 3-63 字符，不能以数字开头）。

    延迟初始化：
        ChromaDB 客户端和 collection 均在首次使用时才创建，
        避免在仅导入模块时就触发耗时操作。
    """

    def __init__(
        self,
        project_id: str,
        embedding_manager: Optional[EmbeddingManager] = None,
    ):
        """
        初始化向量存储。

        Args:
            project_id: 项目唯一标识，用于隔离不同项目的向量数据。
            embedding_manager: 可选，注入外部的 EmbeddingManager 实例。
                               主要用于测试场景中的 mock 替换。
        """
        self.project_id = project_id
        self.config = get_config()
        self._embedding_manager = embedding_manager
        self._client = None
        self._collections: dict[str, object] = {}

    @property
    def embedding_manager(self) -> EmbeddingManager:
        """
        延迟获取 Embedding 管理器单例。

        首次访问时调用 get_embedding_manager() 获取全局单例。
        此后直接返回缓存实例。
        """
        if self._embedding_manager is None:
            self._embedding_manager = get_embedding_manager()
        return self._embedding_manager

    def _get_db_path(self) -> Path:
        """
        获取 ChromaDB 持久化存储路径。

        路径格式：workspace/{project_id}/vector_store/
        目录在首次访问时自动创建。
        """
        workspace = Path(self.config.WORKSPACE_PATH) / self.project_id
        db_path = workspace / "vector_store"
        db_path.mkdir(parents=True, exist_ok=True)
        return db_path

    def _get_collection_name(self, collection: str) -> str:
        """
        生成带项目前缀的 collection 名称。

        ChromaDB 对 collection 名称的约束：
            - 只能包含字母、数字、下划线
            - 长度 3-63 字符
            - 不能以数字开头

        本方法通过以下转换确保合规：
            1. 取项目 ID 前 8 位作为前缀。
            2. 将连字符替换为下划线。
            3. 将 collection 名称中的连字符和空格替换为下划线。

        Args:
            collection: 逻辑 collection 名称（如 "bible_rules"）。

        Returns:
            合规的 ChromaDB collection 名称。
        """
        prefix = self.project_id[:8].replace("-", "_")
        safe_name = collection.replace("-", "_").replace(" ", "_")
        return f"{prefix}_{safe_name}"

    def _get_client(self):
        """
        延迟初始化 ChromaDB 客户端。

        首次访问时创建 PersistentClient，数据持久化到本地 SQLite。
        禁用匿名遥测（anonymized_telemetry=False）以保护用户隐私。

        Raises:
            ImportError: 当 ChromaDB 未安装时抛出，提示用户安装依赖。
        """
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings

                db_path = self._get_db_path()
                self._client = chromadb.PersistentClient(
                    path=str(db_path),
                    settings=Settings(
                        anonymized_telemetry=False,
                    )
                )
                logger.info(f"ChromaDB 客户端初始化: {db_path}")

            except ImportError:
                raise ImportError(
                    "ChromaDB 未安装。请运行：pip install chromadb"
                )

        return self._client

    async def _get_or_create_collection(self, collection: str):
        """
        获取或创建 collection（异步包装）。

        ChromaDB 的 get_or_create_collection 会触发 SQLite 磁盘 I/O，
        属于阻塞操作。本方法通过 asyncio.to_thread 将其卸载到线程池执行，
        避免阻塞主事件循环。

        实例级缓存：
            已获取的 collection 对象缓存在 self._collections 字典中，
            后续访问直接返回缓存，无需重复调用 ChromaDB API。

        Args:
            collection: 逻辑 collection 名称。

        Returns:
            ChromaDB collection 对象。
        """
        if collection not in self._collections:
            client = self._get_client()
            collection_name = self._get_collection_name(collection)

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
        语义检索。

        检索流程：
            1. 使用 EmbeddingManager 将 query 文本转换为向量。
            2. 在指定 collection 中执行向量相似度搜索。
            3. 返回最相似的 n_results 条结果。

        结果格式：
            每条结果包含以下字段：
                - id: 文档唯一标识
                - text / document: 原始文本内容
                - metadata: 存入时的元数据字典
                - distance: 与查询向量的距离（越小越相似）

        容错处理：
            检索失败时会记录详细错误日志并重新抛出异常，
            由调用方（如 InjectionEngine）决定是中断流程还是回退到空结果。

        Args:
            collection: 目标 collection 名称。
            query: 查询文本（自然语言描述，无需关键词精确匹配）。
            n_results: 返回结果数量上限。
            filters: 元数据过滤条件（可选），如 {"type": "protagonist"}。
            include: 返回字段白名单（可选），默认 ["documents", "metadatas", "distances"]。

        Returns:
            检索结果列表，按相似度排序（distance 从小到大）。
        """
        try:
            query_vector = await asyncio.to_thread(
                self.embedding_manager.encode,
                query
            )

            coll = await self._get_or_create_collection(collection)

            include = include or ["documents", "metadatas", "distances"]

            results = await asyncio.to_thread(
                coll.query,
                query_embeddings=[query_vector],
                n_results=n_results,
                where=filters,
                include=include,
            )

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
            raise

    async def upsert(
        self,
        collection: str,
        id: str,
        text: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        插入或更新单条向量。

        若指定 id 已存在，则覆盖原有数据；若不存在，则创建新条目。
        此方法是 upsert_batch() 的单条便捷封装。

        Args:
            collection: 目标 collection 名称。
            id: 文档唯一标识（同一 collection 内不可重复）。
            text: 要向量化的文本内容。
            metadata: 关联元数据字典（可选），用于检索时过滤。

        Returns:
            是否操作成功。
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
        批量插入或更新向量（推荐使用，效率更高）。

        批量处理流程：
            1. 提取所有文本，调用 EmbeddingManager.encode_batch() 批量向量化。
            2. 准备 ids、embeddings、documents、metadatas 数组。
            3. 通过 asyncio.to_thread 异步写入 ChromaDB。

        性能优势：
            相比多次调用单条 upsert，批量处理显著减少了 Embedding 模型和 ChromaDB 的调用次数，
            总体速度提升通常在 5-10 倍。

        Args:
            collection: 目标 collection 名称。
            items: 条目列表，每项必须包含 id 和 text，可选 metadata。

        Returns:
            是否操作成功。
        """
        if not items:
            return True

        try:
            texts = [item["text"] for item in items]
            vectors = await asyncio.to_thread(
                self.embedding_manager.encode_batch,
                texts
            )

            ids = [item["id"] for item in items]
            metadatas = [item.get("metadata") for item in items]

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
            raise

    async def delete(
        self,
        collection: str,
        id: str,
    ) -> bool:
        """
        删除指定向量。

        Args:
            collection: 目标 collection 名称。
            id: 要删除的文档唯一标识。

        Returns:
            是否删除成功。
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
        获取 collection 中的向量数量。

        Args:
            collection: collection 名称。

        Returns:
            向量数量，获取失败时返回 0。
        """
        try:
            coll = await self._get_or_create_collection(collection)
            count = await asyncio.to_thread(coll.count)
            return count
        except Exception as e:
            logger.warning(f"获取 collection 数量失败: {e}")
            return 0

    async def get_all_ids(self, collection: str) -> list[str]:
        """
        获取 collection 中所有文档 ID。

        Args:
            collection: collection 名称。

        Returns:
            ID 列表，获取失败返回空列表。
        """
        try:
            coll = await self._get_or_create_collection(collection)
            result = await asyncio.to_thread(
                coll.get,
                include=[],
            )
            return result.get("ids", [])
        except Exception as e:
            logger.warning(f"获取 collection IDs 失败: {e}")
            return []

    async def delete_except(self, collection: str, keep_ids: set[str]) -> int:
        """
        删除 collection 中不在 keep_ids 集合里的所有向量。

        用于 JSON 数据删除后清理向量库中的残留文档。

        Args:
            collection: collection 名称。
            keep_ids: 需要保留的 ID 集合。

        Returns:
            实际删除的向量数量。
        """
        try:
            all_ids = await self.get_all_ids(collection)
            to_delete = [vid for vid in all_ids if vid not in keep_ids]
            if to_delete:
                coll = await self._get_or_create_collection(collection)
                await asyncio.to_thread(coll.delete, ids=to_delete)
                logger.info(f"向量残留清理: {collection} 删除 {len(to_delete)} 条")
            return len(to_delete)
        except Exception as e:
            logger.warning(f"向量残留清理失败 {collection}: {e}")
            return 0

    async def get_metadata(self, collection: str, doc_id: str) -> dict:
        """
        获取指定文档的元数据。

        Args:
            collection: collection 名称。
            doc_id: 文档唯一标识。

        Returns:
            元数据字典，文档不存在或获取失败时返回空字典。
        """
        try:
            coll = await self._get_or_create_collection(collection)
            result = await asyncio.to_thread(
                coll.get,
                ids=[doc_id],
                include=["metadatas"],
            )
            metadatas = result.get("metadatas", [])
            return metadatas[0] if metadatas else {}
        except Exception as e:
            logger.warning(f"获取文档元数据失败 {collection}/{doc_id}: {e}")
            return {}

    async def get_collection_info(self, collection: str) -> dict:
        """
        获取 collection 的统计信息。

        Args:
            collection: collection 名称。

        Returns:
            包含 collection 名称、向量数量、项目 ID 的字典。
            获取失败时返回包含 error 字段的字典。
        """
        try:
            coll = await self._get_or_create_collection(collection)
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
        获取向量存储的整体统计信息。

        Returns:
            包含 Embedding 模型信息、数据库路径、已加载的 collections 列表、
            项目 ID 的字典。此方法同步执行，不涉及 I/O。
        """
        return {
            "embedding": self.embedding_manager.get_stats(),
            "db_path": str(self._get_db_path()),
            "collections": list(self._collections.keys()),
            "project_id": self.project_id,
        }
