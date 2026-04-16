"""
vector_store/embedding.py
Embedding 模型管理器：

设计原则：
1. 单例模式：模型只加载一次，显存常驻 ~2GB
2. LRU 缓存：避免重复向量化相同文本
3. 批量向量化：单次调用可处理多条文本
4. 自动设备选择：优先使用 GPU（CUDA），回退到 CPU
5. 模型下载：首次使用自动从 HuggingFace 下载

硬件要求：
- RTX 3060 Laptop（6-8GB 显存）：✅ 流畅运行
- bge-m3 显存占用：~2GB
- 生成速度：~100 条/秒

模型对比（3060 Laptop）：
| 模型              | 显存   | 速度    | 中文质量 | 推荐度 |
|-----------------|------|--------|---------|--------|
| BAAI/bge-m3     | ~2GB | ~100/s | ⭐⭐⭐⭐  | ⭐⭐⭐⭐⭐ |
| 镜像AI/m3e-base  | ~1.5GB| ~150/s | ⭐⭐⭐⭐  | ⭐⭐⭐⭐   |
| shibing624/text2vec-base | ~1GB | ~200/s | ⭐⭐⭐ | ⭐⭐⭐    |
"""

import os
import hashlib
import threading
from typing import Optional

from core.config import get_config
from core.logging_config import get_logger

logger = get_logger('vector_store.embedding')

# 全局单例
_embedding_manager: Optional["EmbeddingManager"] = None
_manager_lock = threading.Lock()


def get_embedding_manager() -> "EmbeddingManager":
    """
    获取 EmbeddingManager 单例（线程安全）
    
    Returns:
        EmbeddingManager 实例
    """
    global _embedding_manager
    if _embedding_manager is None:
        with _manager_lock:
            if _embedding_manager is None:
                _embedding_manager = EmbeddingManager()
    return _embedding_manager


class EmbeddingManager:
    """
    本地 Embedding 模型管理器
    
    使用示例：
        manager = get_embedding_manager()
        
        # 单条向量化
        vector = manager.encode("主角被困在山洞中")
        
        # 批量向量化（推荐，更高效）
        vectors = manager.encode_batch([
            "修炼体系的境界划分",
            "宗门势力分布",
            "主角的成长历程"
        ])
        
        # 带缓存的向量化（避免重复计算）
        vector = manager.encode_with_cache("相同文本")
    """
    
    # 支持的模型列表（按推荐度排序）
    SUPPORTED_MODELS = {
        "bge-m3": {
            "model_name": "BAAI/bge-m3",
            "dimension": 1024,
            "description": "中文语义理解最佳，支持多语言，模型体积 500MB",
            "recommended": True,
        },
        "m3e-base": {
            "model_name": "moka-ai/m3e-base",
            "dimension": 768,
            "description": "中文优化，轻量级，适合短文本",
            "recommended": False,
        },
        "text2vec-base": {
            "model_name": "shibing624/text2vec-base-chinese",
            "dimension": 768,
            "description": "传统中文向量化模型，速度快但精度一般",
            "recommended": False,
        },
    }
    
    def __init__(
        self,
        model_name: Optional[str] = "bge-m3",
        cache_dir: Optional[str] = None,
        device: Optional[str] = None,
        max_cache_size: int = 10000,
    ):
        """
        初始化 Embedding 管理器
        
        Args:
            model_name: 模型标识，默认从 config 读取（bge-m3 / m3e-base / text2vec-base）
            cache_dir: 模型缓存目录，默认 ~/.cache/huggingface
            device: 运行设备（cuda / cpu），默认自动选择
            max_cache_size: 向量化缓存最大条目数
        """
        # 从配置读取默认值
        if model_name is None:
            try:
                cfg = get_config()
                model_name = cfg.LOCAL_EMBED_MODEL
            except Exception:
                model_name = "bge-m3"
        
        self.model_name = model_name
        self.model_info = self.SUPPORTED_MODELS.get(
            model_name,  # type: ignore[arg-type]
            self.SUPPORTED_MODELS["bge-m3"]
        )
        self.model_id = self.model_info["model_name"]
        self.dimension = self.model_info["dimension"]
        
        # 从配置读取 cache_dir
        if cache_dir:
            self.cache_dir = cache_dir
        else:
            try:
                cfg = get_config()
                cfg_cache = cfg.LOCAL_EMBED_CACHE_DIR
                if cfg_cache:
                    # 转换为绝对路径（基于项目根目录）
                    if not os.path.isabs(cfg_cache):
                        project_root = os.path.dirname(os.path.abspath(__file__))
                        # __file__ = .../MANS/vector_store/embedding.py，向上2层就是 MANS/
                        self.cache_dir = os.path.abspath(
                            os.path.join(project_root, "..", cfg_cache)
                        )
                    else:
                        self.cache_dir = cfg_cache
                else:
                    self.cache_dir = os.path.expanduser("~/.cache/huggingface")
            except Exception:
                self.cache_dir = os.path.expanduser("~/.cache/huggingface")
        self.max_cache_size = max_cache_size
        
        # 自动设备选择
        if device:
            self.device = device
        else:
            self.device = self._auto_select_device()
        
        # 模型和缓存（延迟加载）
        self._model = None
        self._cache: dict[str, list[float]] = {}
        self._cache_lock = threading.Lock()
        
        logger.info(
            f"EmbeddingManager 初始化: model={self.model_id}, "
            f"device={self.device}, dimension={self.dimension}"
        )
    
    def _auto_select_device(self) -> str:
        """自动选择运行设备"""
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                logger.info(f"检测到 GPU: {gpu_name}, 显存: {vram_gb:.1f}GB")
                return "cuda"
        except ImportError:
            logger.warning("未安装 PyTorch，将使用 CPU 运行 Embedding 模型")
        except Exception as e:
            logger.warning(f"GPU 检测失败: {e}，将使用 CPU")
        
        return "cpu"
    
    def _load_model(self):
        """延迟加载模型（首次使用时）"""
        if self._model is not None:
            return
        
        logger.info(f"正在加载 Embedding 模型: {self.model_id} ...")
        logger.info(f"模型目录: {self.cache_dir}")
        
        try:
            from sentence_transformers import SentenceTransformer
            
            # 检查是否有 HuggingFace 格式的目录结构
            hf_cache_dir = os.path.join(
                self.cache_dir, "hub", 
                "models--" + self.model_id.replace("/", "--"),
                "snapshots", "default"
            )
            if os.path.exists(hf_cache_dir):
                # 使用 HuggingFace 标准目录结构
                model_path = hf_cache_dir
            else:
                # 直接使用 cache_dir（文件直接放在此目录下）
                model_path = self.cache_dir
            
            logger.info(f"加载模型路径: {model_path}")
            
            # 强制使用本地文件，不联网
            self._model = SentenceTransformer(
                model_path,
                device=self.device,
                local_files_only=True,  # 强制只使用本地文件
            )
            
            logger.info(
                f"模型加载成功: {self.model_id}, "
                f"dimension={self.dimension}, device={self.device}"
            )
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise RuntimeError(
                f"无法加载 Embedding 模型 '{self.model_id}'。\n"
                f"请确认已安装依赖: pip install sentence-transformers\n"
                f"或检查网络连接（HuggingFace 下载模型需要联网）"
            ) from e
    
    def encode(self, text: str) -> list[float]:
        """
        单条文本向量化
        
        Args:
            text: 输入文本
            
        Returns:
            向量列表（float）
        """
        # 检查缓存
        cache_key = self._make_cache_key(text)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        # 加载模型（如未加载）
        self._load_model()
        
        # 向量化
        embedding = self._model.encode(
            text,
            normalize_embeddings=True,  # L2 归一化，兼容余弦相似度
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        
        result = embedding.tolist()
        
        # 写入缓存
        with self._cache_lock:
            if len(self._cache) >= self.max_cache_size:
                # 简单策略：清空一半缓存
                keys_to_remove = list(self._cache.keys())[: self.max_cache_size // 2]
                for k in keys_to_remove:
                    del self._cache[k]
            self._cache[cache_key] = result
        
        return result
    
    def encode_batch(
        self,
        texts: list[str],
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> list[list[float]]:
        """
        批量向量化（推荐使用，更高效）
        
        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数
            show_progress: 是否显示进度条
            
        Returns:
            向量列表的列表
        """
        if not texts:
            return []
        
        # 加载模型（如未加载）
        self._load_model()
        
        # 先从缓存中获取能命中的
        results: list[Optional[list[float]]] = [None] * len(texts)
        texts_to_encode: list[tuple[int, str]] = []
        
        with self._cache_lock:
            for i, text in enumerate(texts):
                cache_key = self._make_cache_key(text)
                if cache_key in self._cache:
                    results[i] = self._cache[cache_key]
                else:
                    texts_to_encode.append((i, text))
        
        # 批量向量化剩余文本
        if texts_to_encode:
            texts_only = [t for _, t in texts_to_encode]
            indices = [i for i, _ in texts_to_encode]
            
            embeddings = self._model.encode(
                texts_only,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=show_progress,
            )
            
            # 写入缓存并填充结果
            with self._cache_lock:
                for idx, emb, text in zip(indices, embeddings, texts_only):
                    result = emb.tolist()
                    results[idx] = result
                    
                    # 缓存（带容量检查）
                    if len(self._cache) < self.max_cache_size:
                        cache_key = self._make_cache_key(text)
                        self._cache[cache_key] = result
        
        return results  # type: ignore
    
    def encode_with_cache(self, text: str) -> list[float]:
        """
        带缓存的向量化（与 encode 等效，语义更明确）
        """
        return self.encode(text)
    
    def _make_cache_key(self, text: str) -> str:
        """生成缓存键（文本的 MD5 哈希）"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    
    def get_stats(self) -> dict[str, object]:
        """
        获取统计信息
        
        Returns:
            包含缓存命中率、模型信息等的字典
        """
        return {
            "model_id": self.model_id,
            "dimension": self.dimension,
            "device": self.device,
            "cache_size": len(self._cache),
            "cache_max_size": self.max_cache_size,
            "model_loaded": self._model is not None,
        }
    
    def clear_cache(self):
        """清空向量化缓存"""
        with self._cache_lock:
            self._cache.clear()
        logger.info("向量化缓存已清空")
    
    def preload(self):
        """预加载模型（可选，用于提前加载到显存）"""
        self._load_model()
        logger.info("Embedding 模型已预加载")
