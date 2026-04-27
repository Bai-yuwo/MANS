"""
knowledge_bases/setting_db.py

通用设定节点存储 —— 扁平分类结构。

职责边界：
    - 存储通用设定节点（SettingNode），用于不便归入 Cultivation/Geo/Faction/Tech/Social 的零散设定。
    - 支持按 category 查询、按 importance 过滤。
    - 自动同步节点描述到向量库（setting_nodes collection）。

存储结构：
    workspace/{project_id}/settings/
    └── nodes.json       # 设定节点字典
"""

from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.schemas import SettingNode
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.setting_db')


class SettingDB(BaseDB):
    """通用设定节点存储。"""

    def __init__(self, project_id: str):
        super().__init__(project_id, "settings")

    # ── 节点 CRUD ──

    async def save_node(self, node: SettingNode) -> bool:
        """保存设定节点。"""
        data = await self.load("nodes") or {"nodes": {}}
        nodes: dict = data.get("nodes", {})
        nodes[node.id] = node.model_dump()
        data["nodes"] = nodes
        return await self.save("nodes", data)

    async def get_node(self, node_id: str) -> Optional[SettingNode]:
        """按 ID 获取设定节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        node_data = data["nodes"].get(node_id)
        if not node_data:
            return None
        try:
            return SettingNode(**node_data)
        except Exception as e:
            logger.error(f"解析设定节点失败 {node_id}: {e}")
            return None

    async def get_node_by_name(self, name: str) -> Optional[SettingNode]:
        """按名称获取设定节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        for node_data in data["nodes"].values():
            if node_data.get("name") == name:
                try:
                    return SettingNode(**node_data)
                except Exception:
                    continue
        return None

    async def delete_node(self, node_id: str) -> bool:
        """删除设定节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return False
        nodes = data["nodes"]
        if node_id not in nodes:
            return False
        del nodes[node_id]
        return await self.save("nodes", data)

    async def list_all_nodes(self) -> list[SettingNode]:
        """获取所有设定节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        nodes = []
        for node_data in data["nodes"].values():
            try:
                nodes.append(SettingNode(**node_data))
            except Exception:
                continue
        return nodes

    async def list_by_category(self, category: str) -> list[SettingNode]:
        """按分类获取设定节点。"""
        nodes = await self.list_all_nodes()
        return [n for n in nodes if n.category == category]

    async def list_by_importance(self, importance: str) -> list[SettingNode]:
        """按重要性获取设定节点。"""
        nodes = await self.list_all_nodes()
        return [n for n in nodes if n.importance == importance]

    # ── 向量同步 ──

    async def _after_save(self, key: str, data: dict) -> None:
        """保存后自动同步节点到向量库，并清理已删除节点的向量残留。"""
        if key != "nodes" or "nodes" not in data:
            return
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)

            # 1. 清理已删除节点的向量残留
            current_ids = set(data["nodes"].keys())
            await vs.delete_except("setting_nodes", current_ids)

            # 2. 同步当前节点
            items = []
            for node_id, node_data in data["nodes"].items():
                text = self._node_to_text(node_data)
                items.append({
                    "id": node_id,
                    "text": text,
                    "metadata": {
                        "name": node_data.get("name", ""),
                        "category": node_data.get("category", ""),
                        "importance": node_data.get("importance", ""),
                        "_content_hash": self._compute_hash(node_data),
                    }
                })
            if items:
                await vs.upsert_batch("setting_nodes", items)
                logger.info(f"设定节点向量同步: {len(items)} 个节点")
        except Exception as e:
            log_exception(logger, e, "设定节点向量同步失败")

    @staticmethod
    def _node_to_text(node_data: dict) -> str:
        """将节点数据转换为可向量化的文本。"""
        parts = [f"设定: {node_data.get('name', '')}"]
        parts.append(f"分类: {node_data.get('category', '')}")
        parts.append(f"重要性: {node_data.get('importance', '')}")
        parts.append(f"描述: {node_data.get('description', '')}")
        return "\n".join(parts)

    # ── 同步校验与修复 ──

    async def repair_sync(self) -> dict:
        """强制重新同步所有设定节点到向量库（以 JSON 为准）。"""
        data = await self.load("nodes") or {}
        if "nodes" not in data:
            return {"repaired": 0}
        await self._after_save("nodes", data)
        return {"repaired": len(data["nodes"])}

    async def verify_sync(self) -> dict:
        """校验 JSON 与向量库的内容一致性。"""
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)
        except Exception as e:
            return {"error": f"VectorStore 初始化失败: {e}"}

        data = await self.load("nodes") or {}
        nodes = data.get("nodes", {})

        vector_ids = set(await vs.get_all_ids("setting_nodes"))
        json_ids = set(nodes.keys())

        missing_in_vector = [nid for nid in json_ids if nid not in vector_ids]
        missing_in_json = [vid for vid in vector_ids if vid not in json_ids]
        hash_mismatch = []

        for nid, node_data in nodes.items():
            if nid in vector_ids:
                meta = await vs.get_metadata("setting_nodes", nid)
                expected_hash = self._compute_hash(node_data)
                if meta.get("_content_hash") != expected_hash:
                    hash_mismatch.append(nid)

        return {
            "collection": "setting_nodes",
            "json_count": len(json_ids),
            "vector_count": len(vector_ids),
            "missing_in_vector": missing_in_vector,
            "missing_in_json": missing_in_json,
            "hash_mismatch": hash_mismatch,
        }
