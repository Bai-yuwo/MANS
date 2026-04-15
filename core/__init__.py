"""
MANS - Core Module

系统核心组件：
- schemas: 数据契约
- config: 配置管理
- llm_client: LLM调用封装
- injection_engine: 注入引擎
- update_extractor: 异步更新器
- logging_config: 日志系统
"""

# 导入日志配置（自动初始化）
from .logging_config import get_logger, log_exception, setup_logging

# 初始化日志系统
setup_logging()

__all__ = ['get_logger', 'log_exception', 'setup_logging']