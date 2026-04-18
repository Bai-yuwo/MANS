"""
knowledge_bases/base_db.py
知识库基类（异步版本）

设计原则：
1. 通用接口：定义所有知识库的通用操作
2. 文件存储：JSON 文件作为主数据源
3. 原子写入：先写临时文件再 rename，防止数据损坏
4. 异步安全：使用 asyncio.Lock 保证并发安全
5. 向量索引：自动同步到向量存储
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

import aiofiles

from core.config import get_config
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.base_db')

# ============================================================
# 全局文件路径锁注册表（进程内单例）
# ============================================================

class FileLockRegistry:
    """
    基于文件路径的 asyncio.Lock 注册表

    确保不同 BaseDB 实例操作同一文件时，共享同一把锁，
    防止并发写入导致 JSON 损坏或丢失更新。
    """
    _locks: dict[str, asyncio.Lock] = {}
    _meta_lock = asyncio.Lock()

    @classmethod
    async def acquire(cls, file_path: str) -> asyncio.Lock:
        """获取指定文件路径的锁"""
        async with cls._meta_lock:
            if file_path not in cls._locks:
                cls._locks[file_path] = asyncio.Lock()
            return cls._locks[file_path]


class BaseDB:
    """
    知识库基类（异步版本）

    所有具体知识库继承此类，获得通用读写能力

    使用示例：
        class CharacterDB(BaseDB):
            def __init__(self, project_id: str):
                super().__init__(project_id, "characters")
    """

    def __init__(self, project_id: str, db_name: str):
        """
        初始化知识库

        Args:
            project_id: 项目 ID
            db_name: 知识库名称（对应子目录名）
        """
        self.project_id = project_id
        self.db_name = db_name

        config = get_config()
        self.base_path = Path(config.WORKSPACE_PATH) / project_id
        self.db_path = self.base_path / db_name

        # 确保目录存在
        self.db_path.mkdir(parents=True, exist_ok=True)

        # 实例级锁（仅用于本实例内部并发控制，跨实例通过 FileLockRegistry）
        self._lock = asyncio.Lock()
    
    def _get_file_path(self, key: str) -> Path:
        """获取数据文件路径"""
        return self.db_path / f"{key}.json"
    
    async def load(self, key: str) -> Optional[dict]:
        """
        加载指定 key 的数据（异步）

        Args:
            key: 数据标识

        Returns:
            数据字典，不存在则返回 None
        """
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None

        # 使用全局文件路径锁，确保跨实例并发安全
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    return json.loads(content)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"加载数据失败 {key}: {e}")
                return None

    async def save(self, key: str, data: dict) -> bool:
        """
        保存数据到指定 key（异步原子写入）

        防止丢失更新：在写入前重读最新数据，执行深度合并。

        Args:
            key: 数据标识
            data: 要保存的数据

        Returns:
            是否保存成功
        """
        file_path = self._get_file_path(key)
        temp_path = file_path.with_suffix('.tmp')

        # 使用全局文件路径锁，确保跨实例并发安全
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            try:
                # 如果文件已存在，先读取最新数据，进行深度合并（防止丢失更新）
                if file_path.exists():
                    try:
                        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                            latest = json.loads(await f.read())
                        # 深度合并：data 的字段覆盖 latest 的字段，但保留 latest 中未被覆盖的字段
                        merged = self._deep_merge(latest, data)
                        data = merged
                    except (json.JSONDecodeError, IOError):
                        # 读取失败则继续使用传入的 data
                        pass

                # 添加更新时间
                data['_updated_at'] = datetime.now().isoformat()

                # 写入临时文件
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))

                # 原子替换（os.replace 在 Python 3.3+ 支持跨文件系统原子操作）
                import os
                os.replace(str(temp_path), str(file_path))
                return True

            except IOError as e:
                logger.error(f"保存数据失败 {key}: {e}")
                # 清理临时文件
                if temp_path.exists():
                    temp_path.unlink()
                return False

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """
        深度合并两个字典

        override 中的字段覆盖 base 中的同名字段，
        但 base 中未被覆盖的字段保留。
        对于 dict 类型字段，递归合并。
        对于 list 类型字段，若元素为 dict 且包含 'scene_index' 或 'id'
        等标识，按标识匹配合并；否则 override 优先（直接替换）。
        """
        result = dict(base)
        for key, value in override.items():
            if key.startswith('_'):
                # 内部字段（如 _updated_at）直接覆盖
                result[key] = value
            elif isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = BaseDB._deep_merge(result[key], value)
            elif isinstance(value, list) and isinstance(result.get(key), list):
                result[key] = BaseDB._deep_merge_lists(result.get(key, []), value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _deep_merge_lists(base_list: list, override_list: list) -> list:
        """
        深度合并两个列表（按标识匹配，防丢失更新）

        规则：
        1. 若列表元素为 dict，尝试用 'scene_index'、'id'、'index' 作为标识键匹配。
        2. 匹配到的元素递归合并（保留 base 中未被 override 覆盖的字段）。
        3. override 中新增的标识直接追加。
        4. 若无可用标识键，回退到 override 优先（直接替换）。
        """
        if not override_list:
            return list(base_list)

        # 检查是否有可用标识键
        first = override_list[0]
        id_key = None
        if isinstance(first, dict):
            for candidate in ('scene_index', 'id', 'index'):
                if candidate in first:
                    id_key = candidate
                    break

        if id_key is None:
            # 无标识键，回退到 override 优先
            return list(override_list)

        # 按标识键建立 base 索引
        base_index: dict[Any, dict] = {}
        for item in base_list:
            if isinstance(item, dict) and id_key in item:
                base_index[item[id_key]] = item

        merged: list = []
        seen_ids: set = set()

        for item in override_list:
            if isinstance(item, dict) and id_key in item:
                item_id = item[id_key]
                seen_ids.add(item_id)
                if item_id in base_index:
                    # 匹配到，递归合并 dict 元素
                    merged.append(BaseDB._deep_merge(base_index[item_id], item))
                else:
                    # 新增项
                    merged.append(dict(item))
            else:
                merged.append(item)

        # 保留 base 中未被 override 覆盖的项（追加到末尾）
        for item in base_list:
            if isinstance(item, dict) and id_key in item:
                if item[id_key] not in seen_ids:
                    merged.append(dict(item))
            else:
                merged.append(item)

        return merged
    
    async def _load_no_lock(self, key: str) -> Optional[dict]:
        """无锁加载（调用方必须已持有 file_lock）"""
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"无锁加载数据失败 {key}: {e}")
            return None

    async def _save_no_lock(self, key: str, data: dict) -> bool:
        """无锁保存（调用方必须已持有 file_lock）"""
        file_path = self._get_file_path(key)
        temp_path = file_path.with_suffix('.tmp')
        try:
            if file_path.exists():
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        latest = json.loads(await f.read())
                    merged = self._deep_merge(latest, data)
                    data = merged
                except (json.JSONDecodeError, IOError):
                    pass
            data['_updated_at'] = datetime.now().isoformat()
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            import os
            os.replace(str(temp_path), str(file_path))
            return True
        except IOError as e:
            logger.error(f"无锁保存数据失败 {key}: {e}")
            if temp_path.exists():
                temp_path.unlink()
            return False

    async def append(self, key: str, item: Any) -> bool:
        """
        向数组字段追加条目（异步，知识库只增不覆盖原则）

        使用显式文件锁包裹整个 读取-修改-写入 过程，确保原子性。
        """
        file_path = self._get_file_path(key)
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            data = await self._load_no_lock(key) or {}
            if 'items' not in data:
                data['items'] = []
            if isinstance(item, dict):
                item['_added_at'] = datetime.now().isoformat()
            data['items'].append(item)
            return await self._save_no_lock(key, data)

    async def update_field(self, key: str, field: str, value: Any) -> bool:
        """
        更新指定字段（异步）

        使用显式文件锁包裹整个 读取-修改-写入 过程，确保原子性。
        """
        file_path = self._get_file_path(key)
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            data = await self._load_no_lock(key) or {}
            data[field] = value
            return await self._save_no_lock(key, data)
    
    async def list_keys(self) -> list[str]:
        """
        列出所有数据 key（异步）
        
        Returns:
            key 列表（不含 .json 后缀）
        """
        if not self.db_path.exists():
            return []
        
        # 目录遍历不需要锁
        keys = []
        for file_path in self.db_path.glob('*.json'):
            keys.append(file_path.stem)
        return sorted(keys)
    
    async def delete(self, key: str) -> bool:
        """
        删除指定 key 的数据（异步）

        Args:
            key: 数据标识

        Returns:
            是否删除成功
        """
        file_path = self._get_file_path(key)
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            try:
                if file_path.exists():
                    file_path.unlink()
                return True
            except IOError as e:
                logger.error(f"删除数据失败 {key}: {e}")
                return False
