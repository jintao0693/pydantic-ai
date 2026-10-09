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
