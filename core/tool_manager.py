"""
core/tool_manager.py

工具调度中心 — `BaseTool.__subclasses__()` 自动发现 + tool_scope 过滤 + 异步 dispatch。

设计要点:
    1. **自动发现**:进程启动时 import 完所有 `tools/` 模块后,`ToolManager()` 即可扫到全部 `BaseTool`
       子类。新增 tool 不需要手动注册,但**必须 import 模块本身**(由 `tools/__init__.py` 统一导入)。
    2. **递归扫描**:`BaseTool` 子类可能再被继承(`ExpertTool` → `WriterTool`),只取直接可实例化的
       叶子类(via `inspect.isabstract`)。
    3. **tool_scope 过滤**:每个主管持有一个 tool 名列表,通过 `filter_by_scope` 取出对应 schemas;
       让一个 ToolManager 同时服务多个主管,避免每个主管重新发现工具。
    4. **异步 dispatch**:`handle_tool_calls` 是 `async`(因为 `BaseTool.execute` 是 async),返回
       OpenAI Responses API 标准的 `function_call_output` 列表,可以直接作为下一轮 stream_call 的
       `input_data`。
    5. **错误兜底**:工具执行抛错被捕获,统一封装成 JSON `{"error": "..."}` 字符串返回给 LLM —— LLM
       看到错误信息后通常会自我纠正(改参数重试或换工具)。

参考:`D:\\AI协作任务\\NovelAgent\\core\\manager.py`(改造点:execute 由同步改为异步,
新增 tool_scope 过滤)。
"""

import inspect
import json
from typing import Iterable, Optional

from core.base_tool import BaseTool
from core.logging_config import get_logger
from core.stream_packet import ToolCallData

logger = get_logger("core.tool_manager")


class ToolManager:
    """
    自动发现 + 调度所有 `BaseTool` 子类。

    典型用法:
        # 进程级单例
        manager = ToolManager()

        # 主管发起一轮调用前
        scope_schemas = manager.filter_by_scope(my_agent.tool_scope)

        # 收到 LLM 的 tool_calls 后
        outputs = await manager.handle_tool_calls(tool_calls)
        # outputs 可直接作为下一轮 stream_call 的 input_data
    """

    def __init__(self, exclude: Optional[Iterable[str]] = None):
        """
        Args:
            exclude: 可选,跳过指定 tool name(用于测试或灰度禁用)。
        """
        self._tools: dict[str, BaseTool] = {}
        excluded = set(exclude or [])

        for cls in self._iter_concrete_subclasses(BaseTool):
            # 跳过基础模板类(如 ExpertTool 自身):它们没声明 expert_name 等业务字段,
            # 实例化必然抛 ValueError。直接按类属性判定,避免日志噪音。
            if self._is_template_base(cls):
                continue
            try:
                instance = cls()
            except Exception as e:
                logger.warning(f"工具 {cls.__name__} 实例化失败,已跳过: {e}")
                continue
            name = instance.name
            if name in excluded:
                continue
            if name in self._tools:
                logger.warning(
                    f"工具名冲突 '{name}': 已有 {type(self._tools[name]).__name__},"
                    f"忽略 {cls.__name__}"
                )
                continue
            self._tools[name] = instance

        logger.info(f"ToolManager 初始化:发现 {len(self._tools)} 个工具")
        if logger.isEnabledFor(10):  # DEBUG
            for n in sorted(self._tools.keys()):
                logger.debug(f"  · {n}")

    @staticmethod
    def _iter_concrete_subclasses(base: type) -> Iterable[type]:
        """
        递归遍历 base 的所有子类,只 yield 非抽象的具体类。

        避免重复(同一类经多重继承可能被多次访问)。
        """
        seen: set[type] = set()
        stack = list(base.__subclasses__())
        while stack:
            cls = stack.pop()
            if cls in seen:
                continue
            seen.add(cls)
            stack.extend(cls.__subclasses__())
            if not inspect.isabstract(cls):
                yield cls

    @staticmethod
    def _is_template_base(cls: type) -> bool:
        """
        判断类是否是"模板基类",不应被实例化注册。

        判定:
            - 类自身定义了 `expert_name` 且为空字符串 → ExpertTool 基类
            - 类自身定义了 `target_manager_class` 且为 None → ManagerTool 基类
        """
        if "expert_name" in vars(cls) and getattr(cls, "expert_name", "") == "":
            return True
        if "target_manager_class" in vars(cls) and getattr(cls, "target_manager_class", None) is None:
            return True
        return False

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------
    @property
    def all_schemas(self) -> list[dict]:
        """全部已发现工具的 schemas(调试/管理面板使用)。生产路径请用 `filter_by_scope`。"""
        return [t.schema for t in self._tools.values()]

    @property
    def all_names(self) -> list[str]:
        """全部已发现工具名,排序后输出。"""
        return sorted(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def filter_by_scope(self, scope: Iterable[str]) -> list[dict]:
        """
        按 tool_scope 列表筛选 schemas。

        scope 中存在但未发现的工具名会发 warning 但不抛错(便于灰度上线新主管时
        部分 tool 还在开发中)。

        Returns:
            list[dict]: 顺序与 scope 一致(主管的 prompt 中可能依赖 tools 顺序)。
        """
        result: list[dict] = []
        missing: list[str] = []
        for name in scope:
            tool = self._tools.get(name)
            if tool is None:
                missing.append(name)
                continue
            result.append(tool.schema)
        if missing:
            logger.warning(f"tool_scope 中以下工具未发现,已跳过: {missing}")
        return result

    # --------------------------------------------------------
    # 调度接口
    # --------------------------------------------------------
    async def _execute_single(self, tool_name: str, arguments_json: str) -> str:
        """
        执行单个工具,所有错误都包装为 JSON 字符串返回给 LLM。

        约定:返回的 JSON 至少含 `error` 字段时,调用方应认为工具失败。
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"未知工具 '{tool_name}'"}, ensure_ascii=False)
        try:
            kwargs = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as e:
            return json.dumps(
                {"error": f"参数 JSON 解析失败: {e}", "raw": arguments_json[:200]},
                ensure_ascii=False,
            )
        try:
            result = await tool.execute(**kwargs)
        except TypeError as e:
            return json.dumps(
                {"error": f"工具参数不匹配: {e}", "tool": tool_name},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception(f"工具 {tool_name} 执行异常")
            return json.dumps(
                {"error": f"{type(e).__name__}: {e}", "tool": tool_name},
                ensure_ascii=False,
            )

        # execute 必须返回 str,但生产中可能有人忘记或返回 dict。做一次保护性序列化。
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            return str(result)

    async def handle_tool_calls(
        self, tool_calls: Iterable[ToolCallData]
    ) -> list[dict]:
        """
        批量执行 LLM 给出的 tool_calls,返回 function_call_output 列表。

        每个返回项格式严格遵循 OpenAI Responses API:
            {
                "type": "function_call_output",
                "call_id": <对应 tool_call 的 call_id>,
                "output": <execute 返回的字符串>
            }

        Args:
            tool_calls: completed packet 中携带的 ToolCallData 列表。

        Returns:
            list[dict]: 可直接作为下一轮 stream_call 的 input_data。

        注意:工具按声明顺序**串行执行**(顺序对部分 KB 写入工具有意义,
        如先写主体再追加索引)。需要并行时调用方自行 asyncio.gather。
        """
        outputs: list[dict] = []
        for call in tool_calls:
            logger.info(f"工具调用 -> {call.name} (call_id={call.call_id})")
            output_str = await self._execute_single(call.name, call.arguments)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output_str,
                }
            )
        return outputs


_tool_manager: Optional[ToolManager] = None


def get_tool_manager() -> ToolManager:
    """
    获取全局 ToolManager 单例。

    首次调用时扫描 `BaseTool.__subclasses__()`,因此**必须在所有 tool 模块 import 之后**
    再调用。Orchestrator 启动流程通常先 `import tools` 触发自动注册,再走这里。
    """
    global _tool_manager
    if _tool_manager is None:
        _tool_manager = ToolManager()
    return _tool_manager


def reset_tool_manager() -> None:
    """丢弃 ToolManager 单例(主要供测试用例使用)。"""
    global _tool_manager
    _tool_manager = None
