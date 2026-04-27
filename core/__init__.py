"""
MANS - Core Module(主管-专家二级架构)

P0 基础设施层公共导出。

四大基础抽象:
    - LLMClient    : ARK Responses API 封装,统一 stream_call(主管) + call(专家) 两种语义。
    - BaseTool     : 所有 Agent 工具的抽象基类,name/description/schema/execute 四件套。
    - ExpertTool   : 12 个专家共用的基类,封装"读 prompt → 调一次 LLM → 返回字符串"。
    - BaseAgent    : 5 个主管共用的基类,跑 ReAct 循环、续接 res_id、转发流式专家 packets。

辅助类型:
    - ToolManager  : BaseTool.__subclasses__() 自动发现 + tool_scope 过滤 + async dispatch。
    - StreamPacket : LLM 流响应统一数据载体(reasoning / output / completed / error 四态)。
    - LLMResponse  : 一次性同步调用的返回值。
    - 异常体系     : LLMError / LLMAPIError / LLMTimeoutError / LLMRateLimitError。

配置入口:
    - get_config(), reload_config()
    - AgentRuntime / ARKProvider
    - AGENT_DEFINITIONS / ROLE_DEFAULTS
"""

# 日志(自动初始化)
from .logging_config import get_logger, log_exception, setup_logging

setup_logging()

# 核心抽象
from .base_agent import BaseAgent
from .base_tool import BaseTool
from .config import (
    AGENT_DEFINITIONS,
    LEGACY_ROLE_TO_AGENT,
    ROLE_DEFAULTS,
    AgentRuntime,
    ARKProvider,
    Config,
    get_config,
    reload_config,
)
from .context import (
    get_current_project_id,
    project_context,
    require_current_project_id,
    reset_current_project_id,
    set_current_project_id,
)
from .expert_tool import ExpertTool, PROMPTS_ROOT, StreamSink
from .manager_tool import ManagerTool
from .llm_client import (
    LLMAPIError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    quick_call,
    quick_stream,
)
from .stream_packet import (
    CompletedPayload,
    ConfirmPayload,
    PacketType,
    StreamPacket,
    ToolCallData,
)
from .tool_manager import (
    ToolManager,
    get_tool_manager,
    reset_tool_manager,
)

__all__ = [
    # 日志
    "get_logger",
    "log_exception",
    "setup_logging",
    # 配置
    "AGENT_DEFINITIONS",
    "LEGACY_ROLE_TO_AGENT",
    "ROLE_DEFAULTS",
    "AgentRuntime",
    "ARKProvider",
    "Config",
    "get_config",
    "reload_config",
    # 上下文(project_id 跨任务穿透)
    "get_current_project_id",
    "require_current_project_id",
    "set_current_project_id",
    "reset_current_project_id",
    "project_context",
    # 抽象基类
    "BaseAgent",
    "BaseTool",
    "ExpertTool",
    "ManagerTool",
    # 工具调度
    "ToolManager",
    "get_tool_manager",
    "reset_tool_manager",
    # LLM 客户端
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "LLMAPIError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "quick_call",
    "quick_stream",
    # 流式数据
    "StreamPacket",
    "CompletedPayload",
    "ConfirmPayload",
    "ToolCallData",
    "PacketType",
    "StreamSink",
    # 杂项
    "PROMPTS_ROOT",
]
