"""
core/context.py

跨调用栈的"当前项目 ID"上下文 — 用 contextvars.ContextVar 把 project_id 在
请求/任务级别穿透传递,避免每个 tool 的 input_schema 都强制要求 LLM 给 project_id 字段。

为什么不让 LLM 传 project_id:
    1. 主管的 system prompt 已经在限定"你正在为项目 X 工作",再让 LLM 反复念一遍
       project_id 既浪费 token 又容易出现拼写错误。
    2. 每个 tool 的 schema 多一个固定字段会污染 LLM 的注意力。
    3. project_id 是上下文级常量,放进 ContextVar 后 tool 实现里 `get_current_project_id()`
       即可获取,与 FastAPI 的请求作用域天然契合。

为什么不用模块全局变量:
    asyncio 多任务并发时,模块全局会被互相覆盖。ContextVar 是 PEP 567 设计的"携带跨
    await 的上下文"的标准答案,asyncio.Task 之间不串味。

绑定时机:
    - HTTP 入口(FastAPI 路由)在生成 Orchestrator 前 `set_current_project_id(pid)`
    - CLI 入口(脚本)在 main 里同上
    - 单元测试用 `with project_context(pid):` 装饰整个测试块

读取时机:
    - 每个 KB 工具 execute 头部 `pid = get_current_project_id()`,空值时抛 ValueError
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

# 模块级 ContextVar 实例。default=None 表示未设置时读到 None,
# 而不是 LookupError——便于工具优雅地报错"还没指定项目"。
_current_project_id: ContextVar[Optional[str]] = ContextVar(
    "mans_current_project_id", default=None
)


def set_current_project_id(project_id: Optional[str]) -> "ContextVarToken":
    """
    设置当前线程/任务的项目 ID。

    返回的 token 可传给 `reset_current_project_id(token)` 用于显式回滚到上一个值
    (一般业务代码不需要,首选 `with project_context(...):`)。
    """
    token = _current_project_id.set(project_id)
    return token  # type: ignore[return-value]


def reset_current_project_id(token: "ContextVarToken") -> None:
    """显式回滚到设置前的值。配合 set_current_project_id 使用。"""
    _current_project_id.reset(token)  # type: ignore[arg-type]


def get_current_project_id() -> Optional[str]:
    """
    读取当前生效的项目 ID。

    没有设置时返回 None。工具实现层应判空并抛 ValueError 提示
    "ContextVar 未注入 project_id"。
    """
    return _current_project_id.get()


def require_current_project_id() -> str:
    """
    读取当前生效的项目 ID,空值直接抛 ValueError。

    工具 execute 入口的标准用法:
        pid = require_current_project_id()
    """
    pid = _current_project_id.get()
    if not pid:
        raise ValueError(
            "ContextVar 未注入 project_id。请确认调用方在调用工具前已经使用 "
            "`with project_context(pid):` 或 `set_current_project_id(pid)`。"
        )
    return pid


@contextmanager
def project_context(project_id: str) -> Iterator[str]:
    """
    将一段代码包裹在指定项目 ID 的上下文中。

    示例:
        with project_context("abc123"):
            await some_tool.execute(...)

    退出时自动回滚 ContextVar 到进入前的值,保证嵌套场景下父上下文不被污染。
    """
    if not project_id:
        raise ValueError("project_id 不能为空字符串")
    token = _current_project_id.set(project_id)
    try:
        yield project_id
    finally:
        _current_project_id.reset(token)


# 类型别名(让 set/reset 的签名清晰一些,无运行时含义)
ContextVarToken = object
