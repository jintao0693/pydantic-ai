"""第 2.3 章验收测试：证明「工具与 Toolset」的关键机理确实成立。

全部离线、同步、确定性（`FunctionModel` 精确驱动），无 API Key、无网络。

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_3.py -q
"""

from __future__ import annotations

from ch_2_3 import (
    demo_function_toolset,
    demo_model_retry,
    demo_per_tool_retries_override,
    demo_retries_budget,
    demo_schema_validation_failure,
    demo_tool_plain,
    demo_tool_with_context,
    tool_history,
    tool_takes_context,
)


def test_tool_with_context_passes_deps_and_returns_value() -> None:
    """`@agent.tool` 的工具能拿到 `RunContext`，并读取依赖注入值 `deps`。"""
    demo = demo_tool_with_context()

    assert demo.result.output == "2 + 3 = 5"
    assert demo.spy.names == ["add"]
    assert demo.spy.calls == [("add", {"deps": 100, "a": 2, "b": 3})]  # ctx.deps 确实到了工具里
    assert demo.script.requests == 2  # 一次工具调用 + 一次收尾文本
    assert tool_takes_context(demo.agent, "add") is True


def test_tool_return_part_is_backfilled_into_history() -> None:
    """工具执行结果以 `ToolReturnPart` 回填进历史，模型再据此给出最终答案。"""
    demo = demo_tool_with_context()
    rows = tool_history(demo.result)

    # 完整的四段历史：user-prompt → tool-call → tool-return → text
    assert rows == [
        ("user-prompt", None, "请计算 2 + 3"),
        ("tool-call", "add", None),
        ("tool-return", "add", 5),
        ("text", None, "2 + 3 = 5"),
    ]
    # 回填的内容正是工具返回值
    assert ("tool-return", "add", 5) in rows
    # 工具调用与工具返回必须成对相邻（provider 对消息结构的要求）
    assert [row[0] for row in rows] == ["user-prompt", "tool-call", "tool-return", "text"]


def test_tool_plain_does_not_receive_context() -> None:
    """`@agent.tool_plain` 的工具函数不接收 `RunContext`，参数只有业务字段。"""
    demo = demo_tool_plain()

    assert tool_takes_context(demo.agent, "greet") is False
    assert demo.spy.calls == [("greet", {"name": "Ada"})]  # 没有任何 ctx/deps 被传入
    assert ("tool-return", "greet", "你好，Ada！") in tool_history(demo.result)
    assert demo.result.output == "已向 Ada 问好。"

    # schema 里只有业务参数，`RunContext` 被框架从签名中剥离、不会发给模型
    schema = demo.script.tool_def("greet").parameters_json_schema
    assert set(schema["properties"]) == {"name"}
    assert schema["required"] == ["name"]


def test_tool_definition_mirrors_signature_and_docstring() -> None:
    """`ToolDefinition` 的 name / description / schema 由函数名、docstring、类型注解生成。"""
    demo = demo_tool_with_context()
    tool_def = demo.script.tool_def("add")
    schema = tool_def.parameters_json_schema

    assert tool_def.name == "add"
    assert tool_def.kind == "function"
    assert tool_def.description == "两数相加。"  # 取自 docstring 首段
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"a", "b"}
    assert schema["properties"]["a"] == {"type": "integer", "description": "第一个加数。"}
    assert set(schema["required"]) == {"a", "b"}
    assert schema["additionalProperties"] is False


def test_schema_validation_failure_yields_retry_prompt_without_executing() -> None:
    """参数 schema 校验失败：回填 `RetryPromptPart`，且**工具不会被调用**。"""
    demo = demo_schema_validation_failure()
    rows = tool_history(demo.result)

    # 第一次参数非法（a="oops"）根本没有执行；只有第二次合法参数才执行了一次
    assert demo.spy.calls == [("add", {"a": 2, "b": 3})]
    assert demo.script.requests == 3  # 非法调用 → 重试 → 合法调用 → 收尾
    assert demo.result.output == "重试后成功：5"

    retries = [row for row in rows if row[0] == "retry-prompt"]
    assert len(retries) == 1
    assert retries[0][1] == "add"
    # 校验错误明细里能定位到出错的字段 `a`（ValidationError 的结构化内容）
    assert isinstance(retries[0][2], list)
    assert any(err.get("loc") == ("a",) for err in retries[0][2])


def test_model_retry_yields_retry_prompt_and_recovers() -> None:
    """工具内 `raise ModelRetry`：回填 `RetryPromptPart`，模型改正后可继续。"""
    demo = demo_model_retry()
    rows = tool_history(demo.result)

    # 工具被调用两次：第一次负数触发 ModelRetry，第二次改正后成功
    assert demo.spy.names == ["sqrt_positive", "sqrt_positive"]
    assert demo.result.output == "16 的平方根是 4。"

    retries = [row for row in rows if row[0] == "retry-prompt"]
    assert len(retries) == 1
    assert retries[0][2] == "x 必须为非负数，收到的是 -4；请修正后重试"
    assert ("tool-return", "sqrt_positive", 4) in rows


def test_function_toolset_registers_callable_tool() -> None:
    """`FunctionToolset` 经 `Agent(toolsets=[...])` 注册后，其工具可被模型调用。"""
    demo = demo_function_toolset()

    assert demo.toolset is not None
    assert demo.toolset.id == "math"
    # 模型首次请求时看得到该工具（来自 AgentInfo.function_tools）
    assert demo.script.tool_names() == ["square"]
    assert demo.spy.calls == [("square", {"x": 6})]
    assert ("tool-return", "square", 36) in tool_history(demo.result)
    assert demo.result.output == "6 的平方是 36。"


def test_agent_retries_budget_bounds_retries() -> None:
    """`Agent(retries=...)` 生效：预算耗尽即抛 `UnexpectedModelBehavior`。"""
    # retries=0：第一次 ModelRetry 就超限，只发生 1 次模型请求
    script0, spy0, error0 = demo_retries_budget(0)
    assert error0 is not None and error0.startswith("UnexpectedModelBehavior")
    assert "max retries count of 0" in error0
    assert script0.requests == 1
    assert spy0.names == ["flaky"]

    # retries=2：允许两次重试，共 3 次模型请求后才超限
    script2, _spy2, error2 = demo_retries_budget(2)
    assert error2 is not None and "max retries count of 2" in error2
    assert script2.requests == 3


def test_per_tool_retries_overrides_agent_default() -> None:
    """单工具 `@agent.tool(retries=0)` 覆盖 `Agent(retries=5)` 的默认预算。"""
    script, spy, error = demo_per_tool_retries_override()

    assert error is not None and "max retries count of 0" in error
    assert script.requests == 1  # agent 默认 5 被工具级 0 覆盖，立刻超限
    assert spy.names == ["flaky_once"]
