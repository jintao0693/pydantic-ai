# 第 0.1 章 · Python 关键语法速成

> 篇零 · 零基础补课（可选）
> 本章配套 labs：`../labs/part_0/ch_0_1.py` 与 `../labs/part_0/test_ch_0_1.py`

写 Agent 代码，本质上是在写一群「小零件的协作」：一个工具（tool）是函数，一个依赖（dependency）是数据类，一次模型返回是一条带类型的消息，一次重试是一个装饰器，一次超时是一个上下文管理器…… 如果你对这些语法只有模糊印象，那么进入篇二之后，你会在读框架源码和写工具函数时处处卡壳。

本章**不追求覆盖 Python 全部语法**，只挑出「写 Pydantic AI 这类代码时天天会用到」的那一小撮，讲透它们的**机制与取舍**。读完本章，你应该能做到：看到 `X | None` 不再发怵、看到 `@contextmanager` 能说出执行顺序、能自己写出一个带参数的装饰器。

---

## 1. 本课目标

学完本章，你应当能够独立完成以下可检验的任务：

1. **写出正确的类型注解**：对函数参数/返回值使用 `list[str]`、`X | None`、`Literal[...]`，并能解释「注解在运行时是否被检查」。
2. **用 `dataclass` 定义数据模型**：会用 `kw_only=True` 与 `field(default_factory=...)`，并说清为什么 `list[str] = []` 是陷阱。
3. **用 `Enum` / `TypedDict` / `Literal` 表达受约束的值与结构**：区分「运行时枚举」与「静态字典形状」。
4. **设计异常体系**：从领域基类派生自定义异常，用 `raise ... from ...` 保留根因，并写出 `try/except/else/finally` 的完整分支。
5. **手写装饰器与上下文管理器**：包括带参数装饰器、`functools.wraps`、`contextlib.contextmanager`，并能准确预测 `with` 块的进入/退出顺序。
6. **使用生成器与内置工具**：用 `yield` 表达惰性序列，熟练使用 `match`、`f-string`、`zip`/`enumerate`、`dict` 方法与 `pathlib`。

---

## 2. 前置知识

- 会安装 Python ≥ 3.11（见 [README 第四节](../README.md)）。
- 知道什么是函数、变量、`if`、`for`、类与方法的**基本**写法（哪怕只是「见过」）。
- 无需任何 LLM / 网络 / API Key：本章 labs 只用标准库。

如果你连「类」和「函数」都还没写过，建议先花两小时过一遍任意 Python 入门教程的「函数 + 类」部分，再回到本章。本章的深度在「机制」，不适合作为第一门编程课。

---

## 3. 为什么需要它

看一段真实的 Agent 工具函数（简化自框架文档的典型形态）：

```python
async def search(query: str, limit: int = 5) -> list[str] | None: ...

@dataclass
class Deps:
    user_id: str
    history: list[str] = field(default_factory=list)

class ToolError(Exception): ...
```

短短几行里塞进了：类型注解、`| None`、默认参数、`dataclass`、`field(default_factory=...)`、自定义异常。如果这些语法你读起来是「大概懂」，那么后面会遇到三类具体麻烦：

- **读不懂报错**：框架抛出的异常链里，`raise ... from ...` 的根因才是真凶，你不会看就只能干瞪眼。
- **写不出工具**：工具签名要靠类型注解生成 JSON Schema 给模型看，注解写错 → 模型收到的参数说明就是错的。
- **调不动异步/重试**：装饰器和上下文管理器是 Pydantic AI 的 Hooks、重试、超时的实现骨架。

所以本章的目标很清楚：**把「写 Agent 时高频出现的那 20% 语法」练到能默写**。

---

## 4. 核心概念

先建立三张心智模型图。

**图 1 · 类型注解分两层**

```
源代码里的 annotation
        │
        ├── 静态层：pyright / mypy 在「不运行时」读它，帮你抓 bug
        │
        └── 运行时层：默认被求值后存进 __annotations__，
                Pydantic / Pydantic AI 会把它当「数据契约」，
                用来校验输入、生成 JSON Schema、序列化输出
```

> 关键：注解**不自动阻止**错误调用（Python 不是静态语言），但 Pydantic AI 会在运行时**主动读取**注解，把它变成对模型的约束与对返回值的校验。这是「注解即契约」。

**图 2 · 一个值可以有的几种「表达方式」**

| 机制 | 解决什么 | 运行时是否检查 | 典型场景 |
|------|----------|----------------|----------|
| `Literal["a","b"]` | 少量固定取值 | 静态为主 | `mode: Literal["safe","fast"]` |
| `Enum` | 有名字、可迭代、可带行为的取值集合 | ✅ | `Priority.HIGH`、角色、状态机 |
| `TypedDict` | 字典的「形状」（键与值类型） | ❌（纯静态） | API/JSON 负载 |
| `dataclass` | 有结构的对象（可变，可加方法） | 部分 | 依赖注入容器、内部模型 |
| Pydantic `BaseModel`（篇一） | 需要**运行时校验/序列化**的模型 | ✅✅ | 面向模型的输入输出 |

**图 3 · 装饰器与上下文管理器的执行顺序**

```
装饰器（自下而上包裹）                上下文管理器（with 进入/退出）
─────────────────────                ─────────────────────────
@retry(times=3)                       with stage("build", log):
@record_calls          ──►                print("...")
def f(): ...                          # 顺序：enter → body → exit → done
                                       # 若 body 抛异常：enter → error → done
调用 f() 实际执行 retry(record_calls(f))
```

---

## 5. 最小可运行示例

下面就是本章 labs 的完整代码（`../labs/part_0/ch_0_1.py`），它用一个「迷你任务清单 TodoList」把所有语法点串在一起。请先整体读一遍，再对照第 6 节逐块拆解。

```python
"""第 0.1 章 labs：Python 关键语法速成 —— 迷你任务清单（TodoList）。

仅使用标准库。直接运行：

    python ch_0_1.py

本章用一个 TodoList 把下列语法点串联起来：
类型注解 / dataclass（kw_only、field(default_factory=...)）/ TypedDict /
Enum / 自定义异常与异常链 / 装饰器（带参数、functools.wraps）/
上下文管理器 / 生成器（惰性）/ match 语句 / f-string。
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict

__all__ = [
    "Priority",
    "TaskDict",
    "Task",
    "TodoError",
    "DuplicateTaskError",
    "TaskNotFoundError",
    "InvalidPriorityError",
    "TodoList",
    "record_calls",
    "retry",
    "stage",
    "lazy_squares",
    "describe",
    "run_command",
    "parse_priority",
]


# --------------------------------------------------------------------------- #
# 1. Enum：把「魔法字符串」变成有名字、有行为的常量
# --------------------------------------------------------------------------- #
class Priority(Enum):
    """任务优先级。枚举成员天然唯一，可比较、可迭代。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def weight(self) -> int:
        """把优先级映射为可排序的权重。"""
        return _PRIORITY_WEIGHT[self]


_PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.LOW: 1,
    Priority.MEDIUM: 2,
    Priority.HIGH: 3,
}


# --------------------------------------------------------------------------- #
# 2. TypedDict：描述「JSON 形状」的字典结构，纯静态检查、零运行时开销
# --------------------------------------------------------------------------- #
class TaskDict(TypedDict):
    title: str
    priority: str
    done: bool


# --------------------------------------------------------------------------- #
# 3. dataclass：用声明式语法生成 __init__ / __repr__ / __eq__
#    kw_only=True 强制关键字参数；field(default_factory=...) 避免可变默认值共享
# --------------------------------------------------------------------------- #
@dataclass(kw_only=True)
class Task:
    title: str
    priority: Priority = Priority.MEDIUM
    done: bool = False
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 4. 自定义异常：从领域基类派生，便于统一捕获；异常链保留根因
# --------------------------------------------------------------------------- #
class TodoError(Exception):
    """TodoList 相关错误的基类。"""


class DuplicateTaskError(TodoError):
    def __init__(self, title: str) -> None:
        super().__init__(f"任务已存在：{title!r}")
        self.title = title


class TaskNotFoundError(TodoError):
    def __init__(self, title: str) -> None:
        super().__init__(f"任务不存在：{title!r}")
        self.title = title


class InvalidPriorityError(TodoError):
    def __init__(self, value: str) -> None:
        super().__init__(f"非法优先级：{value!r}")
        self.value = value


def parse_priority(value: str) -> Priority:
    """把字符串解析为 Priority；失败时用 `raise ... from ...` 保留根因。"""
    try:
        return Priority(value)
    except ValueError as exc:
        raise InvalidPriorityError(value) from exc


# --------------------------------------------------------------------------- #
# 5. 装饰器：不修改函数体就附加行为
#    - record_calls：无参装饰器，记录调用
#    - retry：带参装饰器（装饰器工厂）
# --------------------------------------------------------------------------- #
def record_calls(func: Callable[..., object]) -> Callable[..., object]:
    """记录每次调用的 (args, kwargs)，挂在 wrapper.calls 上。"""

    @functools.wraps(func)  # 保留 __name__ / __doc__ 等元数据
    def wrapper(*args, **kwargs):
        wrapper.calls.append((args, kwargs))
        return func(*args, **kwargs)

    wrapper.calls = []  # type: ignore[attr-defined]
    return wrapper


def retry(times: int = 3) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """带参数装饰器：最多重试 times 次，最后一次仍失败则向上抛出。"""

    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if attempt == times:
                        raise
            raise AssertionError("unreachable")  # 让类型检查器确信不会走到这里

        return wrapper

    return decorator


# --------------------------------------------------------------------------- #
# 6. 上下文管理器：用 @contextmanager 把「进入 / 正常退出 / 异常退出」写成一段线性代码
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def stage(label: str, log: list[str]) -> Iterator[str]:
    """记录一个阶段的 enter / exit（含异常）/ done，覆盖 try/except/else/finally。"""
    log.append(f"enter:{label}")
    try:
        yield label
    except Exception:
        log.append(f"error:{label}")
        raise  # 不吞异常：记录后继续抛出
    else:
        log.append(f"exit:{label}")  # 仅当 with 体正常结束时执行
    finally:
        log.append(f"done:{label}")  # 无论成功失败都执行


# --------------------------------------------------------------------------- #
# 7. 生成器：yield 让函数变成「惰性序列」，用多少算多少
# --------------------------------------------------------------------------- #
def lazy_squares(n: int) -> Iterator[int]:
    for i in range(n):
        yield i * i


# --------------------------------------------------------------------------- #
# 8. match 语句：按枚举分支 / 按命令结构做「结构化模式匹配」
# --------------------------------------------------------------------------- #
def describe(task: Task) -> str:
    match task.priority:
        case Priority.HIGH:
            label = "高"
        case Priority.MEDIUM:
            label = "中"
        case Priority.LOW:
            label = "低"
        case _:
            label = "?"
    state = "已完成" if task.done else "进行中"
    return f"[{label}] {task.title} ({state})"


def run_command(todo: TodoList, command: str) -> str:
    """把一行文本命令分发到 TodoList，演示 match 的序列模式与守卫。"""
    match command.split():
        case ["add", *titles] if titles:
            title = " ".join(titles)
            return f"已添加：{todo.add(title).title}"
        case ["done", *titles] if titles:
            title = " ".join(titles)
            todo.complete(title)
            return f"已完成：{title}"
        case ["list"]:
            return "\n".join(describe(task) for task in todo)
        case _:
            return f"无法识别的命令：{command!r}"


# --------------------------------------------------------------------------- #
# 9. TodoList：把上面所有语法点组装成一个可用的小组件
# --------------------------------------------------------------------------- #
class TodoList:
    def __init__(self, name: str = "我的清单") -> None:
        self.name = name
        self._tasks: list[Task] = []

    def add(
        self,
        title: str,
        priority: Priority = Priority.MEDIUM,
        tags: list[str] | None = None,
    ) -> Task:
        if self.find(title) is not None:
            raise DuplicateTaskError(title)
        task = Task(title=title, priority=priority, tags=list(tags) if tags else [])
        self._tasks.append(task)
        return task

    def find(self, title: str) -> Task | None:
        for task in self._tasks:
            if task.title == title:
                return task
        return None

    def complete(self, title: str) -> Task:
        task = self.find(title)
        if task is None:
            raise TaskNotFoundError(title)
        task.done = True
        return task

    def pending(self) -> Iterator[Task]:
        """惰性生成器：只有迭代时才计算。"""
        for task in self._tasks:
            if not task.done:
                yield task

    def to_dicts(self) -> list[TaskDict]:
        return [
            {"title": t.title, "priority": t.priority.value, "done": t.done}
            for t in self._tasks
        ]

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self) -> Iterator[Task]:
        return iter(self._tasks)


def _demo() -> None:
    todo = TodoList("发布准备")
    todo.add("写单元测试", Priority.HIGH, ["dev"])
    todo.add("更新 README", Priority.MEDIUM)
    todo.add("整理会议纪要", Priority.LOW)
    todo.complete("写单元测试")

    pending = sum(1 for _ in todo.pending())
    print(f"清单《{todo.name}》共 {len(todo)} 项，未完成 {pending} 项")
    for index, task in enumerate(todo, start=1):
        print(f"{index}. {describe(task)}")

    print("前 4 个平方数（惰性生成器）:", list(lazy_squares(4)))

    log: list[str] = []
    with stage("build", log) as label:
        print(f"进入阶段：{label}")
    print("阶段日志:", log)

    @record_calls
    def greet(name: str) -> str:
        return f"你好，{name}"

    greet("Agent")
    greet("World")
    print(f"greet 被调用 {len(greet.calls)} 次，函数名仍为 {greet.__name__}")

    for command in ["add 走查 PR", "list", "oops"]:
        first_line = run_command(todo, command).splitlines()[0]
        print(f"> {command} -> {first_line}")

    try:
        parse_priority("urgent")
    except InvalidPriorityError as exc:
        cause = type(exc.__cause__).__name__
        print(f"捕获 {type(exc).__name__}: {exc}（cause={cause}）")

    print("结构化导出:", todo.to_dicts())
    here = Path(__file__)
    print(f"本模块：{here.name}（位于 {here.parent.name}/）")


if __name__ == "__main__":
    _demo()
```

**运行方式与预期输出**（节选）：

```bash
cd textbook/labs
uv run --no-project python part_0/ch_0_1.py
```

```text
清单《发布准备》共 3 项，未完成 2 项
1. [高] 写单元测试 (已完成)
2. [中] 更新 README (进行中)
3. [低] 整理会议纪要 (进行中)
前 4 个平方数（惰性生成器）: [0, 1, 4, 9]
进入阶段：build
阶段日志: ['enter:build', 'exit:build', 'done:build']
greet 被调用 2 次，函数名仍为 greet
> add 走查 PR -> 已添加：走查 PR
> oops -> 无法识别的命令：'oops'
捕获 InvalidPriorityError: 非法优先级：'urgent'（cause=ValueError）
本模块：ch_0_1.py（位于 part_0/）
```

---

## 6. 深入剖析

### 6.1 类型注解：写给人看，也写给框架看

```python
def find(self, title: str) -> Task | None: ...
```

- `list[str]`、`dict[str, int]`、`Task | None` 是 **PEP 585 / PEP 604** 语法，Python 3.10+ 原生支持，**不再需要** `from typing import List, Optional`。`int | str` 等价于旧的 `typing.Union[int, str]`。
- 三种「联合」写法你要能分辨：`X | None`（可空）、`Union[A, B]` 或 `A | B`（多选一）、`Literal["a","b"]`（**具体字面量**多选一）。
- `typing_extensions` 只在**想用比当前 Python 版本更新的特性**时才需要（例如在 3.11 上使用 3.12+ 才稳定的 `TypeIs`）。本仓库的 `pydantic-ai-slim` 就依赖它来抹平版本差异；你写业务代码时，同一工程内**统一用一种来源**即可，别混着 import 造成困惑。
- ⚠️ **注解默认在运行时被求值**（存进 `__annotations__`）。这既让 Pydantic 能读到它，也可能带来循环引用问题——示例里 `run_command(todo: TodoList, ...)` 中使用 `TodoList` 前向引用时，靠文件顶部的 `from __future__ import annotations`（PEP 563）把所有注解放成惰性字符串解决，这是大型代码库的常规做法。
- **泛型基础**：`list[str]` 就是「参数化的泛型」。`Callable[[int], str]` 表示「收一个 int、返回 str 的可调用对象」；`Callable[..., object]` 里的 `...` 表示「任意参数」。你还会见到 `TypeVar` / `Generic` / `ParamSpec`，它们用来写出「本身与类型无关」的容器与装饰器——本章装饰器为简洁起见用了 `Callable[..., object]`。

### 6.2 `dataclass`：别让可变默认值坑了你

```python
@dataclass(kw_only=True)
class Task:
    title: str
    priority: Priority = Priority.MEDIUM
    done: bool = False
    tags: list[str] = field(default_factory=list)
```

- `@dataclass` 会按注解**自动生成** `__init__`、`__repr__`、`__eq__`。
- **最大陷阱**：`tags: list[str] = []` 会被**所有实例共享**同一个 list（Python 的默认参数只求值一次）。所以可变默认值必须用 `field(default_factory=list)`——`list` 是一个「每次调用都新建」的工厂。
- `kw_only=True` 让所有字段**只能关键字传参**：`Task(title="a")` ✅，`Task("a")` ❌（抛 `TypeError`）。这在字段很多时极大提升可读性，也是 Pydantic 里各种配置写法的同款思路。
- `dataclass` vs Pydantic `BaseModel`：`dataclass` **不校验**、可自由加方法、几乎零开销，适合内部容器（依赖注入的 `Deps`）；而面向模型输入输出、需要校验与 JSON Schema 的场景，用 Pydantic 模型（见篇一 1.3 与篇二）。

### 6.3 `Enum` / `TypedDict` / `Literal`

```python
class Priority(Enum):
    LOW = "low"
    ...
    @property
    def weight(self) -> int: ...
```

- `Enum` 成员是**单例**：`Priority("low") is Priority.LOW` 为真，因此可以用 `is` 比较、可以放进 `match` 做模式匹配、可以带方法/属性（如上例 `weight`）。`.value` 拿到原始值，`.name` 拿到名字。
- `TypedDict` 描述**字典的形状**，只在静态检查期生效，运行时就是普通 `dict`——零开销，非常适合描述「要发给某 API 的 JSON」。示例的 `TaskDict` 就是「导出成 JSON 时该长什么样」的声明。
- `Literal["safe","fast"]` 用于「取值只有几个、不值得建枚举」的场合。三者取舍：**需要行为/迭代 → Enum；只是 JSON 结构 → TypedDict；一两个固定字符串参数 → Literal**。

### 6.4 异常：分层、根因、完整分支

```python
def parse_priority(value: str) -> Priority:
    try:
        return Priority(value)
    except ValueError as exc:
        raise InvalidPriorityError(value) from exc   # 异常链
```

- **分层**：`TodoError` 是领域基类，调用方可以 `except TodoError` 一把抓住所有业务异常，同时又能精确 `except DuplicateTaskError`。框架正是这么用的（多数框架都有 `AgentError` 之类的根）。
- **`raise ... from ...`**：把「底层根因」（`ValueError`）挂到新异常的 `__cause__` 上。这样 `traceback` 会打印「The above exception was the direct cause of...」，排障时一眼看到真凶。**不要**用 `raise NewError(...)` 丢掉根因。
- **`try/except/else/finally` 四段**：`try` 放可能出错的代码；`except` 捕获；`else` 只在**没抛异常时**执行（把「成功后的逻辑」和「受保护的代码」分开）；`finally` **无论成败都执行**（清理资源）。示例 `stage()` 利用了全部四段。
- 自定义异常里存原始字段（如 `self.title`）是常见工程实践：上层无需解析错误字符串就能拿到结构化信息。

### 6.5 装饰器：在不改函数体的前提下加行为

```python
def retry(times: int = 3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            ...
        return wrapper
    return decorator
```

- 装饰器本质是「**收函数、回函数**」。`@retry(times=3)` 是**带参数装饰器**，即「装饰器工厂」：先 `retry(times=3)` 返回 `decorator`，再拿它去包 `func`。
- `*args, **kwargs` 让 wrapper 透传任意签名，这样装饰器才通用。
- `functools.wraps(func)` 把原函数的 `__name__`、`__doc__`、`__wrapped__` 复制到 wrapper 上——**不加它**，`greet.__name__` 会变成 `"wrapper"`，日志与调试全乱套。
- 惰性求值思维：装饰器让「附加行为」与「业务逻辑」解耦，这就是 Pydantic AI 的 **Hooks / Capabilities** 的语法根基。

### 6.6 上下文管理器

```python
@contextlib.contextmanager
def stage(label: str, log: list[str]) -> Iterator[str]:
    log.append(f"enter:{label}")
    try:
        yield label
    except Exception:
        log.append(f"error:{label}"); raise
    else:
        log.append(f"exit:{label}")
    finally:
        log.append(f"done:{label}")
```

- `with` 协议由 `__enter__` / `__exit__` 两个方法定义。手写类麻烦，`@contextlib.contextmanager` 让你**只写一个生成器**：`yield` 之前是「进入」，`yield` 之后是「退出」。
- **退出顺序**（务必背下）：`with` 体正常结束 → 生成器在 `yield` 处被「恢复」，`try` 正常走完 → `else` → `finally`；`with` 体抛异常 → 异常被 `throw` 进 `yield` 处 → `except` 捕获 → 重新 raise → `finally`。示例用 `stage()` 把这两种路径都记录成日志，测试里可精确断言。
- 用途：加锁、计时、打开/关闭连接、临时切换配置、以及框架里的「取消令牌」「span 追踪」——凡是有「成对操作」的地方，都该用 `with`。

### 6.7 生成器与惰性求值

```python
def lazy_squares(n: int) -> Iterator[int]:
    for i in range(n):
        yield i * i
```

- 含 `yield` 的函数**调用时不执行函数体**，只返回一个生成器；每次 `next()` 才前进到下一个 `yield`。因此 `lazy_squares(10**9)` 不会占用内存——**用多少算多少**。
- 生成器表达式 `(x * x for x in range(n))` 与列表推导 `[x * x for x in range(n)]` 的区别就在「惰性 vs 立即求值」：前者省内存，后者可重复遍历、可取 `len`。
- 推导式家族：list `[...]`、set `{...}`、dict `{k: v ...}`、generator `(...)`。`sum(1 for _ in todo.pending())` 就是「用生成器喂给聚合函数」的典型写法。
- ⚠️ 生成器**只能遍历一次**。数据要复用就转成 `list(...)`。

### 6.8 模块、包与 `__all__`、常用内置

- `__all__` 声明「`from module import *` 时导出哪些名字」，也是给读者和工具的「公开 API 清单」。示例顶部列出了全部公开符号。
- 包 = 含 `__init__.py` 的目录；包内模块间用**相对导入**：`from .models import Task`（同级）、`from ..utils import helper`（上一级）。相对导入让包可以整体改名/搬运而不炸。
- `match` 语句（3.10+）是**结构化模式匹配**，不是简单的 `switch`：`case ["add", *titles] if titles:` 同时做了「序列解构」+「守卫条件」，比一长串 `if command.split()[0] == "add"` 清晰得多。`case _` 是兜底分支。
- 常用内置：`f-string`（`f"{x!r}"` 用 `repr`、`f"{v:.2f}"` 格式化）、`enumerate(iterable, start=1)`、`zip(a, b)`（并行遍历，`strict=True` 可校验长度）、`dict` 方法（`.get(k, default)`、`.items()`、`.setdefault()`、`.update()`）、`pathlib.Path`（比 `os.path` 更面向对象：`Path(__file__).parent / "data.txt"`）。

---

## 7. 常见变体与工程实践

| 需求 | 写法要点 |
|------|----------|
| 大量字段的可读构造 | `@dataclass(kw_only=True)` 或 `dataclasses.field` + `kw_only=True` 单字段 |
| 只读数据 | `@dataclass(frozen=True)`，实例创建后不可变，可作 `dict` 键 |
| 需要排序的枚举 | `functools.total_ordering` 或像示例那样提供 `weight` |
| 需要校验的「数据类」 | 别用 `dataclass`，用 Pydantic `BaseModel` |
| 装饰器要支持 `async def` | 判断 `inspect.iscoroutinefunction(func)`，或用 `ParamSpec` 保签名 |
| 上下文管理器要复用/线程安全 | 写类实现 `__enter__/__exit__`，或组合 `contextlib.contextmanager` |
| 异常要携带上下文 | 存字段 + `raise ... from exc`；必要时用 `add_note()`（3.11+）补一句人话 |
| 公开 API 稳定 | 定义 `__all__`；包入口用 `__init__.py` 重导出 |

**给 Agent 工程师的额外提醒**：工具的**类型注解就是给模型看的说明书**。`query: str` 会变成 JSON Schema 的 `{"type": "string"}`；`limit: int = 5` 会带默认值；`mode: Literal["safe","fast"]` 会让模型只在两个值里选。**注解越精确，模型调用越可靠**。

---

## 8. 练习

难度递增，建议先自己写，再看「参考答案要点」。

**练习 1（入门）· 改数据类**
给 `Task` 增加一个 `completed_at: str | None = None` 字段，并写一个方法 `mark_done()` 把 `done` 置 `True`。
> 参考答案要点：字段放在有默认值的一侧；因为是 `kw_only=True`，顺序不敏感但仍建议把无默认值字段放前面。`mark_done` 内部改 `self.done = True`（`dataclass` 默认可变）。

**练习 2（进阶）· 写一个计时装饰器**
实现 `@timed`，打印被装饰函数的名称与耗时（毫秒），要求保留原函数元数据。
> 参考答案要点：`@functools.wraps(func)`；用 `time.perf_counter()` 前后取差；打印用 `func.__name__`。切勿用 `time.time()` 测短耗时（精度不足）。

**练习 3（进阶）· 上下文管理器管理「临时状态」**
写一个 `temporary_flag(flags: dict, key: str, value: bool)` 上下文管理器，进入时设置并记录旧值，退出时**还原**（即使 body 抛异常也要还原）。
> 参考答案要点：`@contextlib.contextmanager`；`old = flags.get(key)`；`try: ... yield ... finally: flags[key] = old`。还原必须放 `finally`。

**练习 4（综合）· 惰性读取日志并统计**
写生成器 `read_lines(path)` 逐行 `yield` 文件内容（不 `readlines`），再用推导式统计「含某个关键字的行数」。
> 参考答案要点：`with path.open() as f: for line in f: yield line.rstrip("\n")`；统计 `sum(1 for line in read_lines(p) if kw in line)`。要点是**不把整个文件读进内存**。

**练习 5（挑战）· 用 match 解析简单指令**
实现 `parse_command(text)`，支持 `"set key=value"`、`"del key"`、`"list"`，返回结构化结果（可用 `TypedDict`），非法输入抛自定义异常。
> 参考答案要点：`match text.split():`；`case ["set", pair]` 再 `pair.split("=", 1)`；`case ["del", key]`；`case ["list"]`；`case _: raise InvalidCommandError(text) from None`。用 `TypedDict` 声明返回结构。

---

## 9. 验收标准

本章的「已掌握」由 `../labs/part_0/test_ch_0_1.py` 的断言判定。全绿即通过：

```bash
cd textbook/labs
uv run --no-project --with pytest python -m pytest part_0/test_ch_0_1.py -q
```

测试文件内容如下（**注意：全部为同步测试**）：

```python
"""第 0.1 章验收测试：Python 关键语法速成。

全部同步测试，只需标准库与 pytest：

    cd textbook/labs
    uv run --no-project --with pytest python -m pytest part_0/test_ch_0_1.py -q
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from ch_0_1 import (
    DuplicateTaskError,
    InvalidPriorityError,
    Priority,
    Task,
    TaskNotFoundError,
    TodoError,
    TodoList,
    describe,
    lazy_squares,
    parse_priority,
    record_calls,
    retry,
    run_command,
    stage,
)


def test_dataclass_defaults_and_factory_isolation() -> None:
    a = Task(title="a")
    b = Task(title="b")
    assert a.priority is Priority.MEDIUM
    assert a.done is False
    a.tags.append("x")  # default_factory 保证实例之间互不共享
    assert b.tags == []
    with pytest.raises(TypeError):
        Task("positional")  # kw_only=True 禁止位置参数


def test_typeddict_export_shape() -> None:
    todo = TodoList()
    todo.add("t")
    row = todo.to_dicts()[0]
    assert row == {"title": "t", "priority": "medium", "done": False}
    assert set(row) == {"title", "priority", "done"}


def test_enum_values_and_lookup() -> None:
    assert Priority.HIGH.value == "high"
    assert Priority("low") is Priority.LOW
    assert Priority.HIGH.weight == 3


def test_custom_exceptions_are_raised_and_inherited() -> None:
    todo = TodoList()
    todo.add("dup")
    with pytest.raises(DuplicateTaskError):
        todo.add("dup")
    with pytest.raises(TaskNotFoundError):
        todo.complete("missing")
    assert issubclass(DuplicateTaskError, TodoError)


def test_exception_chaining_preserves_cause() -> None:
    with pytest.raises(InvalidPriorityError) as exc_info:
        parse_priority("urgent")
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_decorator_wraps_and_records_calls() -> None:
    @record_calls
    def fn(x: int) -> int:
        return x + 1

    assert fn.__name__ == "fn"  # functools.wraps 保留元数据
    assert fn(1) == 2
    assert fn(2) == 3
    assert fn.calls == [((1,), {}), ((2,), {})]


def test_parameterized_decorator_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    @retry(times=3)
    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert attempts["n"] == 3


def test_retry_reraises_after_exhaustion() -> None:
    @retry(times=2)
    def always_fail() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        always_fail()


def test_context_manager_normal_exit() -> None:
    log: list[str] = []
    with stage("build", log) as label:
        assert label == "build"
    assert log == ["enter:build", "exit:build", "done:build"]


def test_context_manager_on_error() -> None:
    log: list[str] = []
    with pytest.raises(RuntimeError):
        with stage("build", log):
            raise RuntimeError("boom")
    assert log == ["enter:build", "error:build", "done:build"]


def test_generator_is_lazy() -> None:
    consumed: list[int] = []

    def counting(limit: int) -> Iterator[int]:
        for i in range(limit):
            consumed.append(i)
            yield i

    gen = counting(3)
    assert inspect.isgenerator(gen)
    assert consumed == []  # 尚未迭代，副作用未发生
    assert next(gen) == 0
    assert consumed == [0]
    assert list(gen) == [1, 2]
    assert list(lazy_squares(4)) == [0, 1, 4, 9]


def test_match_priority_branches() -> None:
    assert describe(Task(title="x", priority=Priority.HIGH)) == "[高] x (进行中)"
    assert describe(Task(title="y", priority=Priority.LOW, done=True)) == "[低] y (已完成)"


def test_match_command_dispatch() -> None:
    todo = TodoList()
    assert run_command(todo, "add 写测试") == "已添加：写测试"
    assert len(todo) == 1
    assert run_command(todo, "done 写测试") == "已完成：写测试"
    assert "写测试" in run_command(todo, "list")
    assert run_command(todo, "boom").startswith("无法识别")
```

**可执行断言清单**：

- [ ] `dataclass` 默认值正确，且 `default_factory` 保证 `tags` 实例间**不共享**。
- [ ] `kw_only=True` 生效：位置参数抛 `TypeError`。
- [ ] `TypedDict` 导出结构精确等于 `{"title", "priority", "done"}`。
- [ ] `Enum` 可用 `.value` 取值、可用构造器反查单例（`is`）、具备 `weight` 属性。
- [ ] 三个自定义异常均按预期抛出，且继承自 `TodoError`。
- [ ] `raise ... from ...` 使 `__cause__` 为原始 `ValueError`。
- [ ] 装饰器保留 `__name__` 且记录调用；带参装饰器 `retry` 能重试并在耗尽后抛出。
- [ ] 上下文管理器正常路径日志为 `enter/exit/done`，异常路径为 `enter/error/done`。
- [ ] 生成器惰性：未迭代时无副作用，且 `inspect.isgenerator` 为真。
- [ ] `match` 的枚举分支与命令分发均命中正确分支。

---

## 10. 常见坑与排错

| 现象 | 原因 | 修法 |
|------|------|------|
| 两个实例的 `list` 字段互相污染 | `tags: list[str] = []` 可变默认值 | 改 `field(default_factory=list)` |
| `TypeError: takes 1 positional argument` | 用了 `kw_only=True` 却位置传参 | 全部改成关键字参数 |
| `NameError: name 'Task' is not defined`（注解里） | 前向引用未被字符串化 | 注解加引号 `"Task"` 或文件顶部加 `from __future__ import annotations` |
| `Priority("HIGH")` 报 `ValueError` | `Enum` 构造器匹配的是**值** `"high"`，不是名字 | 用 `Priority["HIGH"]` 按名取，或用 `.value` |
| 装饰器把函数名变成 `wrapper` | 忘了 `functools.wraps` | 加上 `@functools.wraps(func)` |
| `with` 里抛异常后清理没执行 | 清理逻辑不在 `finally` | 移到 `finally`（或 `__exit__`）|
| 生成器第二次遍历是空的 | 生成器只能消费一次 | `list(gen)` 物化后再复用 |
| `match` 分支不命中 | 模式写成了「相等判断」而非「结构匹配」 | 用序列模式 `case [a, b]`、`case {"k": v}`、守卫 `if ...` |
| `pip` 与仓库 `uv` 冲突 | 混用两个环境 | 本教材一律 `uv run --no-project ...` |

排错心法：**先看 `traceback` 的最后一行（异常类型 + 消息），再顺着 `During handling ... / The above exception was the direct cause ...` 找根因**。异常链就是你最好的老师。

---

## 11. 面试延伸

**Q1. `list[str]` 与 `typing.List[str]` 有什么区别？该用哪个？**
> 答题要点：前者是 PEP 585 内置泛型（3.9+），后者是旧 `typing` 别名；功能等价，`list[str]` 更短、更现代、避免额外 import。团队内统一即可。注意 `typing` 里仍有内置无法替代的（如 `Callable`、`TypedDict`、`Literal`）。

**Q2. 为什么 `dataclass` 的可变默认值要用 `field(default_factory=...)`？**
> 答题要点：函数默认参数在**定义时求值一次**，`= []` 会让所有实例共享同一对象，一个实例 `append` 影响全部。`default_factory` 每次实例化都调用工厂生成新对象，彻底隔离。

**Q3. `raise NewError() from exc` 与直接 `raise NewError()` 的区别？**
> 答题要点：`from exc` 设置 `__cause__`，traceback 显示「direct cause」，保留根因链，便于排障；不写 `from` 时 `__context__` 仍会隐式保留，但显式 `from` 语义更清晰，`from None` 则刻意屏蔽上下文。

**Q4. 装饰器与上下文管理器分别适合什么场景？**
> 答题要点：装饰器改变/包裹**函数的调用**（横切行为、重试、缓存、鉴权、日志），作用于「定义处」，是函数级；上下文管理器管理**一段代码块进出时的资源与状态**（加锁、计时、连接、临时配置），作用于「执行处」。二者都能与 `async` 结合（`async with` / `async def` 装饰）。

**Q5. 生成器相对列表的优势与代价？**
> 答题要点：优势是**惰性 + 低内存**，适合大数据流、管道、无限序列、逐行读文件；代价是**只能遍历一次**、不能 `len`/索引、调试时不如列表直观。需要多次使用或随机访问时先物化成列表。

---

## 12. 延伸阅读

- 源码篇总入口：[`../../code_wiki/README.md`](../../code_wiki/README.md) —— 了解 Pydantic AI 的整体架构与各源码篇导航。
- 开发工作流：[`../../code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md) —— `uv`、`pytest`、`ruff`、`pre-commit` 等工程实践，正是本章语法在真实项目里的落地方式。
- 下一章：[0.2 异步、类型系统与虚拟环境](0-2-async-types-and-envs.md) —— 在异步语境下，装饰器、上下文管理器、生成器都会出现 `async` 变体，本章是它们的语法地基。
- 篇一 1.2「工程基座：uv、类型系统、anyio、pytest」与 1.3「Pydantic v2 精要」将把本章的注解与数据类升级为**带运行时校验**的模型。
