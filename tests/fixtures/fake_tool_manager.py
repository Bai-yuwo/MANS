"""
fake_tool_manager.py — 测试用伪 ToolManager

模拟 core.tool_manager.ToolManager 的核心接口:
  - get(name) → BaseTool | None
  - has(name) → bool
  - filter_by_scope(scope) → list[schema]
  - handle_tool_calls(tool_calls) → list[function_call_output]

用法:
    tm = FakeToolManager()
    tm.register("read_bible", lambda args, pid: json.dumps({"world": "测试"}))
    outputs = await tm.handle_tool_calls([
        ToolCallData(name="read_bible", arguments="{}", call_id="c1")
    ])
"""

import json
from typing import Any, Callable, Optional

from core.stream_packet import ToolCallData

Handler = Callable[[dict, Optional[str]], str]


class _FakeTool:
    """FakeToolManager 内部使用的伪工具对象。"""

    def __init__(self, name: str, handler: Handler, schema: dict):
        self.name = name
        self.handler = handler
        self.schema = schema

    async def execute(self, **kwargs) -> str:
        return self.handler(kwargs, kwargs.get("project_id"))


class FakeToolManager:
    """可注册假工具的伪 ToolManager。"""

    def __init__(self):
        self._tools: dict[str, _FakeTool] = {}
        self.call_log: list[dict] = []

    def register(
        self, name: str, handler: Handler, schema: Optional[dict] = None
    ) -> "FakeToolManager":
        """
        注册一个伪工具。

        handler 签名: fn(args: dict, project_id: str | None) -> str
        """
        default_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Fake tool {name}",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        self._tools[name] = _FakeTool(
            name=name, handler=handler, schema=schema or default_schema
        )
        return self

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Optional[_FakeTool]:
        return self._tools.get(name)

    def filter_by_scope(self, scope) -> list[dict]:
        result = []
        for name in scope:
            tool = self._tools.get(name)
            if tool:
                result.append(tool.schema)
        return result

    async def handle_tool_calls(
        self, tool_calls
    ) -> list[dict]:
        outputs = []
        for call in tool_calls:
            self.call_log.append(
                {"tool": call.name, "args": call.arguments, "call_id": call.call_id}
            )
            tool = self._tools.get(call.name)
            if tool is None:
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(
                            {"error": f"未知工具 '{call.name}'"},
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            try:
                kwargs = json.loads(call.arguments) if call.arguments else {}
            except json.JSONDecodeError as e:
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(
                            {"error": f"参数解析失败: {e}"},
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            try:
                result = await tool.execute(**kwargs)
            except Exception as e:
                result = json.dumps(
                    {"error": f"{type(e).__name__}: {e}"},
                    ensure_ascii=False,
                )
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str),
                }
            )
        return outputs
