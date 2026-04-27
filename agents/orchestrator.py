"""
agents/orchestrator.py

MANS 系统入口 — Orchestrator。

职责:
    1. 初始化 ToolManager(import tools 触发自动发现)
    2. 实例化 Director,提供 run/resume 入口
    3. 拦截 Director 的 confirm 包,保存会话状态供外部续接
    4. 所有 packets(包括 Writer 流式、confirm 请求)原样 yield 给上层(API/SSE/CLI)

与前端/API 的协作:
    - Orchestrator.run(user_prompt) → AsyncIterator[StreamPacket]
    - 上层代码消费 yield 流:
        - reasoning/output → 渲染到对应频道
        - confirm → 弹窗/等待用户输入
        - completed/error → 状态更新
    - 用户确认后: Orchestrator.approve(user_reply) → 续接 Director 会话

线程模型:
    - Orchestrator 是轻量级状态机,不跑 LLM,只转发 Director 的 yield 流
    - Director 的 LLMClient 是类级单例,ToolManager 是进程级单例
    - 当前实现不处理并发项目(单用户 web 场景)
"""

from typing import AsyncIterator, Optional

from core import BaseAgent
from core.context import set_current_project_id
from core.logging_config import get_logger
from core.stream_packet import ConfirmPayload, StreamPacket
from core.tool_manager import get_tool_manager
from agents.managers.director import Director

logger = get_logger("agents.orchestrator")


class Orchestrator:
    """
    MANS 系统入口。封装 Director 生命周期管理与 confirm 续接。

    用法:
        orch = Orchestrator(project_id="proj_abc")
        async for packet in orch.run("开始写一部玄幻小说的世界观"):
            if packet.type == "confirm":
                payload = packet.content  # ConfirmPayload
                # 弹窗等待用户...
                # 用户回复后:
                async for p2 in orch.approve("同意,进入下一阶段"):
                    yield p2
            else:
                yield packet  # 转发到 SSE
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._director: Optional[Director] = None
        self._confirm_payload: Optional[ConfirmPayload] = None
        self._last_response_id: str = ""

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    async def run(
        self,
        user_prompt: str,
        *,
        previous_response_id: Optional[str] = None,
    ) -> AsyncIterator[StreamPacket]:
        """
        启动或续接一次 Director 会话。

        Args:
            user_prompt: 给 Director 的 user 内容(首次启动时放需求描述,续接时放用户回复)
            previous_response_id: 续接时传入(内部状态已保存时不需要外部传)

        Yields:
            StreamPacket: 所有 Director 产出的包(含子主管透传的流式 token)
        """
        set_current_project_id(self.project_id)

        # 确保 ToolManager 已初始化(先 import tools 再 get)
        import tools  # noqa: F401

        tm = get_tool_manager()
        if self._director is None:
            self._director = Director(tool_manager=tm)

        res_id = previous_response_id or self._last_response_id

        logger.info(
            f"Orchestrator 启动 Director (project={self.project_id}, "
            f"res_id={'续接' if res_id else '新开'})"
        )

        async for packet in self._director.run(
            user_prompt=user_prompt,
            previous_response_id=res_id or None,
        ):
            if packet.type == "confirm":
                self._confirm_payload = packet.content
                self._last_response_id = self._director.last_response_id
                logger.info(
                    f"Orchestrator 拦截 confirm: "
                    f"{self._confirm_payload.from_stage} → {self._confirm_payload.to_stage}"
                )
            yield packet

    # --------------------------------------------------------
    # 用户确认后续接
    # --------------------------------------------------------
    async def approve(
        self,
        user_reply: str,
        *,
        previous_response_id: Optional[str] = None,
    ) -> AsyncIterator[StreamPacket]:
        """
        用户确认后,续接 Director 会话。

        Args:
            user_reply: 用户回复(如"同意,进入 PLAN 阶段"或"修改...")
            previous_response_id: 可选覆盖,默认使用内部保存的 last_response_id

        Yields:
            StreamPacket: Director 续接后的所有包
        """
        if self._director is None:
            raise RuntimeError("Orchestrator 尚未启动,请先调用 run()")

        set_current_project_id(self.project_id)
        res_id = previous_response_id or self._last_response_id

        # 清除 confirm 状态
        self._confirm_payload = None

        logger.info(f"Orchestrator 续接 Director (res_id={'续接' if res_id else '新开'})")

        async for packet in self._director.run(
            user_prompt=user_reply,
            previous_response_id=res_id or None,
        ):
            if packet.type == "confirm":
                self._confirm_payload = packet.content
                self._last_response_id = self._director.last_response_id
                logger.info(
                    f"Orchestrator 拦截 confirm: "
                    f"{self._confirm_payload.from_stage} → {self._confirm_payload.to_stage}"
                )
            yield packet

    # --------------------------------------------------------
    # 查询状态
    # --------------------------------------------------------
    @property
    def is_waiting_confirm(self) -> bool:
        return self._confirm_payload is not None

    @property
    def confirm_payload(self) -> Optional[ConfirmPayload]:
        return self._confirm_payload

    @property
    def last_response_id(self) -> str:
        return self._last_response_id

    @property
    def director(self) -> Optional[Director]:
        return self._director
