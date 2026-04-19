"""
knowledge_bases/base_db.py

知识库基类，为所有具体知识库提供通用的异步文件读写能力。

职责边界：
    - 封装 JSON 文件的异步读写操作，统一使用 aiofiles 实现非阻塞 I/O。
    - 实现原子写入机制（临时文件 + os.replace），防止写入过程中进程崩溃导致数据损坏。
    - 提供深度合并（deep merge）能力，解决并发读写场景下的"丢失更新"问题。
    - 通过 FileLockRegistry 实现基于文件路径的进程内全局锁，保证同一文件不会被并发修改。
    - 提供数组追加（append）和字段更新（update_field）等高级操作。

原子写入机制：
    所有写入操作遵循以下流程：
        1. 将数据写入以 .tmp 为后缀的临时文件。
        2. 使用 os.replace()（POSIX 原子操作）将临时文件重命名为目标文件。
    即使在第 1 步和第 2 步之间进程崩溃，原目标文件仍然完好无损。

深度合并策略：
    _deep_merge() 在写入前重新读取磁盘上的最新数据，将内存中的修改与磁盘数据合并：
        - 标量字段：内存值覆盖磁盘值。
        - 字典字段：递归合并，保留磁盘上未被覆盖的子字段。
        - 列表字段：若元素包含标识键（id / scene_index / index），按标识匹配合并；
          否则直接替换（override 优先）。
    这确保了并发修改不会互相覆盖。

典型用法（子类继承）：
    class CharacterDB(BaseDB):
        def __init__(self, project_id: str):
            super().__init__(project_id, "characters")
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


class FileLockRegistry:
    """
    基于文件路径的 asyncio.Lock 注册表（进程内单例）。

    核心问题：
        当多个 BaseDB 实例同时操作同一个文件时，若各自持有独立的锁，
        并发写入仍会导致 JSON 损坏或丢失更新。

    解决方案：
        FileLockRegistry 维护一个全局字典，将文件路径映射到唯一的 asyncio.Lock。
        无论有多少个 BaseDB 实例，操作同一文件路径的代码始终共享同一把锁。

    线程安全：
        _meta_lock 保护 _locks 字典本身的并发访问（虽然 asyncio 通常是单线程的，
        但在多线程事件循环场景下仍需要此保护）。

    Attributes:
        _locks: 文件路径到 asyncio.Lock 的映射字典。
        _meta_lock: 保护 _locks 字典的元锁。
    """

    _locks: dict[str, asyncio.Lock] = {}
    _meta_lock = asyncio.Lock()

    @classmethod
    async def acquire(cls, file_path: str) -> asyncio.Lock:
        """
        获取指定文件路径的锁。

        若该路径尚无对应锁，则创建一个新的 asyncio.Lock 并注册。
        返回的锁应由调用方通过 async with 语句使用。

        Args:
            file_path: 目标文件的绝对路径字符串。

        Returns:
            与该文件路径绑定的 asyncio.Lock 实例。
        """
        async with cls._meta_lock:
            if file_path not in cls._locks:
                cls._locks[file_path] = asyncio.Lock()
            return cls._locks[file_path]


class BaseDB:
    """
    知识库基类（异步版本）。

    所有具体知识库（CharacterDB、BibleDB、StoryDB 等）均继承此类，
    获得通用的异步 JSON 读写、原子写入和并发安全能力。

    存储约定：
        每个知识库在 workspace/{project_id}/{db_name}/ 目录下拥有独立的存储空间。
        数据以 JSON 文件形式存储，文件名由 key 参数决定（自动附加 .json 后缀）。

    并发安全：
        所有写入操作（save、append、update_field、delete）均通过 FileLockRegistry
        获取文件级全局锁，保证"读取 → 修改 → 写入"的原子性。

    延迟创建：
        存储目录在 __init__ 时自动创建（mkdir(parents=True, exist_ok=True)），
        无需手动初始化目录结构。
    """

    def __init__(self, project_id: str, db_name: str):
        """
        初始化知识库。

        Args:
            project_id: 项目唯一标识，用于隔离不同项目的数据。
            db_name: 知识库名称，对应 workspace/{project_id}/ 下的子目录名。
        """
        self.project_id = project_id
        self.db_name = db_name

        config = get_config()
        self.base_path = Path(config.WORKSPACE_PATH) / project_id
        self.db_path = self.base_path / db_name

        self.db_path.mkdir(parents=True, exist_ok=True)

        # 实例级锁，用于本实例内部的并发控制。
        # 跨实例的并发控制通过 FileLockRegistry 实现。
        self._lock = asyncio.Lock()

    def _get_file_path(self, key: str) -> Path:
        """
        根据 key 生成对应的 JSON 文件路径。

        Args:
            key: 数据标识，不含 .json 后缀。

        Returns:
            Path 对象，指向 workspace/{project_id}/{db_name}/{key}.json。
        """
        return self.db_path / f"{key}.json"

    async def load(self, key: str) -> Optional[dict]:
        """
        加载指定 key 的数据。

        使用 FileLockRegistry 获取文件级全局锁，确保跨实例读取的一致性。
        若文件不存在，返回 None 而非抛出异常。

        Args:
            key: 数据标识。

        Returns:
            数据字典，文件不存在或读取失败时返回 None。
        """
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None

        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            return await self._load_no_lock(key)

    async def save(self, key: str, data: dict) -> bool:
        """
        保存数据到指定 key（异步原子写入）。

        防止丢失更新：
            在写入前重新读取磁盘上的最新数据，执行深度合并后再写入。
            这确保了并发修改场景下，磁盘上未被覆盖的字段得以保留。

        时间戳：
            自动添加 _updated_at 字段（ISO 格式），便于审计和调试。

        Args:
            key: 数据标识。
            data: 要保存的数据字典。

        Returns:
            是否保存成功。
        """
        file_path = self._get_file_path(key)

        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            return await self._save_no_lock(key, data)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """
        深度合并两个字典。

        合并规则：
            - override 中的字段覆盖 base 中的同名字段。
            - base 中未被覆盖的字段保留。
            - 对于 dict 类型字段，递归合并。
            - 对于 list 类型字段，若元素包含标识键（scene_index / id / index），
              按标识匹配合并（保留 base 中未被覆盖的子字段）；
              若无标识键，直接替换（override 优先）。
            - 以下划线开头的内部字段（如 _updated_at）直接覆盖。

        Args:
            base: 基础字典（通常来自磁盘上的最新数据）。
            override: 覆盖字典（通常来自内存中的修改）。

        Returns:
            合并后的新字典。
        """
        result = dict(base)
        for key, value in override.items():
            if key.startswith('_'):
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
        深度合并两个列表（按标识匹配，防止丢失更新）。

        合并规则：
            1. 检查 override_list 的元素是否包含可用标识键：
               优先尝试 'scene_index'，其次 'id'，最后 'index'。
            2. 若存在标识键，按标识匹配：
               - 匹配到的元素递归合并（保留 base 中未被覆盖的字段）。
               - override 中新增的标识直接追加。
               - base 中未被覆盖的元素保留在结果末尾。
            3. 若无可用标识键，直接返回 override_list（override 优先）。

        Args:
            base_list: 基础列表（通常来自磁盘数据）。
            override_list: 覆盖列表（通常来自内存修改）。

        Returns:
            合并后的新列表。
        """
        if not override_list:
            return list(base_list)

        first = override_list[0]
        id_key = None
        if isinstance(first, dict):
            for candidate in ('scene_index', 'id', 'index'):
                if candidate in first:
                    id_key = candidate
                    break

        if id_key is None:
            return list(override_list)

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
                    merged.append(BaseDB._deep_merge(base_index[item_id], item))
                else:
                    merged.append(dict(item))
            else:
                merged.append(item)

        for item in base_list:
            if isinstance(item, dict) and id_key in item:
                if item[id_key] not in seen_ids:
                    merged.append(dict(item))
            else:
                merged.append(item)

        return merged

    async def _load_no_lock(self, key: str) -> Optional[dict]:
        """
        无锁加载（调用方必须已持有 file_lock）。

        此方法不自行获取锁，专为已在锁保护下的内部调用设计。
        直接使用此方法而不持有锁可能导致并发安全问题。

        Args:
            key: 数据标识。

        Returns:
            数据字典，文件不存在或解析失败时返回 None。
        """
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
        """
        无锁保存（调用方必须已持有 file_lock）。

        原子写入流程：
            1. 若目标文件已存在，读取其最新内容并与 data 深度合并。
            2. 添加 _updated_at 时间戳。
            3. 将合并后的数据写入 .tmp 临时文件。
            4. 使用 os.replace() 原子替换目标文件。

        失败清理：
            若写入过程中发生异常，自动删除临时文件，避免留下垃圾数据。

        Args:
            key: 数据标识。
            data: 要保存的数据字典。

        Returns:
            是否保存成功。
        """
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
        向数组字段追加条目。

        使用显式文件锁包裹"读取 → 修改 → 写入"全过程，确保原子性。
        若目标文件不存在或不含 items 数组，自动创建并初始化。
        追加的条目会自动添加 _added_at 时间戳。

        Args:
            key: 数据标识。
            item: 要追加的条目（通常为字典或 Pydantic 模型的 model_dump() 结果）。

        Returns:
            是否追加成功。
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
        更新指定字段的值。

        使用显式文件锁包裹"读取 → 修改 → 写入"全过程，确保原子性。
        若目标文件不存在，创建新文件并只包含该字段。

        Args:
            key: 数据标识。
            field: 要更新的字段名。
            value: 字段的新值。

        Returns:
            是否更新成功。
        """
        file_path = self._get_file_path(key)
        file_lock = await FileLockRegistry.acquire(str(file_path))
        async with file_lock:
            data = await self._load_no_lock(key) or {}
            data[field] = value
            return await self._save_no_lock(key, data)

    async def list_keys(self) -> list[str]:
        """
        列出当前知识库中所有已保存的数据 key。

        目录遍历不需要加锁，因为只读取文件名而不读取内容。

        Returns:
            key 列表（不含 .json 后缀），按字母顺序排序。
        """
        if not self.db_path.exists():
            return []

        keys = []
        for file_path in self.db_path.glob('*.json'):
            keys.append(file_path.stem)
        return sorted(keys)

    async def delete(self, key: str) -> bool:
        """
        删除指定 key 的数据文件。

        使用显式文件锁防止删除与其他写入操作并发执行。

        Args:
            key: 要删除的数据标识。

        Returns:
            是否删除成功。文件不存在时返回 True（幂等删除）。
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
