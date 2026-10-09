# 04 · 消息协议与输出

[messages.py](../pydantic_ai_slim/pydantic_ai/messages.py) 是 Pydantic AI 的**规范化消息协议**：provider 适配、UI 适配、durable 包装、持久化历史都以此为往返格式。[output.py](../pydantic_ai_slim/pydantic_ai/output.py) / [_output.py](../pydantic_ai_slim/pydantic_ai/_output.py) 则负责把「输出类型」翻译为模型可理解的形式（工具 / 原生 schema / prompt 模板 / 文本 / 图像）并校验其结果。

相关文件：[messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)、[output.py](../pydantic_ai_slim/pydantic_ai/output.py)、[_output.py](../pydantic_ai_slim/pydantic_ai/_output.py)、[tools.py](../pydantic_ai_slim/pydantic_ai/tools.py)。

---

## 1. 顶层消息类型

### 1.1 判别联合与适配器

```python
ModelMessage = Annotated[ModelRequest | ModelResponse, pydantic.Discriminator('kind')]  # L3045

ModelMessagesTypeAdapter = pydantic.TypeAdapter(
    list[ModelMessage],
    config=pydantic.ConfigDict(defer_build=True, ser_json_bytes='base64', val_json_bytes='base64'),
)  # L3049
```

| 类型 | 位置 | 说明 |
|------|------|------|
| `ModelMessage` | L3045 | `kind` 判别：`'request' \| 'response'` |
| `ModelRequestState` | L175 | `Literal['complete', 'interrupted']` |
| `ModelResponseState` | L154 | `Literal['complete', 'incomplete', 'suspended', 'interrupted']` |
| `FinishReason` | L141 | `Literal['stop', 'length', 'content_filter', 'tool_call', 'error']`（已归一化到 OTel 语义约定；**是否继续由 `state` 决定，不由 `finish_reason`**） |

### 1.2 `ModelRequest`（L2089-L2145）

`@dataclass(repr=False)`。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `parts` | `Sequence[ModelRequestPart]` | — | |
| `timestamp` | `datetime \| None` | `None` | 反序列化历史时**不会**被 default_factory 覆盖为当前时间 |
| `instructions` | `str \| None` | `None` | 由结构化 `instruction_parts` 渲染 |
| `kind` | `Literal['request']` | `'request'` | 判别器 |
| `run_id` | `str \| None` | `None` | |
| `conversation_id` | `str \| None` | `None` | 跨 run 共享历史；映射为 `gen_ai.conversation.id` |
| `metadata` | `dict[str, Any] \| None` | `None` | 不发给模型 |
| `state` | `ModelRequestState` | `'complete'` | `'interrupted'` 时 `parts` 只含已收集到的 tool returns |

`__post_init__` 校验：`SpeechPart` 的 `speaker` 必须是 `'user'`（否则抛 `ValueError`，以便 Pydantic 反序列化时变成带定位的 `ValidationError`）。类方法 `user_text_prompt(user_prompt, *, instructions=None) -> ModelRequest`。

### 1.3 `ModelResponse`（L2811-L3024）

`@dataclass(repr=False)`。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `parts` | `Sequence[ModelResponsePart]` | — | |
| `usage` | `RequestUsage` | `RequestUsage()` | |
| `model_name` | `str \| None` | `None` | |
| `timestamp` | `datetime` | `_now_utc()` | |
| `kind` | `Literal['response']` | `'response'` | |
| `provider_name` / `provider_url` | `str \| None` | `None` | |
| `provider_details` | `dict[str, Any] \| None` | `None` | provider 特有数据 |
| `provider_response_id` | `str \| None` | `None` | |
| `finish_reason` | `FinishReason \| None` | `None` | |
| `run_id` / `conversation_id` | `str \| None` | `None` | |
| `metadata` | `dict[str, Any] \| None` | `None` | 含框架协议标记（如 `__pydantic_ai__`） |
| `workspace_ref` | `WorkspaceRef \| None` | `None` | 工作区环境身份（`WorkspaceRef` 定义于 L2797：`provider`、`id`） |
| `state` | `ModelResponseState` | `'complete'` | |

`__post_init__` 校验：`parts` 里的 `SpeechPart` 必须 `speaker='assistant'`。

便捷属性（只读，均为派生视图）：

| 属性 | 返回 | 行为 |
|------|------|------|
| `text` | `str \| None` | 拼接 `TextPart`（以及带 transcript 的 `SpeechPart`）；相邻文本直接相连，被非文本 part 隔开时用 `\n\n` |
| `thinking` | `str \| None` | 拼接 `ThinkingPart`，`\n\n` 分隔 |
| `files` | `list[BinaryContent]` | 所有 `FilePart.content` |
| `images` | `list[BinaryImage]` | `files` 中的图片 |
| `tool_calls` | `list[ToolCallPart]` | |
| `native_tool_calls` | `list[tuple[NativeToolCallPart, NativeToolReturnPart]]` | 按 `tool_call_id` 配对，未配对的 call 被丢弃 |
| `cost()` | `genai_types.PriceCalculation` | 基于 genai-prices，要求 `model_name` |
| `otel_message_parts(settings)` | `list[_otel_messages.MessagePart]` | 供 OTel 序列化 |

---

## 2. 请求 part（`ModelRequestPart`，L2729-L2740）

判别器为 `_model_request_part_discriminator`（可调用判别器，见 §3.4）。

| part | `part_kind` | 关键字段 |
|------|-------------|----------|
| `SystemPromptPart` | `'system-prompt'` | `content: str`、`timestamp`、`dynamic_ref: str \| None` |
| `UserPromptPart` | `'user-prompt'` | `content: str \| Sequence[UserContent]`、`timestamp` |
| `SpeechPart` | `'speech'` | `speaker`、`transcript`、`audio`、`interrupted_at_ms`（realtime） |
| `ToolSearchReturnPart` | `'tool-search-return'` | 见 `pydantic_ai._tool_search` |
| `LoadCapabilityReturnPart` | `'capability-load-return'` | 能力加载返回 |
| `ToolReturnPart` | `'tool-return'` | `BaseToolReturnPart` 子类 |
| `RetryPromptPart` | `'retry-prompt'` | `content: list[ErrorDetails] \| str`、`tool_name`、`tool_call_id` |
| `ToolAvailabilityDeltaPart` | `'tool-availability-delta'` | `tools_added`、`tool_call_id`（工具可用性增量，仅新增） |

> 注意：`InstructionPart`（`part_kind='instruction'`，L1977）**不在** `ModelRequestPart` 联合中；它承载于 `ModelRequestParameters.instruction_parts`，`ModelRequest.instructions` 是从它渲染出来的文本。`InstructionId`（L1924）带 `AgentInstructionSource` / `ToolsetInstructionSource` / `CapabilityInstructionSource`（L1883-L1920）。

`_tool_results_first_sort_key(part)`（L2743）是稳定排序键：把 `ToolReturnPart` / `RetryPromptPart` 排到同请求其它 part 之前（Anthropic 等要求工具结果领起下一条消息）。

---

## 3. 响应 part（`ModelResponsePart`，L2779-L2794）

判别器为 `_model_response_part_discriminator`。

| part | `part_kind` | 关键字段 |
|------|-------------|----------|
| `TextPart` | `'text'` | `content: str`、`id`、`provider_name`、`provider_details` |
| `ToolSearchCallPart` | `'tool-search-call'` | typed，`tool_kind='tool-search'` |
| `LoadCapabilityCallPart` | `'capability-load-call'` | typed，`tool_kind='capability-load'` |
| `ToolCallPart` | `'tool-call'` | `BaseToolCallPart` 子类 |
| `NativeToolSearchCallPart` | `'builtin-tool-search-call'` | native + typed |
| `NativeToolCallPart` | `'builtin-tool-call'` | provider 原生工具调用 |
| `NativeToolSearchReturnPart` | `'builtin-tool-search-return'` | |
| `NativeToolReturnPart` | `'builtin-tool-return'` | `provider_name`、`provider_details` |
| `ThinkingPart` | `'thinking'` | `content`、`id`、`signature`、`provider_name`、`provider_details` |
| `CompactionPart` | `'compaction'` | `content: str \| None`、`id`、`provider_name`、`provider_details` |
| `FilePart` | `'file'` | `content: BinaryContent`（经 `BinaryContent.narrow_type` AfterValidator）、`id`、`provider_name`、`provider_details` |
| `SpeechPart` | `'speech'` | 同请求中的 `SpeechPart`（此处 `speaker='assistant'`） |

### 3.1 `BaseToolCallPart`（L2419-L2531）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `tool_name` | `str` | — | |
| `args` | `str \| dict[str, Any] \| None` | `None` | 视接收方式为 JSON 字符串或 dict |
| `tool_call_id` | `str` | `_generate_tool_call_id()` | 模型未提供时 Pydantic AI 随机生成 |
| `tool_kind` | `ToolPartKind \| None` | `None` | 类型化子类判别 |
| `id` | `str \| None` | `None` | 与 `tool_call_id` 不同的可选 id（OpenAI Responses） |
| `provider_name` | `str \| None` | `None` | 设置 `provider_details` 或 `id` 时必填 |
| `provider_details` | `dict[str, Any] \| None` | `None` | |

helper 方法：

| 方法 | 行为 |
|------|------|
| `args_as_dict(*, raise_if_invalid=False)` | dict 直接返回；非法 JSON 时返回 `{'INVALID_JSON': '<raw>'}`（`raise_if_invalid=True` 则重新抛出） |
| `args_as_json_str()` | 合法对象 JSON **原样**返回（保留字节序，利于 prompt cache）；非法时降级为 `{"INVALID_JSON":"…"}`；**不要**用它渲染仍在流式中的分片 |
| `has_content()` | `self.args not in ('', {}, None)` |

子类 `ToolCallPart.part_kind='tool-call'`（L2535），静态方法 `narrow_type(part, *, tool_kind=None)`。`NativeToolCallPart.part_kind='builtin-tool-call'`（L2557），同样有 `narrow_type`。

### 3.2 `BaseToolReturnPart`（L1427-L1700）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `tool_name` | `str` | — | |
| `content` | `ToolReturnContent` | — | 可含多模态文件 |
| `tool_call_id` | `str` | `_generate_tool_call_id()` | |
| `tool_kind` | `ToolPartKind \| None` | `None` | |
| `metadata` | `Any` | `None` | 不发给模型 |
| `timestamp` | `datetime` | `_now_utc()` | |
| `outcome` | `Literal['success','failed','denied','interrupted']` | `'success'` | 只有 `'failed'` 映射到 provider 原生错误通道 |

helper 方法：

| 方法 | 行为 |
|------|------|
| `files`（属性） | `content` 中的多模态文件（`ImageUrl`/`AudioUrl`/`DocumentUrl`/`VideoUrl`/`BinaryContent`） |
| `content_items(*, mode='raw'\|'str'\|'jsonable', wrap_if_error=True)` | 拍平为列表并可选序列化；文件项原样透传 |
| `model_response_str(*, wrap_if_error=True)` | 数据部分的字符串表示（排除文件）；`'failed'` 时包成 `{"error": ...}` |
| `model_response_object(*, wrap_if_error=True)` | dict 表示；非 dict 包成 `{RETURN_VALUE_KEY: ...}`（`RETURN_VALUE_KEY='return_value'`） |
| `structured_content()` | 返回 `dict`/`list` 或 `None`（JSON 字符串会被解析） |
| `model_response_str_and_user_content(*, wrap_if_error=True)` | 文本结果 + 提取出的文件（文件用 `<tool_result …>` 溯源标签框住，见 `_tool_result_provenance_tags`） |
| `otel_message_parts(settings)` / `has_content()` | |

内部 `_split_content()` / `_unwrap_data()` 负责把标量、列表、多模态 mixed 内容拆成"数据部分 + 文件部分"，并决定是否解包单元素列表。

子类：`ToolReturnPart.part_kind='tool-return'`（含 `narrow_type`）、`NativeToolReturnPart.part_kind='builtin-tool-return'`。

### 3.3 `ToolReturnContent`（L1333-L1368）

```python
if TYPE_CHECKING:
    ToolReturnContent: TypeAlias = MultiModalContent | Sequence[Any] | Mapping[str, Any] | Any
else:
    ToolReturnContent = TypeAliasType('ToolReturnContent', Annotated[
        Annotated[str, _StrPassthrough]
        | Annotated[MultiModalContent, _RequireUrlMediaType]
        | Mapping[str, 'ToolReturnContent']
        | Sequence['ToolReturnContent']
        | Any,
        pydantic.Field(union_mode='left_to_right'),
    ])
```

运行期是递归 `TypeAliasType`，用于在反序列化时**自动重建**嵌套在 dict/list 中的 `BinaryContent` / `FileUrl`。`tool_return_content_ta` 是配套的 `TypeAdapter`（UI 适配器用它从 wire 字段里 rehydrate 多模态项）。

### 3.4 `tool_kind` 判别与 `narrow_type` 注册表

- `ToolPartKind = Literal['tool-search', 'capability-load']`（L1371）；`parse_tool_kind(value)`（L1386）在 wire 边界校验不可信字符串，未知返回 `None`。
- 注册表：`_TOOL_CALL_NARROWERS` / `_TOOL_RETURN_NARROWERS`（L2622-L2623）、`_TYPED_PART_TAGS` / `_TYPED_PART_TAGS_BY_TYPE`（L2664-L2672）。
- 判别器 `_model_request_part_discriminator` / `_model_response_part_discriminator` **按 `(part_kind, tool_kind)` 分派**，避免用户的普通工具恰好同名时被误提升。
- `narrow_message_parts(messages)`（L3175）：把手写构造的 base part 按 `tool_kind` 提升为类型化子类，尽力而为且幂等。
- 新增 typed native tool 的四步（`tools.py#L701-L708` + `messages.py#L2565-L2579`）：扩展 `ToolPartKind` → 定义子类与 narrower 并注册 → 在 `_TYPED_PART_TAGS` / `_TYPED_PART_TAGS_BY_TYPE` 加 `(part_kind, tool_kind) → Tag` → 加入 `ModelResponsePart` 判别联合。

---

## 4. 内容 / 媒体类型体系

### 4.1 媒体类型与格式 Literal

```python
AudioMediaType = Literal['audio/wav', 'audio/mpeg', 'audio/ogg', 'audio/flac', 'audio/aiff', 'audio/aac']
ImageMediaType = Literal['image/jpeg', 'image/png', 'image/gif', 'image/webp']
DocumentMediaType = Literal[...]   # csv/doc/docx/html/md/pdf/txt/xls/xlsx 等
VideoMediaType = Literal[...]
AudioFormat = Literal['wav', 'mp3', 'oga', 'flac', 'aiff', 'aac']
ImageFormat = Literal['jpeg', 'png', 'gif', 'webp']
DocumentFormat = Literal['csv', 'doc', 'docx', 'html', 'md', 'pdf', 'txt', 'xls', 'xlsx']
VideoFormat = Literal['mkv', 'mov', 'mp4', 'webm', 'flv', 'mpeg', 'mpg', 'wmv', 'three_gp']
```

### 4.2 内容联合

| 别名 | 定义 | 位置 |
|------|------|------|
| `UserContent` | `str \| TextContent \| MultiModalContent \| CachePoint` | L990 |
| `MultiModalContent` | `Annotated[ImageUrl \| AudioUrl \| DocumentUrl \| VideoUrl \| BinaryContent \| UploadedFile, Discriminator('kind')]` | L~960-L974 |
| `MULTI_MODAL_CONTENT_TYPES` | 上述类型的显式元组，供 `is_multi_modal_content(obj)` 窄化 | L977 |

内容类：

| 类 | 字段 / 关键点 |
|----|----------------|
| `FileUrl(ABC)` | `url`、`force_download: ForceDownloadMode`、`vendor_metadata`、`media_type`/`identifier`（computed field） |
| `ImageUrl` / `AudioUrl` / `DocumentUrl` / `VideoUrl` | `FileUrl` 子类，各有 `kind`（`'image-url'` 等） |
| `TextContent` | `content: str`、`metadata`、`kind='text-content'` |
| `BinaryContent` | `data: bytes`、`media_type`、`vendor_metadata`；`kind='binary'`；提供 `base64` 等 |
| `BinaryImage` / `BinaryAudio` | `BinaryContent` 子类 |
| `CachePoint` | `kind='cache-point'`、`ttl: Literal['5m','1h']`（默认 `'5m'`） |
| `UploadedFile` | `file_id`、`provider_name`、`vendor_metadata`；`media_type` 从 `file_id` 扩展名推断 |
| `ToolReturn` | 泛型：`return_value`、`content`、`metadata`、`tools: list[str] \| None` |

`ForceDownloadMode = bool | Literal['allow-local']`（L178）：`False` 直接发 URL（不支持则带 SSRF 防护下载）、`True` 总是带防护下载、`'allow-local'` 允许私网但仍拦云元数据。

---

## 5. 流式事件与增量

### 5.1 增量（`ModelResponsePartDelta`，L4419）

```python
ModelResponsePartDelta = Annotated[
    TextPartDelta | ThinkingPartDelta | ToolCallPartDelta | SpeechPartDelta,
    pydantic.Discriminator('part_delta_kind'),
]
```

| delta | `part_delta_kind` | 关键字段 |
|-------|-------------------|----------|
| `TextPartDelta` | `'text'` | `content_delta`、`provider_name`、`provider_details` |
| `ThinkingPartDelta` | `'thinking'` | `content_delta`、`signature_delta`、`provider_details: ProviderDetailsDelta` |
| `ToolCallPartDelta` | `'tool_call'` | `tool_name_delta`、`args_delta`、`tool_call_id`、`provider_details` |
| `SpeechPartDelta` | `'speech'` | `speaker`、`transcript_delta`、`transcript`、`audio_chunk` |

`ProviderDetailsDelta`（L200）允许 `provider_details` 传一个 `Callable[[dict | None], dict]` 合并回调；JSON 序列化时回调降级为 `null`（Python 模式保留）。

### 5.2 事件（`ModelResponseStreamEvent`，L4528）

```python
ModelResponseStreamEvent = Annotated[
    PartStartEvent | PartDeltaEvent | PartEndEvent | FinalResultEvent,
    Discriminator('event_kind'),
]
```

| 事件 | `event_kind` | 字段 |
|------|--------------|------|
| `PartStartEvent` | `'part_start'` | `index`、`part`、`previous_part_kind` |
| `PartDeltaEvent` | `'part_delta'` | `index`、`delta` |
| `PartEndEvent` | `'part_end'` | `index`、`part`、`next_part_kind` |
| `FinalResultEvent` | `'final_result'` | `tool_name`、`tool_call_id` |

`previous_part_kind` / `next_part_kind` 的取值：`'text' | 'thinking' | 'tool-call' | 'builtin-tool-call' | 'builtin-tool-return' | 'compaction' | 'file' | 'speech'`（供 UI 事件流分组）。

`StreamedResponse.__aiter__`（见 [03 · 模型](03-models-providers-profiles.md)）负责：找到首个 FinalResultEvent 后注入并终止、在 `PartStartEvent` 之间合成 `PartEndEvent`、以及取消保护。

---

## 6. 运行事件类型（消息层视角）

| 事件 | `event_kind` | 说明 |
|------|--------------|------|
| `EnqueuedMessagesEvent` | `'enqueued_messages'` | `enqueue_id`、`messages: tuple[ModelMessage, ...]`（交付时刻的真实对象） |
| `ToolCallEvent`（基类） | — | `part: ToolCallPart`、`args_valid: bool \| None`、`tool_call_id` 属性 |
| `FunctionToolCallEvent` | `'function_tool_call'` | 函数工具调用开始 |
| `OutputToolCallEvent` | `'output_tool_call'` | 输出工具调用开始 |
| `ToolResultEvent`（基类） | — | `part: ToolReturnPart \| RetryPromptPart` |
| `FunctionToolResultEvent` | `'function_tool_result'` | 额外 `content` |
| `OutputToolResultEvent` | `'output_tool_result'` | |
| `ToolAvailabilityDeltaEvent` | `'tool_availability_delta'` | `part: ToolAvailabilityDeltaPart` |
| `DeferredToolRequestsEvent` | `'deferred_tool_requests'` | `requests: DeferredToolRequests` |
| `DeferredToolResultsEvent` | `'deferred_tool_results'` | `results: DeferredToolResults` |

realtime 事件：`RealtimeTurnCompleteEvent`、`RealtimeInputSpeechStartEvent` / `RealtimeInputSpeechEndEvent`、`RealtimeOutputSpeechStartEvent` / `RealtimeOutputSpeechEndEvent`、`RealtimeResponseInterruptedEvent`、`RealtimeInputTranscriptionErrorEvent`、`RealtimeSessionReconnectEvent`、`RealtimeSessionErrorEvent`。

扩展点：`CustomEvent`（`event_kind='custom'`，`_register` 机制）与 `CapabilityEvent`（`event_kind='capability'`，`event_dispatch: 'stream' | 'immediate'`）；未注册时回退 `UnknownCustomEvent` / `UnknownCapabilityEvent`。

---

## 7. 历史维护 helper

| helper | 位置 | 行为 |
|--------|------|------|
| `repair_messages(messages, *, repair_last_response=True)` | L3524 | 让历史 provider-valid：丢弃孤立 tool result、给悬空 tool call 合成 `ToolReturnPart`、合并相邻兼容消息。确定性且幂等；合成返回在 `metadata` 带 `{'pydantic_ai_synthesized_tool_return': True}` |
| `sanitize_messages(messages, *, strip_system_prompts=True, strip_compaction_parts=False, allowed_file_url_schemes=('http','https'), allowed_file_url_force_download=(), allow_uploaded_files=False, strip_workspace_refs=True, resolved_tool_call_ids=())` | L3550 | 剥离不可信输入中不该被信任的 part（system prompt、非白名单 scheme 的 `FileUrl`、`UploadedFile`、`workspace_ref`、末尾未解析的 `ToolCallPart` 等） |
| `narrow_message_parts(messages)` | L3175 | 按 `tool_kind` 提升 call/return part |
| `post_compaction_window(messages)` | L3055 | 返回最近一个 `CompactionPart` 之后的窗口（part 级精度，保留该响应中 compaction 之后的 part）；provider 无关的保守交集 |
| `_post_compaction_window_for_response(messages, serving_response)` | L3129 | provider 精确的证据窗口（只认严格早于 serving response 的边界） |
| `_compaction_part_is_wire_boundary(part, provider_name, *, requires_encrypted_content=False)` | L3098 | 该 provider 是否会把此 part 当作线上边界 |

内部：`_clean_message_history`、`_drop_orphaned_tool_results`、`_repair_dangling_tool_calls`、`_merge_consecutive_messages`、`_drop_compaction_parts`、`_stripping` 系列。

其它常量：`INVALID_JSON_KEY='INVALID_JSON'`（L68）、`RETURN_VALUE_KEY='return_value'`（L1221）、`STANDING_PROMPT_PLANTED_KEY='pydantic_ai_standing_prompt_planted'`（L2238）、`INTERRUPTED_TOOL_RETURN_CONTENT`（L1396）。

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter, repair_messages, sanitize_messages

# 反序列化 + 修复历史
history = ModelMessagesTypeAdapter.validate_json(raw_json)
fixed = repair_messages(history)
safe = sanitize_messages(history)   # 用于不可信的客户端提交
```

---

## 8. 公开输出 API（`output.py`）

### 8.1 输出模式

```python
OutputMode = Literal['text', 'tool', 'native', 'prompted', 'tool_or_text', 'image', 'auto']  # L49
StructuredOutputMode = Literal['tool', 'native', 'prompted']                                  # L55
```

`'auto'` 表示由模型的 `ModelProfile.default_structured_output_mode` 决定；`'tool_or_text'` 已废弃。

### 8.2 类型别名

```python
OutputDataT = TypeVar('OutputDataT', default=str, covariant=True)          # L45
OutputTypeOrFunction[T] = type[T] | TypeForm[T] | Callable[..., Awaitable[T] | T]   # L59
TextOutputFunc[T]       = Callable[[RunContext[Any], str], Awaitable[T] | T] | Callable[[str], Awaitable[T] | T]
OutputSpec[T]           = _OutputSpecItem[T] | Sequence[...] | Sequence[_OutputSpecItem[T] | _NoneOutput[T]]
```

`_NoneOutput`（L72）是让 `None`（值而非类型）能出现在 `output_type=[Foo, None]` 里的结构化 Protocol 技巧。

### 8.3 用户面向的标记类全表

| 类 | 字段 | 说明 |
|----|------|------|
| `ToolOutput[OutputDataT]` | `output`、`name`、`description`、`max_retries`、`strict`、`sequential` | 用工具承载输出；`max_retries` 覆盖输出侧重试预算 |
| `NativeOutput[OutputDataT]` | `outputs`、`name`、`description`、`strict`、`template` | 模型原生结构化输出；`template=False` 关闭 schema prompt |
| `PromptedOutput[OutputDataT]` | `outputs`、`name`、`description`、`template` | 用 prompt 携带 schema |
| `TextOutput[OutputDataT]` | `output_function` | 文本由函数处理（流式时 `stream_text()` 不应用函数，需 `stream_output()`） |
| `StructuredDict(json_schema, name=None, description=None)` | 返回 `type` | 生成带 JSON Schema 的 `dict[str, Any]` 子类；不支持递归 `$ref`/`$defs` |
| `Choice[T]` | `description`、`value` | 一个选项；`value` 为 callable 时在被选中时**无参调用**（可 async），支持 `functools.partial` 绑定 |
| `Choices(choices, *, name=None, description=None)` | 返回 `type` | 运行期才确定的选项集；返回类型可直接用作 `output_type`、模型字段或工具参数 |
| `BoolCriteria` | `true`、`false` | `Annotated[bool, BoolCriteria(...)]`，两个描述进入 schema 的 `anyOf`-of-`const` |

`Choices` 的三种输入：`Sequence[str]`、`Mapping[str, str]`（key→描述）、`Mapping[str, Choice]`；空集合抛 `UserError`。含 callable 值的 `Choices` **只能**作为 agent 的 `output_type`（构造时若用于别处会在 core schema 阶段抛 `UserError`）。

### 8.4 `OutputObjectDefinition` 与 `OutputContext`

```python
@dataclass
class OutputObjectDefinition:      # L308
    json_schema: ObjectJsonSchema
    name: str | None = None
    description: str | None = None
    strict: bool | None = None

@dataclass
class OutputContext:               # L318
    mode: OutputMode
    output_type: type[Any] | None
    object_def: OutputObjectDefinition | None
    has_function: bool
    function_name: str | None = None
    tool_call: ToolCallPart | None = None
    tool_def: ToolDefinition | None = None
    allows_text: bool = False
    allows_image: bool = False
    allows_deferred_tools: bool = False
```

`OutputContext` 被传给输出 Hook；`mode` 反映**配置的** schema 而非本次响应的形态（例如带 `text_processor` 的 hybrid 模式仍报 `'tool'`，靠 `tool_call` 区分）。

---

## 9. 内部输出机制（`_output.py`）

### 9.1 `OutputSchema` 与子类

```python
@dataclass(kw_only=True)
class OutputSchema(ABC, Generic[OutputDataT]):     # L442
    allows_none: bool
    text_processor: BaseOutputProcessor[OutputDataT] | None = None
    toolset: OutputToolset[Any] | None = None
    object_def: OutputObjectDefinition | None = None
    allows_deferred_tools: bool = False
    allows_image: bool = False

    @property
    def mode(self) -> OutputMode: ...
    @property
    def allows_text(self) -> bool: return self.text_processor is not None

    @classmethod
    def build(cls, output_spec, *, name=None, description=None, strict=None) -> OutputSchema[OutputDataT]: ...
```

`OutputSchema.build`（L459-L620）是展平与分派的核心：

1. `_flatten_output_spec(output_spec)` 展平嵌套序列与联合。
2. 识别并剔出 `NoneType`（`allows_none`）、`DeferredToolRequests`（`allows_deferred_tools`）、`BinaryImage`（`allows_image`）；各自"只剩它"时抛 `UserError`。
3. `NativeOutput` / `PromptedOutput` 必须是唯一输出类型（否则抛错），且内部不能再含 `DeferredToolRequests` / `BinaryImage`。
4. 其余拆成 `text_outputs`（`str` / `TextOutput`）、`tool_outputs`（`ToolOutput`）、`other_outputs`；`allows_none` 且有结构化输出时把 `NoneType` 作为独立输出工具暴露。
5. `OutputToolset.build(tool_outputs + other_outputs, ...)`；只有一个 `str`/`TextOutput`，多于一个抛错。
6. 返回：`ToolOutputSchema`（有工具）> `TextOutputSchema`（仅文本）> `ToolOutputSchema`（仅工具）> `AutoOutputSchema`（其它类型）> `ImageOutputSchema`（仅图像）。

子类：

| 子类 | 位置 | `mode` |
|------|------|--------|
| `AutoOutputSchema` | L636 | `'auto'` |
| `TextOutputSchema` | L666 | `'text'` |
| `ImageOutputSchema` | L688 | `'image'` |
| `StructuredTextOutputSchema`（ABC） | L697 | —；含 `build_instructions(template, object_def)` |
| `NativeOutputSchema` | L736 | `'native'` |
| `PromptedOutputSchema` | L742 | `'prompted'` |
| `ToolOutputSchema` | L749 | `'tool'` |

`OutputSchema._build_processor(outputs, name, description, strict)`：单个输出 → `ObjectOutputProcessor`，多个 → `UnionOutputProcessor`。

### 9.2 处理器（`BaseOutputProcessor` 及子类）

| 类 / 方法 | 位置 | 说明 |
|-----------|------|------|
| `BaseOutputProcessor(ABC)` | L773 | 抽象：`validate`、`call`、`hook_validate`、`hook_execute`、`get_output_context` |
| `BaseObjectOutputProcessor` | L864 | 结构化输出的公共基类 |
| `ObjectOutputProcessor` | L869 | 单输出：`__init__(output, name, description, strict)`、`validate`、`call`、`hook_unwrap_key`、`hook_validate`、`hook_execute`、`get_output_context` |
| `UnionOutputProcessor` | L1118 | 多输出，按语义 / 子类型分派；含 `_UnionValidatedOutput`、`UnionOutputResult`、`UnionOutputModel`；`_semantic_matches_inner`、`_resolve_inner_for_value` |
| `TextOutputProcessor` | L1416 | 纯文本 |
| `TextFunctionOutputProcessor` | L1439 | 文本 + 处理函数 |
| `OutputValidator[DepsT, OutputDataT_inv]` | L409 | `validate(...)`，在 process hook 内部运行 |

### 9.3 `OutputToolset`

`OutputToolset(AbstractToolset[AgentDepsT])`（L1495-L1631）：

```python
@classmethod
def build(cls, outputs, name=None, description=None, strict=None) -> Self | None: ...  # L1508
```

- 字段：`_tool_defs`、`processors: dict[str, ObjectOutputProcessor]`、`max_retries`（由 Agent 设）、`_max_retries_overrides`（来自 `ToolOutput(max_retries=N)`）、`output_validators`。
- 生成 `ToolDefinition(kind='output', ...)`；单输出用默认名 `DEFAULT_OUTPUT_TOOL_NAME`，多输出追加 `_{safe_name}` 并去重 `_2`、`_3`。描述缺失时用 `DEFAULT_OUTPUT_TOOL_DESCRIPTION`（多输出前缀 `{name}: `）。
- `id` → `OUTPUT_TOOLSET_ID`；`label` → `"the agent's output tools"`。
- `get_tools(ctx)` 返回 `ToolsetTool`（带 `args_validator=processor.validator`）。
- `call_tool(...)` **抛 `NotImplementedError`**：输出工具由 `ToolManager.validate_output_tool_call` / `execute_output_tool_call` 处理，不走常规 toolset 路径。

### 9.4 输出 Hook 运行器

| 函数 | 位置 | 说明 |
|------|------|------|
| `run_output_validate_hooks(capability, *, run_context, output_context, output, do_validate, allow_partial=False, wrap_validation_errors=True)` | L129 | 包裹完整校验生命周期（before/after/wrap/on_error）；把 `ValidationError`/`ModelRetry` 转 `ToolRetryError` |
| `run_output_process_hooks(...)` | L172 | 所有输出类型都跑；`ToolRetryError` 原样透传，`ModelRetry` 交给外层 |
| `run_none_process_hooks(...)` | L221 | `None` 结果（空响应 + `allows_none`） |
| `run_image_process_hooks(...)` | L259 | 图像输出（无 validate 阶段） |
| `run_output_with_hooks(processor, *, text, run_context, capability, schema, allow_partial=False, wrap_validation_errors=True, output_validators=())` | L298 | 组合校验/处理 Hook 的主入口 |
| `execute_output_function(...)` | L358 | 调用输出函数 |
| `execute_choice_action(action)` | L396 | 执行 `Choices` 里 callable 的 `Choice.value` |

辅助：`_build_output_handlers`、`_make_retry_prompt`、`_isinstance_maybe_generic`、`_output_type_name`、`_flatten_output_spec`（L1644）、`types_from_output_spec(output_spec)`（L1663）。

---

## 10. `ToolDefinition`（`tools.py` 视角）

`ToolDefinition` 由 `ModelRequestParameters.function_tools` 承载，并被适配器序列化到线上。完整字段表见 [03 · 模型 / Provider / Profile](03-models-providers-profiles.md#93-tooldefinitiontoolspyl586-l788)，此处只强调与消息协议的交叉点：

| 字段 | 与消息协议的关系 |
|------|------------------|
| `tool_kind: ToolPartKind \| None` | 决定该工具的 call/return part 是否被提升为类型化子类（§3.4） |
| `kind: ToolKind` | `'function' \| 'output' \| 'external' \| 'unapproved'`，是调用语义，**不同于** `tool_kind` |
| `outer_typed_dict_key` | 非 `object` schema 的输出工具在 `OutputToolset` 里设置，供校验时剥外层键 |
| `defer_loading` | 作者意图，配合 `ModelRequestParameters.tool_visibility` 决定线上形状 |
| `return_schema` / `include_return_schema` | 由 `prepare_return_schemas` 解析后可能被清空或注入 description |

`ToolDefinition.function_signature`（`@cached_property -> FunctionSignature`）与 `render_signature(body, **kwargs)` 用于把工具渲染成签名文本（prompted 输出等场景）。

---

## 11. 消息协议的设计约束

- **provider 事实不得污染规范化字段**：provider 特有数据放入 `provider_details` / `provider_metadata`，不要塞进 `id` / `content` / `args`，也不要为实现 provider 行为而在 graph / tool / output / ui 代码里判断 provider 名（见 `models/AGENTS.md` rule:598、`providers/AGENTS.md`）。
- **可序列化往返**：持久化历史、UI 交换、provider 往返都应经由结构化字段；把类型信息编码进字符串会在 JSON 序列化/反序列化中丢失（`ToolReturnContent` 的递归 `TypeAliasType` 正是为此保留多模态项的类型）。
- **流式与非流式一致**：`request()` 与 `request_stream()` 必须得到相同的最终响应形状（`models/AGENTS.md` rule:81）。`stream_text()` 不应用 `TextOutput` 的处理函数——需 `stream_output()`。
- **按 `tool_kind` 而非 `tool_name` 判别**：类型化提升只认 `tool_kind`，防止用户工具与框架工具同名时被误提升（§3.4）。
- **压缩边界保守取值**：`post_compaction_window` 是 provider 无关的保守交集（另一个 provider 会在线上跳过的 compaction part 仍计入），因为该窗口喂给跨 provider 的 run 级状态；`_post_compaction_window_for_response` 才是 provider 精确的。
- **请求/响应 part 的 speaker 不变量**：`SpeechPart` 在 `ModelRequest.parts` 必须是 `'user'`，在 `ModelResponse.parts` 必须是 `'assistant'`，构造时即校验。

---

## 12. 相关文件清单

| 关注点 | 文件 / 位置 |
|--------|-------------|
| 消息类型与 part 联合 | [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)（顶层别名 L112-L204、请求 part L2729、响应 part L2779、判别器 L2702/L2752） |
| 工具 call / return part | [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)（`BaseToolCallPart` L2419、`BaseToolReturnPart` L1427、`ToolReturnContent` L1333） |
| 流式事件与增量 | [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)（增量 L4012-L4419、事件 L4426-L4530、联合 L4419/L4528） |
| 运行事件 | [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)（L4534-L5008、L5215 起的 `CapabilityEvent`） |
| 历史维护 | [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)（`repair_messages` L3524、`sanitize_messages` L3550、`post_compaction_window` L3055、`narrow_message_parts` L3175） |
| 公开输出 API | [output.py](../pydantic_ai_slim/pydantic_ai/output.py) |
| 内部输出机制 | [_output.py](../pydantic_ai_slim/pydantic_ai/_output.py)（`OutputSchema` L442、处理器 L773-L1492、`OutputToolset` L1495、Hook 运行器 L129-L406） |
| 工具定义 | [tools.py](../pydantic_ai_slim/pydantic_ai/tools.py)（`ToolDefinition` L586、`GenerateToolJsonSchema` L275、`ObjectJsonSchema` L574、`ToolKind` L582） |
