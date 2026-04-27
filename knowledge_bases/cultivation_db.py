"""
knowledge_bases/cultivation_db.py

修为节点链存储 —— 递进链 + 分支结构。

职责边界：
    - 存储修为节点（CultivationNode），表达境界的递进关系和分支。
    - 管理修为体系的整体定义（CultivationChain）。
    - 支持链条遍历（正向/反向/双向）、分支查询。
    - 自动同步节点描述到向量库（cultivation_nodes collection）。

存储结构：
    workspace/{project_id}/cultivation/
    ├── nodes.json       # 修为节点字典
    └── chain.json       # 修为体系定义
"""

from typing import Optional
from collections import deque

from knowledge_bases.base_db import BaseDB
from core.schemas import CultivationNode, CultivationChain
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.cultivation_db')


class CultivationDB(BaseDB):
    """修为节点链存储。"""

    def __init__(self, project_id: str):
        super().__init__(project_id, "cultivation")

    # ── 节点 CRUD ──

    async def save_node(self, node: CultivationNode) -> bool:
        """保存修为节点。"""
        data = await self.load("nodes") or {"nodes": {}}
        nodes: dict = data.get("nodes", {})
        nodes[node.id] = node.model_dump()
        data["nodes"] = nodes
        return await self.save("nodes", data)

    async def get_node(self, node_id: str) -> Optional[CultivationNode]:
        """按 ID 获取修为节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        node_data = data["nodes"].get(node_id)
        if not node_data:
            return None
        try:
            return CultivationNode(**node_data)
        except Exception as e:
            logger.error(f"解析修为节点失败 {node_id}: {e}")
            return None

    async def get_node_by_name(self, name: str) -> Optional[CultivationNode]:
        """按名称获取修为节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return None
        for node_data in data["nodes"].values():
            if node_data.get("name") == name:
                try:
                    return CultivationNode(**node_data)
                except Exception:
                    continue
        return None

    async def delete_node(self, node_id: str) -> bool:
        """删除修为节点，自动清理链条引用。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return False
        nodes = data["nodes"]
        if node_id not in nodes:
            return False

        # 从父节点的 next_ids 中移除
        for other_node in nodes.values():
            if node_id in other_node.get("next_ids", []):
                other_node["next_ids"].remove(node_id)
            if other_node.get("parent_id") == node_id:
                other_node["parent_id"] = None
            if other_node.get("branch_from") == node_id:
                other_node["branch_from"] = None

        # 从 chain.json 的 branch_ids 中移除
        chain_data = await self.load("chain")
        if chain_data and node_id in chain_data.get("branch_ids", []):
            chain_data["branch_ids"].remove(node_id)
            await self.save("chain", chain_data)

        del nodes[node_id]
        return await self.save("nodes", data)

    async def list_all_nodes(self) -> list[CultivationNode]:
        """获取所有修为节点。"""
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        nodes = []
        for node_data in data["nodes"].values():
            try:
                nodes.append(CultivationNode(**node_data))
            except Exception:
                continue
        return nodes

    # ── 链条管理 ──

    async def save_chain(self, chain: CultivationChain) -> bool:
        """保存修为体系定义。"""
        return await self.save("chain", chain.model_dump())

    async def get_chain(self) -> Optional[CultivationChain]:
        """获取修为体系定义。"""
        data = await self.load("chain")
        if not data:
            return None
        try:
            return CultivationChain(**data)
        except Exception as e:
            logger.error(f"解析修为体系失败: {e}")
            return None

    # ── 链条遍历 ──

    async def traverse_chain(
        self,
        from_node_id: str,
        direction: str = "forward",
        steps: int = -1
    ) -> list[CultivationNode]:
        """
        沿修为链条递推遍历。

        Args:
            from_node_id: 起始节点 ID。
            direction: "forward" 向后（next_ids）、"backward" 向前（parent_id）、"both" 双向。
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
            if direction in ("forward", "both"):
                next_ids.extend(node.next_ids)
            if direction in ("backward", "both") and node.parent_id:
                next_ids.append(node.parent_id)

            for next_id in next_ids:
                if next_id not in visited:
                    visited.add(next_id)
                    next_node = await self.get_node(next_id)
                    if next_node:
                        result.append(next_node)
                        queue.append((next_id, current_step + 1))

        return result

    async def get_branches(self, node_id: str) -> list[CultivationNode]:
        """
        获取从指定节点分出的所有分支节点。

        Returns:
            所有 branch_from == node_id 的节点列表。
        """
        data = await self.load("nodes")
        if not data or "nodes" not in data:
            return []
        branches = []
        for node_data in data["nodes"].values():
            if node_data.get("branch_from") == node_id:
                try:
                    branches.append(CultivationNode(**node_data))
                except Exception:
                    continue
        return branches

    async def get_full_chain(self, root_id: str) -> dict:
        """
        获取以 root_id 为根的完整修为链条（含所有分支）。

        Returns:
            树形结构：{"node": CultivationNode, "children": [...], "branches": [...]}
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
                "children": [],
                "branches": []
            }
            for next_id in node.next_ids:
                child = await _build(next_id)
                if child:
                    result["children"].append(child)
            # 收集分支
            branches = await self.get_branches(node_id)
            for branch in branches:
                branch_tree = await _build(branch.id)
                if branch_tree:
                    result["branches"].append(branch_tree)
            return result

        return await _build(root_id) or {}

    async def compare_power(self, node_a_id: str, node_b_id: str) -> Optional[str]:
        """
        比较两个修为节点的战力。

        Returns:
            "a>b", "a<b", "a=b", 或 None（无法比较）。
        """
        node_a = await self.get_node(node_a_id)
        node_b = await self.get_node(node_b_id)
        if not node_a or not node_b:
            return None
        if node_a.power_scale is None or node_b.power_scale is None:
            # 回退到 tier 比较
            if node_a.tier < node_b.tier:
                return "a>b"
            elif node_a.tier > node_b.tier:
                return "a<b"
            return "a=b"
        if node_a.power_scale > node_b.power_scale:
            return "a>b"
        elif node_a.power_scale < node_b.power_scale:
            return "a<b"
        return "a=b"

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
            await vs.delete_except("cultivation_nodes", current_ids)

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
                        "tier": node_data.get("tier", 0),
                        "parent_id": node_data.get("parent_id", ""),
                        "_content_hash": self._compute_hash(node_data),
                    }
                })
            if items:
                await vs.upsert_batch("cultivation_nodes", items)
                logger.info(f"修为节点向量同步: {len(items)} 个节点")
        except Exception as e:
            log_exception(logger, e, "修为节点向量同步失败")

    @staticmethod
    def _node_to_text(node_data: dict) -> str:
        """将节点数据转换为可向量化的文本。"""
        parts = [f"修为境界: {node_data.get('name', '')}"]
        parts.append(f"类型: {node_data.get('node_type', '')}")
        parts.append(f"层级: {node_data.get('tier', '')}")
        parts.append(f"描述: {node_data.get('description', '')}")

        for ability in node_data.get("abilities", []):
            parts.append(f"能力: {ability}")

        for limitation in node_data.get("limitations", []):
            parts.append(f"限制: {limitation}")

        for prereq in node_data.get("prerequisites", []):
            parts.append(f"前置: {prereq}")

        if node_data.get("power_scale"):
            parts.append(f"战力标尺: {node_data['power_scale']}")

        return "\n".join(parts)

    # ── 同步校验与修复 ──

    async def repair_sync(self) -> dict:
        """强制重新同步所有修为节点到向量库（以 JSON 为准）。"""
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

        vector_ids = set(await vs.get_all_ids("cultivation_nodes"))
        json_ids = set(nodes.keys())

        missing_in_vector = [nid for nid in json_ids if nid not in vector_ids]
        missing_in_json = [vid for vid in vector_ids if vid not in json_ids]
        hash_mismatch = []

        for nid, node_data in nodes.items():
            if nid in vector_ids:
                meta = await vs.get_metadata("cultivation_nodes", nid)
                expected_hash = self._compute_hash(node_data)
                if meta.get("_content_hash") != expected_hash:
                    hash_mismatch.append(nid)

        return {
            "collection": "cultivation_nodes",
            "json_count": len(json_ids),
            "vector_count": len(vector_ids),
            "missing_in_vector": missing_in_vector,
            "missing_in_json": missing_in_json,
            "hash_mismatch": hash_mismatch,
        }
