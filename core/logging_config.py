"""
core/logging_config.py

MANS 系统的统一日志基础设施。

职责边界：
    - 提供全系统一致的日志格式和输出目的地。
    - 支持控制台输出（开发调试）和分级文件输出（生产审计）。
    - 集成 SSE（Server-Sent Events）日志推送，使前端能够实时查看后端日志。
    - 自动处理日志轮转，防止日志文件无限增长撑爆磁盘。

日志级别说明：
    DEBUG：详细的调试信息，仅在开发环境开启。
    INFO：正常的业务事件，如请求处理、模型调用成功。
    WARNING：需要注意但非致命的问题，如限流等待、配置回退。
    ERROR：影响当前操作但系统仍可继续运行的错误。
    CRITICAL：系统级致命错误，需要立即人工介入。

典型用法：
    from core.logging_config import get_logger, log_exception
    logger = get_logger('knowledge_bases.character_db')
    logger.info("人物已保存")
    log_exception(logger, exc, context="保存人物失败")
"""

import asyncio
import logging
import sys
import threading
import traceback
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


class LogConfig:
    """
    日志系统的静态配置参数。

    此类仅用于集中存放配置常量，不维护运行时状态。
    所有参数均为类属性，无需实例化即可访问。

    Attributes:
        LOG_DIR: 日志文件存放目录，位于项目根目录下的 logs/ 文件夹。
        CONSOLE_FORMAT: 控制台输出格式，包含时间、级别、模块名和消息。
        FILE_FORMAT: 文件输出格式，额外包含文件名和行号，便于定位问题。
        CONSOLE_LEVEL: 控制台日志级别阈值，低于此级别的日志不会输出到控制台。
        MAX_BYTES: 单个日志文件的最大字节数，超过后触发轮转。
        BACKUP_COUNT: 日志轮转时保留的历史备份文件数量。
        LEVEL_FILES: 各级别对应的独立日志文件名，实现按级别分类存储。
    """

    LOG_DIR = Path(__file__).parent.parent / "logs"
    CONSOLE_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s'
    FILE_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(filename)s:%(lineno)d | %(message)s'
    CONSOLE_LEVEL = logging.DEBUG
    MAX_BYTES = 10 * 1024 * 1024
    BACKUP_COUNT = 5
    LEVEL_FILES = {
        logging.DEBUG: 'debug.log',
        logging.INFO: 'info.log',
        logging.WARNING: 'warning.log',
        logging.ERROR: 'error.log',
        logging.CRITICAL: 'critical.log',
    }
    PROMPT_LOG_FILE = 'prompt.log'


# 模块级全局标志，确保 setup_logging() 只执行一次。
# 多次调用 setup_logging() 不会产生重复 handler，而是直接返回。
_logging_initialized = False


def setup_logging(console_level=None):
    """
    初始化全局日志系统。

    创建名为 'mans' 的根日志器，并为其配置：
        1. 控制台 Handler：输出到 stdout，便于开发时实时查看。
        2. 分级文件 Handler：为每个日志级别创建独立的 RotatingFileHandler，
           通过 LevelFilter 确保每个文件只记录对应级别的日志。

    幂等性说明：
        此函数可安全多次调用。首次调用执行实际初始化，后续调用直接返回。

    Args:
        console_level: 控制台日志级别，None 表示使用 LogConfig.CONSOLE_LEVEL。
    """
    global _logging_initialized
    if _logging_initialized:
        return
    _logging_initialized = True

    console_level = console_level or LogConfig.CONSOLE_LEVEL

    LogConfig.LOG_DIR.mkdir(exist_ok=True)

    root_logger = logging.getLogger('mans')
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_formatter = logging.Formatter(LogConfig.CONSOLE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    for level, filename in LogConfig.LEVEL_FILES.items():
        log_file = LogConfig.LOG_DIR / filename

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=LogConfig.MAX_BYTES,
            backupCount=LogConfig.BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(level)

        class LevelFilter(logging.Filter):
            """
            精确级别过滤器。

            只允许 record.levelno 完全等于目标级别的日志通过。
            这使得每个级别的日志文件只包含该级别的记录，便于按级别审计。
            """

            def __init__(self, level):
                super().__init__()
                self.level = level

            def filter(self, record):
                return record.levelno == self.level

        file_handler.addFilter(LevelFilter(level))

        file_formatter = logging.Formatter(LogConfig.FILE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Prompt 专用日志文件：记录每次 LLM 调用的请求详情
    prompt_handler = RotatingFileHandler(
        LogConfig.LOG_DIR / LogConfig.PROMPT_LOG_FILE,
        maxBytes=LogConfig.MAX_BYTES,
        backupCount=LogConfig.BACKUP_COUNT,
        encoding='utf-8'
    )
    prompt_handler.setLevel(logging.DEBUG)
    prompt_formatter = logging.Formatter(
        '%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    prompt_handler.setFormatter(prompt_formatter)
    # 使用过滤器确保只有 mans.prompt logger 的日志进入 prompt.log
    class PromptFilter(logging.Filter):
        def filter(self, record):
            return record.name == 'mans.prompt'
    prompt_handler.addFilter(PromptFilter())
    root_logger.addHandler(prompt_handler)

    root_logger.info("=" * 80)
    root_logger.info(f"MANS 日志系统已启动 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    root_logger.info(f"日志目录: {LogConfig.LOG_DIR}")
    root_logger.info("日志按级别分类: debug.log | info.log | warning.log | error.log | critical.log")
    root_logger.info("Prompt 日志: prompt.log")
    root_logger.info("=" * 80)


class SSELogHandler(logging.Handler):
    """
    将日志记录推送到 SSE（Server-Sent Events）客户端的 Handler。

    实现机制：
        - 内部维护一组 asyncio.Queue，每个前端 SSE 连接对应一个 Queue。
        - emit() 方法在日志产生时，将日志记录格式化为字典，
          然后通过 loop.call_soon_threadsafe() 安全地放入所有活跃 Queue。
        - 前端通过 EventSource 消费 Queue 中的日志，实现实时日志展示。

    线程安全：
        - 使用 threading.Lock 保护 Queue 集合的增删操作。
        - 日志生产（同步线程）和日志消费（异步事件循环）通过 Queue 解耦。

    Attributes:
        _queues: 当前所有活跃的 asyncio.Queue 集合。
        _lock: 保护 _queues 集合的线程锁。
    """

    def __init__(self):
        super().__init__()
        self._queues = set()
        self._lock = threading.Lock()

    def add_queue(self, queue):
        """
        注册一个新的 SSE 客户端 Queue。

        当前端建立 SSE 连接时调用，将对应的 Queue 加入分发集合。

        Args:
            queue: asyncio.Queue 实例，用于接收日志字典。
        """
        with self._lock:
            self._queues.add(queue)

    def remove_queue(self, queue):
        """
        注销一个 SSE 客户端 Queue。

        当前端断开 SSE 连接时调用，将对应的 Queue 从分发集合移除。

        Args:
            queue: 之前通过 add_queue() 注册的 asyncio.Queue 实例。
        """
        with self._lock:
            self._queues.discard(queue)

    def emit(self, record):
        """
        发送日志记录到所有已注册的 SSE 客户端。

        这是 logging.Handler 的抽象方法实现，由 logging 框架自动调用。

        处理流程：
            1. 将日志记录格式化为包含 level、message、time、name 的字典。
            2. 获取 _queues 的快照（避免在遍历时被其他线程修改）。
            3. 对每个 Queue，通过 call_soon_threadsafe 放入日志字典。
            4. 若无运行中的事件循环（如脚本模式），静默跳过。

        Args:
            record: logging.LogRecord 实例。
        """
        try:
            log_entry = {
                'level': record.levelname,
                'message': self.format(record),
                'time': datetime.now().strftime('%H:%M:%S'),
                'name': record.name
            }
            with self._lock:
                queues = list(self._queues)
            for queue in queues:
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(queue.put_nowait, log_entry)
                except RuntimeError:
                    pass
        except Exception:
            self.handleError(record)


# 全局 SSE Handler 单例。
# 此实例被附加到 'mans' 根日志器上，所有通过 get_logger() 获取的日志器都会继承此 Handler。
sse_log_handler = SSELogHandler()
sse_log_handler.setLevel(logging.DEBUG)
sse_formatter = logging.Formatter('%(message)s')
sse_log_handler.setFormatter(sse_formatter)


def setup_sse_logging():
    """
    将 SSE Handler 附加到 'mans' 根日志器。

    应在 SSE 服务端点初始化时调用，确保后续产生的日志能够推送到前端。
    若已附加则不会重复添加。
    """
    root_logger = logging.getLogger('mans')
    if sse_log_handler not in root_logger.handlers:
        root_logger.addHandler(sse_log_handler)


def get_logger(name: str) -> logging.Logger:
    """
    获取模块级别的日志器。

    日志器名称会自动添加 'mans.' 前缀，形成层级结构，
    使日志输出中能够清晰标识日志来源模块。

    例如：
        get_logger('core.injection_engine')
        # 实际日志器名称为 'mans.core.injection_engine'

    Args:
        name: 模块标识名称，通常使用 __name__ 或自定义的模块路径。

    Returns:
        配置好的 logging.Logger 实例。
    """
    if not name.startswith('mans.'):
        name = f'mans.{name}'
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, exc: Exception, context: str = ""):
    """
    记录异常的完整信息，包括异常消息和完整堆栈跟踪。

    此方法是对 logging.exception() 的封装，增加了上下文描述能力，
    便于在日志中快速定位异常发生的业务场景。

    Args:
        logger: 用于输出日志的 Logger 实例。
        exc: 被捕获的异常对象。
        context: 异常发生的上下文描述，如"向量检索失败"。
    """
    error_msg = f"{context}: {str(exc)}" if context else str(exc)
    logger.error(error_msg)
    logger.error(f"Traceback:\n{traceback.format_exc()}")


# 模块导入时自动初始化日志系统。
# 这是为了确保任何模块在导入 core.logging_config 时，日志系统已经就绪。
setup_logging()
