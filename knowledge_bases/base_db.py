"""
knowledge_bases/base_db.py
知识库基类

设计原则：
1. 通用接口：定义所有知识库的通用操作
2. 文件存储：JSON 文件作为主数据源
3. 原子写入：先写临时文件再 rename，防止数据损坏
4. 向量索引：自动同步到向量存储
"""

import json
import shutil
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

from core.config import get_config
from core.logging_config import get_logger, log_exception

logger = get_logger('knowledge_bases.base_db')


class BaseDB:
    """
    知识库基类
    
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
    
    def _get_file_path(self, key: str) -> Path:
        """获取数据文件路径"""
        return self.db_path / f"{key}.json"
    
    def load(self, key: str) -> Optional[dict]:
        """
        加载指定 key 的数据
        
        Args:
            key: 数据标识
        
        Returns:
            数据字典，不存在则返回 None
        """
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载数据失败 {key}: {e}")
            return None
    
    def save(self, key: str, data: dict) -> bool:
        """
        保存数据到指定 key（原子写入）
        
        Args:
            key: 数据标识
            data: 要保存的数据
        
        Returns:
            是否保存成功
        """
        file_path = self._get_file_path(key)
        temp_path = file_path.with_suffix('.tmp')
        
        try:
            # 添加更新时间
            data['_updated_at'] = datetime.now().isoformat()
            
            # 写入临时文件
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 原子替换
            shutil.move(str(temp_path), str(file_path))
            return True
            
        except IOError as e:
            logger.error(f"保存数据失败 {key}: {e}")
            # 清理临时文件
            if temp_path.exists():
                temp_path.unlink()
            return False
    
    def append(self, key: str, item: Any) -> bool:
        """
        向数组字段追加条目（知识库只增不覆盖原则）
        
        Args:
            key: 数据标识
            item: 要追加的条目
        
        Returns:
            是否追加成功
        """
        data = self.load(key) or {}
        
        if 'items' not in data:
            data['items'] = []
        
        # 添加时间戳和来源
        if isinstance(item, dict):
            item['_added_at'] = datetime.now().isoformat()
        
        data['items'].append(item)
        return self.save(key, data)
    
    def update_field(self, key: str, field: str, value: Any) -> bool:
        """
        更新指定字段
        
        Args:
            key: 数据标识
            field: 字段名
            value: 新值
        
        Returns:
            是否更新成功
        """
        data = self.load(key) or {}
        data[field] = value
        return self.save(key, data)
    
    def list_keys(self) -> list[str]:
        """
        列出所有数据 key
        
        Returns:
            key 列表（不含 .json 后缀）
        """
        if not self.db_path.exists():
            return []
        
        keys = []
        for file_path in self.db_path.glob('*.json'):
            keys.append(file_path.stem)
        return sorted(keys)
    
    def delete(self, key: str) -> bool:
        """
        删除指定 key 的数据
        
        Args:
            key: 数据标识
        
        Returns:
            是否删除成功
        """
        file_path = self._get_file_path(key)
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except IOError as e:
            logger.error(f"删除数据失败 {key}: {e}")
            return False
