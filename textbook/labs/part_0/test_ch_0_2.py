"""第 0.2 章验收测试：异步、类型系统与虚拟环境。

约定：使用**同步** pytest（不依赖 pytest-asyncio），在测试函数内部用 ``asyncio.run``
驱动协程。这样测试的入口是普通函数，事件循环的生命周期一目了然。

运行：:

    uv run --no-project --with pytest python -m pytest part_0/test_ch_0_2.py -q
"""

from __future__ import annotations

import asyncio
import time

import pytest

import ch_0_2 as lab

#: 全部服务使用相同延迟，便于对比串行与并发的总耗时。
DELAYS = {'auth': 0.05, 'orders': 0.05, 'search': 0.05, 'profile': 0.05}


def test_gather_is_concurrent_and_preserves_order():
    async def scenario():
        start = time.perf_counter()
        serial = await lab.fetch_serial(DELAYS)
        serial_dt = time.perf_counter() - start

        start = time.perf_counter()
        gathered = await lab.fetch_many_gather(DELAYS)
        gather_dt = time.perf_counter() - start
        return serial, serial_dt, gathered, gather_dt

    serial, serial_dt, gathered, gather_dt = asyncio.run(scenario())

    assert gather_dt < serial_dt / 2  # 并发总耗时显著小于串行
    assert [name for name, _ in gathered] == list(DELAYS)  # 结果顺序与输入一致
    assert gathered == serial  # 并发结果与串行结果完全一致


def test_taskgroup_collects_results_in_order():
    services = {'x': 0.01, 'y': 0.02, 'z': 0.03}
    results = asyncio.run(lab.fetch_many_taskgroup(services))

    assert [name for name, _ in results] == ['x', 'y', 'z']
    assert all(delay > 0 for _, delay in results)


def test_timeout_raises_and_fast_path_succeeds():
    async def scenario():
        with pytest.raises(TimeoutError):
            await lab.fetch_with_timeout('slow', delay=0.5, timeout=0.02)
        return await lab.fetch_with_timeout('fast', delay=0.01, timeout=0.5)

    assert asyncio.run(scenario())[0] == 'fast'


def test_async_context_manager_releases_on_exit():
    async def scenario():
        conn = lab.AsyncConnection('db')
        async with conn as entered:
            assert entered is conn and conn.opened and not conn.closed
            result = await conn.query('select 1')
        return conn, result

    conn, result = asyncio.run(scenario())

    assert conn.opened and conn.closed  # 退出时资源被释放
    assert 'select 1' in result


def test_async_context_manager_releases_on_error():
    conn = lab.AsyncConnection('db')

    async def scenario():
        with pytest.raises(ValueError):
            async with conn:
                raise ValueError('boom')

    asyncio.run(scenario())
    assert conn.closed  # 即使块内抛异常，__aexit__ 仍会释放资源


def test_async_iterator_and_generator_sequences():
    async def scenario():
        iterator = [value async for value in lab.Countdown(3)]
        generator = [value async for value in lab.stream_countdown(3)]
        return iterator, generator

    iterator, generator = asyncio.run(scenario())

    assert iterator == [3, 2, 1]
    assert generator == [3, 2, 1]
