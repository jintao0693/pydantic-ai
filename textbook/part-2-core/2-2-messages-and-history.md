# 2.2 消息协议与历史：ModelMessage、parts、历史维护

> 篇二 · Pydantic AI 核心 · 配套 labs：`labs/part_2/ch_2_2.py`、`labs/part_2/test_ch_2_2.py`

## 1. 本课目标

1. 说清 **`ModelMessage` 判别联合**：`ModelRequest` 与 `ModelResponse` 各自代表谁说的话、有哪些字段、分别承载哪些 part。
2. 记住**请求 part** 与**响应 part** 的关键类型（`SystemPromptPart` / `UserPromptPart` / `ToolReturnPart` / `RetryPromptPart`；`TextPart` / `ThinkingPart` / `ToolCallPart` …）及其 `part_kind`。
3. 用 `message_history=` 续接多轮对话，并解释 **`result.all_messages()` 与 `result.new_messages()` 的区别**，说清为什么生产代码要用 `new_messages()` 追加。
4. 会**手工构造消息**：`ModelRequest.user_text_prompt(...)` 与 `ModelResponse(parts=[...])`，并理解何时需要这么做。
5. 会用 `all_messages_json()` + `ModelMessagesTypeAdapter` 做**无损序列化往返**，并说明持久化历史为什么必须走它。
6. 知道 `sanitize_messages` / `repair_messages` 各自解决什么问题，并能举出「中断的 tool call」「客户端伪造的前导 system prompt」两个真实场景。

## 2. 前置知识

- 完成本章前置：2.1（`Agent` / `run` / `run_sync` / `TestModel` 的基本用法）。
- 会读写 Pydantic v2 的基础用法（`BaseModel`、`TypeAdapter`、判别式联合），篇一 1.3 已覆盖。
- 理解 1.1 里的「上下文 = 消息列表」心智模型：**所谓续接对话，就是把上一轮的消息列表再传进去**。
- 不需要 API Key：本章 labs 全部离线（`TestModel` / `FunctionModel`）。

## 3. 为什么需要它

### 3.1 LLM 是无状态的：历史必须你显式带

一次 `agent.run_sync("我叫 Alice")` 结束后，模型**什么都不会记得**——它没有服务器端会话，每次调用都是一次全新的前向计算。想让第二轮知道「我叫 Alice」，唯一办法是把第一轮的消息列表原样带上：

```python
first = agent.run_sync("我叫 Alice")
second = agent.run_sync("我叫什么？", message_history=first.new_messages())
```

这里有两个必须搞清楚的问题，否则线上一定踩坑：

1. **带哪一段？** `result.all_messages()` 与 `result.new_messages()` 在多轮里并不相等，传错会重复或丢历史。
2. **怎么存？** 历史要落库、要跨进程、要跨版本，必须能**无损**序列化再读回——这就是 `ModelMessagesTypeAdapter` 的职责。

### 3.2 消息协议是框架的「交换格式」

Pydantic AI 的 `messages.py` 定义了一套**规范化消息协议**，它是整个框架对外的公共契约：provider 适配（OpenAI/Anthropic/Google…）、UI 适配（AG-UI/Vercel）、durable 包装（Temporal/DBOS）、历史持久化，全都以这套消息类型作为往返格式。

这意味着：**只要你能正确地构造、读写 `ModelMessage`，你就能把 Pydantic AI 接进任何系统**。反过来，如果你误以为「历史就是一串字符串」，就会在工具调用、多模态、thinking、重试这些场景下丢掉结构信息。本章就是把这套协议讲透。

## 4. 核心概念

### 4.1 顶层：`ModelMessage` 是一个判别联合

```python
ModelMessage = Annotated[ModelRequest | ModelResponse, pydantic.Discriminator('kind')]  # messages.py

ModelMessagesTypeAdapter = pydantic.TypeAdapter(
    list[ModelMessage],
    config=pydantic.ConfigDict(defer_build=True, ser_json_bytes='base64', val_json_bytes='base64'),
)
```

- 只有两种顶层消息，靠 `kind` 字段判别：`ModelRequest.kind == 'request'`、`ModelResponse.kind == 'response'`。
- 一次运行产出的历史，是这两种消息**交替**出现的列表。

```text
[ ModelRequest(user-prompt)  … 用户说
, ModelResponse(text)        … 模型答
, ModelRequest(tool-return)  … 宿主回填工具结果
, ModelResponse(tool-call)   … 模型要求调工具
, ... ]
```

| 类型 | `kind` | 谁说的话 | 典型 part |
|------|--------|----------|-----------|
| `ModelRequest` | `'request'` | **发给模型**的输入（用户 / 宿主 / 系统） | `SystemPromptPart`、`UserPromptPart`、`ToolReturnPart`、`RetryPromptPart` |
| `ModelResponse` | `'response'` | **模型吐出**的输出 | `TextPart`、`ThinkingPart`、`ToolCallPart`、`FilePart` |

### 4.2 `ModelRequest`：发给模型的那一条

`ModelRequest` 是 `@dataclass(repr=False)`，关键字段：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `parts` | `Sequence[ModelRequestPart]` | — | 本条请求的 part 列表（用户输入、工具结果、系统提示…） |
| `timestamp` | `datetime \| None` | `None` | 反序列化历史时**不会**被填成「现在」，以保留原始时间 |
| `instructions` | `str \| None` | `None` | 由结构化 `instruction_parts` 渲染出来的指令文本 |
| `kind` | `Literal['request']` | `'request'` | 判别器 |
| `run_id` / `conversation_id` | `str \| None` | `None` | 运行 / 对话标识；`conversation_id` 跨 run 共享历史 |
| `metadata` | `dict[str, Any] \| None` | `None` | 不发给模型的旁路数据 |
| `state` | `ModelRequestState` | `'complete'` | `'complete' \| 'interrupted'`；中断时 `parts` 只含已收集到的工具结果 |

便捷构造器（本章 labs 用到）：`ModelRequest.user_text_prompt(user_prompt, *, instructions=None) -> ModelRequest`。

### 4.3 请求 part（`ModelRequestPart`）

| part | `part_kind` | 关键字段 | 用途 |
|------|-------------|----------|------|
| `SystemPromptPart` | `'system-prompt'` | `content: str`、`timestamp`、`dynamic_ref` | 系统提示（约束 / 人设） |
| `UserPromptPart` | `'user-prompt'` | `content: str \| Sequence[UserContent]`、`timestamp` | 用户输入（或多模态内容） |
| `ToolReturnPart` | `'tool-return'` | `tool_name`、`content`、`tool_call_id`、`outcome` | 工具执行结果（成功/失败都回填） |
| `RetryPromptPart` | `'retry-prompt'` | `content: list[ErrorDetails] \| str`、`tool_name`、`tool_call_id` | 校验/工具失败后的**重试提示** |
| `SpeechPart` | `'speech'` | `speaker`、`transcript`、`audio` | realtime 语音（请求侧 `speaker='user'`） |

> 注意：`InstructionPart`（`part_kind='instruction'`）**不在** `ModelRequestPart` 联合里。它挂在 `ModelRequestParameters` 上，`ModelRequest.instructions` 是从它渲染出的文本。你手工构造请求时，通常不需要操心它。

### 4.4 响应 part（`ModelResponsePart`）

| part | `part_kind` | 关键字段 | 用途 |
|------|-------------|----------|------|
| `TextPart` | `'text'` | `content: str`、`id` | 普通文本 |
| `ThinkingPart` | `'thinking'` | `content`、`signature` | 思维链 / 推理内容 |
| `ToolCallPart` | `'tool-call'` | `tool_name`、`args`、`tool_call_id` | 模型要求调用工具 |
| `NativeToolCallPart` | `'builtin-tool-call'` | 同 `tool_name`/`args` | provider 原生工具调用 |
| `FilePart` | `'file'` | `content: BinaryContent` | 模型产出的文件/图片 |
| `CompactionPart` | `'compaction'` | `content` | 上下文压缩标记 |

`ModelResponse` 还提供一组只读便捷属性：`text`（拼接所有 `TextPart`）、`thinking`、`tool_calls`、`files`、`images` 等。labs 里就用了 `manual_response.text`。

### 4.5 会话历史：`all_messages()` vs `new_messages()`

这是本章最容易出错、也最该记牢的一个点。两个方法都定义在结果对象（`AgentRunResult` / `StreamedRunResult`）上：

| 方法 | 返回 | 语义 |
|------|------|------|
| `result.all_messages()` | `list[ModelMessage]` | **完整历史**：`message_history` 传进来的 + 本次运行新增的 |
| `result.new_messages()` | `list[ModelMessage]` | **仅本次运行的增量**：等于 `all_messages()[len(输入历史):]` |

对照 labs 的实测（`TestModel` / `FunctionModel` 均如此）：

```text
第一轮：all = 2 条，new = 2 条      # 无历史传入，两者相等
第二轮：all = 4 条，new = 2 条      # 传入了 2 条历史，增量仍是 2 条
```

**为什么多轮对话要用 `new_messages()` 追加？**

```python
# ✅ 正确：每轮只追加「本轮新增」，历史不重复
history: list[ModelMessage] = []
r1 = agent.run_sync("我叫 Alice", message_history=history)
history += r1.new_messages()          # history: 2 条
r2 = agent.run_sync("我叫什么？", message_history=history)
history += r2.new_messages()          # history: 4 条

# ❌ 反模式：把 all_messages() 当作「新增」再 += 到 history
history += r2.all_messages()          # history 变成 2 + 4 = 6 条，前 2 条被重复！
```

`all_messages()` 在**只有一轮**时等于 `new_messages()`，所以单轮 demo 看不出区别；一旦进入第二轮，`all_messages()` 里已经包含了历史，再 `+=` 就重复了。重复历史轻则多花 token，重则触发部分 provider 的「消息结构非法」。

> 补充：`conversation_id` 是另一条路径——把 `conversation_id` 传给 `run` 可以跨 run 关联同一段对话（映射到 OTel 的 `gen_ai.conversation.id`）。但**历史本身仍由你负责持久化和回传**，`conversation_id` 不会替你保存消息。

### 4.6 手工构造消息：什么时候需要

`message_history` 接受的是 `Sequence[ModelMessage]`。绝大多数历史来自 `result.new_messages()`，但以下场景需要你**手工造**：

- 从数据库/日志里**还原**历史（读回 JSON → 反序列化，见 4.7）；
- **测试**：构造确定性的输入历史，绕过真实模型；
- **迁移/改写**：注入一条「已知事实」、冷启动会话；
- **拼接外部来源**：把别处产生的消息并入本会话。

labs 演示了两个构造器：

```python
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart

req = ModelRequest.user_text_prompt("你好呀")           # 等价 ModelRequest(parts=[UserPromptPart("你好呀")])
resp = ModelResponse(parts=[TextPart("你好，很高兴见到你。")])
```

注意：直接 `ModelRequest(parts=[...])` 也可以，但要**自己选对 part 类型**——用户输入是 `UserPromptPart`，不是 `TextPart`（`TextPart` 属于响应侧）。

### 4.7 序列化：持久化历史的唯一正道

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter

raw = result.all_messages_json()                       # 一键序列化为 bytes
history = ModelMessagesTypeAdapter.validate_json(raw)  # 反序列化回 list[ModelMessage]
assert history == list(result.all_messages())          # 无损往返
```

或者对称地：

```python
raw = ModelMessagesTypeAdapter.dump_json(messages)     # list[ModelMessage] -> bytes
messages = ModelMessagesTypeAdapter.validate_json(raw) # bytes -> list[ModelMessage]
```

**为什么持久化一定要用它，而不是 `json.dumps(messages)`？**

1. `ModelMessage` 是**判别联合**：`ModelMessagesTypeAdapter` 知道读 `kind` 与 `part_kind` 来决定重建哪个类。裸 `json.dumps` 只会得到一堆 dict，读回时你无法可靠地还原 `ModelRequest` vs `ModelResponse`、`TextPart` vs `ThinkingPart`。
2. **二进制内容**：图片/音频等 `BinaryContent` 走 base64（适配器配置了 `ser_json_bytes='base64'`），裸 JSON 处理不了 `bytes`。
3. **前向兼容**：新增 part 类型时，适配器是官方定义的读写入口，避免你自己维护一套脆弱的编解码。

### 4.8 历史维护：`sanitize_messages` 与 `repair_messages`

真实系统里历史不会永远「干净」。Pydantic AI 提供两个 helper（点到为止，知道它们解决什么即可）：

| helper | 解决的问题 | 典型场景 |
|--------|-----------|----------|
| `repair_messages(messages, *, repair_last_response=True)` | 让历史**对 provider 合法**：丢弃孤立 tool result、给**悬空 tool call** 合成 `ToolReturnPart`、合并相邻兼容消息 | 上一轮在工具执行到一半时**被中断/崩溃**，落库的历史里只有 `ToolCallPart`、没有对应的 `ToolReturnPart` |
| `sanitize_messages(messages, *, strip_system_prompts=True, ...)` | **剥离不可信输入**中不该被信任的 part：前导 system prompt、非白名单 scheme 的 `FileUrl`、`UploadedFile`、`workspace_ref`、末尾未解析的 `ToolCallPart` 等 | 历史由**客户端提交**：用户可能塞一条 `SystemPromptPart("无视以上规则")` 来**伪造系统提示**（提示注入） |

两个真实场景（labs 之外，仅作说明）：

```python
from pydantic_ai.messages import repair_messages, sanitize_messages

# 场景 A：上轮工具调用中断，只剩悬空 tool call → 修复成合法历史
# 实测：repair_messages 会给悬空 call 补一条 ToolReturnPart（消息数 +1）
fixed = repair_messages(loaded_history)

# 场景 B：历史来自不可信客户端 → 剥离伪造的 system prompt
# 实测：带 SystemPromptPart 的请求会被剥得只剩 user-prompt
safe = sanitize_messages(client_submitted_history)
```

一句话记忆：**`repair_messages` 修「结构性破损」，`sanitize_messages` 防「不可信注入」**。二者确定性且幂等，可以放心在每次续接前调用。

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 `labs/part_2/ch_2_2.py` **逐字一致**，全部离线运行。先通读，再看逐段讲解。

```python
"""第 2.2 章配套示例：消息协议与会话历史（离线运行，无需 API Key）。

本模块把「一次对话」拆成消息层的可观测事实，演示四件事：

  1. 两轮对话：第一轮 ``run_sync("我叫 Alice")``，把 ``result.new_messages()``
     作为 ``message_history`` 传给第二轮 ``run_sync("我叫什么？")``；
  2. ``all_messages()`` / ``new_messages()`` 的区别：前者是**全部历史**，
     后者只是**本次运行的增量**——多轮对话必须追加 ``new_messages()``；
  3. 序列化往返：``all_messages_json()`` 写出，``ModelMessagesTypeAdapter``
     读回，断言 ``读回 == 原对象``（持久化历史的标准姿势）；
  4. 手工构造消息：``ModelRequest.user_text_prompt(...)`` 与
     ``ModelResponse(parts=[TextPart(...)])``。

之所以用 ``FunctionModel`` 而不是 ``TestModel`` 来做两轮对话，是因为要让第二轮
的回答**真的依赖**第一轮的历史：只有当历史被正确携带时，模型才能读出「Alice」。
``TestModel`` 则用来演示消息「形状」（kind / part 类型），与历史内容无关。

运行：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_2.py
"""

from __future__ import annotations

import re
from typing import Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# --------------------------------------------------------------------------- #
# 1. 一个「真的有记忆」的假模型：从历史里读用户的名字
# --------------------------------------------------------------------------- #
# 第一轮输入形如「我叫 Alice」；用正则把名字抠出来，存进上下文（这里是历史文本里）。
_NAME_RE = re.compile(r"我叫\s*([A-Za-z\u4e00-\u9fff]+)")


def user_texts(messages: Sequence[ModelMessage]) -> list[str]:
    """把所有 ``ModelRequest`` 里的 ``UserPromptPart`` 文本按顺序取出来。

    这就是「模型从历史里读用户输入」的读取侧——它遍历的是**全部**消息，
    包括本轮新加的与 ``message_history`` 带进来的。
    """
    texts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    texts.append(part.content if isinstance(part.content, str) else "[多模态内容]")
    return texts


def memory_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """确定性模型：用户问「我叫什么」时，从历史里翻出此前记录的名字。

    若历史没被携带，这里就找不到名字，只能回答「不知道」——第二轮的回答
    因此成了「历史是否真的传过去了」的可观测证据。
    """
    joined = "\n".join(user_texts(messages))
    name_match = _NAME_RE.search(joined)

    if "我叫什么" in joined:
        name = name_match.group(1) if name_match else "不知道"
        return ModelResponse(parts=[TextPart(f"你叫 {name}。")])
    if name_match:
        return ModelResponse(parts=[TextPart(f"记住了，{name_match.group(1)}。")])
    return ModelResponse(parts=[TextPart("好的。")])


def build_agent() -> Agent[None, str]:
    """构造一个带记忆的离线 agent（``FunctionModel`` 无网络请求）。"""
    return Agent(FunctionModel(memory_model))


def build_plain_agent() -> Agent[None, str]:
    """构造一个纯 ``TestModel`` agent，用来观察消息的「形状」而非内容。"""
    return Agent(TestModel())


# --------------------------------------------------------------------------- #
# 2. 两轮对话：new_messages() 作为下一轮的 message_history
# --------------------------------------------------------------------------- #
def two_turn_conversation(agent: Agent[None, str]) -> tuple[object, object]:
    """跑两轮对话，返回 ``(第一轮结果, 第二轮结果)``。

    - 第一轮：``run_sync("我叫 Alice")``；
    - 第二轮：把第一轮的 ``new_messages()`` 作为 ``message_history`` 传入。
    """
    first = agent.run_sync("我叫 Alice")
    second = agent.run_sync("我叫什么？", message_history=first.new_messages())
    return first, second


# --------------------------------------------------------------------------- #
# 3. 序列化往返：all_messages_json() ↔ ModelMessagesTypeAdapter
# --------------------------------------------------------------------------- #
def roundtrip_json(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """把消息列表序列化成 JSON 再读回，返回反序列化结果。

    ``ModelMessagesTypeAdapter`` 是解码 ``ModelMessage`` 判别联合的**公开**入口；
    它知道 ``kind='request' | 'response'`` 与每个 part 的 ``part_kind``，能在
    读回时重建正确的类型（包括多模态内容）。
    """
    raw = ModelMessagesTypeAdapter.dump_json(list(messages))
    return ModelMessagesTypeAdapter.validate_json(raw)


# --------------------------------------------------------------------------- #
# 4. 手工构造消息
# --------------------------------------------------------------------------- #
def make_manual_request(text: str) -> ModelRequest:
    """手工构造一条「用户说了一句话」的请求消息。

    ``ModelRequest.user_text_prompt`` 是官方便捷构造器：等价于
    ``ModelRequest(parts=[UserPromptPart(text)])``，但不必自己拼 part。
    """
    return ModelRequest.user_text_prompt(text)


def make_manual_response(text: str) -> ModelResponse:
    """手工构造一条「模型回了文本」的响应消息。"""
    return ModelResponse(parts=[TextPart(text)])


# --------------------------------------------------------------------------- #
# 5. 打印辅助：把消息的形状摊平给人看
# --------------------------------------------------------------------------- #
def part_label(part: object) -> str:
    """返回 ``part_kind``，若带字符串 ``content`` 则附上内容预览。"""
    kind = getattr(part, "part_kind")
    content = getattr(part, "content", None)
    if isinstance(content, str):
        return f"{kind}({content!r})"
    return f"{kind}"


def message_summary(messages: Sequence[ModelMessage]) -> list[tuple[str, list[str]]]:
    """返回 ``[(kind, [part_kind, ...]), ...]``，供打印与断言共用。"""
    return [(message.kind, [part.part_kind for part in message.parts]) for message in messages]


def print_messages(title: str, messages: Sequence[ModelMessage]) -> None:
    print(f"  {title}（共 {len(messages)} 条）：")
    for index, message in enumerate(messages, start=1):
        parts = " + ".join(part_label(part) for part in message.parts)
        print(f"    [{index}] {message.kind:<8} {parts}")


# --------------------------------------------------------------------------- #
# 6. 演示
# --------------------------------------------------------------------------- #
def main() -> None:
    agent = build_agent()

    print("=" * 74)
    print("① 两轮对话：第一轮 new_messages() 作为第二轮 message_history")
    print("=" * 74)
    first, second = two_turn_conversation(agent)
    print(f"第一轮输出：{first.output!r}")
    print_messages("第一轮 all_messages()", first.all_messages())
    print_messages("第一轮 new_messages()", first.new_messages())
    print(f"第二轮输出：{second.output!r}")
    print_messages("第二轮 all_messages()", second.all_messages())
    print_messages("第二轮 new_messages()", second.new_messages())
    print(
        f"\n  结论：第一轮 all/new 都是 {len(first.all_messages())} 条；"
        f"第二轮 all={len(second.all_messages())} 条（历史在增长），"
        f"new={len(second.new_messages())} 条（只是增量）。"
    )
    assert second.output == "你叫 Alice。", "历史没被带到第二轮——模型读不到名字"
    print("  第二轮能回答出「Alice」，证明历史确实被携带。")

    print()
    print("=" * 74)
    print("② 消息形状：TestModel 走一轮，看 kind 与 part 类型")
    print("=" * 74)
    plain = build_plain_agent().run_sync("你好")
    print_messages("all_messages()", plain.all_messages())
    print(f"  形状摘要：{message_summary(plain.all_messages())}")

    print()
    print("=" * 74)
    print("③ 序列化往返：all_messages_json() ↔ ModelMessagesTypeAdapter")
    print("=" * 74)
    raw = second.all_messages_json()
    restored = ModelMessagesTypeAdapter.validate_json(raw)
    print(f"  all_messages_json() 字节数：{len(raw)}")
    print(f"  读回后消息条数：{len(restored)}")
    print(f"  往返一致（对象相等）：{restored == list(second.all_messages())}")
    assert restored == list(second.all_messages())

    print()
    print("=" * 74)
    print("④ 手工构造消息：user_text_prompt / ModelResponse(parts=[...])")
    print("=" * 74)
    manual_request = make_manual_request("你好呀")
    manual_response = make_manual_response("你好，很高兴见到你。")
    print(f"  手工 ModelRequest.parts：{message_summary([manual_request])}")
    print(f"  手工 ModelResponse.text：{manual_response.text!r}")
    # 手工构造的 request 可以直接当作历史喂给 agent（此处不再传 user_prompt，
    # 即「无新输入、直接续接历史」）。
    resumed = agent.run_sync(message_history=[manual_request])
    print(f"  以手工 request 为历史的运行输出：{resumed.output!r}")
    print(f"  该次运行消息条数：{len(resumed.all_messages())}")
    print("\n全部演示通过。")


if __name__ == "__main__":
    main()
```

### 5.1 逐段讲解

**Part 1 · 有记忆的假模型**：`user_texts` 遍历**全部** `ModelMessage`，把所有 `ModelRequest` 里的 `UserPromptPart.content` 收集起来——这模拟了「模型在上下文里读历史」。`memory_model` 据此回答：看到「我叫什么」就从历史里翻名字。关键点：模型看到的 `messages` 是 `message_history` + 本轮输入的**合并结果**，所以历史有没有传对，一眼能从第二轮回答看出来。

**Part 2 · 两轮对话**：`two_turn_conversation` 是本章的核心两行——第一轮 `run_sync("我叫 Alice")`，第二轮把 `first.new_messages()` 作为 `message_history`。传 `new_messages()`（而非 `all_messages()`）在第一轮里无所谓，但它是**可扩展到 N 轮**的正确写法。

**Part 3 · 序列化**：`roundtrip_json` 用 `ModelMessagesTypeAdapter.dump_json` 写出、`validate_json` 读回。`all_messages_json()` 是它的便捷封装（对 `all_messages()` 调 `dump_json`）。

**Part 4 · 手工构造**：`make_manual_request` / `make_manual_response` 演示两个构造入口。注意 `user_text_prompt` 造出来的是 **request**（用户侧），`ModelResponse(parts=[TextPart(...)])` 造出来的是 **response**（模型侧）——part 类型用错会得到语义错误的消息。

**Part 5 · 打印辅助**：`message_summary` 返回 `[(kind, [part_kind, ...]), ...]`，这是观察消息协议最直观的「形状摘要」，测试里也直接复用它做断言。

**Part 6 · 演示**：四段输出对应四个知识点。运行后会看到「第二轮 all=4 / new=2」以及「往返一致：True」。

运行结果（节选）：

```text
① 两轮对话：第一轮 new_messages() 作为第二轮 message_history
第一轮输出：'记住了，Alice。'
  第一轮 all_messages()（共 2 条）：
    [1] request  user-prompt('我叫 Alice')
    [2] response text('记住了，Alice。')
第二轮输出：'你叫 Alice。'
  第二轮 all_messages()（共 4 条）：
    [1] request  user-prompt('我叫 Alice')
    [2] response text('记住了，Alice。')
    [3] request  user-prompt('我叫什么？')
    [4] response text('你叫 Alice。')
  第二轮 new_messages()（共 2 条）：
    [1] request  user-prompt('我叫什么？')
    [2] response text('你叫 Alice。')

③ 序列化往返：all_messages_json() ↔ ModelMessagesTypeAdapter
  读回后消息条数：4
  往返一致（对象相等）：True
```

## 6. 深入剖析

### 6.1 `message_history` 到底做了什么

`run(..., message_history=...)` 会把传入的消息**前置**到本次运行的内部历史中（`GraphAgentState.message_history`），再在其后追加本轮由 `UserPromptNode` 组装的 `ModelRequest`（含 system prompt 与 `UserPromptPart`）。因此：

- 模型实际收到的 = `message_history` + 本轮的 system prompt + 本轮 user prompt。
- `result.all_messages()` = 这个合并后的完整列表。
- `result.new_messages()` = 只截取「本轮新增」的那一段。

这也解释了为什么 `conversation_id` 不会替你保存历史：它只是标识，历史仍靠 `message_history` 传入。

### 6.2 `new_messages()` 的实现语义

`AgentRunResult.new_messages()` 内部记录了一个 `_new_message_index`——运行开始时历史长度。返回的就是 `all_messages()[_new_message_index:]`。所以：

| 场景 | `all_messages()` | `new_messages()` |
|------|------------------|------------------|
| 单轮、无历史 | 2 条 | 2 条 |
| 第二轮（传入 2 条历史） | 4 条 | 2 条 |
| N 轮累积调用 | N×2 条 | 2 条（每轮） |

**工程结论**：维护一个 `history: list[ModelMessage]`，每轮 `history += result.new_messages()`，这是唯一在 N 轮下都正确的模式。

### 6.3 `ModelMessagesTypeAdapter` 的往返保证

- **Python 模式**（`dump_python` / `validate_python`）：对象级往返，`restored == original` 成立。
- **JSON 模式**（`dump_json` / `validate_json`）：字节级往返，且在 labs 中实测 `dump_json(validate_json(raw)) == raw`，字节完全一致。
- 配置里 `ser_json_bytes='base64'` / `val_json_bytes='base64'` 保证 `BinaryContent` 等二进制内容可安全落盘。

`all_messages_json()` / `new_messages_json()` 就是「对列表调 `dump_json`」的便捷封装，用于把历史直接写进数据库的 `TEXT`/`BLOB` 字段。

### 6.4 `ModelRequest.user_text_prompt` 与「无新输入续接」

`ModelRequest.user_text_prompt(text)` 构造 `ModelRequest(parts=[UserPromptPart(text)])`。labs 演示了 `agent.run_sync(message_history=[manual_request])`——**不传 `user_prompt`**，直接以历史收尾。此时 `UserPromptNode` 会把历史最后一条 `ModelRequest` 弹出复用，等价于「把这一句当成用户输入再问一遍」。这是冷启动 / 回放测试的常用手法。

### 6.5 为什么 `ModelMessage` 不能用于 `isinstance`

`ModelMessage` 是 `Annotated[ModelRequest | ModelResponse, Discriminator('kind')]`，是**类型别名**而非类。直接 `isinstance(m, ModelMessage)` 会抛 `TypeError: Subscripted generics cannot be used with class and instance checks`（labs 测试里就踩过并改成了 `(ModelRequest, ModelResponse)`）。正确的窄化方式：`isinstance(m, ModelRequest)` / `isinstance(m, ModelResponse)`，或读 `m.kind`。

## 7. 常见变体与工程实践

- **多轮会话的标准循环**：维护一个 `history` 列表；每轮 `history += result.new_messages()`；持久化时用 `ModelMessagesTypeAdapter.dump_json(history)` 落库；恢复时 `validate_json` 读回。
- **只存增量、读时拼接**：把每轮的 `new_messages_json()` 分片存库，恢复时按时间顺序拼成一个列表再读回；便于审计与裁剪。
- **历史裁剪 / 压缩**：长会话要限 token——可保留「最近 N 条 + 首条 system」，或使用框架的上下文压缩能力（`CompactionPart`）。
- **可信边界**：任何来自客户端的历史，续接前先 `sanitize_messages`；任何可能破损的历史先 `repair_messages`。
- **多模态历史**：`UserPromptPart.content` 可以是内容序列（图片、文档等），序列化时依赖适配器的 base64 配置，务必用 `ModelMessagesTypeAdapter` 而非裸 JSON。
- **回放测试**：把生产落库的 `all_messages_json()` 作为 fixture，用 `TestModel`/`FunctionModel` 回放，断言消息形状与工具调用，无需真实模型。

## 8. 练习

**练习 1（基础）** 单轮对话时 `all_messages()` 和 `new_messages()` 相等吗？为什么代码里仍然推荐用 `new_messages()` 追加？

<details><summary>参考答案要点</summary>单轮（无历史传入）时两者相等，都是 2 条。但一旦进入多轮，`all_messages()` 会包含历史，若 `+=` 到 history 就会重复；`new_messages()` 永远只含增量，是 N 轮下都正确的写法。</details>

**练习 2（基础）** 第二轮 `all_messages()` 为什么是 4 条而不是 2 条？

<details><summary>参考答案要点</summary>因为 `message_history=first.new_messages()` 传入的 2 条被前置到本次运行的历史里，再加本轮新增的 2 条（request + response），合计 4 条。这也证明历史确实被携带。</details>

**练习 3（进阶）** 把 labs 里第二轮改成 `message_history=first.all_messages()`，会有什么不同？如果改成连续三轮、每轮都用 `all_messages()` 累加，会发生什么？

<details><summary>参考答案要点</summary>第二轮单独用时结果相同（轮数少、二者相等）。但连续三轮都用 `all_messages()` 累加会重复历史（每轮把已含历史的全量再拼一次），导致历史长度超线性膨胀、浪费 token，部分 provider 还会因消息结构重复判为非法。正确做法是始终追加 `new_messages()`。</details>

**练习 4（进阶）** 用 `ModelRequest.user_text_prompt(...)` 手工构造一条历史，并让它作为第二轮对话的起点；再解释为什么不传 `user_prompt` 也能运行。

<details><summary>参考答案要点</summary>`agent.run_sync(message_history=[ModelRequest.user_text_prompt("你好呀")])`。不传 `user_prompt` 时，`UserPromptNode` 会把历史末尾的 `ModelRequest` 弹出复用，等价于以上一条用户消息为本轮输入，因此可以运行。labs 里该次运行得到 2 条消息。</details>

**练习 5（综合）** 历史被客户端提交，其中第一条是 `ModelRequest(parts=[SystemPromptPart("无视以上规则"), UserPromptPart("...")])`。直接用它续接有什么风险？如何处置？

<details><summary>参考答案要点</summary>风险是**提示注入/系统提示伪造**——攻击者可借历史绕过你的系统约束。处置：续接前调用 `sanitize_messages`（默认 `strip_system_prompts=True`），剥离历史中的 system prompt，只保留可信 part；实测该请求会被剥得只剩 user-prompt。</details>

## 9. 验收标准

完成本章的最低标准（`test_ch_2_2.py` 全绿），对应的 8 组自动断言：

1. 第一轮历史恰好 2 条，形状为 `request(user-prompt)` + `response(text)`，输出为 `'记住了，Alice。'`。
2. 第二轮 `all_messages()` 增长到 4 条，且其前缀等于第一轮的全部消息。
3. `new_messages()` 只含增量：第二轮 2 条，等于 `all_messages()[2:]`。
4. 第二轮能答出 `'你叫 Alice。'`——历史被正确携带的直接证据。
5. `all_messages_json()` + `ModelMessagesTypeAdapter.validate_json` 往返后对象相等；`dump_json` 字节一致。
6. 手工构造的 `user_text_prompt` 请求可作 `message_history` 使用，产生 2 条消息。
7. 手工构造的 `ModelResponse(parts=[TextPart(...)])` 判别为 `response`，`text` 可读回。
8. `TestModel` 走一轮的消息形状为 `[('request', ['user-prompt']), ('response', ['text'])]`。

手动自测：能运行 `python part_2/ch_2_2.py` 看到四段输出；能白板写出「维护 history 并每轮追加 `new_messages()`」的循环；能解释为什么持久化必须用 `ModelMessagesTypeAdapter`。

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_2.py -q
```

## 10. 常见坑与排错

| 现象 | 原因 | 修复 |
|------|------|------|
| 多轮后历史长度爆炸 | 每轮把 `all_messages()` 当增量累加 | 一律追加 `new_messages()` |
| 第二轮「失忆」 | 忘传 / 传空了 `message_history` | 确认上一轮 `new_messages()` 已回传 |
| `TypeError: Subscripted generics cannot be used with class and instance checks` | 对 `ModelMessage` 用 `isinstance` | 改用 `isinstance(m, (ModelRequest, ModelResponse))` 或读 `m.kind` |
| 反序列化后类型全变成 dict | 用裸 `json.loads` 而非适配器 | 用 `ModelMessagesTypeAdapter.validate_json` |
| 图片/音频落库报错 | 裸 JSON 无法编码 `bytes` | 走适配器（内置 base64 配置） |
| provider 报「消息结构非法」 | 悬空 tool call / 历史被截断 | 续接前 `repair_messages` |
| 系统提示被用户覆盖 | 客户端提交的历史里夹带 `SystemPromptPart` | 续接前 `sanitize_messages` |
| `timestamp` 反序列化后变成「现在」 | 误以为时间戳会重算 / 自己重造消息 | 反序列化保留原 `timestamp`；不要手工覆盖 |

**labs 专属排错**：`test_second_turn_reads_name_from_history` 失败 → 检查 `two_turn_conversation` 是否把 `first.new_messages()`（而非 `first.all_messages()` 之外的其它东西）传给了第二轮，以及 `memory_model` 是否读取了**全部** `messages`；`test_all_messages_json_roundtrip_is_lossless` 失败 → 确认用的是 `ModelMessagesTypeAdapter` 而非 `json` 模块。

## 11. 面试延伸

**Q1. Pydantic AI 为什么要自建一套 `ModelMessage` 协议？直接用各家 SDK 的消息格式不行吗？**

要点：需要一个 **provider 无关的规范化中间层**。入参归一（不同 provider 的 HTTP 格式差异被适配器吸收）、出参归一（`ModelResponse` 统一形状）、可序列化往返（持久化 / UI 交换 / durable 恢复都靠它）、可跨版本迁移。直接用各家 SDK 格式会把 provider 细节泄漏到业务层，工具调用、thinking、多模态、重试都会各自为政。

**Q2. `all_messages()` 和 `new_messages()` 有什么区别？多轮对话该用哪个？**

要点：`all_messages()` 是完整历史（含传入的 `message_history`），`new_messages()` 只是本次运行的增量。多轮必须用 `new_messages()` 追加，否则重复历史导致 token 浪费甚至结构非法。实现上 `new_messages()` 用运行开始时的历史长度切片。

**Q3. 怎么把历史持久化到数据库？为什么不能用 `json.dumps`？**

要点：用 `ModelMessagesTypeAdapter.dump_json` / `validate_json`（或结果对象上的 `all_messages_json()`）。因为 `ModelMessage` 是判别联合，裸 JSON 读回时无法重建正确的类与 part 类型，且无法处理二进制内容（需 base64）。适配器还提供前向兼容。

**Q4. `repair_messages` 和 `sanitize_messages` 分别解决什么问题？各举一个场景。**

要点：`repair_messages` 让历史对 provider 合法——给悬空 tool call 补 `ToolReturnPart`、丢弃孤立 tool result、合并相邻消息；场景是「上一轮工具执行到一半被中断」。`sanitize_messages` 剥离不可信 part——system prompt、可疑 URL、`UploadedFile`、`workspace_ref` 等；场景是「历史由客户端提交，用户伪造 system prompt 做提示注入」。

**Q5. 手工构造消息有什么实际用途？构造时最容易犯什么错？**

要点：还原落库历史、测试构造确定性输入、冷启动/回放、拼接外部来源。最容易的错是 **part 类型错配**——用户输入必须用 `UserPromptPart`（属于 `ModelRequest`），模型输出用 `TextPart`（属于 `ModelResponse`）；此外要保证工具调用与结果成对相邻，否则 provider 报错。

## 12. 延伸阅读

- [04 · 消息协议与输出](../../code_wiki/04-messages-and-output.md)：`ModelRequest` / `ModelResponse` 全字段表、全部请求/响应 part、`tool_kind` 判别、`repair_messages` / `sanitize_messages` 的精确行为。
- [02 · 核心 Agent 循环](../../code_wiki/02-core-agent-loop.md)：`UserPromptNode` 如何组装 `ModelRequest`、`all_messages()` / `new_messages()` 在 `AgentRun` / `AgentRunResult` 上的定义、`GraphAgentState.message_history` 的角色。
- 上一章：2.1 第一个 Agent：run / run_sync / stream 与结构化输出。
- 下一章：2.3 工具与 Toolset：定义、校验、执行、重试、组合（`ToolCallPart` / `ToolReturnPart` / `RetryPromptPart` 的实际产生）。
