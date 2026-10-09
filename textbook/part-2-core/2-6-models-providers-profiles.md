# 2.6 模型 / Provider / Profile：多模型切换与能力探测

> 配套 labs：[`labs/part_2/ch_2_6.py`](../labs/part_2/ch_2_6.py) · 验收测试：[`labs/part_2/test_ch_2_6.py`](../labs/part_2/test_ch_2_6.py)
>
> 本章全部示例**离线可跑、无需 API Key、不发网络请求**：模型用 `TestModel` / `FunctionModel`。

---

## 1. 本课目标

学完本章，你应当能够：

1. 说清 `'provider:model'` 字符串是怎么变成模型实例的：`parse_model_id` 如何切分、`infer_model` 如何分派，以及构造失败时分几种错误（无前缀 vs 缺 SDK vs 缺 Key）。
2. 用 `Model` 抽象的四个身份字段（`model_name` / `system` / `model_id` / `base_url`）描述「当前跑的是哪个模型」，并解释它们各自流向哪里（尤其是 `system` → OTel `gen_ai.system`）。
3. 解释 Provider 的三件事——**鉴权、base_url、客户端生命周期**——以及环境变量（`OPENAI_API_KEY` 等）在其中的角色。
4. 用 `ModelProfile` 读取模型的**家族固有事实**（是否支持工具 / native 结构化输出 / 思考 / 缓存），并说明为什么工程上**不要**散落 `if provider == ...`。
5. 说清 `ModelSettings` 的**两级合并**：`Agent(model_settings=...)` 是默认值，`run_sync(..., model_settings=...)` 覆盖它。
6. 用 `Agent.override(model=...)` 在不改业务代码的前提下切换模型（测试 / 灰度 / 临时降级）。
7. 用 `FallbackModel` 在**主模型失败时回退**到备选模型，并识别 `FallbackExceptionGroup`。
8. 会用 `defer_model_check` 解耦「构造 Agent」与「解析模型」，从而让单测 / CI 不依赖真实 SDK 与环境变量。
9. 建立一套**多模型选型准则**：成本、延迟、能力三者如何权衡。

---

## 2. 前置知识

- 第 2.1 章：`Agent`、`run` / `run_sync` / `stream` 的基本用法与 `output_type`。
- 第 2.4 章：输出模式（`text` / `tool` / `native` / `prompted`）——本章的 Profile 会直接决定它们的可用性。
- 第 4.1 章预习向：`TestModel` / `FunctionModel` 是离线测试替身；本章用它们演示「不花一分钱」的模型层行为。
- Python 类型：`TypedDict`（`ModelSettings` / `ModelProfile` 都是它）、`Literal`、`dict` 合并语法。
- 基本心智：**Provider 管「怎么连」、Model 管「怎么调」、Profile 管「这个模型能力如何」**——三件事别混。

---

## 3. 为什么需要它

只要项目从「demo」走向「可交付」，模型访问层就会冒出四类真实问题：

| 问题场景 | 如果不懂本章会怎样 |
|---|---|
| **换模型**：今天 GPT、明天 Claude、后天国产模型 | 把模型名写死在业务代码里，换一次改十处；`if model == 'gpt-4o'` 满天飞 |
| **可测性**：CI 里不能真的调 OpenAI | 单测连不上网、烧钱、偶发失败；无法在本地复现 |
| **稳定性**：主供应商限流 / 宕机 | Agent 直接抛错，无回退；线上发一次故障改一次代码 |
| **选型**：这个模型支不支持结构化输出 / 思考？ | 靠试错、靠查文档、靠猜；`prompted` 与 `native` 模式选错导致解析失败 |

Pydantic AI 把这四件事收进一层**薄而明确的抽象**：

- **模型字符串** `'provider:model'` —— 一处配置，全项目贯通；
- **`Model` 抽象** —— 业务代码只依赖这层，不依赖任何具体 SDK；
- **`Provider`** —— 把「鉴权 + base_url + 客户端」从模型逻辑里剥出来；
- **`ModelProfile`** —— 把模型家族的**能力事实**变成可查询的数据，而不是散落的 `if`；
- **`ModelSettings`** —— 把跨 provider 的请求参数统一成一份 TypedDict；
- **`FallbackModel` / `override`** —— 把「切换模型」变成框架能力，而不是业务分支。

> 一句话：**这一章教你把「用哪个模型」从一段段临时判断，变成一层可配置、可测试、可回退的工程结构。**

---

## 4. 核心概念

### 4.1 三层分工：字符串 → Provider → Model

```text
       'openai:gpt-4o'
             │  parse_model_id  ── 在第一个冒号处切分
             ▼
       ('openai', 'gpt-4o')
             │  infer_provider('openai')  ── 造出 Provider（管鉴权 / base_url / 客户端）
             ▼
        OpenAIProvider
             │  infer_model(...)  ── 造出 Model（管一次请求怎么发、怎么解析）
             ▼
        OpenAIChatModel ── 业务代码只依赖这一层的抽象接口
```

- **Provider**：回答「**怎么连**」——用哪个 `base_url`、注入哪个 API Key、复用一个什么样的 HTTP 客户端。
- **Model**：回答「**怎么调**」——把规范化的请求（消息、工具、输出模式）翻译成某家 API 的线上格式，再把响应反过来解析。
- **业务代码**：只依赖 `Model` 的抽象（`model_name` / `request(...)` / `profile`），不 import 任何具体 SDK。

### 4.2 字符串解析的直觉

`parse_model_id(model)` 的规则**极简**：在**第一个** `:` 处切一刀。

| 输入 | 输出 | 含义 |
|---|---|---|
| `'openai:gpt-4o'` | `('openai', 'gpt-4o')` | provider = `openai` |
| `'anthropic:claude-sonnet-4-5'` | `('anthropic', 'claude-sonnet-4-5')` | provider = `anthropic` |
| `'test'` | `(None, 'test')` | **无前缀**，由 `infer_model` 特判为测试模型 |

`infer_model(model)` 则按顺序分派（顺序敏感）：

1. 已经是 `Model` 实例 → 原样返回；
2. 字符串 `'test'` → `TestModel()`；
3. 无 provider 前缀 → 抛 `UserError`（并附拼写建议）；
4. 有前缀 → `infer_provider(provider)` 造 Provider，再按 provider 名选择具体 Model 类。

> 只有**第一个**冒号是分隔符，所以 `'openrouter:meta-llama/llama-3-70b'` 里的斜杠、`'anthropic:claude-sonnet-4-5'` 里的连字符都不会被误切。

### 4.3 `Model` 抽象的四个身份字段

| 字段 | 类型 | 说明 | 流向 |
|---|---|---|---|
| `model_name` | `str` | 供应商侧的模型名，如 `gpt-4o` | 日志、请求体 |
| `system` | `str` | 供应商名，如 `openai`、`test` | **OTel `gen_ai.system`**、历史回放 |
| `model_id` | `str` | 拼出来的 `f'{system}:{model_name}'` | 选择令牌、可观测性 |
| `base_url` | `str \| None` | 自定义端点（自建网关 / 代理），默认 `None` | 请求目标 |

实测（`infer_model('test')`）：

```text
type        = 'TestModel'
model_name  = 'test'
system      = 'test'
model_id    = 'test:test'
base_url    = None
```

> `system` 不只是给人看的字符串：它会被写进每条消息 part 的 `provider_name`，历史回放（thinking / native tool 的重放）会读取它——所以**改 provider 名等于改历史格式**，务必谨慎。

### 4.4 Provider 的三件事

`Provider` 是一层很薄的抽象，只做三件事：

| 职责 | 表现 |
|---|---|
| **鉴权** | 从环境变量（如 `OPENAI_API_KEY`）或构造参数取 API Key；缺失时给出指向 `Agent('test')` 的提示 |
| **base_url** | 决定请求打到哪（官方 / 自建 / 网关） |
| **客户端生命周期** | 持有并复用底层 SDK 客户端 / HTTP 客户端，走引用计数式 `__aenter__` / `__aexit__` |

常见环境变量约定（**仅列出最常见的**，具体以各 provider 文档为准）：

```text
OPENAI_API_KEY           # openai / 各 OpenAI 兼容 provider
ANTHROPIC_API_KEY        # anthropic
GEMINI_API_KEY           # google
GROQ_API_KEY             # groq
MISTRAL_API_KEY          # mistral
PYDANTIC_AI_GATEWAY_API_KEY   # pydantic gateway
```

拿到 key 后，你几乎不需要手写 Provider——`infer_model('openai:gpt-4o')` 会替你造好。只有当你要指到自建端点、复用已有客户端、或注入测试用的假客户端时，才需要显式构造 Provider。

> **注意**：`provider` 与 `base_url` 不是一回事。判断能力时看 **provider / 客户端类型**，不要因为 host 变了就以为能力变了（例如网关只是代理，能力仍由上游决定）。

### 4.5 `ModelProfile`：模型家族固有事实

`ModelProfile` 是一个 `TypedDict`，描述「这个模型**天生**支持什么」。常用字段：

| 字段 | 默认 | 工程含义 |
|---|---|---|
| `supports_tools` | `True` | 是否支持工具调用 |
| `supports_json_schema_output` | `False` | 是否支持 **native 结构化输出**（`NativeOutput`） |
| `default_structured_output_mode` | `'tool'` | `output_type` 未指定模式时的落定方式 |
| `supports_json_object_output` | `False` | 是否支持强制 JSON 对象（`PromptedOutput` 相关） |
| `supports_thinking` | `False` | 是否支持思考 / reasoning |
| `thinking_always_enabled` | `False` | 思考是否强开、不可关 |
| `supports_tool_return_schema` | `False` | 是否原生支持工具返回 schema（否则注入描述） |
| `supports_cache` | `False` | 请求侧缓存配置是否被打开 |
| `context_window` | `None` | 一次可处理的 input+output token 上限 |

实测差异（同一份 `output_type=str` 的代码）：

```text
function_model: {supports_tools: True, supports_json_schema_output: True,
                 default_structured_output_mode: 'tool', supports_thinking: False}
test_model:     {supports_tools: True, supports_json_schema_output: False,
                 default_structured_output_mode: 'tool', supports_thinking: False}
```

**为什么工程上不要散落 `if provider == ...`？**

- 能力是**模型家族**的属性，且会随版本变化。散落判断意味着：新增一个模型就要改 N 处代码。
- 框架已经把它做成**数据**：`model.profile['supports_json_schema_output']`。你应当读数据，而不是猜 provider。
- 当你确实要适配一个新模型时，正确做法是给该模型家族补一条 **profile**，而不是在每个调用点加 `if`。

### 4.6 `ModelSettings` 与两级合并

`ModelSettings` 是跨 provider 的请求参数 TypedDict，常用字段：

| 字段 | 说明 |
|---|---|
| `max_tokens` | 输出上限 |
| `temperature` / `top_p` / `top_k` | 采样 |
| `timeout` | 请求超时（秒） |
| `parallel_tool_calls` / `tool_choice` | 工具调用控制 |
| `thinking` | 思考档位（`bool` 或 `'minimal'/'low'/'medium'/'high'`） |
| `cache` | 缓存配置 |
| `service_tier` | 服务等级 |
| `extra_headers` / `extra_body` | 逃生舱（优先用强类型字段） |

合并规则一句话：**run 级覆盖 Agent 级，未覆盖的键保留**。

```text
Agent(model_settings={'max_tokens': 100, 'timeout': 20})   # 默认值
run_sync(model_settings={'temperature': 0.5})               # 本次覆盖
                    │
                    ▼  按 key 合并
        {'max_tokens': 100, 'timeout': 20, 'temperature': 0.5}
```

这在工程上非常关键：**默认设置放 Agent，临时调整放本次运行**。例如生产默认 `temperature=0`（稳定），某次头脑风暴式调用临时 `temperature=0.9`。

### 4.7 多模型切换的两种机制

| 机制 | 场景 | 行为 |
|---|---|---|
| **`Agent.override(model=...)`** | 测试、灰度、临时替换 | **上下文管理器**，仅在 `with` 块内生效；业务代码零改动 |
| **`FallbackModel`** | 生产稳定性：主模型失败回退 | 包装多个模型，按顺序尝试；默认只在 `ModelAPIError` 时回退 |

`FallbackModel(default, *fallbacks, fallback_on=(ModelAPIError,))`：

```text
主模型 request  ──失败(ModelAPIError)──▶  备选1 request  ──失败──▶  备选2 ...
                                              │全部失败
                                              ▼
                                    FallbackExceptionGroup（聚合各模型原始异常）
```

实测：主模型抛 `ModelAPIError` → 自动切到备选 → 返回备选结果；两次都抛 → 抛 `FallbackExceptionGroup`，其中含两个 `ModelAPIError`。

> `FallbackModel` 的 `model_name` / `system` / `model_id` 是**拼接**出来的（如 `fallback:primary,backup`），`context_window` 取候选中的最小值——这提醒你：回退对象的「身份」是一组模型，不是一个。

### 4.8 `defer_model_check`：解耦构造与解析

默认 `Agent('openai:gpt-4o')` 会在**构造期**就解析模型——这意味着构造时就要能 import `openai` SDK、能读到 Key。加上 `defer_model_check=True`，模型字符串被**惰性保留**，直到第一次运行才解析：

```python
agent = Agent('openai:gpt-4o', defer_model_check=True, output_type=str)
print(repr(agent.model))   # "'openai:gpt-4o'"  —— 仍是字符串，没有 import openai
```

价值：模块导入、单测收集、CI 静态检查都不再依赖真实 SDK 与凭据。

---

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 [`labs/part_2/ch_2_6.py`](../labs/part_2/ch_2_6.py) **完全一致**。它把本章 7 个知识点各做一次可运行、可断言的演示，全部离线。

```python
"""第 2.6 章 · 模型 / Provider / Profile —— 多模型切换与能力探测（离线版）。

本模块回答一个工程问题：**同一个 Agent，怎么在不同模型 / 供应商之间安全、可测地切换？**

主线（全部离线，不需要任何 API Key，也不发起网络请求）：

    字符串 'provider:model' → 模型实例
        parse_model_id / infer_model
    → 模型身份字段
        model_name / system / model_id / base_url
    → ModelSettings 的两级合并
        Agent(model_settings=...) 与 run_sync(..., model_settings=...)
    → 能力事实 ModelProfile
        supports_tools / supports_json_schema_output / default_structured_output_mode /
        supports_thinking ...
    → 多模型切换
        FallbackModel（主模型失败回退）+ FallbackExceptionGroup
        Agent.override(model=...)（测试/临时切换）
    → defer_model_check（构造期不解析模型，解耦 SDK / 环境变量）

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_6.py
"""

from __future__ import annotations

from pydantic_ai import Agent, FallbackExceptionGroup, ModelSettings
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model, infer_model, infer_model_profile, parse_model_id
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# 我们只挑 4 个「工程上最常用来做选型决策」的能力事实来展示。
PROFILE_KEYS: tuple[str, ...] = (
    "supports_tools",
    "supports_json_schema_output",
    "default_structured_output_mode",
    "supports_thinking",
)


# ---------------------------------------------------------------------------
# 1. 模型身份：Model 抽象的四个字段
# ---------------------------------------------------------------------------
def model_identity(model: Model) -> dict[str, str | None]:
    """抽取 `Model` 抽象层的身份字段，便于打印与断言。"""
    return {
        "type": type(model).__name__,
        "model_name": model.model_name,  # 供应商侧的模型名，如 'gpt-4o'
        "system": model.system,  # 供应商名，流入 OTel gen_ai.system
        "model_id": model.model_id,  # f'{system}:{model_name}'
        "base_url": model.base_url,  # 自定义端点，默认 None
    }


def infer_test_model_identity() -> dict[str, str | None]:
    """`'test'` 是最短的模型字符串；它会被解析成离线的 `TestModel`。"""
    return model_identity(infer_model("test"))


# ---------------------------------------------------------------------------
# 2. 字符串解析：'provider:model' 只在第一个冒号处切分
# ---------------------------------------------------------------------------
def parse_examples() -> dict[str, tuple[str | None, str]]:
    """`parse_model_id` 的直觉：无前缀 → (None, 原名)；有前缀 → (provider, 模型名)。"""
    return {
        "openai:gpt-4o": parse_model_id("openai:gpt-4o"),
        "anthropic:claude-sonnet-4-5": parse_model_id("anthropic:claude-sonnet-4-5"),
        "test": parse_model_id("test"),
    }


# ---------------------------------------------------------------------------
# 3. ModelSettings 的两级合并
# ---------------------------------------------------------------------------
def probe_settings(
    agent_settings: ModelSettings,
    run_settings: ModelSettings,
) -> tuple[str, dict[str, object]]:
    """用 FunctionModel 截获「框架实际发给模型」的设置，验证合并结果。

    `Agent(model_settings=...)` 是默认值，`run_sync(..., model_settings=...)` 优先级更高，
    两者按 key 合并（后者覆盖前者），最终结果可从 `AgentInfo.model_settings` 读到。
    """
    captured: dict[str, dict[str, object]] = {}

    def probe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["settings"] = dict(info.model_settings or {})
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(probe, model_name="settings-probe"), model_settings=agent_settings, output_type=str)
    result = agent.run_sync("你好", model_settings=run_settings)
    return result.output, captured["settings"]


# ---------------------------------------------------------------------------
# 4. FallbackModel：主模型失败时回退到备选
# ---------------------------------------------------------------------------
def _failing_model(model_name: str) -> FunctionModel:
    """构造一个「一调用就抛 ModelAPIError」的离线模型，模拟主模型不可用。"""

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelAPIError(model_name, f"{model_name} 模拟不可用（限流 / 宕机 / 网络）")

    return FunctionModel(boom, model_name=model_name)


def fallback_run() -> tuple[str, list[str]]:
    """主模型抛错 → FallbackModel 切换到备选模型 → 返回结果与调用轨迹。"""
    calls: list[str] = []

    def primary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append("primary")
        raise ModelAPIError("primary-model", "主模型不可用")

    def backup(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append("backup")
        return ModelResponse(parts=[TextPart("来自备选模型的回答")])

    model = FallbackModel(
        FunctionModel(primary, model_name="primary"),
        FunctionModel(backup, model_name="backup"),
    )
    result = Agent(model, output_type=str).run_sync("你好")
    return result.output, calls


def fallback_all_fail() -> list[str]:
    """所有候选模型都失败时，框架会聚合抛出 `FallbackExceptionGroup`。"""
    model = FallbackModel(_failing_model("m1"), _failing_model("m2"))
    try:
        Agent(model, output_type=str).run_sync("你好")
    except FallbackExceptionGroup as exc:
        return [type(e).__name__ for e in exc.exceptions]
    return []


# ---------------------------------------------------------------------------
# 5. Agent.override(model=...)：不改业务代码切换模型
# ---------------------------------------------------------------------------
def override_switches() -> tuple[str, str, str]:
    """返回 (override 前, override 中, override 后) 三次运行的结果。

    `override` 是上下文管理器，仅在 `with` 块内生效——这是测试里「用 TestModel 顶替真实
    模型」的标准手法，业务代码一行都不用改。
    """
    baseline = FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart("baseline")]),
        model_name="baseline",
    )
    agent = Agent(baseline, output_type=str)

    before = agent.run_sync("hi").output
    with agent.override(model=TestModel(custom_output_text="overridden")):
        during = agent.run_sync("hi").output
    after = agent.run_sync("hi").output
    return before, during, after


# ---------------------------------------------------------------------------
# 6. ModelProfile：模型家族固有事实
# ---------------------------------------------------------------------------
def profile_facts() -> dict[str, object]:
    """读取 profile，回答「这个模型支不支持结构化输出 / 思考」等选型问题。"""
    function_model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("")]))
    test_model = TestModel()
    return {
        "function_model": {key: function_model.profile[key] for key in PROFILE_KEYS},
        "test_model": {key: test_model.profile[key] for key in PROFILE_KEYS},
        # infer_model_profile 只解析 provider、不构造模型实例
        "test_default_output_mode": infer_model_profile("test")["default_structured_output_mode"],
    }


# ---------------------------------------------------------------------------
# 7. defer_model_check：构造期不解析模型
# ---------------------------------------------------------------------------
def defer_model_check_demo() -> str:
    """`defer_model_check=True` 让 `Agent('openai:gpt-4o')` 在构造时不加载 openai SDK。

    本仓库未安装 `openai` extra，若不加该参数，`Agent(...)` 会立刻抛 ImportError。
    """
    agent = Agent("openai:gpt-4o", defer_model_check=True, output_type=str)
    return repr(agent.model)


def main() -> None:
    print("=" * 68)
    print("1) infer_model('test') → 模型实例的四个身份字段")
    print("=" * 68)
    for key, value in infer_test_model_identity().items():
        print(f"  {key:<12}= {value!r}")

    print()
    print("=" * 68)
    print("2) parse_model_id：'provider:model' 在第一个冒号处切分")
    print("=" * 68)
    for raw, parsed in parse_examples().items():
        print(f"  parse_model_id({raw!r:<32}) = {parsed}")

    print()
    print("=" * 68)
    print("3) ModelSettings 合并：Agent 默认 + run 覆盖")
    print("=" * 68)
    output, merged = probe_settings(
        agent_settings={"max_tokens": 100, "timeout": 20},
        run_settings={"temperature": 0.5},
    )
    print(f"  Agent(model_settings={{'max_tokens': 100, 'timeout': 20}})")
    print(f"  run_sync(model_settings={{'temperature': 0.5}})")
    print(f"  → 实际发给模型：{merged}")
    print(f"  → 输出：{output!r}")

    print()
    print("=" * 68)
    print("4) FallbackModel：主模型失败 → 回退备选")
    print("=" * 68)
    fallback_output, fallback_calls = fallback_run()
    print(f"  调用轨迹：{fallback_calls}")
    print(f"  最终输出：{fallback_output!r}")
    print(f"  全部失败时的子异常：{fallback_all_fail()}")

    print()
    print("=" * 68)
    print("5) Agent.override(model=TestModel())：不改业务代码切换模型")
    print("=" * 68)
    before, during, after = override_switches()
    print(f"  override 前：{before!r}")
    print(f"  override 中：{during!r}")
    print(f"  override 后：{after!r}")

    print()
    print("=" * 68)
    print("6) ModelProfile：能力事实（选型依据）")
    print("=" * 68)
    facts = profile_facts()
    for model_key in ("function_model", "test_model"):
        print(f"  {model_key}: {facts[model_key]}")
    print(f"  infer_model_profile('test')['default_structured_output_mode'] = {facts['test_default_output_mode']!r}")

    print()
    print("=" * 68)
    print("7) defer_model_check：构造期不解析模型（解耦 SDK / 环境变量）")
    print("=" * 68)
    print(f"  Agent('openai:gpt-4o', defer_model_check=True).model = {defer_model_check_demo()!r}")


if __name__ == "__main__":
    main()
```

**逐段讲解：**

- **导入**：`FallbackModel` 与 `FallbackExceptionGroup` 分别来自 `pydantic_ai.models.fallback` 与顶层 `pydantic_ai`；`AgentInfo` 是 `FunctionModel` 回调拿到的「请求信息」——它带 `model_settings` 与 `model_request_parameters`，是我们观测框架行为的窗口。
- **`model_identity`**：直接读四个属性。注意类型标注用 `Model`（抽象），函数对任何模型都成立——这正是「依赖抽象而非实现」。
- **`probe_settings`**：用一个闭包捕获 `info.model_settings`，从而**断言框架实际发出的设置**，而不是断言你传进去的 dict。这是验证「合并语义」唯一可靠的方式。
- **`fallback_run` / `fallback_all_fail`**：用 `FunctionModel` 造两个「会抛 `ModelAPIError`」的模型，精确复现主模型宕机；`calls` 列表记录调用顺序，用于证明「先主后备」。
- **`override_switches`**：`with agent.override(...)` 前后各跑一次，证明切换**只在块内生效**。
- **`profile_facts`**：对 `FunctionModel` 与 `TestModel` 读同一组能力键，直观看出「同一份代码、不同能力事实」。
- **`defer_model_check_demo`**：构造 `Agent('openai:gpt-4o', defer_model_check=True)` 而不触发 SDK 导入。

运行要点（实测）：

```text
model_id                        = 'test:test'
合并后的设置                    = {'max_tokens': 100, 'timeout': 20, 'temperature': 0.5}
FallbackModel 调用轨迹          = ['primary', 'backup'] → '来自备选模型的回答'
全部失败的子异常                = ['ModelAPIError', 'ModelAPIError']
override 三次结果               = baseline / overridden / baseline
```

---

## 6. 深入剖析

### 6.1 解析函数签名

```python
def parse_model_id(model: str) -> tuple[str | None, str]:
    """在第一个 ':' 处切分；无前缀返回 (None, model)。"""

def infer_model(model: Model | KnownModelName | str) -> Model:
    """字符串 → 具体 Model 实例；已是 Model 则原样返回。"""

def infer_model_profile(model: Model | KnownModelName | str) -> ModelProfile:
    """只解析 provider（不构造模型实例），返回该模型的 profile 快照。"""
```

构造失败的三类错误，排错时先看类型：

| 症状 | 原因 | 处理 |
|---|---|---|
| `UserError: ... Unknown model ...` | 字符串**没有** `provider:` 前缀 | 补前缀，如 `'openai:gpt-4o'` |
| `ImportError: Please install the 'xxx' package` | 装了 slim 包但没装该 provider 的 extra | `pip install 'pydantic-ai-slim[openai]'` |
| Provider 初始化时报缺 Key | 环境变量没设 | 设 `OPENAI_API_KEY` 等 |

> 实测：本仓库只装了 `pydantic-ai-slim`（无 extra），`infer_model('openai:gpt-4o')` 会抛
> `ImportError: Please install the 'openai' package ...`。这也是 labs 全程用离线的 `TestModel` /
> `FunctionModel` 的原因。

### 6.2 `Model` 抽象的方法面

| 成员 | 签名 | 说明 |
|---|---|---|
| `model_name` | `property -> str` | 抽象，子类必须实现 |
| `system` | `property -> str` | 抽象；流入 OTel `gen_ai.system` |
| `model_id` | `property -> str` | 默认 `f'{system}:{model_name}'` |
| `base_url` | `property -> str \| None` | 默认 `None` |
| `context_window` | `property -> int \| None` | `profile['context_window']`；`FallbackModel` 取候选最小值 |
| `profile` | `cached_property -> ModelProfile` | 解析顺序：默认 → provider 默认 → 定价数据 → 用户 `profile=` |
| `request(...)` | `async` | **唯一抽象方法**，一次请求的契约 |
| `settings` | `property -> ModelSettings \| None` | 构造时传入的默认设置 |

### 6.3 `fallback_on` 的进阶用法

`FallbackModel` 的 `fallback_on` 不只接受异常类型，还可以是处理器 / 响应处理器 / 混合序列：

```python
FallbackModel(
    primary, backup,
    fallback_on=(ModelAPIError,),          # 只在这类异常时回退（默认）
)
```

- 传**异常类型元组** → 命中才回退；
- 传**函数**（处理器） → 可自定义「什么情况算失败」，例如限流、内容审核拒绝；
- 传**响应处理器** → 根据已返回的响应决定是否回退（如空响应）。
- **工程建议：先窄后宽**。默认只在 `ModelAPIError`（网络 / 限流 / 5xx）回退最安全；把 `UnexpectedModelBehavior`（模型没按格式输出）也纳入回退前，先想清楚「这是模型问题还是 prompt 问题」。

### 6.4 `override` 的语义

`Agent.override(...)` 接受与构造参数同名的若干字段（`model` / `model_settings` / `deps` / `tools` / `instructions` ...），是**上下文管理器**：

```python
with agent.override(model=TestModel()):
    ...   # 仅此块内生效，退出即恢复
```

它比「重新 `Agent(...)`」更优的地方：**同一份 Agent 配置**（指令、工具、deps 类型）被复用，只替换你要临时改的那一项。

### 6.5 Provider 与"客户端复用"

Provider 的 `__aenter__` / `__aexit__` 用**引用计数**管理自建的 HTTP 客户端：进入次数归零时才 `aclose()`。这意味着用 `async with agent:` 或框架内部管理生命周期时，客户端会被复用而非每次新建。你通常不用管它——但要知道「Provider 持有客户端」这件事，才能解释为什么**改 `base_url` 要新建 Provider，而不是改 Model 上的字段**。

---

## 7. 常见变体与工程实践

### 7.1 生产 / 测试双轨：默认真实模型，测试 `override`

```python
# 生产装配
agent = Agent('openai:gpt-4o', instructions='你是客服助手')

# 单测：一行切换，业务代码零改动
def test_greeting():
    with agent.override(model=TestModel()):
        assert agent.run_sync('你好').output  # 不发网络请求
```

### 7.2 主备回退 + 成本护栏

```python
model = FallbackModel(
    'openai:gpt-4o',        # 主：能力强、贵
    'openai:gpt-4o-mini',   # 备：便宜、够用（限流时兜底）
)
agent = Agent(model)
```

配合 `UsageLimits`（第 3.4 章）还能把「回退也不能超预算」表达清楚。

### 7.3 用 `defer_model_check` 让装配与运行解耦

```python
# 模块级：构造期不解析，导入即安全
agent = Agent('openai:gpt-4o', defer_model_check=True)

# 真正运行时（CI 的前置检查 / 部署探针）再显式验证模型可用
agent.run_sync('ping', model=... )  # 或直接调用一次轻量请求
```

### 7.4 读 Profile 而非写 `if`

```python
# ❌ 散落判断：新增模型就要改这里
if model.model_name.startswith('gpt'):
    mode = 'native'

# ✅ 读能力事实
mode = model.profile['default_structured_output_mode']
if model.profile['supports_json_schema_output']:
    ...  # 走 native 结构化输出
```

### 7.5 多模型选型准则（成本 / 延迟 / 能力）

| 维度 | 关注点 | 工程手段 |
|---|---|---|
| **成本** | 输入/输出 token 单价、缓存命中率 | 便宜模型打草稿、强模型做终审；开启缓存（`profile['supports_cache']`） |
| **延迟** | 首 token 时间、总时长 | 流式（第 3.3 章）、小模型做路由、`FallbackModel` 兜底 |
| **能力** | 工具 / native 输出 / 思考 / 上下文窗口 | 读 `ModelProfile`，按 `context_window` 决定压缩策略 |
| **合规** | 数据出境、可观测 | 自建 base_url、`defer_model_check` 延迟绑定 |

> 一条朴素原则：**用「够用的最便宜模型」做默认，把「强模型」留给真正需要的环节。** Profile 让你的选择有据可依，而不是拍脑袋。

---

## 8. 练习

**练习 1（解析）** 给定 `'openrouter:meta-llama/llama-3-70b'`，写出 `parse_model_id` 的返回值，并说明为什么斜杠不会导致切错。

> 答案要点：返回 `('openrouter', 'meta-llama/llama-3-70b')`。`parse_model_id` 只在**第一个** `:` 处 `split(':', maxsplit=1)`，斜杠不是分隔符，原样保留在模型名里。

**练习 2（身份）** 请写出一个模型实例的 `model_id` 通用构造公式，并说明 `system` 除了展示还有什么用途。

> 答案要点：`model_id = f'{system}:{model_name}'`。`system` 会被写入每条消息 part 的 `provider_name` 并流入 OTel `gen_ai.system`；重放历史（thinking / native tool）会读它，因此改名会破坏旧历史回放。

**练习 3（合并）** 若 `Agent(model_settings={'temperature': 0, 'max_tokens': 200})`，一次运行传 `model_settings={'temperature': 0.8}`，最终发给模型的设置是什么？

> 答案要点：`{'temperature': 0.8, 'max_tokens': 200}`——run 级覆盖同名键，未覆盖的键保留。可用 `FunctionModel` 读 `info.model_settings` 断言。

**练习 4（回退）** 用 `FallbackModel` 表达「主模型限流时回退备选」，并说明两个模型都失败时抛出什么异常、如何从中取出各模型的原始异常。

> 答案要点：`FallbackModel(primary, backup)`（默认 `fallback_on=(ModelAPIError,)`，限流通常归于此类）。全部失败抛 `FallbackExceptionGroup`，通过 `exc.exceptions` 拿到各候选的原始异常（如两条 `ModelAPIError`）。

**练习 5（工程）** 团队代码里出现了三处 `if model.model_name.startswith('gpt'): ...`。请说明为什么不推荐，并给出替代方案。

> 答案要点：这类判断把「能力事实」硬编码进调用点，每新增一个模型家族都要改 N 处，且容易与真实能力漂移。替代方案是读 `model.profile[...]`（如 `supports_json_schema_output`、`default_structured_output_mode`）；若要支持新家族，应在其 profile 中声明事实，而不是在调用点加分支。

---

## 9. 验收标准

运行以下命令，全部通过即视为掌握本章：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_6.py -q
```

验收测试断言了以下 **11 条**行为（覆盖模型标识、字符串解析、设置合并、override、回退、Profile、`defer_model_check`）：

1. `infer_model('test')` 得到 `TestModel`，`model_name == 'test'`、`system == 'test'`、`model_id == 'test:test'`、`base_url is None`；
2. `parse_model_id('openai:gpt-4o') == ('openai', 'gpt-4o')`；
3. `parse_model_id('anthropic:claude-sonnet-4-5') == ('anthropic', 'claude-sonnet-4-5')`；
4. `parse_model_id('test') == (None, 'test')`；
5. `Agent(model_settings={max_tokens, timeout})` + `run(model_settings={temperature})` 合并后三者都在；
6. run 级同名键覆盖 Agent 级、未覆盖键保留；
7. `FallbackModel` 主模型失败后回退成功，调用轨迹为 `['primary', 'backup']`；
8. `FallbackModel` 全部失败抛 `FallbackExceptionGroup`，子异常为 `['ModelAPIError', 'ModelAPIError']`；
9. `Agent.override(model=TestModel())` 仅在 `with` 内生效，块外恢复原行为；
10. `FunctionModel` 与 `TestModel` 的 `profile` 反映不同能力事实（结构化输出 `True` / `False`）；
11. `defer_model_check=True` 时 `repr(agent.model)` 仍是 `'openai:gpt-4o'` 字符串。

---

## 10. 常见坑与排错

| 现象 | 根因 | 处理 |
|---|---|---|
| `UserError: Unknown model` | 模型字符串**漏了** `provider:` 前缀 | 写成 `'openai:gpt-4o'`；`'test'` 是唯一无前缀特例 |
| `ImportError: install the 'xxx' package` | slim 包未装该 provider extra | `pip install 'pydantic-ai-slim[openai]'` 等 |
| 构造 `Agent` 就报错，但我只想先写代码 | 默认构造期解析模型 | 加 `defer_model_check=True` |
| 传了 `model_settings` 却没生效 | 被上层的 Agent 级设置在**同名键**上覆盖（或反之） | 记住 run 级优先；用 `FunctionModel` 读 `info.model_settings` 验证 |
| 回退没触发 | 异常不在 `fallback_on` 里 | 默认只回退 `ModelAPIError`；如需可扩展 `fallback_on` |
| 从 `FallbackExceptionGroup` 里取不到原因 | 直接 `except Exception as e` | 捕获 `FallbackExceptionGroup` 并读 `e.exceptions` |
| 换了 `base_url` 但能力判断出错 | 用 base_url 猜能力 | 能力看 provider / 客户端类型；base_url 只决定「打到哪」 |
| 测试里 `TestModel` 不返回我想要的文本 | 没设 `custom_output_text` | `TestModel(custom_output_text='...')` |
| 明明 `override` 了，块外还生效 | 忘了是用 `with` | `override` 是上下文管理器，必须 `with` |
| 把模型名硬编码进工具逻辑 | 直接依赖具体模型名 | 改读 `model.profile[...]` |

> 排错顺序建议：**先看异常类型**（UserError / ImportError / 缺 Key）→ **再看是不是构造期解析** → **最后查设置与回退配置**。

---

## 11. 面试延伸

**Q1：`'provider:model'` 字符串是怎么解析成模型实例的？**

> 答题要点：`parse_model_id` 在第一个 `:`（`maxsplit=1`）切分，得 `(provider, model_name)`；无前缀返回 `(None, name)`。`infer_model` 顺序分派：已是 `Model` 原样返回；`'test'` → `TestModel`；无前缀抛 `UserError`；有前缀则 `infer_provider` 造 Provider，再按 provider 名选具体 Model 类（如 `openai` → `OpenAIChatModel`）。顺序敏感点：一些 OpenAI 兼容 provider（openrouter、ollama 等）必须在 `openai` 之前判断。

**Q2：`Model`、`Provider`、`ModelProfile` 三者各管什么？**

> 答题要点：`Provider` 管「怎么连」——鉴权、base_url、客户端生命周期；`Model` 管「怎么调」——把规范化请求翻译成某家 API 格式并解析响应，只暴露 `model_name` / `system` / `model_id` / `base_url` / `request` / `profile`；`ModelProfile` 是模型家族的**能力事实**（是否支持工具 / native 结构化输出 / 思考 / 缓存 / 上下文窗口），以数据形式暴露，避免散落 `if provider == ...`。

**Q3：`ModelSettings` 在 `Agent(...)` 与 `run(...)` 上都可传，合并规则是什么？为什么这样设计？**

> 答题要点：run 级覆盖 Agent 级，按 key 合并、未覆盖键保留（本质 `{**agent, **run}`）。设计意图：Agent 级承载「默认策略」（如 `temperature=0` 求稳定），run 级承载「一次性的临时调整」（如某次 `temperature=0.9`），避免为每次调用重建 Agent。

**Q4：`FallbackModel` 与 `Agent.override` 分别解决什么问题？**

> 答题要点：`FallbackModel` 是**运行时韧性**——主模型抛 `ModelAPIError`（限流 / 宕机）时按序尝试备选，全部失败抛 `FallbackExceptionGroup`，`fallback_on` 可自定义「什么算失败」。`override` 是**装配期替换**——上下文管理器内临时换模型 / 设置 / deps，典型用于测试（`TestModel` 顶替真实模型）与灰度，块外不改动业务代码。

**Q5：如何让 Agent 的模型层不依赖具体 SDK / 环境变量，从而方便测试与部署？**

> 答题要点：用 `defer_model_check=True` 让构造期只保留模型字符串、首次运行时才解析；测试时用 `agent.override(model=TestModel()/FunctionModel(...))` 完全离线；能力判断统一读 `ModelProfile` 而非判断 provider 名；把 base_url / 鉴权收敛到 Provider。这样「装配、测试、运行」三者解耦。

---

## 12. 延伸阅读

- 源码篇 · 模型访问层：[`../../code_wiki/03-models-providers-profiles.md`](../../code_wiki/03-models-providers-profiles.md)
  —— `models/` 适配层、`providers/` 鉴权与客户端、`profiles/` 能力事实、`settings.py` 设置合并，以及字符串解析与请求归一化的完整链路。
- 源码篇 · 核心 Agent 循环：[`../../code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md)
  —— `Agent` 构造参数（含 `model_settings` / `defer_model_check`）、运行方法、异常体系，理解「模型何时被解析、被调用」。
- 配套 labs：[`../labs/part_2/ch_2_6.py`](../labs/part_2/ch_2_6.py) 与验收测试 [`../labs/part_2/test_ch_2_6.py`](../labs/part_2/test_ch_2_6.py)。
