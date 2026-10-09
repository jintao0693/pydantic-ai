# 2.4 输出模式：text / tool / native / prompted / image 与校验器、重试

> 配套 labs：[`labs/part_2/ch_2_4.py`](../labs/part_2/ch_2_4.py) · 验收测试：[`labs/part_2/test_ch_2_4.py`](../labs/part_2/test_ch_2_4.py)
>
> 本章所有示例均在 `pydantic-ai-slim 2.54.0` 上离线运行（`TestModel`），无需 API Key。

---

## 1. 本课目标

学完本章，你应当能够：

1. 说出 `OutputMode` 的全部取值，并解释每个模式「模型用什么方式把结果交给你」。
2. 在 `output_type` 为一个 Pydantic 模型时，讲清默认走哪种模式、底层多出一个名为 `final_result` 的输出工具、以及它如何被校验成类型安全对象。
3. 用 `@agent.output_validator` 写输出校验器，抛 `ModelRetry` 触发重试，并读懂 `ctx.retry` / `ctx.retries` / `ctx.max_retries` 的含义。
4. 解释**全局输出重试预算**（`AgentRetries(output=N)`）与**每输出工具预算**（`ToolOutput(max_retries=N)`）的区别与覆盖关系。
5. 用用户面向标记类切换模式：`ToolOutput` / `NativeOutput` / `PromptedOutput` / `TextOutput` / `StructuredDict`，并知道哪些需要 provider 支持。
6. 论证 `output_type` 为 `str`、`list[Model]`、可空联合、`None` 时的行为差异，并给出「选哪种输出模式」的工程准则。
7. 让「输出重试耗尽」以可预期的方式失败（`UnexpectedModelBehavior`），并写出可回归的断言。

---

## 2. 前置知识

- 第 1.3 章：Pydantic v2 模型、`Field` 约束、`model_json_schema()`、`ValidationError`。
- 第 2.1 章：`Agent`、`run` / `run_sync`、`output_type` 的最基本用法、结构化输出。
- 第 2.3 章：工具（tool）的概念——模型「调用工具」= 返回一个带名字与参数的结构化调用。**输出工具只是把「产出结果」表达成一次工具调用**。
- 第 1.2 章的 `pytest`：本章验收测试是同步测试。

---

## 3. 为什么需要它

Agent 的「交付物」几乎总是一个**结构化结果**：一条摘要、一份评分、一个待办列表、一张数据库记录。你希望拿到的是**类型安全的 Python 对象**，而不是一段需要自己 `json.loads` 的字符串。

但「让 LLM 稳定地产出符合 schema 的结果」这件事，本身有多种实现路径，每种都有取舍：

| 问题 | 你可能踩的坑 |
|------|--------------|
| 模型不按 schema 输出 | 字段缺失、类型写错、枚举越界，直到下游才炸 |
| 想要更稳 | 有的模型支持「原生结构化输出」，有的只能用工具或提示词模拟 |
| 想约束业务规则 | 评分不能为负、结论不能为空——这些 schema 表达不了，需要在结果出来后再校验 |
| 校验不过 | 不该直接报错给用户，而应把错误回填给模型、让它重出——但要**防止无限重试** |
| 多模型切换 | 一个模型支持的模式，另一个不支持，怎么选一个「兼容性最好」的默认 |

Pydantic AI 的 answer 是一套**统一的输出抽象**：

- 用 `output_type` 声明「我要什么形状」；
- 用 `OutputMode` / 用户面向标记类决定「模型怎么产出它」；
- 用 `@agent.output_validator` 追加「schema 之外的业务校验」，并用重试把模型「掰正」；
- 用**分类重试预算**保证重试有界、失败可预期。

> 一句话：**输出模式决定「模型怎么答」，校验器与重试决定「答得对不对、不对怎么办」。** 这两件事是本章的两条主线。

---

## 4. 核心概念

### 4.1 五种「模型怎么把结果交给你」的方式

把「模型产出结果」想象成一次投递，`OutputMode` 描述的就是投递方式：

| 模式 | 模型如何交付 | 谁负责解析 | 典型用途 |
|------|--------------|------------|----------|
| `'text'` | 直接返回一段文本（`str`） | 你 / `TextOutput` 的处理函数 | 聊天、自由创作 |
| `'tool'` | 调用一个**输出工具**，参数即结构化结果 | 框架按输出工具的 schema 校验 | **默认结构化输出模式**，兼容性最好 |
| `'native'` | 走 provider 的**原生结构化输出**通道（如 OpenAI JSON schema） | provider + 框架 | 支持的 provider 上最省 token、最稳 |
| `'prompted'` | 把 schema 写进**提示词**，模型以文本（JSON）返回 | 框架解析文本并校验 | 不支持工具/原生通道时的兜底 |
| `'image'` | 返回一张图像 | 框架 | 图像生成 |
| `'auto'` | 由模型的 `ModelProfile.default_structured_output_mode` 决定 | 框架 | 交给模型默认值 |

> 实际安装的 `2.54.0` 里，`OutputMode` 的字面量是
> `('text', 'tool', 'native', 'prompted', 'tool_or_text', 'image', 'auto')`。
> 其中 `'tool_or_text'` 是**已废弃**的兼容取值；`StructuredOutputMode = ('tool', 'native', 'prompted')` 是可以显式指定为「结构化输出模式」的子集。**工程上你主要使用前四个 + `image`，几乎不用手写 `'auto'`**。

### 4.2 用户面向的标记类：把模式写进 `output_type`

你通常不直接写 `output_mode=`，而是用**标记类**把「类型 + 模式 + 参数」一起表达：

```python
from pydantic_ai import Agent
from pydantic_ai.output import (
    ToolOutput, NativeOutput, PromptedOutput, TextOutput, StructuredDict,
)

Agent(model, output_type=ToolOutput(Review, max_retries=3, strict=True))
Agent(model, output_type=NativeOutput(Review, name="review", strict=True))
Agent(model, output_type=PromptedOutput(Review))
Agent(model, output_type=TextOutput(Review, output_function=parse_review))
Agent(model, output_type=StructuredDict({...json schema...}, name="Config"))
```

| 标记类 | 关键字段 | 作用 |
|--------|----------|------|
| `ToolOutput[T]` | `output` / `name` / `description` / `max_retries` / `strict` / `sequential` | 用**工具**承载输出；`max_retries` 单独调大该输出工具的重试预算 |
| `NativeOutput[T]` | `outputs` / `name` / `description` / `strict` / `template` | provider **原生**结构化输出；`template=False` 可关掉 schema 提示 |
| `PromptedOutput[T]` | `outputs` / `name` / `description` / `template` | 把 schema 写进**提示词**，从文本里解析 |
| `TextOutput[T]` | `output_function` | 文本由你的函数处理；**流式时 `stream_text()` 不应用该函数**，要用 `stream_output()` |
| `StructuredDict(json_schema, name=None, description=None)` | — | 返回一个「带 JSON Schema 的 `dict[str, Any]` 子类」，适合 schema 在运行期才确定的场景 |

此外还有 `Choices` / `Choice`（运行期确定的枚举选项，`value` 可为 callable）与 `BoolCriteria`（给 `bool` 字段加真假语义），它们与本章主线关系较浅，知道存在即可。

### 4.3 默认行为：`output_type` 是 Pydantic 模型 → **tool 模式**

这是全章最重要的默认值：

```python
agent = Agent(model, output_type=Review)   # Review 是 BaseModel
```

此时框架会：

1. 取 `Review.model_json_schema()`，注册一个**输出工具**（默认名 `final_result`，默认描述 `The final response which ends this conversation`）；
2. 把该工具的参数 schema 作为「模型必须调用的工具」发给模型；
3. 模型返回一次 `ToolCallPart(name='final_result', args={...})`；
4. 框架用 `Review` 校验这些参数，通过则把它作为 `run_result.output`，失败则触发**输出重试**。

因此：**`output_type` 是 BaseModel 时底层会多出一个输出工具，`run_result.response.tool_calls` 里能看到它**——这一点在 labs 里被显式打印出来。

### 4.4 校验与重试的心智模型

```text
模型产出（tool / native / prompted / text）
        │
        ▼
   schema 校验（Pydantic）        ← 形状不对 → 输出重试
        │ 通过
        ▼
 @agent.output_validator 业务校验  ← 抛 ModelRetry → 输出重试
        │ 通过
        ▼
      最终 output
```

- 任一环节失败都会**消耗一次输出重试预算**，并把一段 `RetryPromptPart`（含错误信息）回填给模型，要求它重出；
- 预算耗尽时抛 `UnexpectedModelBehavior('Exceeded maximum output retries (N)')`——**不是**你的业务异常，而是「模型没能在允许的次数内给出合格结果」的框架级失败。

### 4.5 两类重试预算

| 概念 | 参数 | 语义 |
|------|------|------|
| **全局输出预算** | `Agent(..., retries=AgentRetries(output=N))` 或 `retries=N` | 输出校验的总重试上限（文本路径上是全局预算；工具路径上作为每个输出工具的默认值） |
| **每输出工具预算** | `ToolOutput(T, max_retries=N)` | 只覆盖**这一个**输出工具的重试次数 |
| 工具调用预算（对照） | `AgentRetries(tools=N)` | 普通函数工具调用失败的重试次数 |

> 关键区别：**全局预算是「整个输出校验生命周期」的上限，每工具预算是「某一次输出工具调用」的上限**。当 `ToolOutput(max_retries=...)` 存在时，它优先于 agent 级默认值。

---

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 [`labs/part_2/ch_2_4.py`](../labs/part_2/ch_2_4.py) **完全一致**。它用一个「代码评审结论」模型，把 tool 模式、校验器重试、重试耗尽、prompted/native 对照、以及 `str` / `list` / 可空联合串了一遍。

```python
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
```

### 5.1 逐段讲解

**第 0 段 · 输出契约。** `Review` 就是「模型必须产出的形状」。`Field(ge=0, le=10)` 这类约束会进入 JSON Schema（`minimum`/`maximum`），从而**提前影响模型**，而不是等产出后再拒绝。

**第 1 段 · 默认 tool 模式。** `Agent(TestModel(custom_output_args=...), output_type=Review)`——`TestModel` 用固定参数扮演模型，`custom_output_args` 就是它「产出的结构化结果」。运行后 `result.output` 是被校验过的 `Review`；`result.response.tool_calls` 里能看到名为 `final_result` 的输出工具调用，这直接证明了默认走的是 **tool 模式**。

**第 2 段 · 输出校验器。** `@agent.output_validator` 装饰的函数签名是 `(ctx, output) -> output`。它在 schema 校验**之后**运行，适合表达 schema 表达不了的业务规则。这里用 `fail_times` 控制「先失败几次」：前 `fail_times` 次抛 `ModelRetry`，之后返回。注意到第 2 次调用时 `ctx.retry == 1`、`ctx.retries == {'final_result': 1}`——**键是输出工具名 `final_result`**，值是已消耗的重试数。

**`run_per_tool_budget`。** 这个函数演示 4.5 节的预算区别：全局 `AgentRetries(output=1)` 很小，但 `ToolOutput(Review, max_retries=3)` 把该输出工具的预算调到 3，于是校验器能被调用 3 次（失败 2 次后成功）。

**第 3 段 · 重试耗尽。** 校验器**永远**抛 `ModelRetry`，预算设为 1。框架在耗尽后抛 `UnexpectedModelBehavior('Exceeded maximum output retries (1)')`。`run_retry_exhaustion` 把它捕获并返回，方便测试断言。

**第 4 段 · prompted 模式。** `PromptedOutput(Review)` 把 schema 写进提示词，模型返回**文本**。可见 `result.response.tool_calls == []`、`result.response.text` 是一段 JSON 字符串，而 `result.output` 仍是 `Review`——解析与校验由框架完成。

**第 5 段 · native 模式。** `NativeOutput(Review)` 需要模型 profile 支持原生结构化输出。`TestModel` 不支持，运行会抛 `UserError('Native structured output is not supported by this model.')`。这正是「模式受 provider 能力约束」的直观体现。

**第 6 段 · 其它形态。** `str` 走 text 模式；`list[Review]` 的输出工具 schema 是数组（TestModel 自动按外层键 `response` 包装）；`Review | None` 表示「结构化结果或 `None`」的可空联合。

### 5.2 运行输出（节选）

```text
1) 默认 tool 模式：output_type 为 BaseModel，产出结构化结果
output: score=8 summary='结构清晰，边界处理到位'
底层响应里的工具调用名: ['final_result']
底层响应文本 (tool 模式下为 None): None

2) 输出校验器：先失败一次（ModelRetry），重试后成功
校验器被调用次数: 2
每次的 ctx.retry: [0, 1]
每次的 ctx.retries: [{}, {'final_result': 1}]
每输出工具预算 ToolOutput(max_retries=3) 下，校验器调用次数: 3

3) 重试预算耗尽：validator 永远失败 + AgentRetries(output=1)
抛出异常: UnexpectedModelBehavior
异常信息: Exceeded maximum output retries (1)

4) prompted 模式：PromptedOutput（把 schema 写进提示词，从文本解析）
底层是否有输出工具调用: []
底层响应文本: '{"score": 6, "summary": "prompted 模式产出的结论文本"}'

5) native 模式：NativeOutput 需要 provider 支持（TestModel 不支持）
结果: Native structured output is not supported by this model.
```

---

## 6. 深入剖析

### 6.1 `output_type` 如何被翻译成模式（框架内部）

框架在构造 `Agent` 时调用 `OutputSchema.build(output_type)`，大致顺序是：

1. 展平嵌套序列与联合；
2. 识别并剔出「特殊项」：`NoneType`（→ `allows_none`）、`DeferredToolRequests`、`BinaryImage`（→ `allows_image`）；
3. 若含 `NativeOutput` / `PromptedOutput`，它们**必须是唯一的输出类型**；
4. 其余按 `str`/`TextOutput`、`ToolOutput`、其它类型分类；
5. 依内容选择具体的 `OutputSchema` 子类，从而决定 `mode`：

| 输入形态 | 得到的 Schema | `mode` |
|----------|---------------|--------|
| 一个或多个结构化类型（含 BaseModel） | `ToolOutputSchema` | `'tool'` |
| 只有 `str` / `TextOutput` | `TextOutputSchema` | `'text'` |
| `NativeOutput(...)` | `NativeOutputSchema` | `'native'` |
| `PromptedOutput(...)` | `PromptedOutputSchema` | `'prompted'` |
| 只有图像 | `ImageOutputSchema` | `'image'` |

**结论：`output_type` 是 BaseModel（或多个结构化类型）时，一定落到 tool 模式**——这就是本章反复强调的默认行为。

### 6.2 输出工具：为什么它能兼容几乎所有模型

tool 模式把「产出结果」表达成一次普通的工具调用。于是：

- 只要模型会调用工具（几乎所有主流模型都会），就能用它做结构化输出；
- 输出工具的 schema 就是你的类型 schema，校验天然存在；
- 代价是「调用一次工具」带来的额外 token 与一轮对话往返。

框架生成的输出工具定义（`ToolDefinition(kind='output', ...)`）默认名为 `final_result`，可被 `ToolOutput(name=...)` 覆盖；多个结构化输出类型时，名称会带上后缀并去重。

### 6.3 `@agent.output_validator` 的签名与语义

```python
from pydantic_ai import Agent, ModelRetry, RunContext

@agent.output_validator
def check(ctx: RunContext[None], output: Review) -> Review:
    if output.score < 0:
        raise ModelRetry("评分不能为负，请重出")
    return output
```

要点：

- **`output` 是语义值**：tool 模式下你拿到的是内部 dict 经过解包后的 `Review` 实例，而不是 `{'final_result': {...}}`——框架替你剥掉了外层包装。
- **抛 `ModelRetry(msg)`**：框架把 `msg` 作为 `RetryPromptPart` 回填给模型，要求它重新产出；这**消耗一次输出重试预算**。
- **抛 `ValueError` / `AssertionError`**：也会被转换为重试提示（等价于请求模型重出）。
- **返回 `output`**：可以是原地返回，也可以是**改过一个新对象**——校验器允许做「修正而后接受」，例如把 `score` 截断到合法区间。
- 校验器可以有多个，按注册顺序执行。

### 6.4 重试计数与预算：`ctx.retry` / `ctx.retries` / `ctx.max_retries`

在输出校验器内，`RunContext` 提供：

| 属性 | 含义 |
|------|------|
| `ctx.retry` | 当前这个输出工具的**第几次尝试**（第一次为 `0`） |
| `ctx.retries` | `dict[str, int]`，键是工具名（tool 模式下是输出工具名，如 `'final_result'`），值是已消耗的重试数 |
| `ctx.max_retries` | 当前校验可见的重试预算上限 |

预算来源与覆盖关系：

```text
Agent(retries=AgentRetries(output=N))     # 全局默认：每个输出工具默认重试 N 次
        │  被覆盖
        ▼
ToolOutput(T, max_retries=M)              # 只针对这一个输出工具：重试 M 次
```

- 文本路径：`output` 预算就是**全局**上限；
- 工具路径：`output` 预算作为每个输出工具的**默认** `max_retries`，可被 `ToolOutput(max_retries=...)` 覆盖；
- 普通函数工具的预算由 `AgentRetries(tools=N)` 及 `@agent.tool(retries=...)` 管理，与输出预算是**分开的两本账**。

### 6.5 耗尽时的异常

当预算是 `N` 而模型连续 `N` 次都没给出合格结果时，框架抛：

```text
pydantic_ai.exceptions.UnexpectedModelBehavior: Exceeded maximum output retries (N)
```

它属于 `AgentRunError` 一系（表示「运行期模型行为异常」），是**框架级失败信号**。工程上你应当在 agent 边界显式捕获它，转成降级策略（返回兜底结果、转人工、记录告警），而不是让它冒泡到最终用户。

### 6.6 与 `run_stream` 的关系（提前预告）

`TextOutput` 的处理函数**不会**作用于 `stream_text()`（那只是文本增量）；需要处理后的结构化结果请用 `stream_output()`。此外，`run_stream` 以**首个匹配 `output_type` 的输出**为终点——这在第 3.3 章详细展开。

---

## 7. 常见变体与工程实践

### 7.1 `output_type` 的常见形态对照

| `output_type` | 模式 | 得到的结果 | 说明 |
|---------------|------|------------|------|
| `str`（默认） | `text` | `str` | 纯文本，无输出工具 |
| `Review` | `tool` | `Review` | 结构化，兼容性最好 |
| `list[Review]` | `tool` | `list[Review]` | 输出工具 schema 是数组 |
| `Review | None` | `tool` | `Review | None` | 可空联合：允许模型给出 `None` |
| `[Review, Note]` | `tool` | `Review | Note` | 多输出类型 → 多个输出工具 |
| `NativeOutput(Review)` | `native` | `Review` | 需 provider 支持 |
| `PromptedOutput(Review)` | `prompted` | `Review` | 文本兜底，token 略高 |
| `StructuredDict(schema)` | `tool` | `dict[str, Any]` | schema 运行期才确定 |

> 注意：`NativeOutput` / `PromptedOutput` **必须是唯一输出类型**，且不能再与 `DeferredToolRequests` / 图像混用；`NoneType` 与结构化类型混用时，`None` 会作为独立输出工具暴露。

### 7.2 工程准则：该选哪种输出模式？

```text
需要结构化结果？
├─ 否 → output_type=str（text 模式）
└─ 是
   ├─ 想「一处配置、处处可跑」 → 默认 tool 模式（output_type=BaseModel）
   │     • 兼容性最好；代价是多一次工具调用往返
   ├─ 目标 provider 明确支持原生结构化输出，且在意 token/稳定性
   │     → NativeOutput（用 profile 能力探测确认后再启用）
   └─ provider 既不方便给工具、也不支持原生
         → PromptedOutput 兜底
```

实践建议：

1. **默认就用 tool 模式**（即直接写 `output_type=SomeModel`）——这是最稳、最不依赖 provider 的选择。
2. **上 native 前先探测能力**。`NativeOutput` 在 TestModel 上就直接 `UserError`；真实模型也可能因 profile 未标记而失败。多模型切换时尤其要谨慎（第 2.6 章）。
3. **prompted 是诚实的兜底**，不要把它当首选：它把 schema 塞进提示词，token 成本更高、也更依赖模型的指令遵循能力。
4. **模式切换用标记类表达，而不是散落在代码里**。把「类型 + 模式 + 参数」集中在 `output_type=` 一处，便于审阅与回归。
5. **预算要显式**。默认输出重试预算很小（通常 1 次）；生产上按业务容忍度设 `AgentRetries(output=...)`，并对个别「难产出」的输出放宽 `ToolOutput(max_retries=...)`。

### 7.3 校验器的常见用法

- **规范化后接受**：如把空摘要替换为默认文案，或把 `score` 截断到 `[0, 10]`，然后返回修正后的对象（不触发重试）。
- **业务规则拦截**：如「高优先级问题必须给出整改建议」，不满足则 `ModelRetry` 让模型补全。
- **多校验器叠加**：注册多个 `@agent.output_validator`，各司其职，按注册顺序执行。

### 7.4 何时该校验，何时该用 schema

- 能用 `Field(...)` 表达（类型、范围、长度、正则、枚举）的，**放进模型**——这样错误在「模型产出前」就被约束，省一次重试；
- 只有**跨字段/依赖外部状态**的规则才放进校验器（如「摘要不得与你刚检索到的证据矛盾」）。

---

## 8. 练习

> 建议先自己在 [`labs/part_2/ch_2_4.py`](../labs/part_2/ch_2_4.py) 上改动、运行，再看答案要点。

**练习 1（入门）** 用 `TestModel(custom_output_args=...)` 构造一个 `Review` 的 tool 模式 agent，打印 `result.output`、`result.response.tool_calls` 的工具名，以及 `agent.output_json_schema()`。

<details>
<summary>答案要点</summary>

```python
agent = Agent(TestModel(custom_output_args={"score": 8, "summary": "ok"}), output_type=Review)
result = agent.run_sync("评审")
assert result.output.score == 8
assert [c.tool_name for c in result.response.tool_calls] == ["final_result"]
schema = agent.output_json_schema()   # 公共方法，返回输出 JSON Schema
```
关键观察：默认 tool 模式底层确实有 `final_result` 输出工具；`output_json_schema()` 是公开入口。
</details>

**练习 2（入门）** 写一个校验器：当 `score > 10` 时抛 `ModelRetry`，否则返回。用计数让它只失败一次，断言校验器被调用 2 次。

<details>
<summary>答案要点</summary>

在 `@agent.output_validator` 内用外部计数器：第一次 `raise ModelRetry(...)`，第二次 `return output`。断言调用次数为 2，可用 `run_validator_retry(fail_times=1).attempts == 2` 的方式暴露计数器。注意 `ctx.retry` 会从 `0` 递增到 `1`。
</details>

**练习 3（进阶）** 把 `retries` 设得很小、校验器永远失败，确认抛出 `UnexpectedModelBehavior`，并断言异常信息包含 `Exceeded maximum output retries`。

<details>
<summary>答案要点</summary>

```python
agent = Agent(TestModel(custom_output_args={"score": 8, "summary": "ok"}),
              output_type=Review, retries=AgentRetries(output=1))

@agent.output_validator
def always_fail(ctx, output: Review) -> Review:
    raise ModelRetry("never")

import pytest
from pydantic_ai import UnexpectedModelBehavior
with pytest.raises(UnexpectedModelBehavior) as exc:
    agent.run_sync("评审")
assert "Exceeded maximum output retries (1)" in str(exc.value)
```
</details>

**练习 4（进阶）** 演示「全局预算 vs 每输出工具预算」：agent 级 `AgentRetries(output=1)`、`ToolOutput(Review, max_retries=3)`，让校验器失败 2 次后成功，断言最终成功且校验器被调用 3 次。

<details>
<summary>答案要点</summary>

见 labs 的 `run_per_tool_budget`。关键点：`ToolOutput(max_retries=3)` **覆盖**了 agent 级 `output=1` 的默认值，因此 2 次失败不会立刻耗尽。若去掉 `ToolOutput(max_retries=3)`，同样的 2 次失败会抛 `UnexpectedModelBehavior`。
</details>

**练习 5（挑战）** 用 `PromptedOutput` 与 tool 模式各跑一次同一个 `Review`，比较两者底层消息：一个有输出工具调用、一个没有；断言 `PromptedOutput` 的结果依然是被校验过的 `Review`。

<details>
<summary>答案要点</summary>

prompted 用 `TestModel(custom_output_text='{"score": 6, "summary": "..."}')`，tool 用 `custom_output_args={...}`。断言 `prompted.result.response.tool_calls == []` 且 `result.response.text` 非空，而 `isinstance(result.output, Review)`。这解释了「模式不同，产物类型相同」——解析与校验由框架统一负责。
</details>

---

## 9. 验收标准

跑通验收测试即视为掌握本章：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_4.py -q
```

逐条自测（应全部为真）：

1. `output_type=Review` 的默认运行产出 `Review`，且底层 `response.tool_calls` 的工具名为 `final_result`、`response.text is None`。
2. `agent.output_json_schema()` 含 `score`/`summary` 且 `required == ['score', 'summary']`。
3. 校验器先失败一次时，被调用 2 次，`ctx.retry == [0, 1]`，第二次 `ctx.retries == {'final_result': 1}`。
4. 校验器不失败时，被调用 1 次。
5. 校验器永远失败且 `AgentRetries(output=1)` 时，抛 `UnexpectedModelBehavior`，信息含 `Exceeded maximum output retries (1)`。
6. `ToolOutput(max_retries=3)` + 全局 `output=1`，校验器失败 2 次仍成功，被调用 3 次。
7. `PromptedOutput(Review)` 产出 `Review`，`response.tool_calls == []`、`response.text` 非空。
8. `NativeOutput(Review)` 在 `TestModel` 上报错信息含 `not supported`。
9. `output_type=str` 返回 `str`；`list[Review]` 返回 2 个 `Review`；`Review | None` 返回 `Review`。
10. `python part_2/ch_2_4.py` 可直接运行并打印上述观察点，无异常。

---

## 10. 常见坑与排错

| 现象 | 原因 | 处理 |
|------|------|------|
| `Exceeded maximum output retries (N)` | 模型在 N 次内始终没能产出合格结果 | 放宽 `AgentRetries(output=...)` 或 `ToolOutput(max_retries=...)`；检查校验器是否过严、提示词是否清晰 |
| 校验器里看到 `output` 是 `Review` 而非 dict | 这是**预期行为**：框架已剥掉输出工具的外层包装 | 直接按类型使用即可；需要底层原始 dict 时看消息 part |
| `Native structured output is not supported by this model` | 该模型 profile 未声明原生结构化输出能力 | 改用默认 tool 模式；或换支持的 provider（见第 2.6 章能力探测） |
| `custom_output_args` 与 `list[Model]` 对不上 | TestModel 会按外层键（如 `response`）包装数组；传参应是**内层值** | `custom_output_args=[{...}, {...}]`，不要自己再包一层 |
| 同时设置 `custom_output_text` 和 `custom_output_args` | 二者互斥 | 只留一个：文本模式用前者，工具模式用后者 |
| `stream_text()` 拿不到结构化结果 | `TextOutput` 的处理函数只作用于 `stream_output()` | 需要处理后结果时用 `stream_output()` |
| 校验器改了 `output` 却没生效 | 校验器必须 `return` 修改后的对象 | `return output`（或返回新对象） |

---

## 11. 面试延伸

**Q1：`output_type` 是一个 Pydantic 模型时，Pydantic AI 默认怎么让模型产出它？为什么这样设计？**
答题要点：默认走 **tool 模式**——注册一个名为 `final_result` 的输出工具，把类型 schema 作为工具参数 schema，模型通过「调用工具」给出结构化结果。这样设计的核心是**兼容性**：几乎所有主流模型都会调用工具，因此不依赖 provider 的原生结构化输出能力；代价是多一次工具往返。

**Q2：`NativeOutput`、`PromptedOutput`、`ToolOutput` 三者有什么区别，怎么选？**
答题要点：`ToolOutput` 用工具承载输出（兼容最好）；`NativeOutput` 走 provider 原生结构化输出（省 token、稳，但需 provider 支持，能力不足会报 `UserError`）；`PromptedOutput` 把 schema 写进提示词、从文本解析（兜底，token 更高、依赖指令遵循）。工程默认 tool，上 native 前做能力探测，prompted 仅作兜底。

**Q3：输出校验器怎么触发重试？重试计数与预算存在哪里？**
答题要点：`@agent.output_validator` 里抛 `ModelRetry(msg)`（或 `ValueError`）即触发重试，框架把消息作为 `RetryPromptPart` 回填给模型。计数通过 `RunContext.retry`（当前第几次尝试）、`RunContext.retries`（`{工具名: 已用重试数}`）、`RunContext.max_retries`（预算上限）暴露；tool 模式下键是输出工具名（如 `final_result`）。

**Q4：全局输出重试预算和每个输出工具的重试预算是什么关系？**
答题要点：`AgentRetries(output=N)` 是全局默认——文本路径上是总上限，工具路径上作为每个输出工具的默认 `max_retries`；`ToolOutput(T, max_retries=M)` 针对单个输出工具覆盖该默认值。二者是「默认值与覆盖」的关系。`AgentRetries(tools=N)` 管的是普通函数工具，是另一本账。

**Q5：输出重试耗尽时会发生什么？生产上应该怎么处理？**
答题要点：抛 `UnexpectedModelBehavior('Exceeded maximum output retries (N)')`——属 `AgentRunError` 一系的框架级失败。生产上应在 agent 边界捕获它并降级：返回兜底结果/转人工/告警，同时记录命中率与模型/提示词版本，作为质量回归指标；不应让它直接冒泡到终端用户。

---

## 12. 延伸阅读

- [`code_wiki/04-messages-and-output.md`](../../code_wiki/04-messages-and-output.md) · §8「公开输出 API」、§9「内部输出机制」：`OutputMode` / `StructuredOutputMode` 的定义、用户面向标记类全表、`OutputSchema.build` 的展平与分派、处理器（`ObjectOutputProcessor` / `UnionOutputProcessor` / `TextOutputProcessor`）、`OutputToolset`、以及输出 Hook 运行器。
- [`code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md) · §1.1（`retries` 与 `AgentRetries`）、§7（异常体系）、§9「重试与用量限制」：`GraphAgentState.consume_output_retry` 与 `UnexpectedModelBehavior` 的产生位置，以及 tool / output / model 三类重试的区别。
- [`code_wiki/03-models-providers-profiles.md`](../../code_wiki/03-models-providers-profiles.md)：`ModelProfile.default_structured_output_mode` 与能力探测——决定 `native` 模式是否可用的依据。
