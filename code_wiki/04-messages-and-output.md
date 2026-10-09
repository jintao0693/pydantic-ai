# 04 · 消息协议与输出

`messages.py` 是 Pydantic AI 的**规范化消息协议**：provider 适配、UI 适配、durable 包装、持久化历史都应以此为往返格式。`output.py` / `_output.py` 则负责把「输出类型」翻译为模型可理解的形式并校验其结果。

相关文件：[messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)、[output.py](../pydantic_ai_slim/pydantic_ai/output.py)、[_output.py](../pydantic_ai_slim/pydantic_ai/_output.py)。

---

## 1. 顶层消息类型（`messages.py`）

| 类 | 说明 |
|----|------|
| `ModelRequest` | 请求消息。字段：`parts: Sequence[ModelRequestPart]`、`timestamp`、`instructions`、`kind='request'`、`run_id`、`conversation_id`、`metadata`、`state`；类方法 `user_text_prompt(...)` |
| `ModelResponse` | 响应消息。字段：`parts: Sequence[ModelResponsePart]`、`usage: RequestUsage`、`model_name`、`provider_name`、`provider_url`、`provider_details`、`provider_response_id`、`finish_reason`、`workspace_ref`、`state`；便捷属性 `text`、`thinking`、`files`、`images`、`tool_calls`、`native_tool_calls`、`cost()` |

```python
ModelMessage = Annotated[ModelRequest | ModelResponse, pydantic.Discriminator('kind')]
```

`ModelResponseState = 'complete' | 'incomplete' | 'suspended' | 'interrupted'`；`FinishReason = 'stop' | 'length' | 'content_filter' | 'tool_call' | 'error'`。

---

## 2. 请求 part（`ModelRequestPart`）

| part | `part_kind` | 说明 |
|------|-------------|------|
| `SystemPromptPart` | `system-prompt` | system prompt 内容，含 `dynamic_ref` |
| `UserPromptPart` | `user-prompt` | 用户输入，`content: str \| Sequence[UserContent]` |
| `SpeechPart` | `speech` | 语音（`speaker`、`transcript`、`audio`） |
| `ToolReturnPart` | `tool-return` | 工具返回 |
| `RetryPromptPart` | `retry-prompt` | 重试提示（`from_error(...)`） |
| `ToolSearchReturnPart` | `tool-search-return` | 工具搜索返回 |
| `LoadCapabilityReturnPart` | `capability-load-return` | 能力加载返回 |
| `ToolAvailabilityDeltaPart` | `tool-availability-delta` | 工具可用性增量（仅新增） |
| `InstructionPart` | — | 指令实例，带 `AgentInstructionSource` / `ToolsetInstructionSource` / `CapabilityInstructionSource` |

---

## 3. 响应 part（`ModelResponsePart`）

判别器为 `_model_response_part_discriminator`。

| part | `part_kind` | 说明 |
|------|-------------|------|
| `TextPart` | `text` | 文本 |
| `ThinkingPart` | `thinking` | 思考内容，含 `signature`、`provider_details` |
| `ToolCallPart` | `tool-call` | 工具调用（`BaseToolCallPart` 子类） |
| `NativeToolCallPart` | `builtin-tool-call` | provider 原生工具调用 |
| `ToolSearchCallPart` | `tool-search-call` | 工具搜索调用 |
| `LoadCapabilityCallPart` | `capability-load-call` | 能力加载调用 |
| `NativeToolSearchCallPart` / `NativeToolSearchReturnPart` / `NativeToolReturnPart` | `builtin-tool-*` | 原生工具的搜索/返回 |
| `CompactionPart` | `compaction` | 压缩标记 |
| `FilePart` | `file` | 文件（`BinaryContent`） |
| `SpeechPart` | `speech` | 语音 |

`BaseToolCallPart` 字段：`tool_name`、`args`、`tool_call_id`、`tool_kind`、`id`、`provider_name`、`provider_details`；helper：`args_as_dict()`、`args_as_json_str()`、`has_content()`。
`BaseToolReturnPart` 字段：`tool_name`、`content`、`tool_call_id`、`tool_kind`、`metadata`、`timestamp`、`outcome: 'success' | 'failed' | 'denied' | 'interrupted'`。

> **按 `tool_kind` 而非 `tool_name` 判别**：类型提升通过 `narrow_type` 与 `_TOOL_CALL_NARROWERS` / `_NATIVE_CALL_NARROWERS` 注册表按 `tool_kind` 分派。

---

## 4. 内容 / 媒体类型

- `UserContent = str | TextContent | MultiModalContent | CachePoint`。
- `MultiModalContent = ImageUrl | AudioUrl | DocumentUrl | VideoUrl | BinaryContent | UploadedFile`。
- `FileUrl(ABC)` 子类：`VideoUrl`、`AudioUrl`、`ImageUrl`、`DocumentUrl`。
- `TextContent`、`BinaryContent`（子类 `BinaryImage`、`BinaryAudio`）、`CachePoint`、`UploadedFile`、`ToolReturn`。
- 媒体类型字面量：`AudioMediaType`、`ImageMediaType`、`DocumentMediaType`、`VideoMediaType`。

---

## 5. 流式事件与增量

- 增量：`TextPartDelta`、`ThinkingPartDelta`、`ToolCallPartDelta`、`SpeechPartDelta`。
- 事件：`PartStartEvent`、`PartDeltaEvent`、`PartEndEvent`、`FinalResultEvent`。

```python
ModelResponseStreamEvent = Annotated[
    PartStartEvent | PartDeltaEvent | PartEndEvent | FinalResultEvent,
    Discriminator('event_kind'),
]
```

- 更高层的运行事件：`EnqueuedMessagesEvent`、`ToolCallEvent` / `FunctionToolCallEvent` / `OutputToolCallEvent`、`ToolResultEvent` / `FunctionToolResultEvent` / `OutputToolResultEvent`、`DeferredToolRequestsEvent` / `DeferredToolResultsEvent`，以及 realtime 事件。

历史维护 helper：`repair_messages`、`sanitize_messages`、`narrow_message_parts`、`post_compaction_window`。

---

## 6. 公开输出 API（`output.py`）

### 6.1 输出模式

```python
OutputMode = Literal['text', 'tool', 'native', 'prompted', 'tool_or_text', 'image', 'auto']
StructuredOutputMode = Literal['tool', 'native', 'prompted']
```

### 6.2 用户面向的标记类

| 类 | 说明 |
|----|------|
| `ToolOutput[OutputDataT]` | 用工具承载输出（`name`、`description`、`max_retries`、`strict`、`sequential`） |
| `NativeOutput[OutputDataT]` | 用模型原生结构化输出（`outputs`、`name`、`description`、`strict`、`template`） |
| `PromptedOutput[OutputDataT]` | 用 prompt 携带 schema（`template`） |
| `TextOutput[OutputDataT]` | 文本输出，由函数处理 |
| `StructuredDict(...)` / `Choice` / `Choices(...)` / `BoolCriteria` | 结构化字典 / 枚举选择 / 布尔判定 |
| `OutputObjectDefinition` | 发送给模型的 schema（`json_schema`、`name`、`description`、`strict`） |
| `OutputContext` | 输出处理上下文（`mode`、`output_type`、`object_def`、`tool_call`、`allows_text` 等） |

### 6.3 内部机制（`_output.py`）

- `OutputSchema(ABC)`：`build(output_spec, *, name, description, strict)` 展平输出规格并分派。子类：`AutoOutputSchema`、`TextOutputSchema`、`ImageOutputSchema`、`StructuredTextOutputSchema`、`NativeOutputSchema`、`PromptedOutputSchema`、`ToolOutputSchema`。
- 处理器：`BaseOutputProcessor(ABC)`（`validate` / `call` / `hook_validate` / `hook_execute` / `get_output_context`）及子类（`ObjectOutputProcessor`、`UnionOutputProcessor`、`TextOutputProcessor`、`TextFunctionOutputProcessor`）。
- `OutputToolset`：向 Agent 暴露输出工具；`OutputValidator`；`types_from_output_spec`。
- 输出 Hook 运行器：`run_output_validate_hooks`、`run_output_process_hooks`、`run_output_with_hooks`、`execute_output_function`。

---

## 7. 工具定义（`tools.py` 视角）

`ToolDefinition` 是 `ModelRequestParameters.function_tools` 承载、并被适配器序列化到线上的对象：

| 字段 | 说明 |
|------|------|
| `name` / `description` / `parameters_json_schema` | 基本信息 |
| `kind: ToolKind` | `'function' \| 'output' \| 'external' \| 'unapproved'` |
| `tool_kind: ToolPartKind \| None` | 跨 provider 的类型化 call/return 形状判别；`ToolPartKind = 'tool-search' \| 'capability-load'` |
| `strict` / `sequential` / `timeout` / `metadata` | 行为配置 |
| `defer_loading` / `unless_native` / `with_native` | 延迟与 native 交互 |
| `return_schema` / `include_return_schema` | 返回 schema |
| `toolset_id` | 归属的 toolset |

> `kind`（功能角色）与 `tool_kind`（类型化 part 形状）是两个不同的概念，不要混淆。

---

## 8. 消息协议的设计约束

- **provider 事实不得污染规范化字段**：provider 特有数据放入 `provider_details` / `provider_metadata`，不要塞进 `id` / `content` / `args`，也不要为实现 provider 行为而在 graph/tool/output/ui 代码中判断 provider 名。
- **可序列化往返**：持久化历史、UI 交换、provider 往返都应经由结构化字段，编码进字符串会在 JSON 序列化/反序列化中丢类型信息。
- **流式与非流式一致**：`request()` 与 `request_stream()` 必须得到相同的最终响应形状（见 `models/AGENTS.md`）。
