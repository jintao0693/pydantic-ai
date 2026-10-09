# 0.2 异步、类型系统与虚拟环境

> 本章是篇零的第二节，面向「会一点 Python 语法、但没写过生产级异步代码」的读者。它是后续所有章节的工程地基：Pydantic AI 的每一个 Agent 调用、每一次模型请求、每一个工具执行，背后都是本课讲的这套异步与类型机制。

---

## 一、本课目标

学完本章，你应当能够：

1. 解释**事件循环 / 协程 / `async def` / `await` / `asyncio.run`** 之间的关系，并说明为什么 `await` 不等于「开线程」。
2. 在 `asyncio.gather`、`asyncio.TaskGroup`、`anyio.create_task_group` 之间做出**有依据的选型**，并说明 Pydantic AI 为什么以 `anyio` 为并发底座。
3. 用 `async with` 管理异步资源、用 `async for` 消费异步迭代器，并理解它们的**清理语义**。
4. 用 `asyncio.timeout` 给一次 I/O 设置超时，理解「取消是终态（terminal）」这一关键约定。
5. 区分阻塞 I/O 与非阻塞 I/O，论证「LLM 调用是 I/O 密集而非 CPU 密集」。
6. 说明静态类型检查（尤其 pyright `strict`）的价值，并说清 mypy 与 pyright 的差异。
7. 用 `venv` / `uv` 管理解释器、依赖与锁文件，说清 `pyproject.toml` 与 `uv.lock` 各自负责什么。

---

## 二、前置知识

- 能编写并运行基本的 Python 函数、类、`import`。
- 知道「同步」代码是一行接一行执行的，函数调用会阻塞直到返回。
- 会用命令行切换目录、执行 `python xxx.py`。
- （可选）知道 HTTP 请求大概是什么，但不要求写过。

---

## 三、为什么需要它

一个 Agent 的一次回答，往往要经历这样一条链路：

```
用户提问 → 调用 LLM（网络往返 1~30 秒）
        → LLM 要求调用工具 → 执行工具（可能又是一次网络/数据库往返）
        → 把工具结果再送回 LLM（又一次网络往返）→ … → 输出答案
```

这条链路的绝大部分时间，**CPU 都在空转等待网络**。如果用同步写法：

- 一次请求要「干等」好几秒，期间进程什么都做不了；
- 想同时服务 100 个用户，就得开 100 个进程/线程，内存与调度开销爆炸。

异步编程解决的正是这个问题：**用一个线程，在等待 I/O 的同时去处理别的任务**。而类型系统与虚拟环境解决的则是另外两个「就业级」问题：

- **类型系统**：Agent 应用里到处都是「模型返回的字符串要变成结构化对象」「工具参数要校验」——类型标注 + 静态检查能在**运行之前**发现大量低级错误，也让 IDE 与 AI 助手能真正理解你的代码。
- **虚拟环境**：不同项目依赖不同版本的库，锁文件保证「我这里能跑，你那里也能跑」，这是团队协作与 CI 的最低要求。

本课不是「Python 语法补课」，而是**Pydantic AI 三条工程主线的启蒙**：异步（运行模型）、类型（约束数据）、环境（复现依赖）。后续每一章都会反复用到。

---

## 四、核心概念

### 4.1 事件循环：一个线程上的调度器

**事件循环（event loop）** 是一个无限循环，它维护着「就绪任务队列」。它做的事情是：取出一个就绪任务 → 运行到下一个 `await` → 该任务让出控制权 → 换下一个任务。

关键结论：

- 事件循环运行在**单个线程**上。协作式调度：任务只有在 `await`（主动让出）时才会被切换，不存在「被随意打断」。
- 因此，**协程内绝不能做长时间的同步阻塞操作**（如 `time.sleep()`、同步 `requests.get()`），否则整个循环都会被卡住。
- 也正因如此，`await` 不是「开一个线程去等」，而是「把当前任务挂起，把线程还给循环」。

### 4.2 协程：可以被暂停与恢复的函数

```python
async def fetch(service: str, delay: float) -> tuple[str, float]:
    await asyncio.sleep(delay)
    return service, delay
```

- `async def` 定义的函数叫**协程函数**，调用它**不会执行函数体**，而是返回一个**协程对象**。
- 只有被 `await`（或被 `asyncio.run`、被包成 Task）时，协程体才真正开始执行。
- `await` 后面必须跟一个「可等待对象」（awitable）：协程、`asyncio.Task`、`asyncio.Future`。
- 忘记 `await` 是最常见的错误之一：协程对象被创建了却从未运行，还会收到 `RuntimeWarning: coroutine ... was never awaited`。

### 4.3 `asyncio.run`：同步世界通往异步世界的门

```python
if __name__ == '__main__':
    asyncio.run(main())
```

`asyncio.run(main())` 做三件事：创建新的事件循环 → 运行 `main()` 直到结束 → 关闭循环。**一个进程（一个线程）通常只调用一次 `asyncio.run`**，它是整个程序的入口。已经处在事件循环里时（如在协程内部）再调用它会报错——这也是后文「同步 API 与异步 API 混用」的坑。

### 4.4 三种并发写法对比

| 写法 | 引入版本 | 结果收集 | 异常行为 | 结构化清理 |
|------|----------|----------|----------|------------|
| `asyncio.gather(*aws)` | 3.4+ | 返回列表，**顺序与传入一致** | 默认「第一个异常抛出，其余任务**继续跑**」 | 需自己 `cancel()` |
| `asyncio.TaskGroup` | **3.11+** | `tg.create_task(...)` 返回 `Task`，需自行收集 | 任一子任务失败 → **取消其余**，抛 `ExceptionGroup` | 由 `async with` 保证 |
| `anyio.create_task_group()` | 三方库 | 同 TaskGroup | 同 TaskGroup，且跨后端一致 | 由 `async with` 保证 |

一句话选型：**能用 `TaskGroup` 就不用 `gather`**；需要跨 asyncio/Trio 运行、或已在使用 `anyio` 生态时，用 `anyio.create_task_group`。

### 4.5 为什么 Pydantic AI 选择 anyio

Pydantic AI 的异步底座是 [`anyio`](https://anyio.readthedocs.io/)，而不是直接用 `asyncio`。理由有三条，都能在仓库里找到证据：

1. **后端无关**：anyio 提供一套 API，在 asyncio 与 Trio 两种后端上行为一致。仓库的测试配置把 `anyio_mode` 设为 `"auto"`（见 [`code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md) §3.3），所有 `async def` 测试自动经 anyio 运行，默认后端 asyncio，可用 `--anyio-backend=trio` 验证可移植性。
2. **结构化并发是内建原语**：任何一门严肃的异步框架都需要「任务组 + 超时取消范围 + 跨任务通信（内存对象流/事件）+ 同步原语」。anyio 把这些都做成跨后端一致的抽象，代价是框架内部**不直接书写 asyncio 专有 API**。仓库甚至用 ruff 的禁用规则强制这一点：`asyncio.Lock` 被禁用，要求改用 `anyio.Lock`（见 `code_wiki/12-development-workflow.md` §3.1）。
3. **生态一致**：Pydantic 团队的 Logfire、以及在 [`code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md) 中描述的 agent 循环，都建立在同一套 OTel/asyncio+anyio 约定之上；统一底座能减少上下文传播、取消、超时等横切逻辑的分叉。

> **给读者的结论**：学异步要先学 `asyncio`（理解事件循环这一心智模型），但写 Pydantic AI 应用时要习惯 `anyio` 的 `create_task_group` / `move_on_after` / `fail_after`。本课 labs 用标准库 `asyncio` 演示，正是为了让你看清底层机制；到了篇三 3.5「并发与超时」，就会切到 anyio 视角。

### 4.6 异步上下文管理器与异步迭代器

- **异步上下文管理器**：实现 `async def __aenter__` / `async def __aexit__`，用 `async with` 进入。它的价值是「清理可靠」——无论块内正常结束、`return`、还是抛异常，`__aexit__` 都会被执行，是释放连接/文件/事务的正确位置。
- **异步迭代器**：实现 `__aiter__` 与 `async def __anext__`（末尾抛 `StopAsyncIteration`），用 `async for` 消费。**异步生成器**（`async def` 里含 `yield`）是更常用的等价写法。

### 4.7 超时与取消：取消是终态

```python
async with asyncio.timeout(0.1):
    await fetch('slow', delay=0.5)
```

超时时，`asyncio.timeout` 会**取消**内部正在等待的任务：即向该任务抛入 `CancelledError`，然后把它转换成 `TimeoutError` 抛给调用方。

要牢记两条约定：

- **取消是协作式的**：任务不会「被杀死」，它是在下一个 `await` 点收到 `CancelledError`。因此阻塞的同步代码无法被取消。
- **取消是终态**：任务一旦被取消，就不能「恢复」。捕获 `CancelledError` 后应当完成清理再**重新抛出**，绝不能吞掉它假装无事发生。Pydantic AI 的 `RunCancelled`（见 [`code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md) §7）正是把外部取消翻译成「可捕获、携带运行快照」的终态异常。

### 4.8 阻塞 vs 非阻塞 I/O

| 维度 | 阻塞（同步） | 非阻塞（异步） |
|------|--------------|----------------|
| 等待网络时线程状态 | 被占用、休眠 | 空闲，可执行其他任务 |
| 并发模型 | 多线程/多进程 | 单线程事件循环 + 协程 |
| 适合负载 | **CPU 密集**（计算） | **I/O 密集**（网络/磁盘） |
| 典型 API | `requests.get`、`time.sleep` | `httpx.AsyncClient`、`asyncio.sleep` |

**为什么 LLM 调用是 I/O 密集**：一次模型请求的时间几乎全花在「客户端与推理服务之间的网络往返 + 服务端排队/推理」上，你自己的进程大部分时间在等字节到达。所以 Agent 框架天然是异步的：它需要同时管理多条请求、流式增量、工具调用与超时，而不是把 CPU 占满算数。

### 4.9 类型系统：静态检查的价值

- **可执行文档**：`async def fetch(service: str, delay: float) -> tuple[str, float]` 一眼看懂入参出参。
- **提前发现错误**：把 `str` 传给期望 `int` 的参数，pyright/mypy 在**运行前**就报错，而不是等线上崩。
- **AI 与 IDE 的燃料**：补全、跳转、重构都依赖类型信息；给 Agent 写工具时，Pydantic AI 甚至直接用类型标注生成给模型的 JSON Schema。
- **Pydantic AI 本身**：全库类型安全，公开 API 不返回 `Any`，用户无需手写 `isinstance`。

### 4.10 mypy 与 pyright 的差异

| 维度 | mypy | pyright |
|------|------|---------|
| 实现 | 纯 Python（较慢） | TypeScript（快，支持增量/多线程） |
| 生态 | 插件丰富（Django、SQLAlchemy 等） | 无插件，内置推断更强 |
| 典型用法 | `mypy --strict` | `typeCheckingMode = "strict"` |
| 差异点 | 部分收窄/推断更保守 | 推断更激进，常发现 mypy 漏掉的错 |

仓库的做法很有参考价值（见 `code_wiki/12-development-workflow.md` §3.2）：**默认只跑 pyright**（`make typecheck` = `typecheck-pyright`），并把 `typeCheckingMode` 设为 `"strict"`；mypy 只对一个专门的类型用例文件 `tests/typed_agent.py` 以 `strict = true` 运行。换句话说，工程实践里「选一个主检查器、把 strict 打开、接入 CI」，比「两个都全量跑、都调成宽松」更划算。

### 4.11 虚拟环境与依赖管理

- **虚拟环境 `venv`**：一个隔离的目录（通常是 `.venv`），内含独立解释器与第三方包。它解决「项目 A 要 x==1.0、项目 B 要 x==2.0」的冲突。
- **`uv`**：Rust 写的极速包管理器，同时管理解释器与依赖。常用命令：

| 命令 | 作用 |
|------|------|
| `uv venv` | 创建虚拟环境（如 `.venv`） |
| `uv pip install <pkg>` | 在活动环境里安装包（兼容 pip 心智） |
| `uv run python x.py` | 在项目环境里运行脚本，必要时自动同步依赖 |
| `uv sync` | 依据 `uv.lock` 把环境同步到锁定状态 |

- **`pyproject.toml`**：声明「**需要什么**」——项目元数据与依赖约束（可能带版本区间，如 `pydantic-ai-slim>=1,<2`）。
- **`uv.lock`**：记录「**实际装了什么**」——每个依赖的精确版本与哈希，是跨机器、跨平台可复现的关键。
- **语义化版本（SemVer）**：`主版本.次版本.补丁`；主版本升 = 不兼容变更，次版本升 = 向后兼容的新功能，补丁升 = 向后兼容的修复。仓库还用 `exclude-newer` 只接受「7 天内已发布」的依赖白名单策略做供应链防护（`code_wiki/12-development-workflow.md` §1）。

一句话：**`pyproject.toml` 说意图，`uv.lock` 锁事实，`uv sync` 让环境等于事实。**

---

## 五、最小可运行示例（完整代码 + 逐行讲解）

下面这份代码与 [`textbook/labs/part_0/ch_0_2.py`](../labs/part_0/ch_0_2.py) **完全一致**，只依赖标准库。

```python
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
```

运行结果（延迟是随机的，数值会变）：

```
模拟服务延迟（秒）: {'auth': 0.216, 'orders': 0.279, 'search': 0.123, 'profile': 0.209}
串行       : 0.834s  (4 个结果)
gather     : 0.280s  (加速 3.0x)
TaskGroup  : 0.280s  (加速 3.0x)
超时演示   : 0.5s 的调用在 0.1s 超时后抛出 TimeoutError
async with : db: result of 'select 1'
资源释放   : opened=True, closed=True
async for  : 3
async for  : 2
async for  : 1
生成器     : [3, 2, 1]
```

### 逐段讲解

- **`fetch`**：`await asyncio.sleep(delay)` 是「模拟 I/O」的关键——等待期间线程空闲，事件循环可以去跑别的 `fetch`。若换成 `time.sleep(delay)`，串行/并发都会变成「干等」，加速比归零。
- **`fetch_serial`**：列表推导里逐个 `await`，后一个必须等前一个完成，所以总耗时是**延迟之和**。
- **`fetch_many_gather`**：`asyncio.gather(*coros)` 把多个协程同时调度，返回列表**顺序与传入顺序一致**（不是完成顺序）。`*(... for ...)` 把生成器展开成位置参数。
- **`fetch_many_taskgroup`**：`async with asyncio.TaskGroup() as tg` 建组，`tg.create_task(worker(...))` 起任务；`async with` 退出时会**等待组内全部任务结束**，任一失败则取消其余。结果写进 `results` 字典，最后按输入顺序重组。
- **`fetch_with_timeout`**：`async with asyncio.timeout(t)` 划定超时范围；范围外若抛 `TimeoutError`，说明里面的等待超时了。
- **`AsyncConnection`**：`__aenter__` 打开、`__aexit__` 关闭。`query` 先检查 `opened`，用 `RuntimeError` 暴露「未进入就用」的用法错误。
- **`Countdown` / `stream_countdown`**：两种异步迭代写法，都产出 `[3, 2, 1]`。
- **`main`**：先串行测基线，再分别用两种并发测加速比，随后演示超时、资源管理、异步迭代。`if __name__ == '__main__'` 保证被 import 时不会自动运行。

---

## 六、深入剖析

### 6.1 `asyncio.gather` 的关键参数

```python
asyncio.gather(*aws, return_exceptions: bool = False)
```

- `return_exceptions=False`（默认）：遇到第一个异常就**立刻**把它抛给调用方，但**其余任务不会被取消**，仍会在后台继续跑（未观测的异常可能变成警告）。
- `return_exceptions=True`：异常被当作结果放进返回列表，由调用方逐个检查。
- 这正是 `TaskGroup` 更受欢迎的原因：**失败即取消其余**，不留「孤儿任务」。

### 6.2 `asyncio.TaskGroup` 的异常形状

```python
async with asyncio.TaskGroup() as tg:
    tg.create_task(maybe_fail())
# 若内部有异常，这里抛 ExceptionGroup
```

子任务抛出的异常会在退出 `async with` 时被打包成 `ExceptionGroup`（Python 3.11+ 的异常组）。若组内**多于一个**失败，会看到 `BaseExceptionGroup`；只有一个失败时是一个只含该异常的 `ExceptionGroup`。捕获时用 `except*` 语法：

```python
try:
    async with asyncio.TaskGroup() as tg:
        ...
except* ValueError as eg:
    ...
```

### 6.3 `asyncio.timeout` 的语义细节

- `asyncio.timeout(t)` 是一个**异步上下文管理器**，也可用 `asyncio.timeout_at(when)` 指定绝对截止时间。
- 超时发生时，内部任务被**取消**；若被取消的任务正确响应（在 `await` 点抛出 `CancelledError`），最终对外表现为 `TimeoutError`。
- 若内部代码吞掉了 `CancelledError` 并继续运行，超时**无法生效**——这是初学者常踩的坑，也再次印证「取消是协作式的」。
- 3.11 起，`asyncio.TimeoutError` 就是内置的 `TimeoutError`；本课测试直接断言 `pytest.raises(TimeoutError)`。

### 6.4 `async with` / `async for` 的协议

| 协议 | 需要实现的方法 | 语法 |
|------|----------------|------|
| 异步上下文管理器 | `async def __aenter__(self)`、`async def __aexit__(self, ...)` | `async with obj as x:` |
| 异步可迭代 | `def __aiter__(self)`、`async def __anext__(self)` | `async for x in obj:` |
| 异步生成器 | `async def f(): ... yield v` | `async for x in f():` |

`__aexit__` 的三个参数是异常三元组（或无异常时为 `(None, None, None)`），因此可以在 `__aexit__` 里判断「块内是否出错」并决定回滚还是提交。

### 6.5 同步 API 与异步 API 的桥接

Pydantic AI 提供了 `agent.run_sync(...)`，它内部用 `loop.run_until_complete` 包一层。它的**限制**很有教学意义：**不能在已经运行的异步代码或同步工具里调用它**，否则会触发「事件循环已经在运行」的错误。规则是：

- 顶层脚本、脚本式调用 → 可以用 `run_sync`（或自己 `asyncio.run`）。
- 已经在协程里 → 一律 `await agent.run(...)`。

---

## 七、常见变体与工程实践

1. **并发 + 超时组合**：给整个并发批次加一个总超时，而不是逐个加。
   ```python
   async with asyncio.timeout(2.0):
       results = await fetch_many_taskgroup(services)
   ```
2. **限制并发度**：用 `asyncio.Semaphore(n)` 或 anyio 的容量限制器，避免一次打爆下游（Pydantic AI 的 `max_concurrency` 就是这个思路，见 `code_wiki/02-core-agent-loop.md` §1.1）。
3. **优先用 TaskGroup**：新代码默认 `TaskGroup`，只有需要「部分失败也不取消其余」时才显式用 `gather(return_exceptions=True)`。
4. **用 anyio 写库**：需要跨后端时可移植性时，`anyio.create_task_group` / `anyio.move_on_after` / `anyio.fail_after` 是更稳妥的选择。
5. **类型检查先进 CI**：本地用 `uv run pyright path/to/file.py` 定点检查，把全量 strict 检查交给 CI，兼顾速度与门禁（这与仓库约定一致）。
6. **提交锁文件**：把 `uv.lock` 纳入版本控制，CI 用 `uv sync --frozen` 保证「锁内版本、可复现」。

---

## 八、练习

**练习 1（基础）**：把 `fetch_serial` 改成「并发但不使用 `gather`/`TaskGroup`」的 `asyncio.as_completed` 版本，按**完成顺序**返回结果。提示：`for coro in asyncio.as_completed(...)`。
<details><summary>参考答案要点</summary>

`as_completed` 返回一个「完成即可迭代」的迭代器，每个 `await` 到的结果不一定与传入顺序一致；若要恢复顺序，需要给每个协程带上索引再重排。它适合「谁先回来就先处理谁」的场景（如流式 UI）。
</details>

**练习 2（基础）**：让 `fetch_with_timeout` 在超时时**打印一条日志并返回 `None`**（而不是抛异常）。
<details><summary>参考答案要点</summary>

用 `except TimeoutError:` 捕获后打印并 `return None`，返回类型改为 `tuple[str, float] | None`。注意：这是**主动吞掉**超时异常，在本场景是可接受的（超时是业务预期），但**不要**吞掉 `CancelledError` 本身。
</details>

**练习 3（进阶）**：让 `AsyncConnection` 在 `__aexit__` 中区分「正常退出」与「异常退出」：正常时提交，异常时回滚（用打印模拟）。
<details><summary>参考答案要点</summary>

在 `__aexit__(self, exc_type, exc, tb)` 里检查 `exc_type is None`：为 `None` 打印 `commit`，否则打印 `rollback`。若返回 `True` 会抑制异常，通常应返回 `None`/`False` 让异常继续传播。
</details>

**练习 4（进阶）**：实现一个 `async def take(source, n)` 异步生成器，从另一个异步迭代器里最多取 `n` 个元素后停止（提前消费，不再拉取剩余）。
<details><summary>参考答案要点</summary>

```python
async def take(source: AsyncIterator[int], n: int) -> AsyncIterator[int]:
    count = 0
    async for item in source:
        if count >= n:
            break
        yield item
        count += 1
```

对 `Countdown(10)` 取 3 个应得 `[10, 9, 8]`。注意 `break` 会触发源生成器的清理。
</details>

**练习 5（综合）**：给 `main()` 里的并发批次加一个 `asyncio.Semaphore(2)`，把同时进行的 `fetch` 限制为 2 个，观察总耗时相对无限制时的变化。
<details><summary>参考答案要点</summary>

在 `worker` 内 `async with sem:` 包裹 `await fetch(...)`，`sem = asyncio.Semaphore(2)`。4 个 0.2s 任务、并发度 2 时总耗时约 0.4s（两两成批），介于串行（0.8s）与全并发（0.2s）之间——这说明**并发度是资源约束，不是越大约好**。
</details>

---

## 九、验收标准

配套验收测试位于 [`textbook/labs/part_0/test_ch_0_2.py`](../labs/part_0/test_ch_0_2.py)（同步 pytest，测试内部用 `asyncio.run` 驱动，不依赖 `pytest-asyncio`）。运行：

```bash
cd textbook/labs
uv run --no-project --with pytest python -m pytest part_0/test_ch_0_2.py -q
```

用来判断你是否掌握的断言清单：

- [ ] 并发（`gather`）总耗时**显著小于**串行（测试断言 `< 串行 / 2`）。
- [ ] `gather` 的结果**顺序与输入一致**，且与串行结果完全相等。
- [ ] `TaskGroup` 能收集结果并保持输入顺序。
- [ ] `asyncio.timeout` 超时时抛 `TimeoutError`；未超时则正常返回。
- [ ] `async with` 正常退出时释放资源；块内抛异常时**同样**释放资源。
- [ ] 异步迭代器与异步生成器都产出预期序列 `[3, 2, 1]`。

全部通过即可认为本章「已掌握」。

---

## 十、常见坑与排错

| 现象 | 原因 | 排错 |
|------|------|------|
| `RuntimeWarning: coroutine ... was never awaited` | 调用了协程函数却没 `await` | 检查是否漏写 `await`，或误把它当普通函数 |
| `TypeError: object int can't be used in 'await' expression` | `await` 了非可等待对象 | `await` 后应跟协程/Task/Future |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | 在协程里调用了 `run`/`run_sync` | 在协程内改 `await`；`run_sync` 只在顶层用 |
| 并发「没有变快」 | 用了 `time.sleep` / 同步阻塞调用 | 换成 `asyncio.sleep` / 异步客户端 |
| 超时设了却不生效 | 内部吞掉了 `CancelledError` | 不要捕获后忽略 `CancelledError`，应清理后重新抛出 |
| `ExceptionGroup` 看不懂 | `TaskGroup` 打包了子任务异常 | 用 `except* ValueError as eg:` 按类型处理 |
| `ModuleNotFoundError` / 版本不对 | 没在虚拟环境里运行，或锁文件不一致 | `uv sync` 后再 `uv run` |

---

## 十一、面试延伸

**Q1：`asyncio` 的 `await` 是在开线程吗？协程到底怎么实现并发？**
要点：不是。事件循环跑在**单线程**上，`await` 是「挂起当前协程、把控制权还给循环」的协作式让出点；就绪的其他任务在等待期间被调度，于是等待被「重叠」起来。它是并发（并发地推进多个任务），不是并行（同时执行）。

**Q2：`asyncio.gather` 和 `asyncio.TaskGroup` 该怎么选？**
要点：默认选 `TaskGroup`——结构化并发，任一子任务失败会取消其余，且用 `async with` 保证清理与等待；`gather` 在默认模式下首个异常抛出后**其余任务仍在跑**，容易产生孤儿任务。只有确实需要「部分失败也保留其他结果」时才用 `gather(return_exceptions=True)`。

**Q3：Pydantic AI 为什么用 `anyio` 而不是直接用 `asyncio`？**
要点：①后端无关，同一套 API 在 asyncio 与 Trio 上一致，测试可用 `--anyio-backend=trio` 验证可移植性；②内建结构化并发原语（任务组、超时取消范围、内存对象流、锁），统一了取消/超时/跨任务通信；③与 Pydantic/Logfire 生态一致，减少横切逻辑分叉。仓库甚至禁用 `asyncio.Lock` 要求改用 `anyio.Lock`。

**Q4：为什么说「取消是终态」？框架里怎么处理取消？**
要点：任务被取消后不能恢复；`CancelledError` 一旦被吞掉，超时/取消就会失效。正确做法是捕获后完成清理再重新抛出。Pydantic AI 把外部取消翻译成携带运行快照的 `RunCancelled`（可捕获、终态），而外部超时/取消保持 `CancelledError` 传播，并有三条簿记防止取消计数泄漏。

**Q5：为什么 LLM Agent 框架普遍是异步的？**
要点：LLM 调用是**I/O 密集**的——时间几乎全在网络往返与服务端推理上，进程大部分时间在等字节。异步让单线程能同时管理多条请求、流式增量与工具调用，并在等待时继续服务其他用户；而 CPU 密集计算（如嵌入向量的本地计算）才需要线程/进程池。

**Q6：mypy 和 pyright 该选哪个？**
要点：二者都是主流静态检查器。pyright 更快（支持增量/多线程）、内置推断更强；mypy 插件生态更丰富。很多项目（含 Pydantic AI）以 pyright `strict` 为主检查器接入 CI，辅以少量 mypy 用例。重点是**开启 strict 并纳入 CI**，而不是两者全量都跑。

---

## 十二、延伸阅读

- [`code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md) — Agent 执行循环、`RunContext`、异常体系与取消/超时簿记，理解本课的异步/取消机制在生产框架里如何落地。
- [`code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md) — `uv`/`make` 工作流、pyright `strict` 与 mypy 配置、`anyio_mode = "auto"` 测试约定、`asyncio.Lock` 禁用规则。
- [`code_wiki/07-ui-and-realtime.md`](../../code_wiki/07-ui-and-realtime.md) — UI 适配（SSE）与 Realtime 双向会话，是「异步 + 流式 + 长连接」在真实产品里的形态。

配套 labs：`../labs/part_0/ch_0_2.py`；验收测试：`../labs/part_0/test_ch_0_2.py`。
