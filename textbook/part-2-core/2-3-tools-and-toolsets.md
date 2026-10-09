# 2.3 工具与 Toolset：定义、校验、执行、重试、组合

> 篇二 · Pydantic AI 核心 · 配套 labs：`labs/part_2/ch_2_3.py`、`labs/part_2/test_ch_2_3.py`

## 1. 本课目标

1. 说清 `@agent.tool` 与 `@agent.tool_plain` 的唯一区别（首参是否 `RunContext`），并能在两者间正确取舍。
2. 解释工具的参数 JSON Schema 是如何从**函数签名 + 类型注解 + docstring** 自动生成的（`function_schema`）。
3. 复述一次工具调用的完整执行流程：模型产出 `ToolCallPart` → 宿主校验参数 → 执行 → 回填 `ToolReturnPart` → 再请求模型。
4. 区分三类失败语义：**参数校验失败**、**工具内 `raise ModelRetry`**（可重试，回填 `RetryPromptPart`）、**工具内直接抛异常**（中断整个运行）。
5. 掌握重试预算：`Agent(retries=...)`、`AgentRetries`、`@agent.tool(retries=n)` 的层级与解析顺序。
6. 掌握 `ToolDefinition` 的关键字段（`name` / `description` / `parameters_json_schema` / `kind`）。
7. 会用 `FunctionToolset` + `Agent(toolsets=[...])` 打包复用工具，并知道 `Tool` 对象的函数式注册方式。
8. 写出「给模型看」的高质量工具名与 `description`。

## 2. 前置知识

- 已完成 2.1（`Agent` / `run_sync` / 结构化输出）与 2.2（`ModelMessage`、`ToolCallPart`、`ToolReturnPart` 等 parts 与历史维护）。
- 会读写 Python 类型注解与函数签名，理解 Pydantic v2 的模型 / JSON Schema（篇一 1.3）。
- 知道「LLM 只会输出结构化调用意图，执行权在宿主」这一心智模型（篇一 1.1）。
- **不需要** API Key：本章 labs 全程用 `FunctionModel` 离线、确定性地驱动。

## 3. 为什么需要它

### 3.1 没有工具，Agent 只是个「会聊天的文本框」

LLM 只会生成文本，不能读数据库、不能算账、不能调你的内部 API。要让 Agent「真的做事」，宿主必须做两件事：**把可用能力用结构化说明书告诉模型**，**在模型说「我要调用 X」时去执行 X 并把结果塞回去**。这套机制就是「工具（tool）」。

### 3.2 手写工具层的四个坑

上一章你已见过工具调用的四步闭环。真把它做扎实，会立刻撞到四类工程问题，这正是本章要解决的：

| 坑 | 后果 | Pydantic AI 的对策 |
|----|------|--------------------|
| **参数没有契约** | 模型传错类型、少传字段，函数当场 `TypeError` | 从类型注解生成 JSON Schema，执行前用 Pydantic 校验 |
| **错误处理两难** | 一律崩溃太脆，一律吞掉又会让模型「假装成功」 | 区分可重试（`ModelRetry`）与终态（`ToolFailed`）；校验失败自动回填重试提示 |
| **工具散落各处** | 无法复用、无法组合、无法做横切治理 | `Toolset` 抽象与 `FunctionToolset`，`toolsets=` 组合 |
| **工具描述含糊** | 模型选错工具、漏调工具 | `description` + 参数描述来自 docstring，可显式覆盖 |

### 3.3 本章的位置

2.1 让你把 Agent **跑起来**，2.2 让你看懂**消息协议**，本章则让你**把能力交给模型**。后面 2.5（依赖注入）会展开 `RunContext` 的全部能力，3.2（延迟工具 / 审批）会展开 `requires_approval` / `defer_loading`，3.3（流式）会展开工具事件流——它们都建立在本章的工具机制之上。

## 4. 核心概念

### 4.1 工具 = 「给模型看的说明书」+「宿主执行的函数」

一个工具是**一个 Python 函数**加上**一份发给模型的说明书**。说明书由三部分构成，全部来自函数本身：

```text
name                    ← 函数名（可用 name= 覆盖）
description             ← docstring 首段（可用 description= 覆盖）
parameters_json_schema  ← 函数签名 + 类型注解 + docstring 的 Args 段
```

模型只需要「说明书」就能决定何时调用、怎么传参；真正执行的是你注册的函数。二者之间的桥，就是本章的主角。

### 4.2 `@agent.tool` vs `@agent.tool_plain`：唯一区别是首参

| 装饰器 | 函数首参 | 能拿到什么 | 适用场景 |
|--------|----------|-----------|----------|
| `@agent.tool` | `RunContext[DepsT]` | 依赖注入值 `ctx.deps`、历史 `ctx.messages`、用量、取消、`ctx.emit` 事件… | 工具需要「运行时上下文」（用户身份、DB 连接、预算…） |
| `@agent.tool_plain` | 无 | 只有模型传来的业务参数 | 纯函数：`add`、`greet`、`slugify`、纯计算/转换 |

**取舍规则**：工具**只要有一点点**依赖外部状态（当前用户、会话、连接池），就用 `@agent.tool`；否则用 `@agent.tool_plain`。`tool_plain` 的函数签名更干净、更容易单测，是「纯函数工具」的默认选择。

关键点：

- `RunContext` 是**框架注入**的，**不会**出现在发给模型的参数 schema 里——本章 labs 的测试会断言 `greet` 的参数只有 `{"name"}`。
- 即使你用 `tool_plain`，也可以在 `args_validator=` 里拿到 `RunContext`（校验器签名与工具本身解耦）。

### 4.3 参数 schema 从哪来（`function_schema`）

框架把工具函数编译成一个 `FunctionSchema`（`pydantic_ai._function_schema`），它负责两件事：**生成 JSON Schema** 与 **把校验后的参数按签名调用函数**。

```text
def add(ctx: RunContext[int], a: int, b: int) -> int:
    """两数相加。

    Args:
        a: 第一个加数。
        b: 第二个加数。
    """
```

→ 生成的 `parameters_json_schema`（实测输出，`ctx` 已被剔除）：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "a": {"type": "integer", "description": "第一个加数。"},
    "b": {"type": "integer", "description": "第二个加数。"}
  },
  "required": ["a", "b"]
}
```

三条要点：

1. **注解 → 类型**：`int`→`integer`，`float`→`number`，`str`→`string`，`bool`→`boolean`，`Literal[...]`→`enum`，`BaseModel`→嵌套对象。Pydantic v2 的 `model_json_schema()` 是唯一的真相来源。
2. **docstring → 描述**：首段成为工具 `description`；`Args:` 段逐项成为参数的 `description`。支持 `google` / `numpy` / `sphinx` 三种风格，默认 `'auto'` 自动识别。
3. **默认值 → 可选**：有默认值的参数不进 `required`（上例两个参数都必填，故都在 `required` 里）。

> `description` / 参数描述是**给模型看的 prompt**，不是注释。写得越清楚，模型选工具、填参数越准。

### 4.4 一次工具调用的完整执行流程

```text
① 组装 prompt：把「工具说明书」连同历史一起发给模型
        │
        ▼
② 模型返回 ModelResponse，其中含 ToolCallPart(tool_name, args, tool_call_id)
        │   ← 模型只是「说要调」，没有执行
        ▼
③ 宿主解析工具、按 parameters_json_schema 校验参数（Pydantic）
        ├─ 校验失败 → 回填 RetryPromptPart（附 ValidationError 明细）→ 回到 ①
        ▼
④ 执行函数：拿到已校验、已类型转换的参数（ctx 由框架注入）
        ├─ raise ModelRetry → 回填 RetryPromptPart（附提示）→ 回到 ①（可重试）
        ├─ raise ToolFailed → 回填「失败的 ToolReturnPart」（终态，不耗重试）
        ├─ 其它异常 → 直接向上抛出，中断整个 run
        ▼
⑤ 回填 ToolReturnPart(tool_name, content, tool_call_id) 到历史
        │
        ▼
⑥ 再请求模型：模型看到结果，决定「继续调工具」还是「给最终答案」
```

对应到源码：②③④⑤ 由 `CallToolsNode` → `ToolManager`（`validate_tool_call` / `execute_tool_call`）→ `process_tool_calls` 完成，⑥ 是「构造新的 `ModelRequestNode`」。详见 [02 · 核心 Agent 循环](../../code_wiki/02-core-agent-loop.md) §5。

### 4.5 失败语义：三种「出错」，三种命运

这是本章最容易混淆、也最常被面试问到的部分。

| 触发场景 | 框架行为 | 回填的 part | 是否消耗重试预算 | 运行是否继续 |
|----------|----------|-------------|------------------|--------------|
| 参数不符合 schema | 自动捕获 `ValidationError` | `RetryPromptPart`（内容=错误明细列表） | 是 | 是（模型可改正） |
| 工具内 `raise ModelRetry(msg)` | 交给模型重试 | `RetryPromptPart`（内容=`msg`） | 是 | 是（模型可改正） |
| 工具内 `raise ToolFailed(msg)` | 对模型可见的**终态**失败 | **失败的** `ToolReturnPart` | **否** | 是（模型应改弦更张） |
| 工具内抛其它异常（`ValueError`…） | 不处理，向上传播 | 无 | — | **否，整个 run 中断** |

心智模型：

- `ModelRetry` 是**控制流信号**，意思是「这不是崩溃，是我在提示模型换个参数/换个工具再试」。它把「如何修正」的说明交还给模型。
- 直接 `raise` 意味着「宿主出问题了，别再跑了」——适合断言式的编程错误、不可恢复的环境故障。
- `ToolFailed` 介于两者之间：明确告诉模型「这个方向彻底不行了」，但不浪费重试次数（例如「该订单不存在」这种业务终态）。

**重试耗尽会怎样？** 当同一工具的重试次数达到其上限，框架抛 `UnexpectedModelBehavior('Tool X exceeded max retries count of N...')`，运行结束。labs 里用这个异常来证明 `retries` 确实生效。

### 4.6 重试预算：`retries` / `max_retries`

重试上限的**解析链**（就近优先）：

```text
单个工具 @agent.tool(retries=n)   ← 最具体，优先
        ▼ 未设置
所在的 toolset 的 max_retries     ← FunctionToolset(max_retries=…)
        ▼ 未设置
Agent(retries=…) 的 tools 预算    ← 最泛化，默认 1
```

`Agent(retries=…)` 接受两种形态：

- `int`：同时设置 `tools`（工具重试）与 `output`（输出校验重试）两个预算的简写；
- `AgentRetries`（`TypedDict`）：只覆盖指定键，如 `retries={'tools': 3, 'output': 2}`。

每次运行还可以用 `agent.run(..., retries=…)` 临时覆盖 agent 级默认。默认值是 **1**，即「允许模型带着错误信息重试一次」。

### 4.7 `ToolDefinition`：发给模型的「说明书」对象

`ToolDefinition` 是工具与模型之间的**线格式**（函数工具与 output 工具共用）。关键字段：

| 字段 | 含义 |
|------|------|
| `name` | 工具名（模型用它来调用；默认取函数 `__name__`） |
| `description` | 描述（默认取 docstring 首段） |
| `parameters_json_schema` | 参数 schema；无参工具为空对象 `{"type":"object","properties":{}}` |
| `kind` | `'function'`（普通工具）/ `'output'`（输出工具）/ `'external'` / `'unapproved'`（待审批） |
| `sequential` | 是否为「屏障」工具（独占执行，不与其它工具重叠） |
| `timeout` | 单次执行超时秒数；超时向模型回填重试提示 |
| `metadata` | 不发给模型，用于过滤 / 行为定制 |

以后还会见到 `strict`（供应商级严格 schema）、`defer_loading`（延迟加载）、`return_schema`（返回值 schema）等字段——它们服务于 3.2 的进阶主题，本章先记住前四个。

### 4.8 `FunctionToolset` 与 `toolsets=`：把工具打包

`@agent.tool` 适合「属于这个 agent 的零散工具」。当一组工具需要**复用**（多个 agent 共享、按业务域拆分、做成插件）时，用 `FunctionToolset`：

```python
toolset = FunctionToolset(id="math")

@toolset.tool_plain
def square(x: int) -> int: ...

agent = Agent(model, toolsets=[toolset])   # 注册工具集
```

`FunctionToolset` 的装饰器与 `Agent` 完全同构（`tool` / `tool_plain` / `instructions`），且构造参数（`max_retries`、`timeout`、`metadata`…）会作为**逐项回落**的 toolset 级默认值。`id` 在持久化（durable execution）中用于标识工具集，普通场景可省略。

**函数式注册**：`Tool` 对象是函数的可复用包装，可在构造期直接传入：

```python
from pydantic_ai import Agent, Tool

agent = Agent(model)
agent.tool_plain(add)                 # 装饰器式
agent2 = Agent(model, tools=[Tool(add), other_fn])   # 构造式
```

`toolsets=` 还接受**工厂函数** `(ctx) -> Toolset`（每次运行动态构建，即 `DynamicToolset`）——这是 3.1 / 6.x 组合能力的入口。

### 4.9 工具命名与 `description` 最佳实践

模型靠 `name` + `description` + 参数描述来决定「调不调、调哪个、怎么填」。写法直接决定 Agent 的可靠性：

- **名字**：用动词短语、语义精确、长度适中，如 `search_orders`、`create_refund`、`get_weather`；避免 `do_stuff`、`helper1`。同一 agent 内名字必须唯一。
- **description**：写「**做什么 + 什么时候用 + 返回什么**」，必要时点明「什么时候**不要**用」。例如：`查询指定订单的物流状态；当用户询问包裹进度时使用。返回 status 与预计送达时间。`
- **参数描述**：在 docstring 的 `Args:` 段逐项说明含义、格式与单位，如 `max_results: 返回的最大条数，1–20，默认 10。`
- **别把实现细节写进 description**：模型不关心你用了哪个库；它只关心「输入什么、输出什么、何时使用」。
- **副作用工具要讲清楚**：会改数据的工具应显著标注（如「会真正发起退款」），并配合 `requires_approval`（3.2）。

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面代码与 `labs/part_2/ch_2_3.py` **逐字一致**。它用 `FunctionModel` 精确驱动模型每一次输出，覆盖本章全部要点。先通读，再看逐段讲解。

```python
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
```

### 5.1 逐段讲解

**Part 0 · 基础设施。** `Spy` 记录工具被真实调用的痕迹；`ScriptedModel` 把「模型的第 N 次输出」变成一份确定的脚本（`responses` 列表），并在每次请求时快照 `info.function_tools`（模型此刻能看到的工具定义）。这两个类让整章实验**离线、可复现、可断言**。`tool_history()` 把消息历史压平为 `(part_kind, tool_name, content)`，方便一眼看出「模型说了什么、宿主回填了什么」。

**Part 1 · `@agent.tool`。** `add` 的首参是 `RunContext[int]`，`ctx.deps` 就是 `run_sync(..., deps=100)` 传进来的依赖值（记录为 `deps=100`）。注意 `deps_type=int` 只是类型标记，运行时值是你在 `run` 时传入的。

**Part 2 · `@agent.tool_plain`。** `greet` 的签名里**只有** `name`：没有 `RunContext`，也就没有任何上下文；它更适合纯函数。测试会断言它的 `parameters_json_schema.properties` 只有 `{"name"}`——`RunContext` 被框架剥离，绝不会发给模型。

**Part 3 · 参数校验失败。** 脚本第一次让模型传 `{"a": "oops", "b": 3}`：`a` 不是整数，Pydantic 校验失败，框架**不会执行 `add`**，而是回填 `RetryPromptPart`（内容是结构化错误明细，`loc=('a',)`）。第二次模型传合法参数才真正执行。

**Part 4 · `raise ModelRetry`。** `sqrt_positive` 在收到负数时主动 `raise ModelRetry(...)`：这是「可重试」信号，框架把它变成对模型可见的提示，模型据此把 `-4` 改成 `16` 再调一次。对比：如果抛的是普通 `ValueError`，整个 run 会直接中断。

**Part 5 · `FunctionToolset`。** 用 `FunctionToolset(id="math")` 声明工具集，再 `Agent(..., toolsets=[toolset])` 注册。`square` 因此对模型可见、可调用。

**Part 6 · 重试预算。** `demo_retries_budget(n)` 让工具永远失败、模型永远重试：`n=0` 时第一次就超限，只发生 1 次模型请求；`n=2` 时允许两次重试，共 3 次请求后才抛 `UnexpectedModelBehavior`。`demo_per_tool_retries_override()` 演示单工具 `retries=0` 覆盖 agent 的 `retries=5`。

运行结果（节选）：

```text
1) @agent.tool（带 RunContext）：模型产出 ToolCallPart → 执行 → 回填 ToolReturnPart
final output = '2 + 3 = 5'
tool calls    = [('add', {'deps': 100, 'a': 2, 'b': 3})]
model requests= 2
   ('user-prompt', None, '请计算 2 + 3')
   ('tool-call', 'add', None)
   ('tool-return', 'add', 5)
   ('text', None, '2 + 3 = 5')

（其余分组同理：分组 3 会打印 `loc=('a',)` 的校验错误明细；分组 6 会打印 `retries=0/2` 各自在 1 / 3 次请求后抛 `UnexpectedModelBehavior`。）

7) 从函数签名 / 注解 / docstring 生成的 ToolDefinition
name        = add
description = '两数相加。'
kind        = 'function'
parameters_json_schema = {'additionalProperties': False, 'properties': {'a': {'description': '第一个加数。', 'type': 'integer'}, 'b': {'description': '第二个加数。', 'type': 'integer'}}, 'required': ['a', 'b'], 'type': 'object'}
```

## 6. 深入剖析

**6.1 `function_schema` 的双重职责。** `FunctionSchema` 既生成 JSON Schema，也负责「按签名调用函数」：它把模型传来的 `args` 经 Pydantic 校验后，注入 `RunContext`（若 `takes_ctx`），再按位置/关键字调用原函数。这解释了为什么 `RunContext` 不出现在 `parameters_json_schema` 里——它在 schema 之外，由调用层补上。`Tool.takes_ctx` 就是这一判断的载体：未显式指定时由签名首参类型推断。

**6.2 校验与执行的钩子链。** `ToolManager.validate_tool_call` 与 `execute_tool_call` 把每个调用包进能力钩子链（`before_tool_validate → 校验 → after_tool_validate → before_tool_execute → 执行 → after_tool_execute`）。本章只用到默认行为，但你要知道：**参数校验与执行是分开的两段**——`args_validator`（自定义校验）在 schema 校验之后、执行之前运行；「延迟/审批」这类信号也只允许在参数已校验之后抛出。这是 3.1（Capabilities）与 3.2（审批/延迟）的地基。

**6.3 结果回填与 `end_strategy`。** 函数工具的返回被包成 `ToolReturnPart` 追加进历史，随即进入下一轮 `ModelRequest`。默认 `end_strategy='graceful'` 的语义是：排在 output 工具之前的函数工具先跑完；若函数工具产出 `RetryPromptPart`，则**抑制**同批的结构化输出（「retry-wins」），让模型先处理重试。纯文本输出（`str`）不会抢占工具调用。三种策略（`early` / `graceful` / `exhaustive`）的完整差异见 2.4 与源码篇 02 §8。

**6.4 重试账本的解析细节。** 单个工具的重试上限按 `tool.max_retries → toolset.max_retries → ctx.max_retries` 逐级回落，`ctx.max_retries` 默认 1。`ToolManager` 用 `failed_tools` / `succeeded_tools` 在两个 step 之间结转计数：成功即清零，失败则 +1。当 `已用重试数 >= max_retries` 时抛 `UnexpectedModelBehavior`。这就是 labs 中 `retries=0/2` 分别得到 1 / 3 次模型请求的原因。

**6.5 `ToolDefinition` 与消息 parts 的对应。** `ToolDefinition` 是**请求方向**（发给模型的说明书）；`ToolCallPart` 是**响应方向**（模型发出的调用意图）；`ToolReturnPart` / `RetryPromptPart` 是**回填方向**（宿主写回历史）。三者用 `tool_name` + `tool_call_id` 对齐，且调用与返回必须成对相邻——这正是 2.2 里讲过的「顺序即语义」。

**6.6 为什么默认并行、`sequential` 是什么。** 默认情况下，同一轮里互不依赖的多个工具调用会**并行**执行（`parallel_execution_mode` 默认 `'parallel'`）。当某工具会与其它调用产生竞争（例如「先删目录再写文件」），把它标为 `sequential=True` 使其成为**屏障**：它独占执行，之前的调用先完成、之后的等它结束。这是性能与正确性之间的开关。

## 7. 常见变体与工程实践

- **纯函数工具**（`tool_plain`）：无副作用、可单测，优先使用；`add` / `sqrt_positive` 即此类。
- **带上下文的工具**（`tool`）：需要 `ctx.deps`（用户、连接）、`ctx.messages`（读历史）、`ctx.usage`（看预算）或 `ctx.emit`（发事件）时使用。
- **工厂 / 闭包工具**：把依赖（如 `httpx.AsyncClient`）通过闭包捕获，避免用全局变量：

  ```python
  def make_search(client: httpx.AsyncClient):
      @agent.tool_plain
      def search(q: str) -> list[str]:
          """按关键词检索。"""
          return client.get(...).json()
      return search
  ```

- **`FunctionToolset` 复用**：把同一批工具注册到多个 agent；按业务域拆分成多个 toolset，再用 `toolsets=[a, b]` 组合。
- **动态工具集**：`toolsets=` 传工厂函数 `(ctx) -> Toolset`，按运行/用户动态决定暴露哪些工具（详见 3.1、6.x）。
- **`Tool.from_schema(...)`**：已有现成 JSON Schema 时，跳过 schema 生成、只按关键字调用函数——用于对接外部工具协议。
- **`prepare=` 逐 step 改写定义 / 隐藏工具**：返回 `None` 即该 step 不注册此工具。
- **`timeout=` 与 `tool_timeout=`**：单工具 / agent 级执行超时；超时向模型回填重试提示而非崩溃。
- **`metadata=`**：不发给模型，用于按工具过滤、埋点、审批策略等横切逻辑。
- **工程清单**：① 工具名唯一且语义明确；② `description` 写「做什么 + 何时用 + 返回什么」；③ 参数在 docstring 里逐项描述；④ 业务校验优先 `ModelRetry`（把修正权交给模型），环境故障才直接抛；⑤ 给副作用工具加 `requires_approval`、给不确定的工具设 `timeout`；⑥ 重试预算要显式、有限，避免烧钱。

## 8. 练习

**练习 1（基础）** `@agent.tool` 与 `@agent.tool_plain` 的区别是什么？为什么 `RunContext` 不出现在参数 schema 里？

<details><summary>参考答案要点</summary>唯一区别是函数**首参是否为 `RunContext`**（`tool` 有、`tool_plain` 无）。`RunContext` 由框架在执行层注入，不属于「模型要填的业务参数」，因此被 `function_schema` 从 schema 中剥离——测试断言 `greet.parameters_json_schema.properties == {"name"}` 即证明此点。</details>

**练习 2（基础）** 给 `add` 增加一个可选参数 `scale: float = 1.0`，生成的 schema 会怎么变？模型可以怎么调用？

<details><summary>参考答案要点</summary>`properties` 多出 `scale`（`{"type":"number","description":...}`），但它**不进 `required`**（有默认值）。模型可只传 `a`/`b`（`scale` 取 1.0），也可显式传 `scale`。这体现了「默认值 → 可选参数」的映射。</details>

**练习 3（进阶）** 一个工具在「订单不存在」时该 `raise ModelRetry` 还是 `raise ToolFailed`？为什么？

<details><summary>参考答案要点</summary>用 `ToolFailed`。订单不存在是**业务终态**，重试同一个订单号毫无意义；`ToolFailed` 回填一个失败的 `ToolReturnPart`，模型能读到「此路不通」并改弦更张（如改用订单搜索），且**不消耗重试预算**。`ModelRetry` 适合「参数可修正/瞬时错误」，会让模型带着提示重试。</details>

**练习 4（进阶）** `Agent(retries=0)` 下，一个工具 `raise ModelRetry` 会发生什么？`Agent(retries={'tools': 2})` 呢？

<details><summary>参考答案要点</summary>`retries=0` 时第一次 `ModelRetry` 就触发 `UnexpectedModelBehavior('... max retries count of 0')`，运行结束（labs 中模型请求数为 1）。`retries={'tools': 2}` 只把工具预算设为 2（输出预算仍为默认 1），因此允许两次重试、共 3 次模型请求后才超限，与 labs 的 `retries=2` 行为一致。</details>

**练习 5（综合）** 你要做一个客服 Agent，工具有 `search_orders`（查订单）、`create_refund`（发起退款）、`escalate`（转人工）。请给出：哪些用 `tool` / `tool_plain`、各自的 `description` 要点、如何处理「订单不存在」与「参数非法」，以及如何降低 `create_refund` 的风险。

<details><summary>参考答案要点</summary>`search_orders` 需要访问 DB 连接 → `tool`（用 `ctx.deps`）；`create_refund` 需要当前用户/权限 → `tool` 且**必须**是副作用工具；`escalate` 若只发消息也建议 `tool`（需 ctx 拿会话）。`description` 各写「做什么 + 何时用 + 返回什么」，`create_refund` 显式标注「会真正发起退款」。参数非法交框架自动回填 `RetryPromptPart`；「订单不存在」用 `ToolFailed`。风险控制：`create_refund` 加 `requires_approval=True`（人工审批）+ `timeout`，并做幂等键防重复退款。</details>

## 9. 验收标准

完成本章的最低标准：`test_ch_2_3.py` 全绿（9 个测试、20+ 条断言）。它们分别验证：

1. `@agent.tool` 的工具能拿到 `RunContext` 并读到 `deps`，返回值正确。
2. 工具结果以 `ToolReturnPart` 回填进历史，并与 `ToolCallPart` 成对相邻。
3. `@agent.tool_plain` 不接收 context：调用记录里无 ctx，schema 只有业务参数。
4. `ToolDefinition` 的 `name` / `description` / `kind` / `parameters_json_schema` 由签名 + 注解 + docstring 生成。
5. 参数校验失败 → 回填 `RetryPromptPart`（含 `loc=('a',)` 明细）且**工具未执行**。
6. 工具内 `raise ModelRetry` → 回填 `RetryPromptPart`（内容为提示文本），模型改正后可继续。
7. `FunctionToolset` 经 `Agent(toolsets=[...])` 注册的工具对模型可见、可调用。
8. `Agent(retries=...)` 生效：`0` → 1 次请求即超限，`2` → 3 次请求后超限。
9. 单工具 `@agent.tool(retries=0)` 覆盖 `Agent(retries=5)`。

手动自测：能运行 `python part_2/ch_2_3.py` 打印 7 组演示；能白板画出 §4.4 的执行流程图与三类失败的分支；能口述 `retries` 的解析链。

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_3.py -q
```

## 10. 常见坑与排错

| 现象 | 原因 | 修复 |
|------|------|------|
| 工具从未被调用 | `description` 太模糊；工具名与调用名不符 | 写清 description；确认模型用的名字与 `tool_def.name` 一致 |
| `TypeError: add() missing ...` | 函数首参忘了 `RunContext` 却用了 `@agent.tool` | 需要上下文用 `@agent.tool`（首参 `RunContext`），纯函数用 `@agent.tool_plain` |
| `RunContext` 出现在参数里 / 参数对不上 | 用 `tool_plain` 却写了 `RunContext` 参数 | 二者必须匹配：有 ctx 用 `tool`，无 ctx 用 `tool_plain` |
| 模型始终填错参数 | 参数缺少 docstring 描述、类型注解缺失 | 补 `Args:` 描述与注解；必要时 `require_parameter_descriptions=True` |
| 一次工具抖动就崩掉整个 run | 工具里抛了普通异常 | 业务错误改 `raise ModelRetry(...)`；终态错误用 `ToolFailed` |
| 重试停不下来（烧 token） | 没设重试上限 / 工具永远 `ModelRetry` | 显式设 `Agent(retries=…)` 或 `@tool(retries=n)`；确保重试路径能收敛 |
| `UnexpectedModelBehavior: ... max retries count of N` | 重试预算耗尽 | 提高预算，或修正工具使其不再反复失败 |
| 多个 toolset 工具重名 | 两个 toolset 用了同一工具名 | 改名或用 `PrefixedToolset` / `toolset.prefixed(prefix)` |
| 并行工具互相干扰 | 有竞争的工具没设屏障 | 给该工具设 `sequential=True` |
| 工具结果太大撑爆上下文 | 返回了巨大列表/文本 | 限制返回条数与长度，或做分页 |

**labs 专属排错**：`test_schema_validation_failure_...` 失败 → 检查第一次 `model_call` 的 `a` 是否仍传了非整数；`test_agent_retries_budget_...` 失败 → 检查 `ScriptedModel` 的 `responses` 是否够长（脚本用尽会抛 `AssertionError`，而非 `UnexpectedModelBehavior`）；`tool_takes_context` 读取的是 agent 内部 `_function_toolset`，若换成真实模型运行时工具由其他 toolset 提供，需改从对应 toolset 取。

## 11. 面试延伸

**Q1. 工具的参数 schema 是怎么来的？`RunContext` 为什么不在里面？**
要点：由 `FunctionSchema` 从函数签名 + 类型注解 + docstring 生成——注解映射类型、docstring 提供描述、默认值决定是否 required。`RunContext` 是框架在执行层注入的运行时上下文，不属于模型要填的业务参数，因此被从 schema 中剔除（`Tool.takes_ctx` 承载这一判断）。

**Q2. `ModelRetry` 和直接抛异常有什么区别？各自适合什么场景？**
要点：`ModelRetry` 是控制流信号——框架回填 `RetryPromptPart` 让模型改正后重试，且消耗（有限）重试预算；直接抛异常会中断整个 run。业务规则不满足、参数可修正用 `ModelRetry`；程序性错误、不可恢复故障才直接抛。另有 `ToolFailed` 表示「对模型可见的终态失败」，不消耗重试预算。

**Q3. 参数校验失败时，工具会被执行吗？框架做了什么？**
要点：不会执行。框架在 `validate_tool_call` 阶段用 Pydantic 按 `parameters_json_schema` 校验，失败即把 `ValidationError` 转成 `RetryPromptPart`（含结构化错误明细）回填历史，模型下一轮看到明细后可修正参数。这与工具内部 `ModelRetry` 是两条不同路径，但都回填 `RetryPromptPart`。

**Q4. `retries` 的优先级是怎样的？默认是多少？**
要点：`tool.max_retries` → `toolset.max_retries` → agent 级（`ctx.max_retries`，默认 1）逐级回落。`Agent(retries=int)` 同时设 `tools` 与 `output`；`AgentRetries` 字典可只覆盖其一；`run(retries=…)` 可临时覆盖。耗尽后抛 `UnexpectedModelBehavior`。

**Q5. 什么时候用 `FunctionToolset` 而不是 `@agent.tool`？**
要点：当一组工具需要跨 agent 复用、按业务域拆分、或作为可分发插件时用 `FunctionToolset`（可设 `max_retries`/`timeout`/`metadata` 作为默认值），经 `toolsets=` 注册或组合；零散的、agent 专属的工具用 `@agent.tool` 更直接。二者装饰器与默认值语义一致，`Tool` 对象则提供了构造期的函数式注册入口。

## 12. 延伸阅读

- [05 · 工具 / Toolset / Capability](../../code_wiki/05-tools-toolsets-capabilities.md)：`Tool` / `ToolDefinition` / `FunctionToolset` / Toolset 包装器 / Capability 的完整机制与本篇本章的源码依据。
- [04 · 消息协议与输出](../../code_wiki/04-messages-and-output.md)：`ToolCallPart` / `ToolReturnPart` / `RetryPromptPart` 的确切字段与序列化。
- [02 · 核心 Agent 循环](../../code_wiki/02-core-agent-loop.md)：`CallToolsNode` → `ToolManager` → `process_tool_calls` 的函数级细节、`end_strategy` 与重试账本。
- 上一章：2.2 消息协议与历史；下一章：2.4 输出模式（text / tool / native / prompted / image 与校验器、重试）——`models/AGENTS.md` 的两条核心约束「按是什么而非叫什么识别工具」与「本地工具与 provider 原生工具概念分离」将在那里展开。
