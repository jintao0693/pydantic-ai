"""第 0.2 章配套 labs：异步、类型系统与虚拟环境。

本模块**只依赖标准库**（``asyncio``），无需任何第三方包、无需 API Key。

直接运行：:

    uv run --no-project python part_0/ch_0_2.py

运行后会把「串行」与两种并发写法的耗时并排打印出来，并演示超时、异步资源管理
（``async with``）与异步迭代（``async for``）。

核心演示点：

1. 用 ``async def`` / ``await`` 表达 I/O 密集任务；
2. 对比串行、``asyncio.gather`` 与 ``asyncio.TaskGroup`` 的耗时差异；
3. 用 ``asyncio.timeout`` 为单次调用设置超时，超时抛 ``TimeoutError``；
4. 用 ``async with`` 管理异步资源（连接）的获取与释放；
5. 用 ``async for`` 消费异步迭代器与异步生成器。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from time import perf_counter
from types import TracebackType

#: 被"抓取"的模拟服务名。
SERVICE_NAMES: tuple[str, ...] = ('auth', 'orders', 'search', 'profile')


async def fetch(service: str, delay: float) -> tuple[str, float]:
    """模拟一次 I/O 密集调用（例如一次 LLM 请求或一次数据库查询）。

    ``asyncio.sleep`` 在等待期间**不占用线程**：事件循环会在这段时间里切换到
    其他就绪的协程，这正是「并发」得以发生的地方。
    """
    await asyncio.sleep(delay)
    return service, delay


async def fetch_serial(services: Mapping[str, float]) -> list[tuple[str, float]]:
    """逐个 ``await``：总耗时 ≈ 各次延迟之和（没有任何并发）。"""
    return [await fetch(name, delay) for name, delay in services.items()]


async def fetch_many_gather(services: Mapping[str, float]) -> list[tuple[str, float]]:
    """用 ``asyncio.gather`` 并发执行，返回值顺序与传入顺序一致。"""
    return list(await asyncio.gather(*(fetch(name, delay) for name, delay in services.items())))


async def fetch_many_taskgroup(services: Mapping[str, float]) -> list[tuple[str, float]]:
    """用 ``asyncio.TaskGroup`` 并发执行，并按输入顺序收集结果。

    ``TaskGroup`` 是结构化并发的首选写法：任一子任务抛异常时，其余子任务会被
    自动取消，且异常会以 ``ExceptionGroup`` 的形式向外传播。
    """
    results: dict[str, tuple[str, float]] = {}

    async def worker(name: str, delay: float) -> None:
        results[name] = await fetch(name, delay)

    async with asyncio.TaskGroup() as tg:
        for name, delay in services.items():
            tg.create_task(worker(name, delay))

    return [results[name] for name in services]


async def fetch_with_timeout(service: str, delay: float, timeout: float) -> tuple[str, float]:
    """带超时的单次调用：超过 ``timeout`` 秒抛 ``TimeoutError``。

    ``asyncio.timeout`` 会在超时时**取消**内部正在等待的任务，并把 ``CancelledError``
    转换为 ``TimeoutError`` 抛出。
    """
    async with asyncio.timeout(timeout):
        return await fetch(service, delay)


class AsyncConnection:
    """最小的异步资源：进入时"打开连接"，退出时"关闭连接"。

    无论 ``async with`` 块内是否抛异常，``__aexit__`` 都会被执行，
    因此它是释放连接、文件句柄、事务等资源的可靠位置。
    """

    def __init__(self, name: str, delay: float = 0.0) -> None:
        self.name = name
        self.delay = delay
        self.opened = False
        self.closed = False

    async def __aenter__(self) -> AsyncConnection:
        await asyncio.sleep(self.delay)
        self.opened = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await asyncio.sleep(self.delay)
        self.closed = True

    async def query(self, sql: str) -> str:
        if not self.opened:
            raise RuntimeError('connection is not open')
        await asyncio.sleep(self.delay)
        return f'{self.name}: result of {sql!r}'


class Countdown:
    """异步迭代器：从 ``start`` 倒数到 1，末尾抛 ``StopAsyncIteration`` 结束。"""

    def __init__(self, start: int) -> None:
        self._current = start

    def __aiter__(self) -> Countdown:
        return self

    async def __anext__(self) -> int:
        if self._current <= 0:
            raise StopAsyncIteration
        await asyncio.sleep(0)  # 让出控制权，模拟"每个元素都来自一次异步来源"
        value = self._current
        self._current -= 1
        return value


async def stream_countdown(start: int, delay: float = 0.0) -> AsyncIterator[int]:
    """异步生成器：与 ``Countdown`` 等价的、更常见的写法。"""
    for value in range(start, 0, -1):
        await asyncio.sleep(delay)
        yield value


def random_services(names: Sequence[str] = SERVICE_NAMES) -> dict[str, float]:
    """为每个服务生成一个 0.1~0.4 秒的随机延迟，模拟真实网络的抖动。"""
    return {name: random.uniform(0.1, 0.4) for name in names}


async def main() -> None:
    services = random_services()
    print('模拟服务延迟（秒）:', {name: round(delay, 3) for name, delay in services.items()})

    # 1) 串行：总耗时 ≈ 各延迟之和
    start = perf_counter()
    serial = await fetch_serial(services)
    serial_dt = perf_counter() - start

    # 2) asyncio.gather：并发，总耗时 ≈ 最慢的一个
    start = perf_counter()
    gathered = await fetch_many_gather(services)
    gather_dt = perf_counter() - start

    # 3) asyncio.TaskGroup：并发 + 结构化清理，结果一样
    start = perf_counter()
    grouped = await fetch_many_taskgroup(services)
    group_dt = perf_counter() - start

    assert gathered == grouped == serial

    print(f'串行       : {serial_dt:.3f}s  ({len(serial)} 个结果)')
    print(f'gather     : {gather_dt:.3f}s  (加速 {serial_dt / gather_dt:.1f}x)')
    print(f'TaskGroup  : {group_dt:.3f}s  (加速 {serial_dt / group_dt:.1f}x)')

    # 4) 超时：0.5s 的调用在 0.1s 后被取消并抛 TimeoutError
    try:
        await fetch_with_timeout('slow', delay=0.5, timeout=0.1)
    except TimeoutError:
        print('超时演示   : 0.5s 的调用在 0.1s 超时后抛出 TimeoutError')

    # 5) async with：资源在退出时被可靠释放
    async with AsyncConnection('db', delay=0.02) as conn:
        print('async with :', await conn.query('select 1'))
    print(f'资源释放   : opened={conn.opened}, closed={conn.closed}')

    # 6) async for：消费异步迭代器
    async for value in Countdown(3):
        print('async for  :', value)

    # 7) async for：消费异步生成器
    print('生成器     :', [value async for value in stream_countdown(3)])


if __name__ == '__main__':
    asyncio.run(main())
