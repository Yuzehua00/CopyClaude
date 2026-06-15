from __future__ import annotations

from copy_claude.core.trace.record import TraceRecord

from pathlib import Path

import asyncio


class TraceWriter:  # 在指定路径生成TraceRecord格式组成的文件，供用户查看。TraceWriter 类是一个典型的异步生产者-消费者模式
    # 利用 asyncio.Queue 和 asyncio.Task 实现非阻塞的、批量化的文件写入。
    def __init__(self, path: Path) -> None:
        self._path = path
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()  # 一个异步队列，用于在线程安全的方式下传递 TraceRecord 对象。
        # put_nowait(item)：立即将元素放入队列，如果队列满则抛出异常（这里不会满）。

        self._task: asyncio.Task[None] | None = None
        # asyncio.create_task 将协程 _drain 包装为 Task，并立即调度执行（不会阻塞当前协程）。
        # 这个任务负责持续消费队列中的数据，写入文件。

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._drain())

    # 等待队列清空后取消 drain task
    async def stop(self) -> None:
        # join() 会阻塞直到队列中所有元素都被消费并调用了 task_done()。这保证了所有已 emit 的记录都被写入文件后才继续。
        await self._queue.join()

        if self._task is not None:
            self._task.cancel()
            # 取消后台任务，这会导致 _drain 中的 while True 循环在下一次 await self._queue.get() 时抛出 CancelledError。
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def emit(self, record: TraceRecord) -> None:  # 生产方法 :将收到的TraceRecord信息记录到队列里。
        # 非阻塞：只负责将记录放入队列，立即返回。
        # 调用者（可能是任意协程或普通函数）不需要await。
        self._queue.put_nowait(record)

    # 持续从队列读取 record 并追加写入文件
    async def _drain(self) -> None:  # 消费者协程
        with open(self._path, "a") as f:
            while True:
                record = await self._queue.get()  # 等待元素：await self._queue.get() 会挂起，直到队列中有新元素。
                try:
                    # 获取到 record 后，转换为 JSON 字符串并写入文件，然后 flush 确保数据立即落盘（避免丢失）。
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()
