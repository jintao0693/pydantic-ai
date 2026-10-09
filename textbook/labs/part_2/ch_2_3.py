"""第 2.3 章配套示例：工具与 Toolset。

本模块用 **FunctionModel（离线、确定性）** 精确驱动一次或多次「工具调用闭环」，把
Pydantic AI 里「模型产出 ToolCallPart → 宿主校验参数 → 执行 → 回填 ToolReturnPart →
再请求模型」的全过程摊开给你看：

    1) tool vs tool_plain      —— 首参 RunContext 的有无，决定了工具能否读取依赖
    2) FunctionModel 驱动      —— 用脚本精确指定模型每一次的输出，无需 API Key
    3) 参数 schema 校验        —— 参数不合法时框架自动回填 RetryPromptPart（工具不会执行）
    4) ModelRetry vs 直接抛异常 —— 可重试的信号 vs 会中断整条运行的异常
    5) FunctionToolset         —— 把一组工具打包，经 `Agent(toolsets=[...])` 注册
    6) retries 预算            —— `Agent(retries=...)` 与 `@agent.tool(retries=...)` 的层级

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_3.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, FunctionToolset, ModelRetry, RunContext, ToolDefinition
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.run import AgentRunResult


# --------------------------------------------------------------------------- #
# 0. 基础设施：调用记录器 + 脚本化模型 + 一次演示的产物
# --------------------------------------------------------------------------- #
@dataclass
class Spy:
    """记录一次工具调用（名字 + 关键字参数），用于断言「工具确实被执行了」。"""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def record(self, tool_name: str, **args: Any) -> None:
        self.calls.append((tool_name, args))

    @property
    def names(self) -> list[str]:
        """按顺序返回被调用的工具名。"""
        return [tool_name for tool_name, _ in self.calls]


@dataclass
class ScriptedModel:
    """按脚本逐次返回 `ModelResponse` 的模型函数（再包成 `FunctionModel`）。

    - `responses`：第 N 次模型请求返回第 N 个响应（`ToolCallPart` 或 `TextPart`）。
    - `requests`：实际发生的模型请求次数（= 工具轮数 + 1）。
    - `seen_tool_defs`：每次请求时模型「看得到」的函数工具定义快照。

    真实模型的输出不可复现；只有把不确定性拿掉，才能用 pytest 精确断言
    「工具被调用了几次」「回填了什么」「什么时候重试」这些机理。
    """

    responses: list[ModelResponse]
    requests: int = 0
    seen_tool_defs: list[list[ToolDefinition]] = field(default_factory=list)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests += 1
        self.seen_tool_defs.append(list(info.function_tools))
        if not self.responses:
            raise AssertionError("模型脚本已用尽：请提供足够的 ModelResponse")
        return self.responses.pop(0)

    def as_model(self) -> FunctionModel:
        """包成 `FunctionModel`，可直接传给 `Agent(...)`。"""
        return FunctionModel(self)

    def tool_names(self) -> list[str]:
        """首次请求时，模型可见的函数工具名（升序）。"""
        if not self.seen_tool_defs:
            return []
        return sorted(t.name for t in self.seen_tool_defs[0])

    def tool_def(self, name: str) -> ToolDefinition:
        """取某个工具第一次发给模型的 `ToolDefinition`。"""
        for defs in self.seen_tool_defs:
            for tool_def in defs:
                if tool_def.name == name:
                    return tool_def
        raise KeyError(name)


@dataclass
class Demo:
    """一次演示的产物：运行结果 + 调用记录 + 脚本模型 + agent（+ 可选 toolset）。"""

    result: AgentRunResult[str]
    spy: Spy
    script: ScriptedModel
    agent: Agent[Any, str]
    toolset: FunctionToolset | None = None


def model_text(text: str) -> ModelResponse:
    """构造一个「只含文本」的模型响应，用来结束一轮运行。"""
    return ModelResponse(parts=[TextPart(content=text)])


def model_call(tool_name: str, args: dict[str, Any], tool_call_id: str) -> ModelResponse:
    """构造一个「只含一次工具调用」的模型响应。"""
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=tool_call_id)])


def tool_history(result: AgentRunResult[str]) -> list[tuple[str, str | None, Any]]:
    """把消息历史压平成便于断言的 `(part_kind, tool_name, content)` 三元组列表。"""
    rows: list[tuple[str, str | None, Any]] = []
    for message in result.all_messages():
        for part in message.parts:
            rows.append((part.part_kind, getattr(part, "tool_name", None), getattr(part, "content", None)))
    return rows


def tool_takes_context(agent: Agent[Any, Any], tool_name: str) -> bool:
    """读取 agent 的内部工具注册表，判断该工具首参是否为 `RunContext`。

    `@agent.tool` 注册的函数首参为 `RunContext` → `True`；`@agent.tool_plain` → `False`。
    """
    return agent._function_toolset.tools[tool_name].takes_ctx


# --------------------------------------------------------------------------- #
# 1. @agent.tool（首参 RunContext）——工具能读取依赖注入值
# --------------------------------------------------------------------------- #
def demo_tool_with_context() -> Demo:
    """一个带 `RunContext[int]` 的 `add` 工具：闭环一次。"""
    spy = Spy()
    script = ScriptedModel(
        responses=[
            model_call("add", {"a": 2, "b": 3}, "call-add"),
            model_text("2 + 3 = 5"),
        ]
    )
    agent = Agent(script.as_model(), deps_type=int)

    @agent.tool
    def add(ctx: RunContext[int], a: int, b: int) -> int:
        """两数相加。

        Args:
            a: 第一个加数。
            b: 第二个加数。
        """
        # ctx.deps 就是 run_sync(..., deps=...) 传进来的依赖值；记下来证明它确实到了工具里。
        spy.record("add", deps=ctx.deps, a=a, b=b)
        return a + b

    result = agent.run_sync("请计算 2 + 3", deps=100)
    return Demo(result, spy, script, agent)


# --------------------------------------------------------------------------- #
# 2. @agent.tool_plain（无 context）——纯函数工具
# --------------------------------------------------------------------------- #
def demo_tool_plain() -> Demo:
    """一个不带 `RunContext` 的 `greet` 工具：函数签名里只有业务参数。"""
    spy = Spy()
    script = ScriptedModel(
        responses=[
            model_call("greet", {"name": "Ada"}, "call-greet"),
            model_text("已向 Ada 问好。"),
        ]
    )
    agent = Agent(script.as_model())

    @agent.tool_plain
    def greet(name: str) -> str:
        """生成一句问候语。

        Args:
            name: 收问候人的名字。
        """
        spy.record("greet", name=name)
        return f"你好，{name}！"

    result = agent.run_sync("向 Ada 问好")
    return Demo(result, spy, script, agent)


# --------------------------------------------------------------------------- #
# 3. 参数 schema 校验失败 —— 框架自动回填 RetryPromptPart，工具**不会**执行
# --------------------------------------------------------------------------- #
def demo_schema_validation_failure() -> Demo:
    """第一次参数非法（`a` 不是整数）→ 校验失败 → 回填 RetryPromptPart；第二次参数合法才执行。"""
    spy = Spy()
    script = ScriptedModel(
        responses=[
            model_call("add", {"a": "oops", "b": 3}, "call-bad"),  # a 传了字符串，校验必失败
            model_call("add", {"a": 2, "b": 3}, "call-good"),  # 模型改正后重试
            model_text("重试后成功：5"),
        ]
    )
    agent = Agent(script.as_model(), deps_type=int)

    @agent.tool
    def add(ctx: RunContext[int], a: int, b: int) -> int:
        """两数相加。

        Args:
            a: 第一个加数。
            b: 第二个加数。
        """
        spy.record("add", **{"a": a, "b": b})
        return a + b

    result = agent.run_sync("请计算 2 + 3", deps=0)
    return Demo(result, spy, script, agent)


# --------------------------------------------------------------------------- #
# 4. 工具内 raise ModelRetry —— 可重试的自定义校验（业务规则）
# --------------------------------------------------------------------------- #
def demo_model_retry() -> Demo:
    """工具在业务规则不满足时 `raise ModelRetry(...)`：回填 RetryPromptPart，让模型改参数再试。"""
    spy = Spy()
    script = ScriptedModel(
        responses=[
            model_call("sqrt_positive", {"x": -4}, "call-neg"),  # 负数：工具会 raise ModelRetry
            model_call("sqrt_positive", {"x": 16}, "call-pos"),  # 模型收到提示后改正
            model_text("16 的平方根是 4。"),
        ]
    )
    agent = Agent(script.as_model())

    @agent.tool_plain
    def sqrt_positive(x: int) -> int:
        """返回一个非负整数的平方根（要求 x 是完全平方数）。

        Args:
            x: 被开方的数，必须为非负数。
        """
        spy.record("sqrt_positive", x=x)
        if x < 0:
            # ModelRetry 是「控制流信号」：框架把它转成对模型可见的重试提示，而不是崩溃。
            raise ModelRetry(f"x 必须为非负数，收到的是 {x}；请修正后重试")
        root = int(x**0.5)
        if root * root != x:
            raise ModelRetry(f"{x} 不是完全平方数，请换一个数")
        return root

    result = agent.run_sync("求 -4 的平方根")
    return Demo(result, spy, script, agent)


# --------------------------------------------------------------------------- #
# 5. FunctionToolset + Agent(toolsets=[...]) —— 把一组工具打包复用
# --------------------------------------------------------------------------- #
def demo_function_toolset() -> Demo:
    """用 `FunctionToolset` 定义工具，再经 `Agent(toolsets=[...])` 注册。"""
    spy = Spy()
    toolset = FunctionToolset(id="math")

    @toolset.tool_plain
    def square(x: int) -> int:
        """求一个整数的平方。

        Args:
            x: 待求平方的整数。
        """
        spy.record("square", x=x)
        return x * x

    script = ScriptedModel(
        responses=[
            model_call("square", {"x": 6}, "call-square"),
            model_text("6 的平方是 36。"),
        ]
    )
    agent = Agent(script.as_model(), toolsets=[toolset])
    result = agent.run_sync("6 的平方是多少")
    return Demo(result, spy, script, agent, toolset)


# --------------------------------------------------------------------------- #
# 6. retries 预算 —— Agent(retries=...) 与单工具 @agent.tool(retries=n)
# --------------------------------------------------------------------------- #
def demo_retries_budget(agent_retries: int) -> tuple[ScriptedModel, Spy, str | None]:
    """让「工具永远 raise ModelRetry、模型永远要求再试」，观察重试预算耗尽时机。

    返回 `(script, spy, error)`：`error` 为 `None` 表示未耗尽（不该发生），否则是异常文本。
    """
    spy = Spy()
    # 预算足够小，正常不会用尽这些脚本；多备一些以防万一。
    script = ScriptedModel(responses=[model_call("flaky", {}, f"call-{i}") for i in range(10)])
    agent = Agent(script.as_model(), retries=agent_retries)

    @agent.tool_plain
    def flaky() -> int:
        """永远失败的工具，用于演示重试预算。"""
        spy.record("flaky")
        raise ModelRetry("再试一次")

    error: str | None = None
    try:
        agent.run_sync("调用 flaky")
    except Exception as exc:  # noqa: BLE001 - 演示：捕获后交给调用方断言
        error = f"{type(exc).__name__}: {exc}"
    return script, spy, error


def demo_per_tool_retries_override() -> tuple[ScriptedModel, Spy, str | None]:
    """`@agent.tool(retries=0)` 覆盖 `Agent(retries=5)`：该工具一次也不许重试。"""
    spy = Spy()
    script = ScriptedModel(responses=[model_call("flaky_once", {}, f"call-{i}") for i in range(10)])
    agent = Agent(script.as_model(), retries=5)

    @agent.tool_plain(retries=0)
    def flaky_once() -> int:
        """该工具自身重试上限为 0。"""
        spy.record("flaky_once")
        raise ModelRetry("马上失败")

    error: str | None = None
    try:
        agent.run_sync("调用 flaky_once")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    return script, spy, error


# --------------------------------------------------------------------------- #
# 7. 演示入口
# --------------------------------------------------------------------------- #
def main() -> None:
    print("=" * 72)
    print("1) @agent.tool（带 RunContext）：模型产出 ToolCallPart → 执行 → 回填 ToolReturnPart")
    print("=" * 72)
    demo = demo_tool_with_context()
    print(f"final output = {demo.result.output!r}")
    print(f"tool calls    = {demo.spy.calls}")
    print(f"model requests= {demo.script.requests}")
    for row in tool_history(demo.result):
        print("  ", row)

    print()
    print("=" * 72)
    print("2) @agent.tool_plain（无 context）：纯函数工具")
    print("=" * 72)
    demo = demo_tool_plain()
    print(f"final output = {demo.result.output!r}")
    print(f"tool calls    = {demo.spy.calls}")
    for row in tool_history(demo.result):
        print("  ", row)

    print()
    print("=" * 72)
    print("3) 参数校验失败：a=\"oops\" 被 schema 拒绝 → RetryPromptPart（工具未执行）")
    print("=" * 72)
    demo = demo_schema_validation_failure()
    print(f"final output = {demo.result.output!r}")
    print(f"tool calls（只统计真正执行的）= {demo.spy.calls}")
    print(f"model requests= {demo.script.requests}")
    for row in tool_history(demo.result):
        if row[0] == "retry-prompt":
            print("   校验错误明细：", row[2])

    print()
    print("=" * 72)
    print("4) 工具内 raise ModelRetry：业务规则不满足时让模型改正参数再试")
    print("=" * 72)
    demo = demo_model_retry()
    print(f"final output = {demo.result.output!r}")
    print(f"tool calls    = {demo.spy.calls}")
    for row in tool_history(demo.result):
        if row[0] == "retry-prompt":
            print("   ModelRetry 提示：", row[2])

    print()
    print("=" * 72)
    print("5) FunctionToolset + Agent(toolsets=[...])")
    print("=" * 72)
    demo = demo_function_toolset()
    print(f"toolset id    = {demo.toolset.id if demo.toolset else None!r}")
    print(f"final output  = {demo.result.output!r}")
    print(f"tool calls    = {demo.spy.calls}")

    print()
    print("=" * 72)
    print("6) retries 预算")
    print("=" * 72)
    for budget in (0, 2):
        script, spy, error = demo_retries_budget(budget)
        print(f"Agent(retries={budget}): model requests={script.requests}, tool calls={spy.names}, error={error}")
    script, spy, error = demo_per_tool_retries_override()
    print(f"@agent.tool(retries=0) 覆盖 Agent(retries=5): model requests={script.requests}, error={error}")

    print()
    print("=" * 72)
    print("7) 从函数签名 / 注解 / docstring 生成的 ToolDefinition")
    print("=" * 72)
    demo = demo_tool_with_context()
    tool_def = demo.script.tool_def("add")
    print(f"name        = {tool_def.name}")
    print(f"description = {tool_def.description!r}")
    print(f"kind        = {tool_def.kind!r}")
    print(f"parameters_json_schema = {tool_def.parameters_json_schema}")


if __name__ == "__main__":
    main()
