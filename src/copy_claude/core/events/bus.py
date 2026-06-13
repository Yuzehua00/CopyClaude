from __future__ import annotations
from collections.abc import Awaitable, Callable # 获取类型可等待和可调用
from pydantic import BaseModel # 所有事件都是基于pydantic.BaseModel，便于检查核验
from typing import List
type EventHandler = Callable[[BaseModel], Awaitable[None]] # 声明类型EventHandler是可调用的，传入BaseModel返回可等待对象（协程）。
class EventBus: # 广播处理器，它负责给所有的订阅者发送消息。
    def __init__(self)->None:
        self._subscribers:List[EventHandler] = []
    def subscribe(self, handler: EventHandler)->None:
        self._subscribers.append(handler)

    async def publish(self, event: BaseModel)->None:
        for handler in self._subscribers:
            await handler(event)