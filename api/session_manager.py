"""
api/session_manager.py

Orchestrator 会话管理 — 把 Orchestrator 的异步生成器桥接到 SSE 长连接。

设计:
    - 每个项目(project_id)同一时间只能有一个活跃会话
    - 会话用 asyncio.Queue 作为 packet 缓冲区,SSE 端点从 queue 消费
    - Orchestrator 的 run()/approve() 产出的 StreamPacket 通过 _pump 协程入队
    - confirm 包到达后,_pump 暂停(不结束),等待 approve() 被调用后复用同一 queue 继续
    - 会话超时 30 分钟无活动自动清理

线程安全:
    - 所有方法都是 async,通过 asyncio.Lock 保护 _sessions 字典
    - Queue 本身是线程安全的,不需要额外锁
"""

import asyncio
import time
import uuid
from typing import AsyncIterator, Optional

from core.logging_config import get_logger
from core.stream_packet import StreamPacket
from agents.orchestrator import Orchestrator

logger = get_logger("api.session_manager")

# 会话超时(秒)
SESSION_TIMEOUT = 30 * 60


class OrchestratorSession:
    """
    单个项目的 Orchestrator 会话封装。

    生命周期:
        1. 创建 → start(user_prompt) → _pump 协程开始往 queue 放 packet
        2. SSE 端点从 queue 读取并推送给前端
        3. confirm 包到达 → queue 中出现 confirm → SSE 推给前端 → _pump 协程自然结束
        4. 前端收到 confirm,用户回复后 → approve(reply) → 新的 _pump 协程启动,复用同一 queue
        5. 项目完成或超时 → 会话被清理
    """

    def __init__(self, project_id: str):
        self.session_id = str(uuid.uuid4())[:8]
        self.project_id = project_id
        self.orch = Orchestrator(project_id=project_id)
        self.queue: asyncio.Queue[Optional[StreamPacket]] = asyncio.Queue()
        self._pump_task: Optional[asyncio.Task] = None
        self._last_active = time.time()
        self._closed = False

    # --------------------------------------------------------
    # 启动 / 续接
    # --------------------------------------------------------
    async def start(self, user_prompt: str) -> None:
        """首次启动 Director。"""
        if self._closed:
            raise RuntimeError("会话已关闭")
        self._last_active = time.time()
        self._pump_task = asyncio.create_task(
            self._pump(self.orch.run(user_prompt))
        )
        logger.info(f"Session {self.session_id} 启动 (project={self.project_id})")

    async def approve(self, user_reply: str) -> None:
        """用户确认后续接 Director。"""
        if self._closed:
            raise RuntimeError("会话已关闭")
        if not self.orch.is_waiting_confirm:
            logger.warning(f"Session {self.session_id} approve 时不在等待确认状态")
        self._last_active = time.time()
        self._pump_task = asyncio.create_task(
            self._pump(self.orch.approve(user_reply))
        )
        logger.info(f"Session {self.session_id} 续接 (project={self.project_id})")

    # --------------------------------------------------------
    # 消费端(SSE 用)
    # --------------------------------------------------------
    async def consume(self) -> AsyncIterator[StreamPacket]:
        """从 queue 消费 packet,直到收到 None sentinel。"""
        while True:
            try:
                pkt = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # 超时检查会话是否仍活跃
                if self._closed:
                    break
                continue

            if pkt is None:
                break
            self._last_active = time.time()
            yield pkt

    # --------------------------------------------------------
    # 内部:把 Orchestrator 的 yield 流泵入 queue
    # --------------------------------------------------------
    async def _pump(self, generator: AsyncIterator[StreamPacket]) -> None:
        """将 Orchestrator 的 AsyncIterator 泵入 queue。"""
        try:
            async for pkt in generator:
                await self.queue.put(pkt)
                if pkt.type in ("confirm", "ask_user"):
                    # confirm/ask_user 包之后,Director 的 run() 会自然退出,
                    # 所以 generator 也会结束,_pump 随之结束
                    logger.info(
                        f"Session {self.session_id} {pkt.type} 到达,暂停等待用户"
                    )
                    break
        except Exception as e:
            logger.exception(f"Session {self.session_id} pump 异常")
            await self.queue.put(
                StreamPacket(type="error", content=f"pump 异常: {e}")
            )
        finally:
            # 放 sentinel 告诉 consume 本轮结束(confirm 或完成或错误)
            await self.queue.put(None)

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def is_waiting_confirm(self) -> bool:
        return self.orch.is_waiting_confirm

    @property
    def is_pump_running(self) -> bool:
        return self._pump_task is not None and not self._pump_task.done()

    async def cancel_pump(self) -> None:
        """取消当前 pump task（不会中断 LLM 调用，但会停止向 queue 放包）。"""
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            logger.info(f"Session {self.session_id} pump 取消中...")
            try:
                # 等待 pump task 真正结束，避免并发启动新 pump 导致 response_id 冲突
                await asyncio.wait_for(self._pump_task, timeout=5.0)
            except asyncio.CancelledError:
                logger.info(f"Session {self.session_id} pump 已取消")
            except asyncio.TimeoutError:
                logger.warning(f"Session {self.session_id} pump 取消超时，强制继续")
            except Exception as e:
                logger.warning(f"Session {self.session_id} pump 取消异常: {e}")

    def close(self) -> None:
        """关闭会话,取消 pump 任务。"""
        self._closed = True
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()


class SessionManager:
    """全局会话管理器(单例)。project_id -> OrchestratorSession。"""

    def __init__(self):
        self._sessions: dict[str, OrchestratorSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def get_or_create(self, project_id: str) -> OrchestratorSession:
        async with self._lock:
            if project_id not in self._sessions:
                self._sessions[project_id] = OrchestratorSession(project_id)
            return self._sessions[project_id]

    async def get(self, project_id: str) -> Optional[OrchestratorSession]:
        async with self._lock:
            return self._sessions.get(project_id)

    async def remove(self, project_id: str) -> None:
        async with self._lock:
            sess = self._sessions.pop(project_id, None)
            if sess:
                sess.close()

    async def start_cleanup_loop(self) -> None:
        """启动后台清理协程,每 5 分钟检查一次超时会话。"""
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_worker())

    async def _cleanup_worker(self) -> None:
        while True:
            await asyncio.sleep(300)  # 5 分钟
            now = time.time()
            to_remove = []
            async with self._lock:
                for pid, sess in self._sessions.items():
                    if now - sess._last_active > SESSION_TIMEOUT:
                        to_remove.append(pid)
                for pid in to_remove:
                    sess = self._sessions.pop(pid, None)
                    if sess:
                        sess.close()
                        logger.info(f"会话超时清理: {pid}")


# 全局单例
_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager
