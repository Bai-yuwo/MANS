"""
knowledge_bases/faction_db.py

势力节点网存储 —— 关系网 + 层级结构。

职责边界：
    - 存储势力节点（FactionNode），表达势力间的敌对/同盟/隶属等关系。
    - 支持势力关系网展开、按地理区域查询势力分布。
    - 自动同步节点描述到向量库（faction_nodes collection）。

存储结构：
    workspace/{project_id}/factions/
    └── nodes.json
        {
          "nodes": {"fac_xxx": {...FactionNode...}, ...},
          "root_ids": ["fac_xxx", ...],
          "_updated_at": "..."
        }
"""

from typing import Optional
from collections import deque

from knowledge_bases.base_db import BaseDB
from core.schemas import FactionNode, FactionRelation
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.faction_db')


class FactionDB(BaseDB):
    """势力节点网存储。"""

    def __init__(self, project_id: str):
        super().__init__(project_id, "factions")

    # ── 基础 CRUD ──

    async def save_node(self, node: FactionNode) -> bool:
        """保存势力节点，自动维护层级关系一致性。"""
        data = await self.load("nodes") or {"nodes": {}, "root_ids": []}
        nodes: dict = data.get("nodes", {})
        root_ids: list = data.get("root_ids", [])

        old_node_data = nodes.get(node.id)
        old_parent_id = old_node_data.get("parent_faction_id") if old_node_data else None

        nodes[node.id] = node.model_dump()

        # 维护 parent_faction_id ↔ sub_faction_ids 双向引用
        if old_parent_id and old_parent_id != node.parent_faction_id:
            old_parent = nodes.get(old_parent_id)
            if old_parent and node.id in old_parent.get("sub_faction_ids", []):
                old_parent["sub_faction_ids"].remove(node.id)

        if node.parent_faction_id:
            parent = nodes.get(node.parent_faction_id)
            if parent and node.id not in parent.get("sub_faction_ids", []):
                parent["sub_faction_ids"].append(node.id)
            if node.id in root_ids:
                root_ids.remove(node.id)
        else:
            if node.id not in root_ids:
                root_ids.append(node.id)

        data["nodes"] = nodes
        data["root_ids"] = root_ids
        return await self.save("nodes", data)

    async def get_node(self, node_id: str) -> Optional[FactionNode]:
        """按 ID 获取势力节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        node_data = data["nodes"].get(node_id)
        if not node_data:
            return None
        try:
            return FactionNode(**node_data)
        except Exception as e:
            logger.error(f"解析势力节点失败 {node_id}: {e}")
            return None

    async def get_node_by_name(self, name: str) -> Optional[FactionNode]:
        """按名称获取势力节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        for node_data in data["nodes"].values():
            if node_data.get("name") == name:
                try:
                    return FactionNode(**node_data)
                except Exception:
                    continue
        return None

    async def delete_node(self, node_id: str) -> bool:
        """删除势力节点，自动清理关系引用。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return False
        nodes = data["nodes"]
        node_data = nodes.get(node_id)
        if not node_data:
            return False

        # 从父节点的 sub_faction_ids 中移除
        parent_id = node_data.get("parent_faction_id")
        if parent_id and parent_id in nodes:
            sub_ids = nodes[parent_id].get("sub_faction_ids", [])
            if node_id in sub_ids:
                sub_ids.remove(node_id)

        # 子节点提升为根节点
        for sub_id in node_data.get("sub_faction_ids", []):
            if sub_id in nodes:
                nodes[sub_id]["parent_faction_id"] = None
                if sub_id not in data.get("root_ids", []):
                    data.setdefault("root_ids", []).append(sub_id)

        # 从其他势力的 relations 中移除指向此节点的关系
        for other_node in nodes.values():
            if "relations" in other_node:
                other_node["relations"] = [
                    r for r in other_node["relations"]
                    if r.get("target_faction_id") != node_id
                ]

        del nodes[node_id]
        if node_id in data.get("root_ids", []):
            data["root_ids"].remove(node_id)

        return await self.save("nodes", data)

    async def list_all_nodes(self) -> list[FactionNode]:
        """获取所有势力节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        nodes = []
        for node_data in data["nodes"].values():
            try:
                nodes.append(FactionNode(**node_data))
            except Exception:
                continue
        return nodes

    # ── 图遍历 ──

    async def get_network(self, center_id: Optional[str] = None, depth: int = 1) -> dict:
        """
        展开势力关系网。

        Args:
            center_id: 中心节点 ID，None 则展开全部根节点。
            depth: 关系展开深度（沿 relations 遍历的层数）。

        Returns:
            {"nodes": {...}, "edges": [...]}
        """
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return {"nodes": {}, "edges": []}

        all_nodes = data["nodes"]

        if center_id is None:
            # 展开所有根节点及其子树
            root_ids = data.get("root_ids", [])
            included_ids = set()
            for root_id in root_ids:
                self._collect_subtree(all_nodes, root_id, included_ids)
            center_id = None  # 标记为"全景模式"
        else:
            if center_id not in all_nodes:
                return {"nodes": {}, "edges": []}
            included_ids = {center_id}
            # BFS 沿 relations 展开
            queue = deque([(center_id, 0)])
            while queue:
                current_id, current_depth = queue.popleft()
                if current_depth >= depth:
                    continue
                node_data = all_nodes.get(current_id)
                if not node_data:
                    continue
                for rel in node_data.get("relations", []):
                    target_id = rel.get("target_faction_id")
                    if target_id and target_id in all_nodes and target_id not in included_ids:
                        included_ids.add(target_id)
                        queue.append((target_id, current_depth + 1))

        nodes_subset = {nid: all_nodes[nid] for nid in included_ids if nid in all_nodes}
        edges = []
        for nid, node_data in nodes_subset.items():
            for rel in node_data.get("relations", []):
                target_id = rel.get("target_faction_id")
                if target_id in nodes_subset:
                    edges.append({
                        "source": nid,
                        "target": target_id,
                        "relation_type": rel.get("relation_type", ""),
                        "intensity": rel.get("intensity", ""),
                        "description": rel.get("description", ""),
                    })

        return {"nodes": nodes_subset, "edges": edges, "center_id": center_id}

    def _collect_subtree(self, all_nodes: dict, node_id: str, included: set) -> None:
        """递归收集子树中的所有节点 ID。"""
        if node_id in included or node_id not in all_nodes:
            return
        included.add(node_id)
        for sub_id in all_nodes[node_id].get("sub_faction_ids", []):
            self._collect_subtree(all_nodes, sub_id, included)

    async def get_factions_by_territory(self, geo_node_id: str) -> list[FactionNode]:
        """查询控制/存在于指定地理节点的所有势力。"""
        nodes = await self.list_all_nodes()
        result = []
        for node in nodes:
            if geo_node_id in node.controlled_territories:
                result.append(node)
        return result

    async def find_path(self, from_id: str, to_id: str, relation_types: Optional[list[str]] = None) -> list[FactionNode]:
        """
        查找两个势力之间的关系路径（BFS）。

        Args:
            relation_types: 限定可经过的关系类型，None 表示不限。

        Returns:
            路径上的势力节点列表（含两端），不可达返回空列表。
        """
        if from_id == to_id:
            node = await self.get_node(from_id)
            return [node] if node else []

        queue = deque([(from_id, [from_id])])
        visited = {from_id}

        while queue:
            current_id, path = queue.popleft()
            node = await self.get_node(current_id)
            if not node:
                continue

            for rel in node.relations:
                if relation_types and rel.relation_type not in relation_types:
                    continue
                target_id = rel.target_faction_id
                if target_id in visited:
                    continue
                if target_id == to_id:
                    full_path = path + [target_id]
                    return [n for n in [await self.get_node(nid) for nid in full_path] if n]
                visited.add(target_id)
                queue.append((target_id, path + [target_id]))

        return []

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
            await vs.delete_except("faction_nodes", current_ids)

            # 2. 同步当前节点
            items = []
            for node_id, node_data in data["nodes"].items():
                text = self._node_to_text(node_data)
                items.append({
                    "id": node_id,
                    "text": text,
                    "metadata": {
                        "name": node_data.get("name", ""),
                        "type": node_data.get("node_type", ""),
                        "stance": node_data.get("stance", ""),
                        "_content_hash": self._compute_hash(node_data),
                    }
                })
            if items:
                await vs.upsert_batch("faction_nodes", items)
                logger.info(f"势力节点向量同步: {len(items)} 个节点")
        except Exception as e:
            log_exception(logger, e, "势力节点向量同步失败")

    @staticmethod
    def _node_to_text(node_data: dict) -> str:
        """将节点数据转换为可向量化的文本。"""
        parts = [f"势力: {node_data.get('name', '')}"]
        parts.append(f"类型: {node_data.get('node_type', '')}")
        parts.append(f"立场: {node_data.get('stance', '')}")
        parts.append(f"描述: {node_data.get('description', '')}")

        if node_data.get("leader"):
            parts.append(f"领袖: {node_data['leader']}")

        for rel in node_data.get("relations", []):
            rel_type = rel.get("relation_type", "")
            target = rel.get("target_faction_id", "")
            intensity = rel.get("intensity", "")
            desc = rel.get("description", "")
            rel_text = f"关系[{rel_type}]->{target} 强度:{intensity}"
            if desc:
                rel_text += f" ({desc})"
            parts.append(rel_text)

        return "\n".join(parts)

    # ── 同步校验与修复 ──

    async def repair_sync(self) -> dict:
        """强制重新同步所有势力节点到向量库（以 JSON 为准）。"""
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

        vector_ids = set(await vs.get_all_ids("faction_nodes"))
        json_ids = set(nodes.keys())

        missing_in_vector = [nid for nid in json_ids if nid not in vector_ids]
        missing_in_json = [vid for vid in vector_ids if vid not in json_ids]
        hash_mismatch = []

        for nid, node_data in nodes.items():
            if nid in vector_ids:
                meta = await vs.get_metadata("faction_nodes", nid)
                expected_hash = self._compute_hash(node_data)
                if meta.get("_content_hash") != expected_hash:
                    hash_mismatch.append(nid)

        return {
            "collection": "faction_nodes",
            "json_count": len(json_ids),
            "vector_count": len(vector_ids),
            "missing_in_vector": missing_in_vector,
            "missing_in_json": missing_in_json,
            "hash_mismatch": hash_mismatch,
        }
