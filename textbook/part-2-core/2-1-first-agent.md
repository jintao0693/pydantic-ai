# 2.1 第一个 Agent：run / run_sync / run_stream 与结构化输出

> 本课配套 labs：[`labs/part_2/ch_2_1.py`](../../labs/part_2/ch_2_1.py)、验收测试 [`labs/part_2/test_ch_2_1.py`](../../labs/part_2/test_ch_2_1.py)。

---

## 1. 本课目标

学完本章，你应当能够：

1. 熟练构造一个 `Agent`：说清 `model` / `instructions` / `system_prompt` / `output_type` 四个参数各自的作用，并解释为什么推荐 `'provider:model'` 字符串。
2. 在 `run` / `run_sync` / `run_stream` / `run_stream_sync` 之间做出正确选择，并说清它们「阻塞还是流式」「同步还是异步」「何时结束」的语义差异。
3. 读懂 `AgentRunResult` 的四个面：`result.output`、`all_messages()`、`new_messages()`、`usage`，知道它们各自回答什么问题。
4. 用 `output_type` 声明结构化输出，理解 `result.output` 为什么是一个**已被 Pydantic 校验过**的实例。
5. 建立「模型输出不合法 → `ModelRetry` → 重试」的直觉，并知道重试预算在哪里配置（机制细节留给 2.4）。
6. 说清 `instructions` 与 `system_prompt` 的区别与落点，并在工程代码里**返回结构化对象而不是 `print` 聊天记录**。

---

## 2. 前置知识

- 已完成 **1.1 智能体心智模型**：知道「Agent = 模型 + 指令 + 工具 + 循环」。
- 已完成 **1.2 工程基座**：会用 `uv run --no-project` 跑 labs，认识 `TestModel` / `FunctionModel` 这两种「假模型」（本章不再重复其基础用法，只把它们当作实验台）。
- 已完成 **1.3 Pydantic v2 精要**：会定义 `BaseModel`、字段与校验；结构化输出一章直接建立在它之上。
- Python 异步基础：知道 `async def` / `await`，知道 `asyncio.run` 的作用。不熟可回看篇零 0.2 章。

---

## 3. 为什么需要它

上一章你已经有能力「把 Agent 跑通」。但在真实交付里，跑通只是起点。真正拉开差距的问题是：

| 场景 | 新手写法 | 工程写法 |
|------|----------|----------|
| 拿到模型回答 | `print(chat)` | `result.output`（可类型检查、可断言、可入库） |
| 需要结构化数据 | 用正则从字符串里抠字段 | `output_type=CityInfo`，拿到的就是校验后的实例 |
| 要喂给前端「边生成边显示」 | 傻等，最后一次性返回 | `run_stream`，逐块 `stream_text(delta=True)` |
| 要把多轮对话续上 | 自己拼字符串 | `message_history=result.all_messages()` |
| 要知道这次花了多少 | 不统计 | `result.usage`（请求数 / token / 成本） |

一句话：**`print` 是 demo 的终点，`result.output` 是产品的起点。** 本章讲的就是「从 run 到 result，再到结构化输出」这条最短、也最常被面试官追问的主干道。

---

## 4. 核心概念

### 4.1 Agent 的四要素

一个 `Agent` 至少由四件事决定：

```python
from pydantic import BaseModel

from pydantic_ai import Agent


class CityInfo(BaseModel):
    city: str
    country: str


agent = Agent(
    'openai:gpt-5.2',                  # ① model：用哪个模型
    instructions='你是城市信息助手。',    # ② instructions：指导模型行为
    system_prompt='始终用简体中文回答。',  # ③ system_prompt：固定的系统提示
    output_type=CityInfo,              # ④ output_type：输出契约（默认是 str）
)
```

| 要素 | 作用 | 默认 |
|------|------|------|
| `model` | 指定 provider + 模型 | `None`（必须运行时补） |
| `instructions` | 指导本次运行的行为，可动态、可调用、可追加 | `None` |
| `system_prompt` | 静态系统提示，进入消息历史 | `()` |
| `output_type` | 声明输出类型，框架据此约束并校验模型输出 | `str` |

### 4.2 为什么推荐 `'provider:model'` 字符串

`model` 参数接受三种形态：

```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

Agent('openai:gpt-5.2')     # ① 字符串：'provider:model'，运行期才解析
Agent(TestModel())          # ② 模型实例：直接给一个 Model 对象
Agent()                     # ③ 不传：每次 run 时再给（如 run(model=...)）
```

推荐字符串，理由有四条：

1. **延迟解析、不绑定 SDK**：字符串在构造时只会被存下；真正解析发生在首次运行时。因此你不必在导入 agent 模块时就把某个 provider 的 SDK 拉起来，也就不会因为「只跑测试、没装 openai」而导入失败。
2. **配置可外部化**：`'openai:gpt-5.2'` 可以直接来自环境变量或配置文件，切换 provider 不需要改代码结构。
3. **多 provider 切换成本低**：把 provider 当成一个坐标，`'anthropic:...'`、`'google-gla:...'` 只是换一个前缀，模型相关的能力探测由 profile 统一处理（见 2.6）。
4. **可序列化**：Agent Spec、durable execution 等场景需要「一个能写进 YAML/JSON 的模型标识」，字符串天然满足。

代价是：拼写错误要到运行时才暴露。构造时若想提前校验，可设 `defer_model_check=False`（默认行为之一），但那要求对应 SDK 已安装——测试场景下通常反而希望 `defer_model_check=True`，以便 `override` 成 `TestModel`。

> 需要自定义 `base_url`、注入自定义 HTTP client、指定 profile 时，才传入**模型实例**。

### 4.3 运行方法矩阵

同一个 Agent 有多个运行入口，别被数量吓到——它们只是「同步/异步」×「一次性/流式」的组合：

| 方法 | 阻塞形态 | 返回 | 何时结束 |
|------|----------|------|----------|
| `run(...)` | `await`（异步） | `AgentRunResult[T]` | 图跑到终点 |
| `run_sync(...)` | 同步阻塞 | `AgentRunResult[T]` | 同上 |
| `run_stream(...)` | `async with`（异步） | `StreamedRunResult[T]` | 首个匹配 `output_type` 的输出 |
| `run_stream_sync(...)` | `with`（同步） | `StreamedRunResultSync[T]` | 同上 |
| `run_stream_events(...)` | `async with`（异步） | 事件流 | 运行 + 全部事件 |
| `iter(...)` | `async for`（异步） | 可逐步驱动的 `AgentRun` | 图跑到终点 |

两条必须记住的纪律：

- **`run_sync` 不能在异步代码里调用**，也不能在「正在运行的同步工具」里调用；它内部用的是「跑完一个事件循环」的方式，嵌套会直接报错。异步上下文请用 `run`。
- **流式会「提前结束」**：`run_stream` 以**第一个匹配 `output_type` 的输出**为终点，此后模型发出的工具调用不再执行。这是流式「低延迟」必须付出的确定性代价。

### 4.4 结果对象的四个面

```python
result = agent.run_sync('法国的首都是哪座城市？')
```

| 面 | 类型 | 回答什么问题 |
|----|------|--------------|
| `result.output` | `T`（由 `output_type` 决定） | 最终答案是什么 |
| `result.all_messages()` | `list[ModelMessage]` | 截至目前的**完整**历史（含你传进来的 `message_history`） |
| `result.new_messages()` | `list[ModelMessage]` | **本次运行新增**的历史（不含传入的历史） |
| `result.usage` | `RunUsage` | 花了多少：`requests` / `input_tokens` / `output_tokens` / 成本 |

另外还有 `result.response`（最后一条 `ModelResponse`）、`result.run_id` / `result.conversation_id`（可观测性与对话续接）。

> **注意一个坑**：`usage` 在 2.x 是**属性**（property），写成 `result.usage`；写成 `result.usage()` 会得到 `TypeError: 'RunUsage' object is not callable`。

### 4.5 结构化输出：`output_type`

```python
class CityInfo(BaseModel):
    city: str
    country: str


agent = Agent('openai:gpt-5.2', output_type=CityInfo)
result = agent.run_sync('法国的首都是哪座城市？')
assert isinstance(result.output, CityInfo)   # 不是 dict，也不是 str
print(result.output.city)                    # 'Paris'
```

`output_type=CityInfo` 做了三件事：

1. **把类型翻译成模型能理解的契约**：框架会根据 `CityInfo` 生成 JSON Schema，并默认用一个「输出工具」（output tool）让模型调用，参数即结构。
2. **校验**：模型返回的 JSON 会被 Pydantic 校验成 `CityInfo` 实例。字段缺失 / 类型不符 → 校验失败。
3. **类型安全**：`result.output` 的静态类型就是 `CityInfo`，IDE 与类型检查器都能顺着 `.city` 提示。

想知道框架实际暴露给模型什么，可以打印 `agent.output_json_schema()`。

### 4.6 `instructions` vs `system_prompt`

两者都「告诉模型怎么做」，但落点不同：

| 维度 | `instructions` | `system_prompt` |
|------|----------------|-----------------|
| 动态性 | 可以是字符串 / 模板 / 函数，随运行求值 | 静态字符串（或字符串序列） |
| 落点 | 写入 `ModelRequest.instructions`，并在 `AgentInfo.instructions` 暴露 | 渲染成 `SystemPromptPart` 进入消息 `parts` |
| 是否进历史 | 不作为消息 part 持久化 | 是消息历史的一部分 |
| 适用 | 应用级行为约束、需要按运行/依赖变化 | 固定的、长期稳定的系统人设 |

工程建议：**能用 `instructions` 表达的行为约束就用 `instructions`**；`system_prompt` 留给那些真正「一句话写死、且需要进入历史」的文本。本章第 5.4 节会用 `FunctionModel` 把两者的落点「照」出来。

---

## 5. 最小可运行示例

### 5.1 运行方式

labs 不需要任何 API Key，也不需要联网（全部用 `TestModel` / `FunctionModel`）：

```bash
cd textbook/labs

# 直接运行示例
uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_1.py

# 跑本章验收测试
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_1.py -q
```

### 5.2 完整代码：`labs/part_2/ch_2_1.py`

```python
"""第 2.1 章 · 第一个 Agent：run / run_sync / run_stream 与结构化输出。

本模块用 `TestModel` 与 `FunctionModel` 在**离线、无 API Key** 的前提下演示：

1. `Agent(...)` 的构造（`model` / `instructions` / `system_prompt` / `output_type`）；
2. `run_sync` / `run` / `run_stream_sync` / `run_stream` 四个运行入口；
3. `result.output` / `all_messages()` / `new_messages()` / `usage` 四个结果面；
4. 结构化输出：`output_type` 指定 Pydantic 模型，`result.output` 是校验后的实例；
5. `instructions` 与 `system_prompt` 在模型侧落到不同位置。

运行方式（无需任何 API Key，也不需要联网）：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_1.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


class CityInfo(BaseModel):
    """结构化输出用的数据模型：模型必须同时给出城市与国家两个字段。"""

    city: str = Field(description='城市名称')
    country: str = Field(description='所属国家')


# ---------------------------------------------------------------------------
# 1) TestModel + run_sync：一行构造一个「永远成功」的离线 Agent
# ---------------------------------------------------------------------------


def run_with_test_model() -> AgentRunResult[str]:
    """用 TestModel 跑通一次同步运行，并把整个结果对象交出去。"""
    agent: Agent[None, str] = Agent(
        TestModel(),  # 「黑盒假模型」：不联网，固定返回 success (no tool calls)
        instructions='你是一个用于离线演示的助手。',
    )
    return agent.run_sync('用一句话介绍你自己')


# ---------------------------------------------------------------------------
# 2) FunctionModel：用函数精确控制模型返回的文本
# ---------------------------------------------------------------------------


def _last_user_text(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条用户消息的文本。"""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return part.content
    return ''


def build_function_model() -> FunctionModel:
    """构造一个「白盒假模型」：返回文本由我们的函数写死，可精确断言。"""

    def rule_based(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        user_text = _last_user_text(messages)
        return ModelResponse(parts=[TextPart(content=f'【FunctionModel】你说了：{user_text}')])

    return FunctionModel(rule_based)


def run_with_function_model(prompt: str) -> str:
    """用 FunctionModel 驱动 Agent，返回完全可预测的文本输出。"""
    agent = Agent(build_function_model())
    return agent.run_sync(prompt).output


# ---------------------------------------------------------------------------
# 3) 结构化输出：output_type 为一个 Pydantic 模型
# ---------------------------------------------------------------------------


def run_structured_output() -> CityInfo:
    """让 Agent 产出 CityInfo 实例：result.output 是经过校验的 Pydantic 实例。"""
    agent: Agent[None, CityInfo] = Agent(
        TestModel(custom_output_args=CityInfo(city='Paris', country='France')),
        output_type=CityInfo,  # 声明输出契约，框架据此约束并校验模型输出
    )
    return agent.run_sync('法国的首都是哪座城市？').output


# ---------------------------------------------------------------------------
# 4) instructions vs system_prompt：读 AgentInfo / ModelRequest 观察落点
# ---------------------------------------------------------------------------


def compare_instructions_and_system_prompt() -> dict[str, Any]:
    """用 FunctionModel 观察 instructions 与 system_prompt 分别被放到了哪里。"""
    captured: dict[str, Any] = {}

    def observe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # instructions 会被渲染进 AgentInfo.instructions，也写在 ModelRequest.instructions 上
        captured['instructions_from_info'] = info.instructions
        captured['instructions_on_request'] = next(
            (message.instructions for message in messages if isinstance(message, ModelRequest)),
            None,
        )
        # system_prompt 则以 SystemPromptPart 的形式进入第一条 ModelRequest 的 parts
        captured['system_prompts'] = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, SystemPromptPart)
        ]
        return ModelResponse(parts=[TextPart(content='已收到指令与系统提示。')])

    agent = Agent(
        FunctionModel(observe),
        instructions='你是城市信息助手，只回答地理问题。',
        system_prompt='始终用简体中文回答。',
    )
    agent.run_sync('上海在哪里？')
    return captured


# ---------------------------------------------------------------------------
# 5) 流式：run_stream_sync（同步）与 run_stream（异步）
# ---------------------------------------------------------------------------

_STREAM_TEXT = '为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。'


def stream_text_sync(prompt: str = '讲个冷笑话') -> str:
    """同步流式：`with agent.run_stream_sync(...) as streamed` + `stream_text(delta=True)`。"""
    agent = Agent(TestModel(custom_output_text=_STREAM_TEXT))
    with agent.run_stream_sync(prompt) as streamed:
        return ''.join(chunk for chunk in streamed.stream_text(delta=True))


async def stream_text_async(prompt: str = '讲个冷笑话') -> str:
    """异步流式：`async with agent.run_stream(...) as streamed` 形态与同步版本一致。"""
    agent = Agent(TestModel(custom_output_text=_STREAM_TEXT))
    async with agent.run_stream(prompt) as streamed:
        return ''.join([chunk async for chunk in streamed.stream_text(delta=True)])


async def run_async(prompt: str) -> str:
    """`run` 是原生异步入口：直接 `await`，与 `run_sync` 返回同类结果对象。"""
    agent = Agent(TestModel(custom_output_text='异步运行的输出'))
    result = await agent.run(prompt)
    return result.output


# ---------------------------------------------------------------------------
# 演示：直接 `python part_2/ch_2_1.py` 打印各段结果
# ---------------------------------------------------------------------------


def main() -> None:
    print('=== 1) TestModel + run_sync：离线跑通 ===')
    result = run_with_test_model()
    print('output         :', result.output)
    print('all_messages   :', len(result.all_messages()), '条')
    print('new_messages   :', len(result.new_messages()), '条')
    print('usage          :', result.usage)
    print()

    print('=== 2) FunctionModel：精确控制文本 ===')
    print(run_with_function_model('你好，世界'))
    print()

    print('=== 3) 结构化输出：output_type 为 Pydantic 模型 ===')
    city = run_structured_output()
    print('output         :', repr(city))
    print('isinstance     :', isinstance(city, CityInfo))
    print()

    print('=== 4) instructions vs system_prompt ===')
    captured = compare_instructions_and_system_prompt()
    print('info.instructions    :', captured['instructions_from_info'])
    print('request.instructions :', captured['instructions_on_request'])
    print('system_prompt parts  :', captured['system_prompts'])
    print()

    print('=== 5) 同步 / 异步流式 ===')
    print('run_stream_sync      :', stream_text_sync())
    print('run_stream (async)   :', asyncio.run(stream_text_async()))
    print('run (async)          :', asyncio.run(run_async('你好')))


if __name__ == '__main__':
    main()
```

### 5.3 逐行讲解

- **`from __future__ import annotations`**：推迟注解求值，配合 `Agent[None, str]` 这类泛型标注更省心。
- **`CityInfo`**：结构化输出的契约。`Field(description=...)` 会进入 JSON Schema，帮助模型理解字段含义。
- **`run_with_test_model()`**：`TestModel()` 是「永远成功」的假模型，`run_sync` 会驱动「用户输入 → 模型请求 → 输出」整条图。注意它返回的是**整个结果对象**，这样调用方既能拿 `.output`，也能看历史与用量。
- **`_last_user_text()`**：从判别联合里取最后一条用户文本——先 `isinstance(message, ModelRequest)` 再找 `UserPromptPart`，二者都必不可少。
- **`FunctionModel(rule_based)`**：白盒假模型。函数每被调用一次，就等价于「模型思考一次」。返回必须是 `ModelResponse`（2.x 不再接受裸字符串）。
- **`run_structured_output()`**：`output_type=CityInfo` 声明契约；`TestModel(custom_output_args=CityInfo(...))` 让假模型直接产出符合该契约的对象。**真实模型场景**下，框架会把 Schema 交给模型、再把模型产出的 JSON 校验成 `CityInfo`。
- **`compare_instructions_and_system_prompt()`**：在假模型函数里「顺手记录」两件事——`info.instructions`（instructions 的落点）与 `SystemPromptPart`（system_prompt 的落点），从而把 4.6 节的表格变成可断言的证据。
- **`stream_text_sync()`**：`run_stream_sync` 返回一个「同步上下文管理器」，`with ... as streamed` 进入；`stream_text(delta=True)` 逐块产出增量，拼接起来就是完整文本。
- **`stream_text_async()` / `run_async()`**：异步版本形态完全对称，只是把 `with` 换成 `async with`、`for` 换成 `async for`、`run_sync` 换成 `await agent.run(...)`。
- **`main()` + `if __name__ == '__main__'`**：既能被 `pytest` import，也能被 `python` 直接运行。

### 5.4 实际运行输出

```
=== 1) TestModel + run_sync：离线跑通 ===
output         : success (no tool calls)
all_messages   : 2 条
new_messages   : 2 条
usage          : RunUsage(input_tokens=51, output_tokens=4, requests=1)

=== 2) FunctionModel：精确控制文本 ===
【FunctionModel】你说了：你好，世界

=== 3) 结构化输出：output_type 为 Pydantic 模型 ===
output         : CityInfo(city='Paris', country='France')
isinstance     : True

=== 4) instructions vs system_prompt ===
info.instructions    : 你是城市信息助手，只回答地理问题。
request.instructions : 你是城市信息助手，只回答地理问题。
system_prompt parts  : ['始终用简体中文回答。']

=== 5) 同步 / 异步流式 ===
run_stream_sync      : 为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。
run_stream (async)   : 为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。
run (async)          : 异步运行的输出
```

请重点看第 4 段：`instructions` 出现在 `info.instructions` / `request.instructions`，而 `system_prompt` 出现在 `parts` 里的 `SystemPromptPart`——**同一句「怎么回答」，落点确实不同**。

---

## 6. 深入剖析

### 6.1 `Agent.__init__` 的关键参数

```text
Agent(
    model=None, *,                          # Model 实例 / 'provider:model' 字符串 / None
    output_type=str,                        # 输出契约（见 2.4）
    instructions=None,                      # 字符串 / 模板 / 函数 / 序列
    system_prompt=(),                       # 单个字符串或字符串序列
    deps_type=object,                       # 依赖类型标记（见 2.5）
    name=None,                              # 日志 / 可观测性名称
    retries=None,                           # int 或 {'tools': n, 'output': n}
    model_settings=None,                    # 温度、max_tokens 等
    end_strategy='graceful',                # 与终结结果并发的工具处理策略
    ...
)
```

本章只需牢牢掌握前四个；`deps_type` / `retries` / `model_settings` 会在 2.5 / 2.6 展开。

### 6.2 四个运行入口的签名与行为

```text
run(user_prompt, *, message_history=None, model=None, instructions=None, deps=None,
    usage_limits=None, ...) -> AgentRunResult[T]                       # async
run_sync(user_prompt, *, ...) -> AgentRunResult[T]                    # sync
run_stream(user_prompt, *, ...) -> AsyncContextManager[StreamedRunResult[T]]     # async
run_stream_sync(user_prompt, *, ...) -> StreamedRunResultSync[T]      # sync CM
```

| 关注点 | `run` / `run_sync` | `run_stream` / `run_stream_sync` |
|--------|--------------------|----------------------------------|
| 拿到结果 | 一次性 `.output` | 增量 `stream_text(delta=True)`，或 `get_output()` 等收尾 |
| 结束时机 | 图跑到终点 | **首个匹配 `output_type` 的输出** |
| 之后的工具调用 | 正常执行 | **不再执行** |
| 适用 | 后端批处理、需要完整结果 | 前端逐字显示、低延迟交互 |

`run_stream` 的返回对象 `StreamedRunResult` 提供 `stream_text()` / `stream_output()` / `stream_response()` / `get_output()` / `all_messages()` / `usage` / `cancel()`。其中 `cancel()` **只停止当前响应**，与取消整个运行的 `RunContext.cancel()` 不同。

### 6.3 结果对象的完整 API（本章用到的部分）

| 成员 | 返回 | 说明 |
|------|------|------|
| `.output` | `T` | 最终输出（结构化时是 Pydantic 实例） |
| `.all_messages()` | `list[ModelMessage]` | 完整历史（含传入的 `message_history`） |
| `.new_messages()` | `list[ModelMessage]` | 本次运行新增的消息 |
| `.usage` | `RunUsage` | **属性**：`requests` / `input_tokens` / `output_tokens` / 成本 |
| `.response` | `ModelResponse` | 最后一条模型响应 |
| `.run_id` / `.conversation_id` | `str` | 运行 / 对话标识 |

`all_messages()` 与 `new_messages()` 的差别，只有在你传了 `message_history` 时才显现：

```python
r1 = agent.run_sync('第一句')
r2 = agent.run_sync('第二句', message_history=r1.all_messages())
len(r1.all_messages())   # 2：request + response
len(r1.new_messages())   # 2
len(r2.all_messages())   # 4：把上一轮的历史也带上了
len(r2.new_messages())   # 2：本轮只新增两条
```

**续接对话就用 `message_history=r1.all_messages()`**；做增量持久化则用 `new_messages()`。消息协议的完整形态见 2.2 章。

### 6.4 `output_type` 如何在运行期落地

`output_type=CityInfo` 时，框架默认走 **tool 输出模式**：把 `CityInfo` 的 JSON Schema 包装成一个「输出工具」暴露给模型，模型通过调用它来「提交答案」。校验发生在输出工具被调用时：

- 参数符合 Schema → Pydantic 校验为 `CityInfo` 实例，成为 `result.output`。
- 参数不符合 → 产出 `ToolRetryError`，向模型发送重试提示（下一节）。

`run_stream` 的「提前结束」正是「首个匹配 `output_type` 的输出」这一规则的结果。

想直接看暴露给模型的 Schema：

```python
import json

print(json.dumps(agent.output_json_schema(), ensure_ascii=False, indent=2))
```

### 6.5 `instructions` 与 `system_prompt` 的实现落点

回顾 5.4 的实测：

- `instructions` → 渲染进 `ModelRequest.instructions`（字符串），并在 `AgentInfo.instructions` 暴露。它**不是**一个 message part。
- `system_prompt` → 渲染成 `SystemPromptPart`，作为 `ModelRequest.parts` 的一员，因而**进入消息历史**。

由此可以推出一个选型原则：

- 需要**按运行、按依赖动态变化**的行为约束（例如「当前用户是 VIP，请优先安抚」）→ 用 `instructions`（可传模板/函数）。
- 需要**跨轮稳定、且希望它成为历史一部分**的系统文本 → 用 `system_prompt`。

两者都可以在构造时传，也可以在运行时追加（`@agent.instructions` / `agent.run(instructions=...)`）。

### 6.6 失败路径：`ModelRetry` 与重试直觉

当输出校验失败，框架会抛出一个**控制流异常** `ModelRetry`，把「哪里不对」作为提示重新发给模型，让它再试一次。重试预算的默认值是 **1 次**：

```python
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

agent: Agent[None, str] = Agent(TestModel(), retries={'output': 3})


@agent.output_validator
def check(data: str) -> str:
    raise ModelRetry('输出不符合要求，请重试')


try:
    agent.run_sync('hi')
except UnexpectedModelBehavior as e:
    print(e)   # Exceeded maximum output retries (3)
```

实测可以得到两条确定的事实：

- 预算耗尽时抛 `UnexpectedModelBehavior('Exceeded maximum output retries (N)')`，`N` 就是你配的预算（不配则默认 1）。
- `ModelRetry` 本身是**控制流**而非错误：它是「再试一次」的信号，只有在预算耗尽时才升级为异常。

本章只需建立这个直觉：**输出不合法不会让程序直接崩，而是触发一次「带反馈的重试」**。输出模式、校验器、`retries` 的完整分类见 2.4。

---

## 7. 常见变体与工程实践

1. **同步优先，异步按需**：脚本 / 批处理用 `run_sync`；Web 框架（FastAPI 等）内部用 `run`；只有需要「逐字显示」时才上 `run_stream`。
2. **结构化输出替代字符串解析**：任何需要下游消费的结果，都用 `output_type=PydanticModel`，把「解析 + 校验」交给框架。
3. **返回对象，不要 `print`**：业务函数应返回 `result.output`（或整个 `AgentRunResult`），让调用方决定怎么展示、怎么落库、怎么断言。
4. **续话用 `message_history`**：`agent.run_sync('下一句', message_history=result.all_messages())`，不要自己拼字符串。
5. **用量进可观测性**：把 `result.usage` 与 `result.run_id` 打进日志/trace，这是成本与排障的抓手（见 3.4）。
6. **接口可替换**：把模型标识做成配置项（`'openai:gpt-5.2'`），测试时用 `agent.override(model=TestModel())`，生产再切回真实模型。
7. **流式的收敛**：流式只用于展示层；需要完整结果时，仍以 `get_output()` 或改用 `run` 为准，避免「流到一半被打断」的中间态泄漏到业务逻辑。

---

## 8. 练习

**练习 1（基础）**：构造一个 `Agent(TestModel(), instructions='你是助手')`，跑一次 `run_sync`，打印 `result.output`、`len(result.all_messages())`、`len(result.new_messages())`、`result.usage.requests`。
> 参考要点：`TestModel()` 固定返回 `success (no tool calls)`；一次运行 `all_messages() == new_messages() == 2`，`usage.requests == 1`。

**练习 2（结构化）**：定义 `class Book(BaseModel): title: str; year: int`，用 `TestModel(custom_output_args=Book(title='...', year=...))` 让 `output_type=Book` 的 Agent 产出实例，并断言 `result.output.title`。
> 参考要点：`result.output` 是 `Book` 实例；用 `isinstance(result.output, Book)` 同时验证「是实例」与「类型正确」。

**练习 3（运行矩阵）**：分别用 `run_sync` 与 `run_stream_sync` 跑同一个 `TestModel(custom_output_text='你好')` 的 Agent，验证两者输出一致；再用 `run_stream`（异步）验证增量拼接结果相同。
> 参考要点：流式用 `with agent.run_stream_sync(...) as s: ''.join(s.stream_text(delta=True))`；异步用 `async with agent.run_stream(...)`。

**练习 4（落点）**：用 `FunctionModel` 读 `AgentInfo.instructions` 与请求里的 `SystemPromptPart`，写测试断言：`instructions` 出现在前者、不出现在后者。
> 参考要点：在假模型函数里记录 `info.instructions` 与 `[p.content for m if ModelRequest for p in m.parts if SystemPromptPart]`，两者互不包含；细节见 5.4 与 6.5。

**练习 5（失败路径）**：给一个 `output_type=str` 的 Agent 挂一个「永远抛 `ModelRetry`」的输出校验器，用 `retries={'output': 2}` 配置预算，断言抛出 `UnexpectedModelBehavior` 且消息含 `(2)`。
> 参考要点：`with pytest.raises(UnexpectedModelBehavior, match=r'Exceeded maximum output retries \(2\)')`；若用 `match`，注意括号要转义。

---

## 9. 验收标准

完成本章后，下列命令必须全绿：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_1.py
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_1.py -q
```

自测清单：

- [ ] 能说出 `Agent` 四要素（`model` / `instructions` / `system_prompt` / `output_type`）的作用与默认值。
- [ ] 能列出 `run` / `run_sync` / `run_stream` / `run_stream_sync` 的语义差异，并说出「流式为何会提前结束」。
- [ ] 能区分 `result.all_messages()` 与 `result.new_messages()`，并知道续接历史该用哪个。
- [ ] 能写出一个 `output_type` 为 Pydantic 模型的 Agent，并断言 `result.output` 是该校验实例。
- [ ] 能说出 `ModelRetry` 是控制流、默认输出重试预算为 1、耗尽后抛 `UnexpectedModelBehavior`。
- [ ] 能解释 `instructions` 与 `system_prompt` 的落点差异。
- [ ] 能说出为什么业务代码应返回结构化对象而不是 `print(chat)`。

---

## 10. 常见坑与排错

| 现象 | 原因 | 解决 |
|------|------|------|
| `TypeError: 'RunUsage' object is not callable` | 把 `result.usage` 当方法调用 | 2.x 里 `usage` 是**属性**，写 `result.usage` |
| `UserError: 'model' must either be set...` | `Agent()` 未给 `model`，运行时也没给 | 构造时传 `'provider:model'`，或 `run(model=...)` |
| 在 async 函数里调用 `run_sync` 报错/挂起 | `run_sync` 不能在事件循环里用 | 异步上下文改用 `await agent.run(...)` |
| `result.output` 是字符串而不是对象 | 忘了 `output_type=`，默认是 `str` | 构造时显式 `output_type=MyModel` |
| 流式拿到一半就结束、后续工具没跑 | `run_stream` 以首个匹配输出为终点 | 需要跑完工具就用 `run`；流式只做展示 |
| `FunctionModel` 返回字符串导致行为异常 | 2.x 要求返回 `ModelResponse` | 统一 `ModelResponse(parts=[TextPart(content=...)])` |
| 从 `messages` 取用户文本报 `AttributeError` | 消息是判别联合，需先判类型 | 先 `isinstance(message, ModelRequest)` 再取 `UserPromptPart` |
| 续接对话后 `len(new_messages())` 比预期大 | 误把 `all_messages()` 当成本轮新增 | 本轮新增用 `new_messages()` |
| 模型标识拼错，运行才报 | 字符串模型是延迟解析 | 生产环境尽早跑一次冒烟；或构造时 `defer_model_check=False` 提前校验 |

---

## 11. 面试延伸

**Q1：`run` / `run_sync` / `run_stream` 该怎么选？为什么 `run_sync` 不能在异步里调用？**
> 要点：按「同步/异步」×「一次性/流式」选；`run_sync` 通过「跑完一个事件循环」实现，在已有事件循环中嵌套会冲突，异步上下文一律用 `run`；流式接口用于低延迟展示，且在首个匹配输出处提前结束。

**Q2：`result.output` 和 `print(chat)` 的本质区别是什么？**
> 要点：`result.output` 是被 `output_type` 约束并校验过的类型化值（结构化时是 Pydantic 实例），可断言、可入库、可组合、类型安全；`print` 只产出不可复用的字符串，无法回归测试。

**Q3：`all_messages()` 与 `new_messages()` 的区别？**
> 要点：前者是截至目前的完整历史（含传入的 `message_history`），后者只含本次运行新增；续接对话用前者，增量持久化/事件投递用后者。

**Q4：为什么推荐 `'provider:model'` 字符串而不是直接传模型实例？**
> 要点：延迟解析、不导入 provider SDK、配置可外部化、多 provider 切换成本低、可序列化进 Spec；代价是拼错要到运行时才发现。需要自定义 client/base_url 时才传实例。

**Q5：模型输出不合法时会发生什么？**
> 要点：触发 `ModelRetry`（控制流异常），框架把校验反馈作为提示重新请求模型；预算由 `retries={'output': N}`（默认 1）控制，耗尽后抛 `UnexpectedModelBehavior`。输出校验器的细节在 2.4。

---

## 12. 延伸阅读

- 源码篇·核心 Agent 循环：[`../../code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md)（`Agent` 构造参数、运行方法矩阵、结果对象、异常体系、重试与用量限制）。
- 源码篇·消息与输出：[`../../code_wiki/04-messages-and-output.md`](../../code_wiki/04-messages-and-output.md)（`ModelRequest` / `ModelResponse`、part 联合、`OutputSchema` 与输出工具、校验与重试）。
- 上一章：**1.3 Pydantic v2 精要**——结构化输出的地基。
- 下一章：**2.2 消息协议与历史**——把 `all_messages()` / `new_messages()` 背后的 `ModelMessage`、parts 与历史维护讲透。
