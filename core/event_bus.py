"""
MANS 事件总线模块

提供全局异步事件发布/订阅机制，支持 SSE 流式输出。
基于 Python 3.10+ asyncio 实现。
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncGenerator, Callable


class EventType(Enum):
    """
    系统事件类型枚举
    
    定义了 MANS 系统中所有可能的事件类型，
    用于事件的分类和过滤。
    """
    # 系统级信息事件
    SYSTEM_INFO = "system_info"
    # Agent 启动事件
    AGENT_START = "agent_start"
    # Prompt 构建完成事件
    PROMPT_BUILT = "prompt_built"
    # LLM 流式输出 token 事件
    LLM_STREAM_TOKEN = "llm_stream_token"
    # LLM 输出结束事件
    LLM_END = "llm_end"
    # 错误事件
    ERROR = "error"


@dataclass
class Event:
    """
    事件数据类
    
    用于封装所有事件的通用数据结构，
    包含事件类型、负载数据、时间戳和唯一标识。
    
    Attributes:
        event_type: 事件类型，参考 EventType 枚举
        payload: 事件携带的数据载荷，字典格式
        timestamp: 事件发生的时间戳
        event_id: 事件的唯一标识符
    """
    event_type: EventType
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class EventBus:
    """
    全局单例事件总线
    
    提供异步的事件发布和订阅能力，支持多个并发订阅者。
    事件通过 asyncio.Queue 实现线程安全的异步传递。
    
    Usage:
        # 获取单例实例
        bus = EventBus()
        
        # 发布事件
        await bus.publish(EventType.SYSTEM_INFO, {"message": "Hello"})
        
        # 订阅事件
        async for event in bus.subscribe():
            print(event)
    """
    
    _instance: "EventBus | None" = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    
    def __new__(cls) -> "EventBus":
        """
        实现单例模式，确保全局只有一个 EventBus 实例
        
        Returns:
            EventBus: 单例实例
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        """
        初始化事件总线
        
        仅在首次初始化时执行，防止重复初始化。
        """
        if self._initialized:
            return
        self._initialized = True
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers: dict[str, asyncio.Queue[Event]] = {}
        self._sub_lock = asyncio.Lock()
    
    async def publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """
        发布事件到事件总线
        
        创建一个新的 Event 对象并放入所有订阅者的队列中。
        
        Args:
            event_type: 事件类型，参考 EventType 枚举
            payload: 事件携带的数据载荷，字典格式
        
        Example:
            await bus.publish(EventType.LLM_STREAM_TOKEN, {"token": "Hello"})
        """
        event = Event(event_type=event_type, payload=payload)
        
        async with self._sub_lock:
            subscriber_items = list(self._subscribers.items())
        
        # 将事件分发到所有订阅者的队列
        for sub_id, sub_queue in subscriber_items:
            try:
                sub_queue.put_nowait(event)
            except asyncio.QueueFull:
                # 如果队列已满，跳过该订阅者
                pass
    
    async def subscribe(self, buffer_size: int = 100) -> AsyncGenerator[Event, None]:
        """
        订阅事件流（异步生成器）
        
        创建一个新的订阅者队列，并持续 yield 事件。
        适用于 SSE 流式输出场景。
        
        Args:
            buffer_size: 队列缓冲区大小，默认为 100
        
        Yields:
            Event: 依次获取的事件对象
        
        Example:
            async for event in bus.subscribe():
                if event.event_type == EventType.LLM_STREAM_TOKEN:
                    print(event.payload["token"], end="")
        """
        sub_queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=buffer_size)
        sub_id = str(uuid.uuid4())
        
        async with self._sub_lock:
            self._subscribers[sub_id] = sub_queue
        
        try:
            while True:
                try:
                    # 等待并获取事件，设置超时以支持优雅退出
                    event = await asyncio.wait_for(sub_queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    # 超时时继续等待，实现心跳检测
                    continue
        except GeneratorExit:
            # 生成器退出时清理订阅者
            pass
        finally:
            async with self._sub_lock:
                self._subscribers.pop(sub_id, None)
    
    async def close(self) -> None:
        """
        关闭事件总线
        
        清空所有订阅者队列，释放资源。
        """
        async with self._sub_lock:
            self._subscribers.clear()
        
        # 清空主队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# 全局单例实例
event_bus = EventBus()
