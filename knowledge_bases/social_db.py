"""
knowledge_bases/social_db.py

社会制度/阶层存储 —— 层级树结构。

职责边界：
    - 存储社会节点（SocialNode），表达社会阶层、法律制度、文化传统的层级关系。
    - 管理社会体系的整体定义（SocialSystem）。
    - 支持层级遍历、分支查询。
    - 自动同步节点描述到向量库（social_nodes collection）。

存储结构：
    workspace/{project_id}/social/
    ├── nodes.json       # 社会节点字典
    └── system.json      # 社会体系定义
"""

from typing import Optional
from collections import deque

from knowledge_bases.base_db import BaseDB
from core.schemas import SocialNode, SocialSystem
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.social_db')


class SocialDB(BaseDB):
    """社会制度/阶层存储。"""

    def __init__(self, project_id: str):
        super().__init__(project_id, "social")

    # ── 节点 CRUD ──

    async def save_node(self, node: SocialNode) -> bool:
        """保存社会节点。"""
        data = await self.load("nodes") or {"nodes": {}}
        nodes: dict = data.get("nodes", {})
        nodes[node.id] = node.model_dump()
        data["nodes"] = nodes
        return await self.save("nodes", data)

    async def get_node(self, node_id: str) -> Optional[SocialNode]:
        """按 ID 获取社会节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        node_data = data["nodes"].get(node_id)
        if not node_data:
            return None
        try:
            return SocialNode(**node_data)
        except Exception as e:
            logger.error(f"解析社会节点失败 {node_id}: {e}")
            return None

    async def get_node_by_name(self, name: str) -> Optional[SocialNode]:
        """按名称获取社会节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        for node_data in data["nodes"].values():
            if node_data.get("name") == name:
                try:
                    return SocialNode(**node_data)
                except Exception:
                    continue
        return None

    async def delete_node(self, node_id: str) -> bool:
        """删除社会节点，自动清理层级引用。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return False
        nodes = data["nodes"]
        if node_id not in nodes:
            return False

        # 从父节点的 sub_ids 中移除
        for other_node in nodes.values():
            if node_id in other_node.get("sub_ids", []):
                other_node["sub_ids"].remove(node_id)
            if other_node.get("parent_id") == node_id:
                other_node["parent_id"] = None

        del nodes[node_id]
        return await self.save("nodes", data)

    async def list_all_nodes(self) -> list[SocialNode]:
        """获取所有社会节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        nodes = []
        for node_data in data["nodes"].values():
            try:
                nodes.append(SocialNode(**node_data))
            except Exception:
                continue
        return nodes

    # ── 体系管理 ──

    async def save_system(self, system: SocialSystem) -> bool:
        """保存社会体系定义。"""
        return await self.save("system", system.model_dump())

    async def get_system(self) -> Optional[SocialSystem]:
        """获取社会体系定义。"""
        data = await self.load("system")
        if not data:
            return None
        try:
            return SocialSystem(**data)
        except Exception as e:
            logger.error(f"解析社会体系失败: {e}")
            return None

    # ── 层级遍历 ──

    async def traverse_hierarchy(
        self,
        from_node_id: str,
        direction: str = "down",
        steps: int = -1
    ) -> list[SocialNode]:
        """
        沿社会层级递推遍历。

        Args:
            from_node_id: 起始节点 ID。
            direction: "down" 向下（sub_ids）、"up" 向上（parent_id）、"both" 双向。
            steps: 递推步数，-1 表示走到尽头。

        Returns:
            遍历路径上的节点列表（不含起始节点）。
        """
        result = []
        visited = {from_node_id}
        queue = deque([(from_node_id, 0)])

        while queue:
            current_id, current_step = queue.popleft()
            if steps != -1 and current_step >= steps:
                continue

            node = await self.get_node(current_id)
            if not node:
                continue

            next_ids = []
            if direction in ("down", "both"):
                next_ids.extend(node.sub_ids)
            if direction in ("up", "both") and node.parent_id:
                next_ids.append(node.parent_id)

            for next_id in next_ids:
                if next_id not in visited:
                    visited.add(next_id)
                    next_node = await self.get_node(next_id)
                    if next_node:
                        result.append(next_node)
                        queue.append((next_id, current_step + 1))

        return result

    async def get_subtree(self, root_id: str) -> dict:
        """
        获取以 root_id 为根的完整子树。

        Returns:
            树形结构：{"node": SocialNode, "children": [...]}
        """
        root = await self.get_node(root_id)
        if not root:
            return {}

        async def _build(node_id: str) -> Optional[dict]:
            node = await self.get_node(node_id)
            if not node:
                return None
            result = {
                "node": node.model_dump(),
                "children": []
            }
            for sub_id in node.sub_ids:
                child = await _build(sub_id)
                if child:
                    result["children"].append(child)
            return result

        return await _build(root_id) or {}

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
            await vs.delete_except("social_nodes", current_ids)

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
                        "_content_hash": self._compute_hash(node_data),
                    }
                })
            if items:
                await vs.upsert_batch("social_nodes", items)
                logger.info(f"社会节点向量同步: {len(items)} 个节点")
        except Exception as e:
            log_exception(logger, e, "社会节点向量同步失败")

    @staticmethod
    def _node_to_text(node_data: dict) -> str:
        """将节点数据转换为可向量化的文本。"""
        parts = [f"社会节点: {node_data.get('name', '')}"]
        parts.append(f"类型: {node_data.get('node_type', '')}")
        parts.append(f"描述: {node_data.get('description', '')}")

        if node_data.get("influence_scope"):
            parts.append(f"影响范围: {node_data['influence_scope']}")

        for privilege in node_data.get("privileges", []):
            parts.append(f"特权: {privilege}")

        for obligation in node_data.get("obligations", []):
            parts.append(f"义务: {obligation}")

        return "\n".join(parts)

    # ── 同步校验与修复 ──

    async def repair_sync(self) -> dict:
        """强制重新同步所有社会节点到向量库（以 JSON 为准）。"""
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

        vector_ids = set(await vs.get_all_ids("social_nodes"))
        json_ids = set(nodes.keys())

        missing_in_vector = [nid for nid in json_ids if nid not in vector_ids]
        missing_in_json = [vid for vid in vector_ids if vid not in json_ids]
        hash_mismatch = []

        for nid, node_data in nodes.items():
            if nid in vector_ids:
                meta = await vs.get_metadata("social_nodes", nid)
                expected_hash = self._compute_hash(node_data)
                if meta.get("_content_hash") != expected_hash:
                    hash_mismatch.append(nid)

        return {
            "collection": "social_nodes",
            "json_count": len(json_ids),
            "vector_count": len(vector_ids),
            "missing_in_vector": missing_in_vector,
            "missing_in_json": missing_in_json,
            "hash_mismatch": hash_mismatch,
        }
