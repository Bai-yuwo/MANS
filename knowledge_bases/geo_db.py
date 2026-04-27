"""
knowledge_bases/geo_db.py

地理节点图存储 —— 层级树 + 空间连接图。

职责边界：
    - 存储地理节点（GeoNode），表达"大陆→区域→城邦→据点"的层级结构。
    - 管理节点间的空间连接（相邻、通道、传送、边界等）。
    - 支持层级树展开、递推遍历、最短路径查询。
    - 自动同步节点描述到向量库（geo_nodes collection），支持语义检索。

存储结构：
    workspace/{project_id}/geography/
    └── nodes.json
        {
          "nodes": {"geo_xxx": {...GeoNode...}, ...},
          "root_ids": ["geo_xxx", ...],
          "_updated_at": "..."
        }

关系一致性：
    save_node 自动维护 parent_id ↔ child_ids 的双向引用：
    - 若节点 parent_id 改变，自动从原父节点的 child_ids 中移除，
      并向新父节点的 child_ids 中添加。
"""

from typing import Optional
from collections import deque

from knowledge_bases.base_db import BaseDB
from core.schemas import GeoNode, GeoConnection
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.geo_db')


class GeoDB(BaseDB):
    """地理节点图存储。"""

    def __init__(self, project_id: str):
        super().__init__(project_id, "geography")

    # ── 基础 CRUD ──

    async def save_node(self, node: GeoNode) -> bool:
        """
        保存地理节点，自动维护层级关系一致性。

        关系维护：
            若节点的 parent_id 发生变化，自动更新原父节点和新父节点的 child_ids。
        """
        data = await self.load("nodes") or {"nodes": {}, "root_ids": []}
        nodes: dict = data.get("nodes", {})
        root_ids: list = data.get("root_ids", [])

        old_node_data = nodes.get(node.id)
        old_parent_id = old_node_data.get("parent_id") if old_node_data else None

        # 更新节点自身
        nodes[node.id] = node.model_dump()

        # 维护 parent_id ↔ child_ids 双向引用
        if old_parent_id and old_parent_id != node.parent_id:
            old_parent = nodes.get(old_parent_id)
            if old_parent and node.id in old_parent.get("child_ids", []):
                old_parent["child_ids"].remove(node.id)

        if node.parent_id:
            parent = nodes.get(node.parent_id)
            if parent and node.id not in parent.get("child_ids", []):
                parent["child_ids"].append(node.id)
            # 此节点不可能是根节点
            if node.id in root_ids:
                root_ids.remove(node.id)
        else:
            # 无 parent_id → 根节点
            if node.id not in root_ids:
                root_ids.append(node.id)

        data["nodes"] = nodes
        data["root_ids"] = root_ids
        return await self.save("nodes", data)

    async def get_node(self, node_id: str) -> Optional[GeoNode]:
        """按 ID 获取地理节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        node_data = data["nodes"].get(node_id)
        if not node_data:
            return None
        try:
            return GeoNode(**node_data)
        except Exception as e:
            logger.error(f"解析地理节点失败 {node_id}: {e}")
            return None

    async def get_node_by_name(self, name: str) -> Optional[GeoNode]:
        """按名称获取地理节点（精确匹配）。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        for node_data in data["nodes"].values():
            if node_data.get("name") == name:
                try:
                    return GeoNode(**node_data)
                except Exception:
                    continue
        return None

    async def delete_node(self, node_id: str) -> bool:
        """
        删除地理节点，自动清理父子引用。
        """
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return False
        nodes = data["nodes"]
        node_data = nodes.get(node_id)
        if not node_data:
            return False

        # 从父节点的 child_ids 中移除
        parent_id = node_data.get("parent_id")
        if parent_id and parent_id in nodes:
            child_ids = nodes[parent_id].get("child_ids", [])
            if node_id in child_ids:
                child_ids.remove(node_id)

        # 将所有子节点的 parent_id 置空（提升为根节点）
        for child_id in node_data.get("child_ids", []):
            if child_id in nodes:
                nodes[child_id]["parent_id"] = None
                if child_id not in data.get("root_ids", []):
                    data.setdefault("root_ids", []).append(child_id)

        # 从其他节点的 connections 中移除指向此节点的连接
        for other_node in nodes.values():
            if "connections" in other_node:
                other_node["connections"] = [
                    c for c in other_node["connections"]
                    if c.get("target_id") != node_id
                ]

        del nodes[node_id]
        if node_id in data.get("root_ids", []):
            data["root_ids"].remove(node_id)

        return await self.save("nodes", data)

    async def list_all_nodes(self) -> list[GeoNode]:
        """获取所有地理节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        nodes = []
        for node_data in data["nodes"].values():
            try:
                nodes.append(GeoNode(**node_data))
            except Exception as e:
                logger.warning(f"跳过解析失败的地理节点: {e}")
        return nodes

    # ── 图遍历 ──

    async def get_subtree(self, root_id: str, depth: int = -1) -> dict:
        """
        展开以 root_id 为根的地理子树。

        Args:
            root_id: 根节点 ID。
            depth: 展开深度，-1 表示不限深度。

        Returns:
            树形结构字典：{"node": GeoNode, "children": [...]}
        """
        root = await self.get_node(root_id)
        if not root:
            return {}

        async def _build(node_id: str, current_depth: int) -> Optional[dict]:
            node = await self.get_node(node_id)
            if not node:
                return None
            result = {"node": node.model_dump(), "children": []}
            if depth == -1 or current_depth < depth:
                for child_id in node.child_ids:
                    child = await _build(child_id, current_depth + 1)
                    if child:
                        result["children"].append(child)
            return result

        return await _build(root_id, 0) or {}

    async def get_full_graph(self, max_depth: int = -1) -> list[dict]:
        """
        获取完整的地理图（从所有根节点展开）。

        Returns:
            多棵子树的列表，每棵结构同 get_subtree。
        """
        data = await self.load("nodes") or {"root_ids": []}
        root_ids = data.get("root_ids", [])
        trees = []
        for root_id in root_ids:
            tree = await self.get_subtree(root_id, max_depth)
            if tree:
                trees.append(tree)
        return trees

    async def traverse(self, start_id: str, direction: str = "down", steps: int = 1) -> list[GeoNode]:
        """
        沿指定方向递推遍历地理节点。

        Args:
            start_id: 起始节点 ID。
            direction: "down" 向下（子节点）、"up" 向上（父节点）、"lateral" 横向（连接节点）。
            steps: 递推步数。

        Returns:
            遍历路径上的节点列表（不含起始节点）。
        """
        result = []
        visited = {start_id}
        queue = deque([(start_id, 0)])

        while queue:
            current_id, current_step = queue.popleft()
            if current_step >= steps:
                continue

            node = await self.get_node(current_id)
            if not node:
                continue

            next_ids = []
            if direction == "down":
                next_ids = node.child_ids
            elif direction == "up":
                if node.parent_id:
                    next_ids = [node.parent_id]
            elif direction == "lateral":
                next_ids = [c.target_id for c in node.connections]

            for next_id in next_ids:
                if next_id not in visited:
                    visited.add(next_id)
                    next_node = await self.get_node(next_id)
                    if next_node:
                        result.append(next_node)
                        queue.append((next_id, current_step + 1))

        return result

    async def find_path(self, from_id: str, to_id: str) -> list[GeoNode]:
        """
        查找两个地理节点之间的最短路径（BFS）。

        路径同时考虑 parent-child 关系和 connections 关系。

        Returns:
            从 from_id 到 to_id 的路径节点列表（含两端），不可达返回空列表。
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

            # 收集所有邻居
            neighbors = set()
            if node.parent_id:
                neighbors.add(node.parent_id)
            neighbors.update(node.child_ids)
            neighbors.update(c.target_id for c in node.connections)

            for neighbor_id in neighbors:
                if neighbor_id in visited:
                    continue
                if neighbor_id == to_id:
                    full_path = path + [neighbor_id]
                    return [n for n in [await self.get_node(nid) for nid in full_path] if n]
                visited.add(neighbor_id)
                queue.append((neighbor_id, path + [neighbor_id]))

        return []

    async def get_nodes_by_faction(self, faction_id: str) -> list[GeoNode]:
        """查询某势力控制/存在的所有地理节点。"""
        nodes = await self.list_all_nodes()
        result = []
        for node in nodes:
            for presence in node.faction_presence:
                if presence.faction_id == faction_id:
                    result.append(node)
                    break
        return result

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
            await vs.delete_except("geo_nodes", current_ids)

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
                        "depth_level": node_data.get("depth_level", 0),
                        "parent_id": node_data.get("parent_id", ""),
                        "_content_hash": self._compute_hash(node_data),
                    }
                })
            if items:
                await vs.upsert_batch("geo_nodes", items)
                logger.info(f"地理节点向量同步: {len(items)} 个节点")
        except Exception as e:
            log_exception(logger, e, "地理节点向量同步失败")

    @staticmethod
    def _node_to_text(node_data: dict) -> str:
        """将节点数据转换为可向量化的文本。"""
        parts = [f"地理节点: {node_data.get('name', '')}"]
        parts.append(f"类型: {node_data.get('node_type', '')}")
        parts.append(f"描述: {node_data.get('description', '')}")

        if node_data.get("scale"):
            parts.append(f"规模: {node_data['scale']}")

        for conn in node_data.get("connections", []):
            rel = conn.get("relation_type", "")
            target = conn.get("target_id", "")
            dist = conn.get("distance", "")
            desc = conn.get("description", "")
            conn_text = f"连接[{rel}]->{target}"
            if dist:
                conn_text += f" 距离:{dist}"
            if desc:
                conn_text += f" ({desc})"
            parts.append(conn_text)

        for presence in node_data.get("faction_presence", []):
            parts.append(
                f"势力分布: {presence.get('faction_name', '')} "
                f"({presence.get('strength', '')})"
            )

        return "\n".join(parts)

    # ── 同步校验与修复 ──

    async def repair_sync(self) -> dict:
        """
        强制重新同步所有地理节点到向量库（以 JSON 为准）。

        用途：
            当检测到向量库与 JSON 不一致时，调用此方法强制重建
            geo_nodes collection 的全部向量。

        Returns:
            {"repaired": 实际同步的节点数量}
        """
        data = await self.load("nodes") or {}
        if "nodes" not in data:
            return {"repaired": 0}
        await self._after_save("nodes", data)
        return {"repaired": len(data["nodes"])}

    async def verify_sync(self) -> dict:
        """
        校验 JSON 与向量库的内容一致性。

        检测维度：
            - missing_in_vector: JSON 中有但向量库中无的节点
            - missing_in_json: 向量库中有但 JSON 中无的节点（残留）
            - hash_mismatch: 双方都有但内容哈希不一致的节点

        Returns:
            差异报告字典。所有差异均可通过 repair_sync() 自动修复（以 JSON 为准）。
        """
        try:
            from vector_store.store import VectorStore
            vs = VectorStore(self.project_id)
        except Exception as e:
            return {"error": f"VectorStore 初始化失败: {e}"}

        data = await self.load("nodes") or {}
        nodes = data.get("nodes", {})

        vector_ids = set(await vs.get_all_ids("geo_nodes"))
        json_ids = set(nodes.keys())

        missing_in_vector = [nid for nid in json_ids if nid not in vector_ids]
        missing_in_json = [vid for vid in vector_ids if vid not in json_ids]
        hash_mismatch = []

        for nid, node_data in nodes.items():
            if nid in vector_ids:
                meta = await vs.get_metadata("geo_nodes", nid)
                expected_hash = self._compute_hash(node_data)
                if meta.get("_content_hash") != expected_hash:
                    hash_mismatch.append(nid)

        return {
            "collection": "geo_nodes",
            "json_count": len(json_ids),
            "vector_count": len(vector_ids),
            "missing_in_vector": missing_in_vector,
            "missing_in_json": missing_in_json,
            "hash_mismatch": hash_mismatch,
        }
