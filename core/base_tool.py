"""
core/base_tool.py

所有 Agent 工具的抽象基类。

设计契约(强约束):
    1. 子类必须实现 name(全局唯一)、description、schema、execute 四个成员。
    2. schema 必须返回 OpenAI Responses API 的 Tools 格式:
        {
            "type": "function",
            "name": <self.name>,
            "description": <自然语言说明>,
            "parameters": {<JSON Schema>}
        }
    3. execute 必须为 async,返回值必须是 str(JSON 字符串或纯文本)。
       —— 因为 LLM 会把这个字符串作为 function_call_output.output 字段读回。
    4. 工具不维护权限,权限由各 Agent 通过 tool_scope 列表过滤。
       —— 同一个 Tool 类可以同时在多个 Agent 的 scope 中。

发现机制:
    - ToolManager 通过 BaseTool.__subclasses__() 自动扫描所有已导入的子类。
    - 因此使用前必须 import 对应的 tool 模块(由 tools/__init__.py 统一负责)。

错误处理:
    - execute 抛出的异常会被 ToolManager 捕获并转为 JSON error 字符串返回给 LLM。
    - 子类无需自己 try/except 框死结果,但建议在 execute 内显式处理可恢复的业务错误。

参考:D:\\AI协作任务\\NovelAgent\\Tools\\base.py(改造点:execute 由同步改为异步)
"""

from abc import ABC, abstractmethod


class BaseTool(ABC):
    """所有 Agent 工具的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识,通常用「分组.动作」命名,如 'character.read_card'、'world.append_rule'。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """供 LLM 决策时阅读的简短说明,1-2 句话。"""

    @property
    @abstractmethod
    def schema(self) -> dict:
        """
        OpenAI Responses Tools 格式的 schema。

        典型形态:
            {
                "type": "function",
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": { ... },
                    "required": [ ... ]
                }
            }
        """

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """实际执行逻辑。返回值必须是 str(JSON 序列化结果或纯文本)。"""
