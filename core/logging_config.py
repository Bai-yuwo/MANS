"""
MANS - 日志系统配置

提供统一的日志管理，支持：
- 控制台输出（开发调试）
- 文件输出（生产记录）
- 分级日志（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 自动日志轮转（按大小和时间）
- 模块化日志（不同模块独立日志）
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime


class LogConfig:
    """日志配置类"""
    
    # 日志目录
    LOG_DIR = Path(__file__).parent.parent / "logs"
    
    # 日志格式
    CONSOLE_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s'
    FILE_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(filename)s:%(lineno)d | %(message)s'
    
    # 日志级别
    CONSOLE_LEVEL = logging.DEBUG      # 控制台日志级别
    
    # 文件轮转配置
    MAX_BYTES = 10 * 1024 * 1024       # 单个文件最大 10MB
    BACKUP_COUNT = 5                   # 保留 5 个备份文件
    
    # 按日志级别分类的文件配置
    LEVEL_FILES = {
        logging.DEBUG: 'debug.log',
        logging.INFO: 'info.log',
        logging.WARNING: 'warning.log',
        logging.ERROR: 'error.log',
        logging.CRITICAL: 'critical.log',
    }


def setup_logging(console_level=None):
    """
    设置全局日志配置
    
    Args:
        console_level: 控制台日志级别（默认使用 LogConfig.CONSOLE_LEVEL）
    """
    console_level = console_level or LogConfig.CONSOLE_LEVEL
    
    # 确保日志目录存在
    LogConfig.LOG_DIR.mkdir(exist_ok=True)
    
    # 配置根日志器
    root_logger = logging.getLogger('mans')
    root_logger.setLevel(logging.DEBUG)  # 根日志器捕获所有级别
    root_logger.handlers.clear()  # 清除已有handlers
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_formatter = logging.Formatter(LogConfig.CONSOLE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # 为每个日志级别创建独立的文件处理器
    for level, filename in LogConfig.LEVEL_FILES.items():
        log_file = LogConfig.LOG_DIR / filename
        
        # 创建文件处理器（按大小轮转）
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=LogConfig.MAX_BYTES,
            backupCount=LogConfig.BACKUP_COUNT,
            encoding='utf-8'
        )
        
        # 设置级别过滤器（只记录该级别及以上的日志）
        file_handler.setLevel(level)
        
        # 添加过滤器，确保每个文件只记录对应级别的日志
        class LevelFilter(logging.Filter):
            def __init__(self, level):
                super().__init__()
                self.level = level
            
            def filter(self, record):
                # 只允许等于该级别的日志通过
                return record.levelno == self.level
        
        file_handler.addFilter(LevelFilter(level))
        
        # 设置格式
        file_formatter = logging.Formatter(LogConfig.FILE_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_formatter)
        
        # 添加到根日志器
        root_logger.addHandler(file_handler)
    
    root_logger.info("="*80)
    root_logger.info(f"MANS 日志系统已启动 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    root_logger.info(f"日志目录: {LogConfig.LOG_DIR}")
    root_logger.info("日志按级别分类: debug.log | info.log | warning.log | error.log | critical.log")
    root_logger.info("="*80)


def get_logger(name: str) -> logging.Logger:
    """
    获取模块日志器
    
    Args:
        name: 模块名称（如 'mans.core.injection_engine'）
        
    Returns:
        logging.Logger 实例
    """
    # 确保日志器名称以 'mans.' 开头
    if not name.startswith('mans.'):
        name = f'mans.{name}'
    
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, exc: Exception, context: str = ""):
    """
    记录异常详细信息
    
    Args:
        logger: 日志器实例
        exc: 异常对象
        context: 异常上下文描述
    """
    import traceback
    
    error_msg = f"{context}: {str(exc)}" if context else str(exc)
    logger.error(error_msg)
    logger.error(f"Traceback:\n{traceback.format_exc()}")


# 自动初始化（导入时执行）
setup_logging()
