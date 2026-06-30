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
            try:
                await handler(event)
                if event.type == "session.waiting_for_input":
                    print(f"Handler: {handler}")
            except Exception as e:
                # 1. 打印到控制台，立即看到错误
                print(f"[EVENT BUS ERROR] Handler  failed for event {type(event).__name__}")
                print(f"Handler: {handler}")
                # 2. 如果有 logger，记录到文件
                # logging.error(f"Handler {handler} failed: {e}", exc_info=True)

                # 3. 关键决策：是否继续执行其他 handler？
                # 如果业务要求“一个失败不影响其他”，可以 continue；
                # 如果要求“必须全部成功才算成功”，则 raise。
                continue  # 当前保持原逻辑，让 create 失败，便于你发现错误