"""
vector_store/embedding.py

本地 Embedding 模型管理器，负责将文本转换为高维向量。

职责边界：
    - 加载并管理 sentence-transformers 模型（bge-m3 等）。
    - 提供单条和批量文本向量化接口。
    - 实现 LRU 缓存，避免重复向量化相同文本。
    - 自动选择最优运行设备（GPU 优先，CPU 回退）。

模型选择：
    当前默认使用 BAAI/bge-m3，该模型在中文语义理解任务上表现优异：
        - 维度：1024
        - 显存占用：约 2GB
        - 速度：GPU 上约 100 条/秒
        - 特点：支持多语言，对长文本（ up to 8192 tokens）效果稳定

    其他可选模型：
        - m3e-base（moka-ai/m3e-base）：更轻量，768 维，适合短文本。
        - text2vec-base（shibing624/text2vec-base-chinese）：速度最快，但精度一般。

硬件适配：
    - GPU（CUDA）：自动检测并优先使用，速度提升显著。
    - CPU：完全可运行，适合没有独立显卡的部署环境。
    - Apple Silicon（MPS）：当前未显式支持，但 PyTorch 可能自动回退到 CPU。

缓存策略：
    - 缓存键：文本的 MD5 哈希值。
    - 最大容量：默认 10000 条。
    - 溢出处理：当缓存满时，清空前半部分条目（简单策略，非严格 LRU）。

典型用法：
    manager = get_embedding_manager()
    vector = manager.encode("主角被困在山洞中")
    vectors = manager.encode_batch(["文本1", "文本2", "文本3"])
"""

import os
import hashlib
import threading
from typing import Optional

from core.config import get_config
from core.logging_config import get_logger

logger = get_logger('vector_store.embedding')

# 全局单例缓存和创建锁，确保多线程环境下只创建一个 EmbeddingManager 实例。
_embedding_manager: Optional["EmbeddingManager"] = None
_manager_lock = threading.Lock()


def get_embedding_manager() -> "EmbeddingManager":
    """
    获取 EmbeddingManager 单例（线程安全的双检锁模式）。

    首次调用时创建实例并缓存，后续调用直接返回缓存实例。
    双检锁机制确保即使在多线程并发调用时，也只创建一个实例。

    Returns:
        EmbeddingManager 全局单例。
    """
    global _embedding_manager
    if _embedding_manager is None:
        with _manager_lock:
            if _embedding_manager is None:
                _embedding_manager = EmbeddingManager()
    return _embedding_manager


class EmbeddingManager:
    """
    本地 Embedding 模型管理器。

    核心设计：
        模型加载是耗时操作（首次加载可能需要数秒到数分钟，取决于网络速度和硬件），
        因此采用延迟加载策略（lazy loading）：模型在首次调用 encode() 时才被加载到内存/显存。
        加载后的模型常驻内存，后续调用直接使用，无需重复加载。

    缓存机制：
        相同的文本在不同检索请求中可能多次出现（如"主角的修炼境界"可能被多次查询）。
        缓存避免了重复的模型前向传播计算，显著提升检索性能。

    设备自动选择：
        优先检测 CUDA 可用性，若可用则使用 GPU（cuda），否则回退到 CPU。
        对于 Apple Silicon 设备，当前版本未显式支持 MPS，由 PyTorch 自动回退处理。
    """

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
        初始化 Embedding 管理器。

        配置加载优先级：
            1. 传入的参数（model_name、cache_dir、device）。
            2. 环境变量 / .env 配置文件。
            3. 代码默认值。

        Args:
            model_name: 模型标识（bge-m3 / m3e-base / text2vec-base），
                        None 表示从配置自动读取。
            cache_dir: 模型缓存目录，None 表示使用 HuggingFace 默认路径（~/.cache/huggingface）。
            device: 运行设备（cuda / cpu），None 表示自动检测。
            max_cache_size: 向量化结果缓存的最大条目数。
        """
        if model_name is None:
            try:
                cfg = get_config()
                model_name = cfg.LOCAL_EMBED_MODEL
            except Exception:
                model_name = "bge-m3"

        self.model_name = model_name
        self.model_info = self.SUPPORTED_MODELS.get(
            model_name,
            self.SUPPORTED_MODELS["bge-m3"]
        )
        self.model_id = self.model_info["model_name"]
        self.dimension = self.model_info["dimension"]

        if cache_dir:
            self.cache_dir = cache_dir
        else:
            try:
                cfg = get_config()
                cfg_cache = cfg.LOCAL_EMBED_CACHE_DIR
                if cfg_cache:
                    if not os.path.isabs(cfg_cache):
                        project_root = os.path.dirname(os.path.abspath(__file__))
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

        if device:
            self.device = device
        else:
            self.device = self._auto_select_device()

        # 延迟加载：模型和缓存仅在首次 encode 时初始化。
        self._model = None
        self._cache: dict[str, list[float]] = {}
        self._cache_lock = threading.Lock()

        logger.info(
            f"EmbeddingManager 初始化: model={self.model_id}, "
            f"device={self.device}, dimension={self.dimension}"
        )

    def _auto_select_device(self) -> str:
        """
        自动选择最优运行设备。

        选择优先级：
            1. CUDA（NVIDIA GPU）：检测 PyTorch 是否可用且 CUDA 是否可访问。
               记录 GPU 型号和显存大小到日志，便于性能调优。
            2. CPU：当没有可用 GPU 或 PyTorch 未安装时的回退选项。

        Returns:
            "cuda" 或 "cpu"。
        """
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
        """
        延迟加载 sentence-transformers 模型。

        首次调用 encode() 或 encode_batch() 时触发。
        模型加载后会常驻内存/显存，直到进程结束。

        加载策略：
            优先尝试从本地缓存目录加载（local_files_only=True），
            避免在离线环境中尝试联网下载导致阻塞。
            若本地不存在模型文件，会抛出 RuntimeError 并提示安装方式。

        Raises:
            RuntimeError: 模型加载失败时抛出。
        """
        if self._model is not None:
            return

        logger.info(f"正在加载 Embedding 模型: {self.model_id} ...")
        logger.info(f"模型目录: {self.cache_dir}")

        try:
            from sentence_transformers import SentenceTransformer

            hf_cache_dir = os.path.join(
                self.cache_dir, "hub",
                "models--" + self.model_id.replace("/", "--"),
                "snapshots", "default"
            )
            if os.path.exists(hf_cache_dir):
                model_path = hf_cache_dir
            else:
                model_path = self.cache_dir

            logger.info(f"加载模型路径: {model_path}")

            self._model = SentenceTransformer(
                model_path,
                device=self.device,
                local_files_only=True,
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
        单条文本向量化。

        执行流程：
            1. 检查缓存：若该文本已有缓存向量，直接返回。
            2. 加载模型（若尚未加载）。
            3. 调用模型前向传播，生成向量。
            4. 将结果写入缓存（带容量检查）。
            5. 返回向量。

        向量归一化：
            输出向量经过 L2 归一化，可直接用于余弦相似度计算。
            cosine_similarity(a, b) = dot(a, b)（因为 ||a|| = ||b|| = 1）。

        Args:
            text: 输入文本字符串。

        Returns:
            浮点数向量列表，长度等于模型的 dimension。
        """
        cache_key = self._make_cache_key(text)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        self._load_model()

        embedding = self._model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        result = embedding.tolist()

        with self._cache_lock:
            if len(self._cache) >= self.max_cache_size:
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
        批量文本向量化（推荐使用，效率更高）。

        批量优化策略：
            1. 先检查缓存，将已有缓存的文本直接填充到结果中。
            2. 仅对缓存未命中的文本调用模型，大幅减少实际计算量。
            3. 使用模型原生 batch 接口，充分利用 GPU 并行计算能力。

        缓存处理：
            批量写入缓存时进行容量检查，避免缓存无限增长。

        Args:
            texts: 输入文本列表。
            batch_size: 每批送入模型的文本数量，根据 GPU 显存调整。
                        显存较大时可增大此值，反之减小。
            show_progress: 是否显示 tqdm 进度条（大量文本时建议开启）。

        Returns:
            向量列表，与输入文本一一对应。
        """
        if not texts:
            return []

        self._load_model()

        results: list[Optional[list[float]]] = [None] * len(texts)
        texts_to_encode: list[tuple[int, str]] = []

        with self._cache_lock:
            for i, text in enumerate(texts):
                cache_key = self._make_cache_key(text)
                if cache_key in self._cache:
                    results[i] = self._cache[cache_key]
                else:
                    texts_to_encode.append((i, text))

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

            with self._cache_lock:
                for idx, emb, text in zip(indices, embeddings, texts_only):
                    result = emb.tolist()
                    results[idx] = result

                    if len(self._cache) < self.max_cache_size:
                        cache_key = self._make_cache_key(text)
                        self._cache[cache_key] = result

        return results

    def encode_with_cache(self, text: str) -> list[float]:
        """
        带缓存的向量化（与 encode() 行为完全一致，语义更明确）。

        此方法的存在是为了在调用点明确表达"使用缓存"的意图，
        便于代码审查时快速识别缓存友好的调用路径。
        """
        return self.encode(text)

    def _make_cache_key(self, text: str) -> str:
        """
        生成缓存键。

        使用 MD5 哈希将任意长度的文本映射为固定长度的字符串，
        作为字典的键。MD5 虽然不再推荐用于安全场景，但在缓存键生成中足够高效。

        Args:
            text: 输入文本。

        Returns:
            32 字符的十六进制 MD5 哈希字符串。
        """
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def get_stats(self) -> dict[str, object]:
        """
        获取管理器的运行统计信息。

        Returns:
            包含模型 ID、向量维度、运行设备、缓存当前大小、
            缓存上限、模型是否已加载等信息的字典。
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
        """
        清空向量化缓存。

        适用场景：
            - 内存紧张时需要释放缓存占用的空间。
            - 怀疑缓存数据损坏时进行重置。
        """
        with self._cache_lock:
            self._cache.clear()
        logger.info("向量化缓存已清空")

    def preload(self):
        """
        预加载模型到内存/显存。

        适用场景：
            - 服务启动时提前加载模型，避免首个请求时的冷启动延迟。
            - 在 GPU 环境下预热 CUDA 上下文，减少后续调用的初始化开销。
        """
        self._load_model()
        logger.info("Embedding 模型已预加载")
