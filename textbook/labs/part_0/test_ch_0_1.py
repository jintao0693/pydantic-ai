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
