"""
core/stream_packet.py

LLM 流式响应统一数据载体。

把 OpenAI Responses SSE 事件流(reasoning_summary_text、output_text、function_call、completed、
error、incomplete 等)归并为四态:reasoning / output / completed / error。

任何流式调用方都消费 StreamPacket 而非裸事件,这样:
    1. 屏蔽 ARK / OpenAI 等不同 Provider 的事件命名差异。
    2. 前端 SSE 可按 type 直接分流到「思考区」「正文区」「状态区」。
    3. 工具调用、token 用量、续接 res_id 都收敛在 completed.content。

参考实现:D:\\AI协作任务\\NovelAgent\\core\\LLM_api.py
"""

from typing import List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class ToolCallData(BaseModel):
    """
    LLM 在 completed 事件中产生的单次工具调用指令。

    字段:
        call_id: ARK/OpenAI 分配的调用 ID,工具执行结果必须带回此 ID 以便 LLM 关联。
        name: 工具名(对应 BaseTool.name)。
        arguments: JSON 字符串形式的参数,由 ToolManager 反序列化。
    """
    model_config = ConfigDict(extra="allow")

    call_id: str
    name: str
    arguments: str


class CompletedPayload(BaseModel):
    """
    response.completed 事件的载荷。

    字段:
        res_id: 本次响应的 ID,作为下一轮的 previous_response_id 续接。
        total_tokens: 本次累计消耗的 token 数(input + output)。
        tool_calls: 本轮 LLM 决定调用的所有工具,空列表表示 ReAct 循环可以退出。
        output_types: 本轮响应中包含的输出类型列表(如 ["reasoning", "output_text", "function_call"]),
                      供前端了解本轮有哪些类型的输出。
    """
    model_config = ConfigDict(extra="allow")

    res_id: str
    total_tokens: int = 0
    tool_calls: List[ToolCallData] = []
    output_types: List[str] = Field(default_factory=list)


class ConfirmPayload(BaseModel):
    """
    Director 调用 confirm_stage_advance 时透出的确认请求载荷。

    字段:
        from_stage: 当前阶段(如 "INIT" / "PLAN" / "WRITE")
        to_stage: 待进入阶段(如 "PLAN" / "WRITE")
        summary: 当前阶段成果摘要(供用户决策)
        prompt: 给用户的确认问句
        previous_response_id: Director 在发出确认时的最后 res_id,
                              Orchestrator 续会话时传入
        from_chapter: WRITE 阶段跨章节时使用(可选)
        pending_outputs: function_call_output 列表(供 Orchestrator 续会话时重播)
    """
    model_config = ConfigDict(extra="allow")

    from_stage: str
    to_stage: str
    summary: str
    prompt: str
    previous_response_id: str = ""
    from_chapter: Optional[int] = None
    pending_outputs: List[dict] = []


PacketType = Literal["reasoning", "output", "completed", "error", "confirm"]


class StreamPacket(BaseModel):
    """
    流式响应统一数据包。

    type 取值:
        reasoning  — 深度思考摘要 token,可单独渲染(灰色折叠区等)。
        output     — 正文 token,直接拼接给用户/Writer 落盘。
        completed  — 本轮响应终结,content 必为 CompletedPayload,持有 res_id 与 tool_calls。
        error      — 流式过程中出现异常,content 为错误描述字符串。
        confirm    — 阶段间确认请求,content 为 ConfirmPayload,供 Orchestrator 拦截并弹窗。

    序列化:
        Pydantic v2 模型,可直接 .model_dump_json() 推送给前端。
    """
    model_config = ConfigDict(extra="allow")

    type: PacketType
    content: Union[str, CompletedPayload, ConfirmPayload]
    agent_name: str = ""
