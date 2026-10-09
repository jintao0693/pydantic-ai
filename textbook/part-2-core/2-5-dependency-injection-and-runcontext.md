# 2.5 依赖注入与 RunContext：状态、上下文、取消、事件

> 配套 labs：[`labs/part_2/ch_2_5.py`](../labs/part_2/ch_2_5.py) · 验收测试：[`labs/part_2/test_ch_2_5.py`](../labs/part_2/test_ch_2_5.py)

---

## 1. 本课目标

学完本章，你应当能够：

1. 说清「为什么智能体需要依赖注入（DI）」：工具、指令、动态设置都要访问数据库、配置与用户身份，而这些**不该**靠模块级全局变量传递。
2. 区分 `deps_type=` 与运行期 `deps=` 两个概念：前者是**静态类型参数化**，后者是**运行期注入**，二者职责不同、缺一不可。
3. 熟练使用 `RunContext` 的常见字段：`ctx.deps`、`ctx.usage`、`ctx.model`、`ctx.run_step`、`ctx.metadata`、`ctx.model_settings`，并知道它们各自是什么、什么时候可用。
4. 按「首参约定」在 `@agent.tool` 里用 `RunContext[Deps]` 取依赖，在 `@agent.instructions` 里按 `ctx.deps.user_name` 生成个性化指令，并让 `model_settings: Callable[[RunContext], ModelSettings]` 依赖驱动配置。
5. 读懂 `result.usage`（`RunUsage`：`requests` / `input_tokens` / `output_tokens` / `total_tokens`）与 `ctx.usage` 的关系，建立成本直觉。
6. 知道 `ctx.emit` / `ctx.cancel` / `ctx.enqueue` 的存在与用途（本章只建立心智模型，不深挖实现）。
7. 识别 deps 相关的反模式：把 deps 当全局状态、在工具里修改 deps 造成隐藏耦合、在工具里嵌套 `run`。

---

## 2. 前置知识

- 第 1.3 章 Pydantic v2 精要：`BaseModel` / `dataclass`、类型注解。deps 通常就是其中一种。
- 第 2.3 章 工具与 Toolset：`@agent.tool` / `@agent.tool_plain`、工具的参数校验、`RunContext` 作为首参。
- 第 2.4 章 输出模式：`output_type` 与结构化输出（本章示例用最简单的 `str`）。
- Python 泛型与 `dataclass`：`RunContext[Deps]` 中的 `Deps` 是一个类型参数。
- 函数式概念：把「依赖」作为参数传进去（而不是读取全局），即控制反转（IoC）思想。

---

## 3. 为什么需要它

### 3.1 一个真实的困境

假设你要写一个客服 Agent，它有一个工具用来查订单：

```python
# 反例：依赖藏在全局变量里
CURRENT_USER = None          # 谁在请求？取决于上一次 set...
DB_CONN = None               # 数据库连接：什么时候建？什么时候关？

def lookup_order(order_id: str) -> str:
    return DB_CONN.query("...", order_id, CURRENT_USER)
```

这段代码在「本地跑一个 demo」时毫无问题，但一上生产就暴露四个致命问题：

| 问题 | 后果 |
|------|------|
| **并发不安全** | Web 服务同时处理多个用户请求，`CURRENT_USER` 会被互相覆盖，A 用户查到 B 用户的订单 |
| **不可测试** | 单测要跑工具，必须先改全局变量、连真库；无法注入一个假的 `DB` |
| **生命周期失控** | 连接池该在「一次请求」的边界内创建与释放，全局变量让你没有边界 |
| **类型不安全** | `DB_CONN` 是 `Any`，`DB_CONN.query` 拼错字段，只有运行时才炸 |

这就是依赖注入要解决的核心矛盾：**智能体的每个工具都需要「当前这次运行的外部世界」，但这份「外部世界」不能藏在模块级状态里。**

### 3.2 依赖注入的本质

依赖注入（Dependency Injection，DI）不是某个框架、也不是某个容器，而是一句话：

> **组件不自己去创建或查找依赖，而是由外部把依赖「递」进来。**

在 Pydantic AI 里，这份「递进来」的依赖就是**每次 `run` 时传入的 `deps`**，它在整个运行期间都可以通过 `RunContext` 取到。于是：

```text
调用方 ──run(deps=Deps(...))──► Agent ──► 工具/指令/动态设置
                                            │
                                            └─ 通过 ctx.deps 读取依赖
```

对比一下两种写法：

```python
# 反例：工具去「找」依赖（全局查找）
def lookup_order(order_id: str) -> str:
    return DB_CONN.query(order_id, CURRENT_USER)

# 正例：依赖由运行期「注入」，工具只负责用
@agent.tool
def lookup_order(ctx: RunContext[Deps], order_id: str) -> str:
    return ctx.deps.db.query(order_id, ctx.deps.user_name)
```

差别看起来只是「常量从哪儿来」，但含义完全不同：正例里，`Deps` 是**每次运行的一份快照**，天然并发安全；测试时换成假的 `Deps` 即可离线复现；类型检查器能验证 `db` / `user_name` 是否存在。

> 记住这一句：**`deps` 是「运行期的依赖包」，`RunContext` 是「运行期的工具箱」。** 工具通过 `ctx.deps` 拿依赖，通过 `ctx.usage` / `ctx.run_step` / `ctx.metadata` 拿运行时信息。

### 3.3 它不是 DI 容器

Pydantic AI 刻意没有引入 Spring 那样的 DI 容器（注册表 + 自动装配）。它只做一件事：**把一个已经构造好的对象，随每次运行传递到需要它的地方，并给你完整的类型信息。** 谁构造 `Deps`、什么时候构造、构造什么，完全由你（调用方）决定——这反而是最灵活、最易测试的形态。

---

## 4. 核心概念

### 4.1 两个动作：`deps_type=`（静态）与 `deps=`（动态）

这是初学者最容易混淆的一点。请看这张对照表：

| | `Agent(..., deps_type=Deps)` | `agent.run(..., deps=Deps(...))` |
|---|---|---|
| 发生时机 | **构造期**（只声明一次） | **运行期**（每次调用） |
| 传的是什么 | 一个**类型**（`type[Deps]`） | 一个**实例** |
| 作用 | 静态类型参数化：让 `RunContext[Deps]`、工具签名、`agent.deps_type` 有类型 | 实际的依赖值，注入到本次运行 |
| 默认值 | `object` | 需要时必填（否则 `ctx.deps` 是 `None` 之类） |
| 是否创建对象 | **否** | 是（由你创建） |

一句话：**`deps_type` 是「给我类型」，`deps` 是「给我值」。**

`deps_type` 只参与类型检查，不做运行期校验。如果你传的 `deps=` 实例与 `deps_type` 不符，Pydantic AI **不会**在运行时拦截，而是在工具真正访问不存在的属性时抛 `AttributeError`——所以「类型对齐」要靠静态检查和纪律。

### 4.2 `RunContext`：运行期的工具箱

`RunContext[DepsT]` 是框架在运行期间传给工具、指令、动态设置、输出校验器等处的「上下文对象」。它的字段可以粗分为四类：

```text
RunContext[Deps]
├── 依赖与身份
│   ├── deps                 # 你注入的依赖（RunContext[Deps].deps -> Deps）
│   ├── agent                # 运行它的 Agent
│   └── prompt               # 本次的原始用户输入
├── 运行时信息
│   ├── usage                # RunUsage：本运行的用量（与 result.usage 同一对象）
│   ├── usage_limits         # UsageLimits：请求数 / token / 成本上限
│   ├── model                # 当前模型对象；model_id 属性
│   ├── run_step             # 当前步（每发起一次模型请求 +1）
│   ├── run_id / conversation_id
│   └── metadata             # 运行元数据（agent 级 + run 级合并）
├── 当前工具调用（仅工具内有效）
│   ├── tool_name / tool_call_id
│   ├── retry / max_retries
│   └── tool_call_approved   # 人工审批是否已通过
└── 模型设置与消息
    ├── model_settings       # 本步解析后的 ModelSettings（工具内可用）
    └── messages             # 会话历史（改写它会改写运行历史，慎用）
```

> `ctx.model_settings` 在**工具执行期间**是已解析的 `ModelSettings`；而在工具 Hook / 输出校验器 / 构造期为 `None`。本章示例会在工具与 `FunctionModel` 里观察它。

### 4.3 心智模型：一次运行 = 一次依赖作用域

```text
run #1: deps = Deps(user_name="Alice", db={...Alice 的数据})   ┐
run #2: deps = Deps(user_name="Bob",   db={...Bob   的数据})   ├─ 各自独立
run #3: deps = Deps(user_name="Carol", db={...})               ┘
```

每个 `run` 的 `deps` 互不影响，可并发执行；同一个 `Agent` 对象可以服务任意多个用户。这正是「无状态 Agent + 每次运行注入状态」带来的可扩展性。

### 4.4 三个「运行期动作」：`emit` / `enqueue` / `cancel`

除了「读」，`RunContext` 还提供了三个改变运行走向的方法（本章只需认识，不必展开）：

| 方法 | 签名要点 | 用途 |
|------|----------|------|
| `await ctx.emit(event)` | **异步**，投递自定义事件 | 把应用自定义事件（`CustomEvent`）推入运行事件流，配合 `run_stream_events` / `event_stream_handler` 消费，常用于向前端推送进度 |
| `ctx.enqueue(*content, priority=...)` | 同步，返回 `enqueue_id` | 向正在进行的运行**注入新的用户消息**（如运行中追加指令），`priority` 取 `'asap'` / `'when_idle'` |
| `ctx.cancel()` | 同步，终态 | 请求取消当前运行（在下一个 `await` 处投递），捕获 `RunCancelled` 读取已有历史 |

> `emit` 必须是 `await`——它面向 async 工具与 Hook；无事件流的合成上下文调用它会报错。取消与事件流的完整语义会在篇三（流式与事件、并发与取消）展开。

---

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 [`labs/part_2/ch_2_5.py`](../labs/part_2/ch_2_5.py) **完全一致**。它用 `FunctionModel` 离线驱动（无需任何 API Key），把 DI 的四个落点——工具、指令、动态设置、`override`——串成一条可验证的链路。

```python
"""第 2.5 章 · 依赖注入与 RunContext —— 离线可运行示例。

本模块把「依赖注入（DI）」这条主线摊开来讲：工具、指令、动态模型设置都需要访问
外部资源（数据库、配置、当前用户身份），但**不该**用全局变量把状态藏起来。Pydantic AI
的答案是：

    构造期声明 deps_type=Deps  ──►  给 Agent / RunContext / 工具做静态类型参数化
    运行期传入 deps=<实例>       ──►  把依赖真正注入到每次运行

随后所有个性化逻辑都通过 `RunContext[Deps]` 的 `ctx.deps` 读取依赖：

    - 工具（@agent.tool，首参约定 RunContext）
    - 指令（@agent.instructions，按用户身份生成个性化指令）
    - 动态模型设置（model_settings=Callable[[RunContext], ModelSettings]）

运行结束后用 `result.usage` 查看本次运行的 token / 请求计数。

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_5.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import (
    Agent,
    ModelMessage,
    ModelResponse,
    RunContext,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

# ---------------------------------------------------------------------------
# 0. 依赖对象：一次运行的「外部世界」快照
# ---------------------------------------------------------------------------
@dataclass
class Deps:
    """运行期依赖。

    它是**每次 run 一份**的普通对象，由调用方注入。工具、指令、动态设置都只**读取**它，
    绝不在工具里原地修改——否则就退化成「藏在参数里的全局状态」。

    - user_name：当前请求的用户身份（用于个性化指令）。
    - db：一个玩具用户库（真实系统里是数据库连接池 / Repository / 缓存客户端）。
    """

    user_name: str
    db: dict[str, str]


# ---------------------------------------------------------------------------
# 1. 观测探针：记录 FunctionModel 每次被调用时看到的 AgentInfo
# ---------------------------------------------------------------------------
@dataclass
class ModelProbe:
    """离线观测工具。

    `FunctionModel` 的模型函数会收到 `AgentInfo`，其中就包含本次请求真正生效的
    `instructions`（说明指令函数已求值）与 `model_settings`（说明动态设置已生效）。
    把这两者记下来，就能在 main 与 pytest 里**断言注入确实到达了模型**。
    """

    instructions: list[str | None] = field(default_factory=list)
    model_settings: list[ModelSettings | None] = field(default_factory=list)
    tool_returns: list[str] = field(default_factory=list)
    requests: int = 0


def _last_tool_return(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条工具返回，用来验证工具确实读到了注入的 deps。"""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart):
                return str(part.content)
    raise AssertionError("消息历史里没有 ToolReturnPart —— 工具没有被调用")


# ---------------------------------------------------------------------------
# 2. 动态模型设置：只读地使用 deps，决定采样温度
# ---------------------------------------------------------------------------
def settings_from_deps(ctx: RunContext[Deps]) -> ModelSettings:
    """`model_settings` 可以是一个以 RunContext 为入参的可调用对象。

    这里演示「依赖驱动配置」：有账户数据时用 0 温度保证可复现，否则放宽到 0.7。
    注意：这只是**读** deps，没有副作用。
    """
    return ModelSettings(temperature=0.0 if ctx.deps.db else 0.7)


# ---------------------------------------------------------------------------
# 3. 组装 Agent：deps_type 做静态类型参数化
# ---------------------------------------------------------------------------
def build_agent(probe: ModelProbe | None = None) -> Agent[Deps, str]:
    """构造一个注入了依赖契约的 Agent。

    - `deps_type=Deps`：只做**静态类型参数化**，让 `RunContext[Deps]`、工具的
      `ctx.deps`、动态设置都获得类型安全（IDE 能补全 `ctx.deps.user_name`）。
      它**不会**创建 Deps 实例——真正的值在 `run_sync(..., deps=...)` 时注入。
    - `output_type=str`：本轮输出是纯文本。
    - `model_settings=settings_from_deps`：每步按 RunContext 动态求值。
    """
    probe = probe if probe is not None else ModelProbe()

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """一个确定性的假模型：第一轮调工具，第二轮回显工具结果。"""
        probe.requests += 1
        probe.instructions.append(info.instructions)
        probe.model_settings.append(info.model_settings)

        if len(messages) == 1:
            # 第一轮：确认工具已注册，然后发起一次工具调用。
            assert any(t.name == "lookup_balance" for t in info.function_tools), "lookup_balance 未注册"
            return ModelResponse(parts=[ToolCallPart("lookup_balance", {"account": "alice"})])

        # 第二轮：把工具返回原样作为最终输出，便于断言「工具拿到了注入的 deps」。
        balance = _last_tool_return(messages)
        probe.tool_returns.append(balance)
        return ModelResponse(parts=[TextPart(balance)])

    agent: Agent[Deps, str] = Agent(
        FunctionModel(model_fn),
        deps_type=Deps,
        output_type=str,
        model_settings=settings_from_deps,
    )

    # 工具：首参约定为 RunContext[Deps]，通过 ctx.deps 读取注入的依赖。
    @agent.tool
    def lookup_balance(ctx: RunContext[Deps], account: str) -> str:
        """查询某个账户的余额。数据来自注入的 ctx.deps.db。"""
        return ctx.deps.db.get(account, "<未知账户>")

    # 指令：同样通过 RunContext 拿 deps，生成个性化指令（每次运行前重算）。
    @agent.instructions
    def personalize(ctx: RunContext[Deps]) -> str:
        return f"你在为 {ctx.deps.user_name} 服务，请用第二人称称呼对方。"

    return agent


# 默认依赖：一个 Alice 用户与她的玩具数据库。
EXAMPLE_DEPS = Deps(user_name="Alice", db={"alice": "1200", "bob": "80"})


def run_once(deps: Deps, probe: ModelProbe | None = None):
    """便捷入口：构造 Agent、注入 deps、跑一次，返回 (probe, result)。"""
    probe = probe if probe is not None else ModelProbe()
    agent = build_agent(probe)
    result = agent.run_sync("帮我查一下 alice 的余额", deps=deps)
    return probe, result


# ---------------------------------------------------------------------------
# 4. 演示
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 68)
    print("1) 注入 deps：工具通过 RunContext[Deps] 读到 ctx.deps.db")
    print("=" * 68)
    probe = ModelProbe()
    agent = build_agent(probe)
    result = agent.run_sync("帮我查一下 alice 的余额", deps=EXAMPLE_DEPS)

    print(f"最终输出（= 工具返回的余额）：{result.output!r}")
    print(f"探针记录的工具返回值：{probe.tool_returns}")

    print()
    print("=" * 68)
    print("2) 个性化指令：指令函数读取 ctx.deps.user_name")
    print("=" * 68)
    print(f"模型看到的 instructions（第 1 次请求）：{probe.instructions[0]!r}")

    print()
    print("=" * 68)
    print("3) 动态模型设置：model_settings=Callable[[RunContext], ModelSettings]")
    print("=" * 68)
    # 注意：ModelSettings 是 TypedDict，实例就是普通 dict，用下标取值。
    for i, settings in enumerate(probe.model_settings, start=1):
        print(f"第 {i} 次请求 temperature = {settings.get('temperature') if settings else None}")

    print()
    print("=" * 68)
    print("4) 运行用量：result.usage（属性，不是方法）")
    print("=" * 68)
    usage = result.usage
    print(f"requests      = {usage.requests}")
    print(f"input_tokens  = {usage.input_tokens}")
    print(f"output_tokens = {usage.output_tokens}")
    print(f"total_tokens  = {usage.total_tokens}")

    print()
    print("=" * 68)
    print("5) agent.override(deps=...)：临时替换依赖，无需改动工具签名")
    print("=" * 68)
    bob = Deps(user_name="Bob", db={"alice": "9999"})
    with agent.override(deps=bob):
        overridden = agent.run_sync("再查一次 alice 的余额")  # 不传 deps，用覆盖值
    print(f"override 后的输出：{overridden.output!r}")
    print(f"工具这次读到的是 Bob 的 db：{probe.tool_returns[-1]!r}")

    print()
    print("=" * 68)
    print("6) 反模式提醒（详见章节正文）")
    print("=" * 68)
    print("- deps 是「每次运行一份」的对象，不要在工具里原地改它 → 会变成隐藏耦合。")
    print("- 不要在工具里调用 agent.run_sync(...) → 会嵌套运行 / 死锁。")
    print("- deps_type 只做静态类型；运行期真正的值是 deps=... 注入的。")


if __name__ == "__main__":
    main()
```

运行后你会看到（节选）：

```text
1) 注入 deps：工具通过 RunContext[Deps] 读到 ctx.deps.db
最终输出（= 工具返回的余额）：'1200'
探针记录的工具返回值：['1200']

2) 个性化指令：指令函数读取 ctx.deps.user_name
模型看到的 instructions（第 1 次请求）：'你在为 Alice 服务，请用第二人称称呼对方。'

4) 运行用量：result.usage（属性，不是方法）
requests      = 2
input_tokens  = 107
output_tokens = 11
total_tokens  = 118
```

### 5.1 逐段讲解

**① `Deps`：一次运行的依赖包。** 它是个普通 `dataclass`，字段就是工具真正需要的东西（`user_name`、`db`）。你可以用 `dataclass`、`BaseModel` 或任意类型，只要能表达「这次运行的外部世界」。注意它**只描述形状**，不创建任何连接。

**② `deps_type=Deps`：构造期的类型参数化。** 这一行让 `Agent` 变为 `Agent[Deps, str]`：工具签名里的 `RunContext[Deps]`、动态设置里的 `ctx.deps` 全都获得了类型，IDE 能补全 `ctx.deps.db`。它**不创建** `Deps`，只告诉静态检查器「依赖长这样」。

**③ 工具首参约定 `ctx: RunContext[Deps]`。** `@agent.tool` 注册的函数，**第一个参数**必须是 `RunContext`（哪怕你不读它）。`lookup_balance` 用 `ctx.deps.db.get(account, ...)` 读取注入的依赖——注意它没有任何全局变量，纯函数式地使用上下文。若工具不需要上下文，用 `@agent.tool_plain`。

**④ 指令 `@agent.instructions`。** 指令函数同样可以接收 `RunContext[Deps]`。它返回的字符串会被折入本次请求的指令中，模型每次都能看到「你在为 Alice 服务」。指令是按运行求值的（不是构造期固定），所以换一份 `deps` 就会生成不同的指令。

**⑤ 动态模型设置 `model_settings=Callable[[RunContext], ModelSettings]`。** `model_settings` 既可以是一个静态的 `ModelSettings`，也可以是「以 `RunContext` 为入参、每步求值」的可调用对象。示例里 `settings_from_deps` 读 `ctx.deps.db` 决定温度——这就是「依赖驱动配置」。

**⑥ `result.usage`：读懂本次运行的成本。** 它是 `RunUsage`，包含 `requests`（模型请求次数）、`input_tokens`、`output_tokens`，以及属性 `total_tokens = input_tokens + output_tokens`。本章示例跑了两次模型请求（一次工具调用 + 一次收尾），所以 `requests == 2`。

**⑦ `agent.override(deps=...)`：临时替换依赖。** 在 `with agent.override(deps=bob):` 块内调用 `run`，无需在调用点传 `deps`——覆盖值会被注入。这是做测试（注入假依赖）和做灰度（注入特殊配置）的利器。

---

## 6. 深入剖析

### 6.1 `Agent` 构造签名中的 DI 相关参数

摘自 `Agent.__init__`：

```python
def __init__(
    self,
    model=None,
    *,
    output_type=str,
    instructions=None,
    system_prompt=(),
    deps_type: type[AgentDepsT] = object,          # ← 静态类型参数化
    name=None,
    description=None,
    model_settings=None,                            # 静态 或 Callable[[RunContext], ModelSettings]
    retries=None,
    validation_context=None,                        # 任意值 或 Callable[[RunContext], Any]
    tools=(),
    toolsets=None,
    ...
) -> None: ...
```

要点：

- `deps_type` 的默认值是 `object`。不指定时 `Agent` 等价于 `Agent[object, ...]`，工具里 `ctx.deps` 是 `object`——能用但没类型信息。
- `model_settings` / `instructions` / `metadata` / `validation_context` 都可以是**函数形式**，入参常是 `RunContext`，因此都能「看见 deps」。
- `output_type` 默认 `str`，与 deps 无关。

### 6.2 `RunContext` 关键字段速查

| 字段 | 类型 | 在工具里可用？ | 说明 |
|------|------|:---:|------|
| `deps` | `DepsT` | ✅ | 注入的依赖值 |
| `usage` | `RunUsage` | ✅ | 本运行用量；**与 `result.usage` 是同一对象**，运行中持续累加 |
| `usage_limits` | `UsageLimits \| None` | ✅ | 运行限制（运行中恒非空，默认 `request_limit=50`） |
| `model` | `AbstractModel` | ✅ | 当前模型对象；`model_id` 为只读属性 |
| `model_settings` | `ModelSettings \| None` | ✅（本步解析后） | 本步已解析的模型设置；Hook / 输出校验器 / 构造期为 `None` |
| `run_step` | `int` | ✅ | 当前步；每发起一次模型请求递增（首次请求时为 1） |
| `metadata` | `dict \| None` | ✅ | 运行元数据（agent 级 + run 级合并） |
| `run_id` / `conversation_id` | `str \| None` | ✅ | 运行 / 对话 ID |
| `prompt` | `Any \| None` | ✅ | 本次原始用户输入 |
| `messages` | `list[ModelMessage]` | ✅ | 会话历史；**就地改写会改写运行历史**，慎用 |
| `agent` | `AbstractAgent \| None` | ✅ | 运行它的 Agent |
| `tool_name` / `tool_call_id` | `str \| None` | ✅（仅工具） | 当前工具调用 |
| `retry` / `max_retries` | `int` | ✅（仅工具） | 已用重试数 / 上限 |
| `tool_call_approved` | `bool` | ✅（仅工具） | 人工审批是否已通过 |

> `model_settings` 是一个 `TypedDict`，**实例就是普通 `dict`**。所以在代码里应写 `settings["temperature"]` 或 `settings.get("temperature")`，而不是 `settings.temperature`——这是本章 labs 踩过的第一个坑。

### 6.3 `ctx.usage` 与「全局 usage」的关系

- `RunContext.usage` 与 `AgentRunResult.usage` 指向**同一个 `RunUsage` 对象**（框架按引用共享给每次 `RunContext`）。因此工具里看到的是「截至此刻」的用量；运行结束后 `result.usage` 是最终值。
- 一次运行的 `RunUsage.requests` 等于模型请求次数，不是工具调用次数。
- 续接对话时可以传入起始用量：`agent.run(..., usage=previous_usage)` 或直接传 `conversation`，让成本跨轮累计。
- 想设上限用 `agent.run(..., usage_limits=UsageLimits(request_limit=..., cost_limit=...))`；`ctx.usage_limits` 就是它。超限抛 `UsageLimitExceeded`。

### 6.4 三个方法签名（建立心智模型即可）

```python
async def emit(self, event: CustomEvent | CapabilityEvent) -> None: ...
def enqueue(self, *content: UserContent, priority: Literal['asap', 'when_idle'] = 'asap') -> str | None: ...
def cancel(self) -> None: ...
```

- `emit` 是唯一 `async` 的方法——它面向 async 工具与 Hook，把事件推入运行事件流。
- `enqueue` / `cancel` 是同步的；`cancel` 是终态，运行会抛出可捕获的 `RunCancelled`（携带已有历史快照）。
- 这三个方法改的是「运行走向」，与「读依赖」是两条正交的轴。篇三会分别展开。

### 6.5 为什么 `deps` 不做运行期校验？

框架故意不校验 `deps` 是否符合 `deps_type`：因为 `deps_type` 纯粹是给类型检查器的提示，运行期不该引入额外开销或强制继承关系。代价是——**类型对齐靠纪律**。实践中：

- 用 `mypy` / `pyright` 跑类型检查，让 `deps_type` 与工具签名一起被验证；
- 把 `Deps` 定义成一个明确的类型（`dataclass` / `BaseModel`），避免 `dict` / `Any`。

---

## 7. 常见变体与工程实践

### 7.1 用 Pydantic 模型当 deps

`Deps` 不必是 `dataclass`。用 `BaseModel` 可以获得校验与序列化能力：

```python
from pydantic import BaseModel

class Deps(BaseModel):
    user_name: str
    db: dict[str, str]

agent = Agent('openai:gpt-5.2', deps_type=Deps)
```

### 7.2 把「连接池 / 客户端」放进 deps

真实系统里，`deps` 常装的是长生命周期的资源句柄：

```python
@dataclass
class Deps:
    user_id: str
    db: AsyncSession           # SQLAlchemy 会话
    http: httpx.AsyncClient    # 外部 API 客户端
    config: AppConfig          # 应用配置
```

一次请求创建一个 `Deps`，在请求边界内共享连接、在请求结束时释放——这正是「无状态 Agent + 每次注入」的价值。

### 7.3 用 `override` 注入测试替身

单元测试常把真依赖换成替身：

```python
fake = Deps(user_name="tester", db={"alice": "0"})
with agent.override(deps=fake):
    result = agent.run_sync("查余额")
assert result.output == "0"
```

`override` 也支持 `model=` / `model_settings=` / `instructions=` / `tools=` 等，是测试的核心工具（详见第 4.1 章）。

### 7.4 用 `agent.iter` / 事件流观察 `ctx`

想在运行中读取 `RunContext`（例如打点、写审计日志），除了在工具里访问 `ctx`，还可以：

- `async with agent.iter(...) as run:` 手动驱动，读 `run.ctx` / `run.usage`；
- `agent.run_stream_events(...)` 消费类型化事件（`FunctionToolCallEvent` 等）。

### 7.5 deps 是不可变的「只读契约」

约定：**工具只读 `ctx.deps`，不写 `ctx.deps`。** 需要「工具之间传递状态」时，让工具**返回**值（走消息历史），或把状态放到外部存储并让 deps 持有它的句柄——而不是就地修改 deps。理由见下一节的排错清单。

### 7.6 `validation_context` 依赖驱动校验

`Agent(validation_context=...)` 也接受 `Callable[[RunContext], Any]`，因此可以让 Pydantic 的校验上下文依赖 deps（例如按用户权限放宽/收紧某个字段）。这是 DI 在「输出校验」上的延伸。

---

## 8. 练习（附答案要点）

**练习 1（基础）· 认识两个动作。**
下面的代码错在哪里？请改正，并说明 `deps_type` 与 `deps` 各自的职责。

```python
agent = Agent('test', deps_type=Deps(user_name="Alice", db={}))
result = agent.run_sync("hi", deps_type=Deps)
```

> 答案要点：`deps_type` 要的是**类型**不是实例，应写 `deps_type=Deps`；`deps` 要的是**实例**，应写 `deps=Deps(user_name="Alice", db={})`。`deps_type` 只在构造期做类型参数化，`deps` 在每次运行注入实际值。

**练习 2（基础）· 工具读取 deps。**
写一个 `@agent.tool` 工具 `current_region(ctx: RunContext[Deps])`，返回 `ctx.deps.region`，并说明为什么不能写成不带 `ctx` 的 `@agent.tool_plain`。

> 答案要点：`@agent.tool` 的首参约定是 `RunContext`，通过 `ctx.deps` 取依赖。若用 `@agent.tool_plain`，函数收不到 `RunContext`，也就拿不到 deps（除非用全局变量——正是要避免的）。

**练习 3（进阶）· 指令 + 动态设置。**
给一个 Agent 增加：① 一条按 `ctx.deps.user_name` 生成的中文指令；② 当 `ctx.deps.debug` 为真时把 `temperature` 设为 0，否则设为 0.7。用 `FunctionModel` 断言两处都生效。

> 答案要点：`@agent.instructions` 返回 f-string；`model_settings=lambda ctx: ModelSettings(temperature=0.0 if ctx.deps.debug else 0.7)`。在 `FunctionModel` 的模型函数里读 `info.instructions` 与 `info.model_settings`（后者是 `dict`，用 `.get("temperature")`）做断言。

**练习 4（进阶）· overide 与并发。**
用 `asyncio.gather` 并发跑两次 `agent.run`，分别注入 Alice 与 Bob 的 deps，证明两者的输出互不串扰。

> 答案要点：每次 `run` 一份 `deps`，天然隔离。用同一个 `AsyncClient`/`AsyncExitStack` 下 `await asyncio.gather(agent.run(..., deps=a), agent.run(..., deps=b))`，断言输出分别来自各自 db。若用全局变量则会出现串扰——这正是 DI 的价值。

**练习 5（挑战）· 用量与预算。**
给定 `UsageLimits(request_limit=1)`，跑一个需要「工具调用 + 收尾」两轮请求的任务，观察会抛什么异常，并把 `request_limit` 调到 2 后复现成功。

> 答案要点：超限抛 `UsageLimitExceeded`（`AgentRunError` 子类）。`request_limit` 按模型请求数计，不按工具调用数计；把工具与收尾合并到一轮，或放宽上限即可。可用 `agent.run(..., usage_limits=UsageLimits(request_limit=1))` 复现。

---

## 9. 验收标准

跑通配套测试即认为本章已掌握：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_5.py -q
```

测试覆盖以下断言（对应 [`labs/part_2/test_ch_2_5.py`](../labs/part_2/test_ch_2_5.py)）：

1. 工具通过 `RunContext[Deps]` 读到了注入的 `ctx.deps.db`（返回值正确）。
2. `agent.deps_type is Deps`（静态类型契约存在）。
3. 指令里包含注入的用户名（Alice）。
4. 换一份 `deps` 后指令按新用户名重算（非全局缓存）。
5. `result.usage.requests == 2`，`input_tokens > 0`，`total_tokens == input + output`。
6. 动态 `model_settings` 随 deps 变化：有数据 → `temperature == 0.0`；空库 → `0.7`。
7. `agent.override(deps=...)` 成功替换依赖。
8. 账户不存在时工具走兜底分支（`<未知账户>`）。

自查清单：

- [ ] 能向别人解释 `deps_type`（类型）与 `deps`（值）的区别。
- [ ] 能不看文档写出 `@agent.tool` 读取 `ctx.deps` 的最小代码。
- [ ] 知道 `ctx.usage` 与 `result.usage` 是同一对象，并说清 `requests` 的含义。
- [ ] 能说出 `ctx.emit` / `ctx.enqueue` / `ctx.cancel` 各自的作用（`emit` 是 async）。
- [ ] 能举出至少两个「deps 反模式」及其后果。

---

## 10. 常见坑与排错

| 症状 | 根因 | 处理 |
|------|------|------|
| 运行时报 `AttributeError: 'NoneType' object has no attribute ...` | 没传 `deps=`，或 `deps_type` 未声明 | 每次 `run` 传 `deps=`；构造期写 `deps_type=Deps` |
| 工具里 `ctx.deps` 是 `object`，没有类型 | 忘了 `deps_type` | 加 `deps_type=Deps`，让 `RunContext[Deps]` 有类型 |
| `settings.temperature` 报 `'dict' object has no attribute` | `ModelSettings` 是 `TypedDict`，运行时是 `dict` | 用 `settings["temperature"]` / `settings.get("temperature")` |
| 并发请求互相串数据 | 用了模块级全局变量存用户/连接 | 改用 deps，每次 `run` 注入一份 |
| 工具在 `def` 里改了 `ctx.deps` 的字段，别的工具看到脏值 | 就地修改 deps = 隐藏耦合 | 工具只读 deps；要传状态就 `return`，或持久化到外部存储 |
| 工具里调用 `agent.run_sync(...)` 卡住 | 在运行中嵌套同步运行 / 事件循环冲突 | 不要在工具里跑另一个 agent 的同步入口；需要子任务用工具组合或 `iter` |
| `ctx.model_settings` 在输出校验器里是 `None` | 该位置不是「本步工具执行」上下文 | 别在 Hook/校验器里依赖 `ctx.model_settings`；用 `info.model_settings`（模型函数）或显式传参 |
| `ctx.emit(...)` 报未 await / 报无事件流 | `emit` 是 async，且需要事件流 | `await ctx.emit(...)`；用 `run_stream_events` / `event_stream_handler` 提供事件流 |
| `UsageLimitExceeded` | 请求数 / token / 成本超 `usage_limits` | 放宽 `UsageLimits` 或减少轮次 |
| 改了 `ctx.messages` 导致历史异常 | `ctx.messages` 与运行历史共享 | 不要就地改写；需要改历史用 `message_history` / history processor |

---

## 11. 面试延伸

**Q1. 为什么智能体框架需要依赖注入？不用行不行？**
> 答案要点：工具/指令/动态设置都要访问 DB、配置、身份。用全局变量会带来并发串扰、不可测试、生命周期失控、类型丢失四大问题。DI 把「本次运行的外部世界」作为参数注入，天然并发隔离、可注入测试替身、边界清晰、类型完整。不用「不行」——小 demo 能跑，生产必炸。

**Q2. `deps_type` 和 `deps` 有什么区别？只写一个会怎样？**
> 答案要点：`deps_type`（构造期）传**类型**，只做静态类型参数化，让 `RunContext[Deps]` 有类型；`deps`（运行期）传**实例**，是真正的依赖值。只写 `deps_type` 不传 `deps`，`ctx.deps` 就没有值；只传 `deps` 不写 `deps_type`，则没有类型安全。二者互补。

**Q3. `ctx.usage` 和 `result.usage` 是一回事吗？成本怎么算？**
> 答案要点：是**同一 `RunUsage` 对象**（按引用共享）。运行中 `ctx.usage` 是「截至此刻」，结束后 `result.usage` 是最终值。`requests` 是模型请求次数（不是工具次数）；`input_tokens`/`output_tokens` 来自 provider；`total_tokens` 是二者之和。要设上限用 `UsageLimits`（`request_limit`/`cost_limit`/`*_tokens_limit`），超限抛 `UsageLimitExceeded`。

**Q4. 如何在工具里安全地向用户界面推送进度、或在运行中追加指令、或中止运行？**
> 答案要点：三条正交的路。进度用 `await ctx.emit(CustomEvent(...))`（async，配合事件流消费）；追加用户消息用 `ctx.enqueue(content, priority=...)`（`'asap'` 立即 / `'when_idle'` 空闲时）；中止用 `ctx.cancel()`（终态，抛可捕获的 `RunCancelled`，可读已有历史）。它们改的是运行走向，不是依赖。

**Q5. 为什么说「在工具里修改 deps」是反模式？**
> 答案要点：deps 语义上是「本次运行的只读依赖契约」。就地修改会导致：跨工具/跨步的隐藏耦合（谁改了、何时改的不明显）、并发工具下的竞态（多个工具并行读写同一对象）、难以复现与测试（依赖值被悄悄污染）。正确做法：工具返回值走消息历史，或把可变状态交给外部存储（deps 只持有其句柄）。

---

## 12. 延伸阅读

- 源码篇 [`code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md)：`RunContext` 的**全字段**清单与只读属性、`Agent.__init__` 的 `deps_type`、`build_run_context` 的构建不变量，以及 `ctx.emit` / `ctx.enqueue` / `ctx.cancel` 的源码位置。
- 源码篇 [`code_wiki/05-tools-toolsets-capabilities.md`](../../code_wiki/05-tools-toolsets-capabilities.md)：工具如何拿到并校验 `RunContext`、`ToolManager` 如何构造工具上下文、`@agent.tool` 与 `@agent.tool_plain` 的差异。
- 官方文档：`RunContext` 与依赖注入、`agent.override`、`UsageLimits` 与成本统计。
- 下一章 2.6《模型 / Provider / Profile》：把 `ctx.model` / `ctx.model_settings` 背后的模型选择与能力探测打通。
