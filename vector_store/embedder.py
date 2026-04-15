"""
vector_store/embedder.py
文本向量化封装

设计原则：
1. 统一接口：屏蔽不同 embedding 模型的差异
2. 批量处理：支持批量文本向量化
3. 缓存机制：避免重复计算
"""

from typing import Union
import hashlib

from core.config import get_config


class Embedder:
    """
    文本向量化器
    
    将文本转换为向量表示
    
    使用示例：
        embedder = Embedder()
        vector = await embedder.embed("要向量化的文本")
    """
    
    def __init__(self):
        self.config = get_config()
        self._cache = {}
    
    def _get_cache_key(self, text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(text.encode()).hexdigest()
    
    async def embed(self, text: str) -> list[float]:
        """
        单文本向量化
        
        Args:
            text: 要向量化的文本
        
        Returns:
            向量表示（浮点数列表）
        """
        # 检查缓存
        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # TODO: 实现实际的向量化
        # 目前返回空向量，避免调用失败
        print(f"文本向量化（未实现）: {text[:50]}...")
        
        # 返回空向量（维度 768）
        vector = [0.0] * 768
        
        # 缓存结果
        self._cache[cache_key] = vector
        
        return vector
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本向量化
        
        Args:
            texts: 要向量化的文本列表
        
        Returns:
            向量列表
        """
        vectors = []
        for text in texts:
            vector = await self.embed(text)
            vectors.append(vector)
        return vectors
    
    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
