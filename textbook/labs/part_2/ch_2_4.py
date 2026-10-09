"""第 2.4 章 · 输出模式与校验/重试 —— 离线 labs。

本模块用「代码评审结论」这一个贴近真实业务的模型，串起 Pydantic AI 的
输出（output）体系，全程使用 ``TestModel`` 离线运行，不需要任何 API Key：

    output_type 为 Pydantic 模型时的默认行为（tool 模式）
    → 通过默认输出工具 final_result 产出结构化结果
    → @agent.output_validator 抛 ModelRetry 触发重试（先失败一次、再成功）
    → 重试预算耗尽（retries=AgentRetries(output=1)）抛 UnexpectedModelBehavior
    → ToolOutput(max_retries=...) 覆盖单个输出工具的重试预算（全局 vs 每工具）
    → PromptedOutput：把 JSON Schema 写进提示词、从文本里解析结构化结果
    → NativeOutput：需要 provider 原生支持（TestModel 不支持，观察报错）
    → str / list[Model] / 可空联合等 output_type 变体

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_4.py
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from pydantic_ai import Agent, AgentRetries, ModelRetry, UnexpectedModelBehavior
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.test import TestModel
from pydantic_ai.output import NativeOutput, PromptedOutput, ToolOutput

# ---------------------------------------------------------------------------
# 0. 输出契约：一个 Pydantic 模型，就是「模型必须产出的数据形状」
# ---------------------------------------------------------------------------
class Review(BaseModel):
    """代码评审结论。字段约束会被翻译成 JSON Schema 交给模型。"""

    score: int = Field(ge=0, le=10, description="评分，0–10")
    summary: str = Field(min_length=1, description="一句话结论")


# TestModel 用固定的参数来「扮演」模型产出的结构化结果。
DEFAULT_ARGS: dict[str, object] = {"score": 8, "summary": "结构清晰，边界处理到位"}
PROMPTED_TEXT = '{"score": 6, "summary": "prompted 模式产出的结论文本"}'
LIST_ARGS: list[dict[str, object]] = [
    {"score": 5, "summary": "第一条评审结论"},
    {"score": 3, "summary": "第二条评审结论"},
]


# ---------------------------------------------------------------------------
# 1. 默认模式：output_type 为 BaseModel → tool 模式（把输出当成一个工具）
# ---------------------------------------------------------------------------
def make_tool_mode_agent() -> Agent[None, Review]:
    """默认输出模式：``output_type`` 是 Pydantic 模型时，框架注册一个名为
    ``final_result`` 的输出工具，模型以「调用工具」的方式给出结构化结果。"""
    return Agent(TestModel(custom_output_args=dict(DEFAULT_ARGS)), output_type=Review)


def run_tool_mode():
    """运行 tool 模式，返回完整的 ``AgentRunResult``（便于检查底层消息）。"""
    return make_tool_mode_agent().run_sync("请评审这段代码：def add(a, b): return a + b")


# ---------------------------------------------------------------------------
# 2. 输出校验器：抛 ModelRetry 触发重试
# ---------------------------------------------------------------------------
def make_validator_agent(fail_times: int = 1):
    """构造一个带输出校验器的 agent：前 ``fail_times`` 次校验失败并抛
    ``ModelRetry``，之后通过。返回 ``(agent, state)``，state 记录每次校验的
    重试信息，供演示与测试断言。"""
    agent = Agent(TestModel(custom_output_args=dict(DEFAULT_ARGS)), output_type=Review)
    state: dict[str, object] = {"attempts": 0, "retry_indexes": [], "retries_snapshots": []}

    @agent.output_validator
    def check_score(ctx, output: Review) -> Review:
        state["attempts"] = int(state["attempts"]) + 1  # type: ignore[arg-type]
        state["retry_indexes"].append(ctx.retry)  # type: ignore[union-attr]
        # ctx.retry 是当前输出工具的第几次尝试；ctx.retries 是各工具已用重试数。
        state["retries_snapshots"].append(dict(ctx.retries))  # type: ignore[union-attr]
        if int(state["attempts"]) <= fail_times:  # type: ignore[arg-type]
            # 抛 ModelRetry：把这条提示作为 RetryPromptPart 回填给模型，要求它重出。
            raise ModelRetry(f"评分 {output.score} 偏高，请下调评分并重写结论")
        return output

    return agent, state


@dataclass
class ValidatorOutcome:
    """校验器重试演示的结果快照。"""

    output: Review
    attempts: int
    retry_indexes: list[int]
    retries_snapshots: list[dict[str, int]]


def run_validator_retry(fail_times: int = 1) -> ValidatorOutcome:
    """运行「先失败 ``fail_times`` 次、再成功」的校验器，返回结果快照。"""
    agent, state = make_validator_agent(fail_times)
    result = agent.run_sync("请评审这段代码")
    return ValidatorOutcome(
        output=result.output,
        attempts=int(state["attempts"]),  # type: ignore[arg-type]
        retry_indexes=list(state["retry_indexes"]),  # type: ignore[arg-type]
        retries_snapshots=list(state["retries_snapshots"]),  # type: ignore[arg-type]
    )


def run_per_tool_budget(fail_times: int = 2) -> tuple[Review, int]:
    """演示「全局预算 vs 每输出工具预算」：agent 级 ``AgentRetries(output=1)`` 很小，
    但 ``ToolOutput(Review, max_retries=3)`` 单独把这个输出工具的重试预算调大，
    于是校验器可以先失败 2 次、第 3 次成功。返回 ``(output, attempts)``。"""
    agent = Agent(
        TestModel(custom_output_args=dict(DEFAULT_ARGS)),
        output_type=ToolOutput(Review, max_retries=3),
        retries=AgentRetries(output=1),
    )
    state = {"attempts": 0}

    @agent.output_validator
    def check(ctx, output: Review) -> Review:
        state["attempts"] += 1
        if state["attempts"] <= fail_times:
            raise ModelRetry("再改一版")
        return output

    result = agent.run_sync("请评审这段代码")
    return result.output, state["attempts"]


# ---------------------------------------------------------------------------
# 3. 重试预算耗尽：validator 永远失败 + 很小的预算 → 抛异常
# ---------------------------------------------------------------------------
def make_always_fail_agent(max_output_retries: int = 1) -> Agent[None, Review]:
    """校验器永远失败、输出重试预算为 ``max_output_retries`` 的 agent。"""
    agent = Agent(
        TestModel(custom_output_args=dict(DEFAULT_ARGS)),
        output_type=Review,
        retries=AgentRetries(output=max_output_retries),
    )

    @agent.output_validator
    def always_fail(ctx, output: Review) -> Review:
        raise ModelRetry("无论模型怎么改都不合格")

    return agent


def run_retry_exhaustion(max_output_retries: int = 1) -> UnexpectedModelBehavior:
    """运行「预算耗尽」场景并捕获异常；若未抛出则视为演练失败。"""
    try:
        make_always_fail_agent(max_output_retries).run_sync("请评审这段代码")
    except UnexpectedModelBehavior as exc:
        return exc
    raise AssertionError("预期耗尽输出重试预算时抛出 UnexpectedModelBehavior，但没有")


# ---------------------------------------------------------------------------
# 4. 第二种模式：PromptedOutput —— 把 schema 写进提示词，从文本里解析结果
# ---------------------------------------------------------------------------
def make_prompted_agent() -> Agent[None, Review]:
    """``PromptedOutput`` 让模型以纯文本（通常是 JSON）产出结果，框架再把这段
    文本解析、校验成 ``Review``。任何支持文本输出的模型都可用。"""
    return Agent(TestModel(custom_output_text=PROMPTED_TEXT), output_type=PromptedOutput(Review))


def run_prompted_mode():
    """运行 prompted 模式，返回完整结果（便于检查底层是文本而非工具调用）。"""
    return make_prompted_agent().run_sync("请评审这段代码")


# ---------------------------------------------------------------------------
# 5. NativeOutput：需要 provider 原生结构化输出支持
# ---------------------------------------------------------------------------
def run_native_mode_offline() -> str:
    """``NativeOutput`` 走 provider 的「原生结构化输出」通道。TestModel 的 profile
    不支持它，这里捕获 ``UserError`` 并把提示返回，用于说明「模式受 provider 能力约束」。"""
    agent = Agent(TestModel(custom_output_args=dict(DEFAULT_ARGS)), output_type=NativeOutput(Review))
    try:
        agent.run_sync("请评审这段代码")
    except UserError as exc:
        return str(exc)
    return "（本次运行未报错）"


# ---------------------------------------------------------------------------
# 6. output_type 的其它形态：str / list[Model] / 可空联合
# ---------------------------------------------------------------------------
def run_text_mode() -> str:
    """``output_type=str``（默认值）走 text 模式：不注册输出工具，模型直接说话。"""
    return Agent(TestModel(custom_output_text="这是一段纯文本输出"), output_type=str).run_sync("你好").output


def run_list_mode() -> list[Review]:
    """``output_type=list[Review]``：输出工具的 schema 是数组；TestModel 会按外层键
    ``response`` 包装，对使用者透明。"""
    return Agent(TestModel(custom_output_args=list(LIST_ARGS)), output_type=list[Review]).run_sync(
        "请分别评审两段代码"
    ).output


def run_union_mode() -> Review | None:
    """``output_type=Review | None``：模型既可能给出结构化结果，也可能给出 ``None``。"""
    return Agent(TestModel(custom_output_args=dict(DEFAULT_ARGS)), output_type=Review | None).run_sync(
        "请评审这段代码"
    ).output


# ---------------------------------------------------------------------------
# main：把上面每组都跑一遍并打印观察点
# ---------------------------------------------------------------------------
def main() -> None:
    line = "=" * 72

    print(line)
    print("1) 默认 tool 模式：output_type 为 BaseModel，产出结构化结果")
    print(line)
    result = run_tool_mode()
    print("output:", result.output)
    print("底层响应里的工具调用名:", [call.tool_name for call in result.response.tool_calls])
    print("底层响应文本 (tool 模式下为 None):", repr(result.response.text))
    print("output_json_schema():", make_tool_mode_agent().output_json_schema())

    print()
    print(line)
    print("2) 输出校验器：先失败一次（ModelRetry），重试后成功")
    print(line)
    outcome = run_validator_retry(fail_times=1)
    print("最终 output:", outcome.output)
    print("校验器被调用次数:", outcome.attempts)
    print("每次的 ctx.retry:", outcome.retry_indexes)
    print("每次的 ctx.retries:", outcome.retries_snapshots)
    budget_output, budget_attempts = run_per_tool_budget(fail_times=2)
    print("每输出工具预算 ToolOutput(max_retries=3) 下，校验器调用次数:", budget_attempts)
    print("  最终 output:", budget_output)

    print()
    print(line)
    print("3) 重试预算耗尽：validator 永远失败 + AgentRetries(output=1)")
    print(line)
    exc = run_retry_exhaustion(max_output_retries=1)
    print("抛出异常:", type(exc).__name__)
    print("异常信息:", exc)

    print()
    print(line)
    print("4) prompted 模式：PromptedOutput（把 schema 写进提示词，从文本解析）")
    print(line)
    prompted = run_prompted_mode()
    print("output:", prompted.output)
    print("底层是否有输出工具调用:", [call.tool_name for call in prompted.response.tool_calls])
    print("底层响应文本:", repr(prompted.response.text))

    print()
    print(line)
    print("5) native 模式：NativeOutput 需要 provider 支持（TestModel 不支持）")
    print(line)
    print("结果:", run_native_mode_offline())

    print()
    print(line)
    print("6) output_type 的其它形态")
    print(line)
    print("str:", repr(run_text_mode()))
    print("list[Review]:", run_list_mode())
    print("Review | None:", run_union_mode())


if __name__ == "__main__":
    main()
