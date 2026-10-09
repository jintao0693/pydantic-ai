# 07 · UI 适配层与 Realtime

本篇覆盖两个不经过「graph 运行」的界面层：

- **UI 适配**：把规范化消息/事件翻译为前端协议（AG-UI、Vercel AI），并提供内置 Web 应用。
- **Realtime**：双向语音会话（OpenAI Realtime、Gemini Live、Azure、xAI Grok Voice、OpenAI GPT-Live）。

相关目录：

- [`pydantic_ai_slim/pydantic_ai/ui/`](../pydantic_ai_slim/pydantic_ai/ui/)
- [`pydantic_ai_slim/pydantic_ai/realtime/`](../pydantic_ai_slim/pydantic_ai/realtime/)

两者的共同点：它们都在**同一套共享消息词汇**（`pydantic_ai.messages`）与外部协议之间做双向翻译；不同点是 UI 适配是「请求—响应 + SSE」，Realtime 是「持久双向连接 + 实时上下行」。

---

## 1. UI 适配层（`ui/`）

### 1.1 包结构与导出

目录布局：

| 文件 | 职责 |
|------|------|
| [`ui/__init__.py`](../pydantic_ai_slim/pydantic_ai/ui/__init__.py) | 顶层导出 + 懒加载 `DEFAULT_HTML_URL` / `OFFLINE_HTML_URL` |
| [`ui/_adapter.py`](../pydantic_ai_slim/pydantic_ai/ui/_adapter.py) | `UIAdapter` 抽象、`StateHandler` / `StateDeps`、安全设置、`DEFAULT_ALLOWED_CONTENT_TYPES` |
| [`ui/_event_stream.py`](../pydantic_ai_slim/pydantic_ai/ui/_event_stream.py) | `UIEventStream` 状态机、`SSE_CONTENT_TYPE`、`NativeEvent`、回调类型 |
| [`ui/_messages_builder.py`](../pydantic_ai_slim/pydantic_ai/ui/_messages_builder.py) | `MessagesBuilder` / `BuilderCheckpoint` |
| [`ui/_utils.py`](../pydantic_ai_slim/pydantic_ai/ui/_utils.py) | `set_ui_message_id` / `get_ui_message_id` 与保留元数据命名空间 |
| [`ui/ag_ui/`](../pydantic_ai_slim/pydantic_ai/ui/ag_ui/) | AG-UI 协议适配器 + 版本兼容层 |
| [`ui/vercel_ai/`](../pydantic_ai_slim/pydantic_ai/ui/vercel_ai/) | Vercel AI SDK 适配器 |
| [`ui/_web/`](../pydantic_ai_slim/pydantic_ai/ui/_web/) | 内置聊天 Web 应用（`create_web_app` / `create_api_app`） |
| [`ui/AGENTS.md`](../pydantic_ai_slim/pydantic_ai/ui/AGENTS.md) | 向后兼容策略与事件流设计约定 |

[`ui/__init__.py`](../pydantic_ai_slim/pydantic_ai/ui/__init__.py) 的 `__all__`：

```
UIAdapter, UIEventStream, SSE_CONTENT_TYPE, DEFAULT_ALLOWED_CONTENT_TYPES,
StateDeps, StateHandler, NativeEvent, OnCompleteFunc, OnCancelFunc,
MessagesBuilder, BuilderCheckpoint, DEFAULT_HTML_URL, OFFLINE_HTML_URL
```

`DEFAULT_HTML_URL` 与 `OFFLINE_HTML_URL` 通过模块级 `__getattr__` 懒加载（导入它们会拉起 `_web` 里的 `starlette` 依赖，因此不放在顶层导入）。

### 1.2 `UIAdapter`（[`ui/_adapter.py`](../pydantic_ai_slim/pydantic_ai/ui/_adapter.py)）

```python
@dataclass
class UIAdapter(ABC, Generic[RunInputT, MessageT, EventT, AgentDepsT, OutputDataT]):
```

职责：把前端传入的运行输入翻译成 `Agent.run_stream_events()` 的参数，运行 Agent，再通过协议专属的 `UIEventStream` 子类把 Pydantic AI 事件翻译为协议事件。

**泛型参数**

| 参数 | 含义 |
|------|------|
| `RunInputT` | 协议专属运行输入类型（AG-UI 的 `RunAgentInput`、Vercel 的 `RequestData`） |
| `MessageT` | 协议专属消息类型 |
| `EventT` | 协议专属事件类型 |
| `AgentDepsT` | Agent 依赖类型 |
| `OutputDataT` | Agent 输出类型 |

**字段表**

| 字段 | 类型 / 默认值 | 说明 |
|------|---------------|------|
| `agent` | `AbstractAgent[AgentDepsT, OutputDataT]` | 要运行的 Pydantic AI Agent |
| `run_input` | `RunInputT` | 协议专属运行输入对象 |
| `accept` | `str \| None = None` | 请求的 `Accept` 头，决定流式响应的编码方式 |
| `manage_system_prompt` | `Literal['server','client'] = 'server'` | 谁拥有 system prompt；见下 |
| `allowed_file_url_schemes` | `frozenset[str] = frozenset({'http','https'})` | 客户端消息中允许的 `FileUrl` scheme |
| `allowed_file_url_force_download` | `frozenset[ForceDownloadMode] = frozenset()` | 额外信任的 `force_download` 取值 |
| `allow_uploaded_files` | `bool = False` | 是否信任客户端 `UploadedFile` 引用 |
| `strip_workspace_refs` | `bool = True` | 是否把客户端消息的 `ModelResponse.workspace_ref` 重置为 `None` |

（`_: KW_ONLY` 之后的所有字段都是 keyword-only。）

**`manage_system_prompt` 语义**

- 只影响 `system_prompt`；`instructions` 永远由 Agent 在每个请求注入。
- `'server'`（默认）：Agent 配置的 `system_prompt` 是权威。前端发的 `SystemPromptPart` 会被剥离并告警，Agent 自己的 system prompt 在首个请求开头通过 [`ReinjectSystemPrompt`](../pydantic_ai_slim/pydantic_ai/capabilities/) 能力重新注入。
- `'client'`：前端拥有 system prompt。前端 `SystemPromptPart` 原样保留，Agent 配置的 `system_prompt` 不注入；如需 server 模式那样的回退，可自行给 Agent 加 `ReinjectSystemPrompt`。

**信任模型（安全相关字段）**

这些字段共同构成「客户端提交内容不可信」的边界：

- `allowed_file_url_schemes`：非 HTTP scheme（`s3://`、`gs://` 等）会让模型 provider 用服务端 IAM 角色/服务账号去拉取对象——能提供任意 URL 的客户端就能读取该身份能触及的任何东西。HTTPS URL 可安全转发（provider 用自己的公有凭据抓取，或库自身的 `download_item` 做 SSRF 防护）。上传场景优先用预签名 `https://`。
- `allowed_file_url_force_download`：`False` 始终允许；`force_download=True` 让服务端自行下载，`'allow-local'` 还会关闭 `download_item` 的私网 IP 拦截（可被用来探测内网）。默认两者都会被重置为 `False` 并告警。
- `allow_uploaded_files`：`UploadedFile` 引用同一类「服务端凭据抓取对象」风险，默认丢弃并告警。这是纯入站的安全设置，不影响出站序列化。
- `strip_workspace_refs`：最新 workspace 引用会提供给能力的 `get_workspace`，能设置它的客户端可能把重连指向一个用服务端凭据附加的环境。Vercel AI 与 AG-UI 协议本身不携带 workspace 引用，此设置只影响 `sanitize_messages` 与自定义适配器。

**`DEFAULT_ALLOWED_CONTENT_TYPES`（CSRF 控制）**

```python
DEFAULT_ALLOWED_CONTENT_TYPES = frozenset({'application/json'})
```

这是 CSRF 控制而非内容协商。浏览器可以无预检跨域发送三个 CORS 安全列表内容类型（`text/plain`、`multipart/form-data`、`application/x-www-form-urlencoded`）或不带内容类型，且都能携带 JSON body。要求 `application/json`（不在安全列表）会强制预检。这是白名单而非黑名单：黑名单会漏掉「无内容类型」这一情况，也会在安全列表将来扩容时静默失效。

`_check_content_type(request, allowed_content_types)`：

- `allowed_content_types is None` 时直接返回（跳过检查）；
- 否则把配置项也做 `strip().lower()` 归一化后比较请求 `content-type` 的第一段；
- 不匹配则抛 Starlette `HTTPException(415)`，`detail` 形如 `` Expected `Content-Type: application/json`, got text/plain ``。

**类方法 `from_request`**

```python
@classmethod
async def from_request(
    cls, request: Request, *,
    agent: AbstractAgent[AgentDepsT, OutputDataT],
    manage_system_prompt: Literal['server', 'client'] = 'server',
    allowed_file_url_schemes: frozenset[str] = frozenset({'http', 'https'}),
    allowed_file_url_force_download: frozenset[ForceDownloadMode] = frozenset(),
    allow_uploaded_files: bool = False,
    strip_workspace_refs: bool = True,
    allowed_content_types: frozenset[str] | None = DEFAULT_ALLOWED_CONTENT_TYPES,
    **kwargs: Any,
) -> Self:
```

流程：`_check_content_type` → 构造 `cls(agent=..., run_input=cls.build_run_input(await request.body()), accept=request.headers.get('accept'), ...)`。多余 kwargs 转发给适配器构造函数（供子类扩展）。

**抽象 / 可覆写方法全表**

| 成员 | 类型 | 说明 |
|------|------|------|
| `build_run_input(cls, body: bytes) -> RunInputT` | `@classmethod` + `@abstractmethod` | 从请求 body 构造运行输入 |
| `load_messages(cls, messages: Sequence[MessageT]) -> list[ModelMessage]` | `@classmethod` + `@abstractmethod` | 协议消息 → Pydantic AI 消息 |
| `dump_messages(cls, messages: Sequence[ModelMessage]) -> list[MessageT]` | `@classmethod` | Pydantic AI 消息 → 协议消息（默认 `NotImplementedError`） |
| `build_event_stream(self) -> UIEventStream[...]` | `@abstractmethod` | 构造协议事件流转换器 |
| `messages` | `@cached_property` + `@abstractmethod` | 运行输入对应的 Pydantic AI 消息 |
| `toolset` | `@cached_property` | 前端工具对应的 toolset（默认 `None`） |
| `state` | `@cached_property` | 前端状态（默认 `None`） |
| `deferred_tool_results` | `@cached_property` | 从请求提取的延迟工具结果（默认 `None`） |
| `conversation_id` | `@cached_property` | 会话/线程 ID，作为 `gen_ai.conversation.id` span 属性（默认 `None`） |
| `sanitize_messages(self, messages, *, deferred_tool_results=None)` | 方法 | 剥离不可信客户端 part |

**`sanitize_messages`**

委托给 [`pydantic_ai.messages.sanitize_messages`](../pydantic_ai_slim/pydantic_ai/messages.py)，带入适配器设置：

- 仅当 `manage_system_prompt == 'server'` 时剥离 `SystemPromptPart`（并触发 `ReinjectSystemPrompt` 在下一请求重注入）；
- file URL scheme 与 `force_download` 按两个白名单校验；`UploadedFile` 仅在 `allow_uploaded_files=True` 时保留；
- 除非 `strip_workspace_refs=False`，重置 `ModelResponse.workspace_ref`；
- 若提供了 `deferred_tool_results`，把其 `approvals` + `calls` 收集为 `resolved_tool_call_ids`，让历史末尾与解析结果对应的工具调用被保留（HITL 恢复需要）。

注意：**调用方传入的 `message_history` 不经过 `sanitize_messages`**——它被视为来自服务端持久化。若 `message_history` 来自不可信客户端，应先自行调用 `sanitize_messages`。

**运行方法**

| 方法 | 说明 |
|------|------|
| `transform_stream(stream, on_complete=None, on_cancel=None)` | 委托 `build_event_stream().transform_stream(...)` |
| `encode_stream(stream)` | 委托 `build_event_stream().encode_stream(...)` |
| `streaming_response(stream)` | 委托 `build_event_stream().streaming_response(...)`，产出 Starlette `StreamingResponse` |
| `run_stream_native(...)` | 运行 Agent 并流式产出 `NativeEvent` |
| `run_stream(...)` | `transform_stream(run_stream_native(...))` |
| `dispatch_request(...)` | 类方法：整条 HTTP 请求 → 流式响应 |

**`run_stream_native` 关键参数**

签名含有大量与 `Agent.iter` 对齐的参数（`output_type`、`message_history`、`deferred_tool_results`、`conversation_id`、`run_id`、`model`、`instructions`、`deps`、`model_settings`、`usage_limits`、`cancellation_token`、`usage`、`metadata`、`infer_name`、`toolsets`、`capabilities`、`workspace`）。内部逻辑：

1. `deferred_tool_results` / `conversation_id` 缺省时回落到 `self.deferred_tool_results` / `self.conversation_id`；
2. `frontend_messages = self.sanitize_messages(self.messages, deferred_tool_results=...)`；
3. **若已有 `message_history`，对 `frontend_messages` 调 `_drop_compaction_parts`**——客户端提交的 compaction 边界会裁掉可信的服务端历史，所以只承认服务端自己的边界；
4. `message_history = [*message_history, *frontend_messages]`；
5. 若 `self.toolset` 存在：`output_type = [output_type or agent.output_type, DeferredToolRequests]`，并把前端 toolset 追加进 `toolsets`；
6. **状态注入**：若 `deps` 是 `StateHandler` 实例：`raw_state = self.state or {}`，`deps.state` 为 `BaseModel` 时用 `type(deps.state).model_validate(raw_state)` 校验，否则直接赋值；若 `self.state` 非空但 `deps` 不实现协议，发 `UserWarning` 并忽略；
7. **`manage_system_prompt == 'server'` 时**追加 `ReinjectSystemPrompt(replace_existing=True, id=None)` 到本次运行的 capabilities——`id=None` 让它避开 `reinject_system_prompt` 这个固定默认 id 槽位，从而不与用户自带的 reinjector 冲突；
8. 用 `async with self.agent.run_stream_events(...)` 逐事件 `yield`。

**`dispatch_request`**

整条请求流水线：

```
_check_content_type → from_request（可能抛 ValidationError）
  → ValidationError 时返回 422 JSON（非 UTF-8 body 用 e.json(include_input=False)）
  → adapter.streaming_response(adapter.run_stream(...))
```

注：`dispatch_request` 缺 `starlette` 时报 `ImportError` 并提示安装 `pydantic-ai-slim[ui]`。

**辅助函数**

| 函数 | 说明 |
|------|------|
| `resolve_allow_uploaded_files(allow_uploaded_files, preserve_file_data, *, stacklevel=3)` | 把已废弃的 `preserve_file_data` 映射到 `allow_uploaded_files`，非 `None` 时发 `PydanticAIDeprecationWarning` |
| `compaction_payload(part) -> dict[str, Any]` | `CompactionPart` → UI payload（省略值为 `None` 的字段） |
| `compaction_part_from_payload(payload) -> CompactionPart \| None` | payload → `CompactionPart`；校验失败返回 `None`（跳过而非降级，避免空 part 变成可见性边界） |
| `tool_availability_delta_from_payload(payload) -> ToolAvailabilityDeltaPart` | payload → 类型化 part；失败返回空 part，并用 `_TOOL_NAME_PATTERN`（`[a-zA-Z0-9_-]{1,64}`）过滤工具名 |

### 1.3 `UIEventStream`（[`ui/_event_stream.py`](../pydantic_ai_slim/pydantic_ai/ui/_event_stream.py)）

```python
SSE_CONTENT_TYPE = 'text/event-stream'
NativeEvent: TypeAlias = AgentStreamEvent | AgentRunResultEvent[Any]
OnCompleteFunc: TypeAlias = _CallbackFunc[AgentRunResult[Any], EventT]
OnCancelFunc:     TypeAlias = _CallbackFunc[RunCancelled, EventT]
```

```python
@dataclass
class UIEventStream(ABC, Generic[RunInputT, EventT, AgentDepsT, OutputDataT]):
```

**字段表**

| 字段 | 说明 |
|------|------|
| `run_input: RunInputT \| None = None` | 构造它的运行输入；独立编码器场景（durable workflow / 队列 / websocket 扇出）为 `None` |
| `accept: str \| None = None` | `Accept` 头，决定编码 |
| `message_id: str = field(default_factory=lambda: str(uuid4()))` | 下一个事件的 message ID |
| `_turn: Literal['request','response'] \| None` | 当前回合 |
| `_result: AgentRunResult \| None` | 完成后的结果 |
| `_cancelled: RunCancelled \| None` | 取消信息 |
| `_final_result_event: FinalResultEvent \| None` | 错误路径的兜底 |
| `_pending_tool_calls: dict[str, _PendingToolCall]` | 已派发未完成的工具调用（按 `tool_call_id` 索引） |
| `_open_part: TextPart \| ThinkingPart \| ToolCallPart \| NativeToolCallPart \| None` | 当前正在流式输出的 part |
| `_open_part_index: int` | 上述 part 的索引，用于在错误时重建 `PartEndEvent` |
| `_open_part_deltas: list[...]` | 仅在需要合成 end 事件时才用于回放 part |

`_PendingToolCall = NamedTuple('_PendingToolCall', kind: Literal['function','output'], tool_name: str)`。

**`transform_stream` 状态机**

```python
async def transform_stream(self, stream, on_complete=None, on_cancel=None) -> AsyncIterator[EventT]
```

主循环要点：

1. 先 `before_stream()` 发出开头事件。
2. 遍历原生事件：
   - `PartStartEvent` → `_turn_to('response')`；
   - `PartEndEvent` → 清空 `_open_part` 与 deltas（同一时刻只有一个 part 打开）；
   - `ToolCallEvent`（`FunctionToolCallEvent` / `OutputToolCallEvent` / `ToolCallEvent`）→ 记入 `_pending_tool_calls`；output 类型时清掉 `_final_result_event`；`_turn_to('request')`；
   - `AgentRunResultEvent` → 存 `self._result`，`_turn_to(None)`，并在有 `on_complete` 时派发回调；
   - `FinalResultEvent` → 记录兜底；
   - `ToolResultEvent` → 从 `_pending_tool_calls` 弹出；
   - 之后交给 `handle_event(event)` 产出协议事件（`PartDeltaEvent` 会被 `_record_part_delta` 记录）；
   - 只有 `PartStartEvent` 且 part 类型受支持时，才把 `_open_part` 置为该 part（在 start 事件 emit 之后，避免钩子中途抛错留下客户端从未见过的 part）。
3. **错误路径**（`except Exception`）：
   - 先关闭打开的 part（回放 `_open_part_deltas` 得到最新 part，再 `handle_part_end(PartEndEvent(...))`），保证在错误 chunk 处中断的客户端（如 AI SDK）不会卡在流式状态；
   - 把 `FinalResultEvent` 中挂起的 output 工具调用补进 `_pending_tool_calls`；
   - 对每个挂起工具调用 `_turn_to('request')`，构造 `ToolReturnPart(content='Tool execution was interrupted by an error.' 或 INTERRUPTED_TOOL_RETURN_CONTENT, outcome='failed' 或 'interrupted')`，按 kind 走 `handle_output_tool_result` / `handle_function_tool_result`；
   - `exc` 是 `RunCancelled` 视为第一方取消：存 `_cancelled`，派发 `on_cancel` 与 `on_cancelled`；否则走 `on_error`。
4. `finally` 关闭源流（`aclose_if_supported`），再 `_turn_to(None)`、`after_stream()`。

`_turn_to(to_turn)` 在切回合时触发对应 after/before 钩子：离开 `'request'` → `after_request()`，离开 `'response'` → `after_response()`；进入 `'request'` → `before_request()`，进入 `'response'` → `before_response()`。

`_dispatch_callback` 支持三类回调：async generator（快路径）、async callable、普通 callable（用 `run_in_executor` 跑，再接受其返回的 async iterator 或 awaitable）。

**钩子全表**

| 钩子 | 触发时机 |
|------|----------|
| `before_stream()` | 所有 Agent 事件处理之前 |
| `after_stream()` | 所有事件处理之后 |
| `on_error(error)` | 流式过程中出错 |
| `on_cancelled(cancelled)` | 第一方取消（默认转发到 `on_error`） |
| `before_request()` / `after_request()` | 进入/离开请求回合 |
| `before_response()` / `after_response()` | 进入/离开响应回合 |

**`handle_event` 扇出**

| 原生事件 | 目标方法 |
|----------|----------|
| `PartStartEvent` | `handle_part_start` |
| `PartDeltaEvent` | `handle_part_delta` |
| `PartEndEvent` | `handle_part_end` |
| `FinalResultEvent` | `handle_final_result` |
| `EnqueuedMessagesEvent` | `handle_enqueued_messages` |
| `FunctionToolCallEvent` | `handle_function_tool_call` |
| `FunctionToolResultEvent` | `handle_function_tool_result` |
| `ToolAvailabilityDeltaEvent` | `handle_tool_availability_delta` |
| `OutputToolCallEvent` | `handle_output_tool_call` |
| `OutputToolResultEvent` | `handle_output_tool_result` |
| `DeferredToolRequestsEvent` | `handle_deferred_tool_requests` |
| `DeferredToolResultsEvent` | `handle_deferred_tool_results` |
| `CustomEvent` | `handle_custom_event` |
| `CapabilityEvent` | `handle_capability_event` |
| `AgentRunResultEvent` | `handle_run_result` |
| Realtime 系列事件 | 忽略（`pass`，见下） |

Realtime 会话事件不经过 UI 事件流——`handle_event` 中显式列出 `RealtimeTurnCompleteEvent` / `RealtimeInputSpeechStartEvent` / ... / `RealtimeSessionErrorEvent` 并 `pass`。

**`CustomEvent` 失败关闭**

`CustomEvent` 分支只在其 `ui` 为真**且**不是 `UnknownCustomEvent` 时才转发。理由：一个「未知」事件意味着该进程从未导入定义它的模块，其 `ui` 标志不能说明应用声明了什么（标志在类上，不在 wire 上）。转发未解析的 custom event 可能泄漏一个在发出处被声明为 `ui=False` 的事件负载，所以未解析分支失败关闭。

**`handle_part_start` / `handle_part_delta` / `handle_part_end` 扇出**

- start：`TextPart` → `handle_text_start(part, follows_text=...)`；`ThinkingPart` → `handle_thinking_start(part, follows_thinking=...)`；`ToolCallPart` → `handle_tool_call_start`；`NativeToolCallPart` → `handle_builtin_tool_call_start`；`NativeToolReturnPart` → `handle_builtin_tool_return`；`FilePart` → `handle_file`；`CompactionPart` → `handle_compaction`；`SpeechPart` 忽略。
- delta：`TextPartDelta` → `handle_text_delta`；`ThinkingPartDelta` → `handle_thinking_delta`；`ToolCallPartDelta` → `handle_tool_call_delta`；`SpeechPartDelta` 忽略。
- end：`TextPart` → `handle_text_end(part, followed_by_text=...)`；`ThinkingPart` → `handle_thinking_end(part, followed_by_thinking=...)`；`ToolCallPart` → `handle_tool_call_end`；`NativeToolCallPart` → `handle_builtin_tool_call_end`；`NativeToolReturnPart` / `FilePart` / `CompactionPart` 无需 end；`SpeechPart` 忽略。

**编码与响应**

| 成员 | 说明 |
|------|------|
| `encode_event(event) -> str` | 抽象；协议专属编码 |
| `encode_stream(stream)` | 逐事件 `encode_event` |
| `streaming_response(stream)` | Starlette `StreamingResponse(encode_stream(...), headers=response_headers, media_type=content_type)`；缺 starlette 时抛 `ImportError` |
| `content_type` | 默认返回 `SSE_CONTENT_TYPE`；子类可结合 `self.accept` |
| `response_headers` | 默认 `None` |
| `cancelled` | `_cancelled` 的只读访问 |
| `new_message_id()` | 生成并存储新 message ID |

**基类 `handle_*` 默认方法**（全部为 async generator；子类覆盖具体方法而非 `handle_event`）：`handle_text_start` / `handle_text_delta` / `handle_text_end`、`handle_thinking_*`、`handle_tool_call_*`、`handle_builtin_tool_call_start` / `handle_builtin_tool_call_end` / `handle_builtin_tool_return`、`handle_file`、`handle_compaction`、`handle_final_result`、`handle_enqueued_messages`、`handle_function_tool_call` / `handle_function_tool_result`、`handle_tool_availability_delta`、`handle_output_tool_call` / `handle_output_tool_result`、`handle_custom_event`、`handle_capability_event`、`handle_deferred_tool_requests` / `handle_deferred_tool_results`、`handle_run_result`。其中 `handle_enqueued_messages` / `handle_tool_availability_delta` / `handle_custom_event` / `handle_capability_event` / `handle_deferred_*` 默认不产出任何协议事件。

### 1.4 `MessagesBuilder` / `BuilderCheckpoint` / 状态类型

[`ui/_messages_builder.py`](../pydantic_ai_slim/pydantic_ai/ui/_messages_builder.py)：

```python
@dataclass(frozen=True)
class BuilderCheckpoint:
    message_count: int
    last_message: ModelMessage | None
    last_message_part_count: int

@dataclass
class MessagesBuilder:
    messages: list[ModelMessage] = field(default_factory=list[ModelMessage])
```

- `add(part)`：若 part 是 `ModelRequestPart` 且尾消息是 `ModelRequest`，追加到其 parts；否则新起 `ModelRequest`。response part 同理。注意 `add` **重新赋值** `parts` 列表而非原地修改。
- `checkpoint()`：快照 `message_count` / `last_message` / `last_message_part_count`。
- `last_modified(checkpoint, *, of_type)`：返回自快照以来最新被创建或扩展的、类型匹配的 `ModelMessage`，用于把元数据归属到「刚构建的那条消息」。

`BuilderCheckpoint` 是进程内关联 token（`last_message` 是活引用），不适于 pickle/JSON 往返；快照与查询之间也不要原地修改 `last_message.parts`。

[`ui/_adapter.py`](../pydantic_ai_slim/pydantic_ai/ui/_adapter.py)：

```python
@runtime_checkable
class StateHandler(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]
    @property
    def state(self) -> Any: ...
    @state.setter
    def state(self, state: Any) -> None: ...

@dataclass
class StateDeps(Generic[StateT]):
    state: StateT
```

`StateHandler` 要求实现类是 dataclass 且有 `state` 字段——因为在 `run_stream_native` 里用 `deps.state = ...` 赋值而不是 `replace`。`StateDeps[...]` 是现成实现，`StateT` 必须是 `BaseModel` 子类。

### 1.5 共享工具（[`ui/_utils.py`](../pydantic_ai_slim/pydantic_ai/ui/_utils.py)）

- `INTERNAL_METADATA_KEY = '__pydantic_ai__'`：保留命名空间，适配器从不读写客户端可控元数据里的同名键。
- `set_ui_message_id(message, ui_message_id)` / `get_ui_message_id(message) -> str | None`：把 UI 消息 id 存在该命名空间下，供 dump/load 往返。

---

### 1.6 AG-UI 适配器（[`ui/ag_ui/`](../pydantic_ai_slim/pydantic_ai/ui/ag_ui/)）

包导出：`AGUIAdapter`、`AGUIEventStream`、`DEFAULT_AG_UI_VERSION`。

**`AGUIAdapter`**

```python
@dataclass
class AGUIAdapter(UIAdapter[RunAgentInput, Message, BaseEvent, AgentDepsT, OutputDataT]):
    ag_ui_version: str = DEFAULT_AG_UI_VERSION
    preserve_file_data: bool = False
```

`ag_ui_version` 控制行为阈值：

| 版本阈值 | 行为 |
|----------|------|
| `< 0.1.11` | 流式发 `THINKING_*`；`dump_messages` 丢弃 `ThinkingPart` |
| `>= 0.1.11` | 流式发 `REASONING_*`（含加密元数据）；`dump_messages` 把 `ThinkingPart` 作为 `ReasoningMessage` 输出，thinking 签名与 provider 元数据可完整往返 |
| `>= 0.1.15` | 发类型化多模态输入内容（`ImageInputContent` 等），而非通用 `BinaryInputContent` |

`load_messages` 始终接受 `ReasoningMessage` 与多模态内容类型，不受此设置影响；`build_run_input` 会跳过已安装 `ag-ui-protocol` 尚不认识的入站内容类型而非拒绝请求。

覆盖的 `cached_property`：

| 属性 | 来源 |
|------|------|
| `messages` | `load_messages(self.run_input.messages, preserve_file_data=...)` |
| `toolset` | `run_input.tools` 非空时构造 `_AGUIFrontendToolset`（`ExternalToolset` 子类） |
| `state` | `run_input.state`（是 str→str 字典且非空时返回） |
| `conversation_id` | `run_input.thread_id` |
| `deferred_tool_results` | 把 `run_input.resume[]` 翻译为 `DeferredToolResults`（见下） |

`_AGUIFrontendToolset` 把每个 AG-UI `Tool` 转成 `ToolDefinition(name, description, parameters_json_schema=tool.parameters or {})`。

**`deferred_tool_results`（deny-by-default）**

- `HAS_INTERRUPTS` 为假（`ag-ui-protocol < 0.1.19`）或 `resume` 为空 → `None`；
- 每个 `ResumeEntry` 映射为一个按原 `tool_call_id` 索引的审批：
  - `status == 'cancelled'` → `ToolDenied('Cancelled by user.')`；
  - payload 校验通过且 `approved is True` 且有 `editedArgs` dict → `ToolApproved(override_args=...)`；
  - `approved is True` 无编辑 → `ToolApproved()`；
  - 其他（`False`、缺失、`null`、非 bool、非 dict、非 dict editedArgs、非字符串 reason）→ 若能取到非空字符串 `reason` 则 `ToolDenied(reason)`，否则 `ToolDenied()`。

**`load_messages`**

- 用 `MessagesBuilder` 逐条处理 `UserMessage` / `SystemMessage` / `DeveloperMessage` / `AssistantMessage` / `ToolMessage` / `ReasoningMessage` / `ActivityMessage`；
- `ToolCall`/`ToolMessage.encrypted_value` 只在已安装版本 `>= 0.1.11` 时读取（`use_encrypted_value`）；
- `tool_kind` 来自客户端声明，先设在**基类 part** 上，最后由 `narrow_message_parts(builder.messages)` 做一次尽力提升（校验不过则丢弃声明）；
- 内建工具调用 id 形如 `pyd_ai_builtin|provider|original_id`（`BUILTIN_TOOL_CALL_ID_PREFIX`），由 `parse_builtin_tool_call_id` 拆回；
- `ToolMessage.content` 经 `rehydrate_tool_return_content` 复原结构化/多模态内容；
- 每条 AG-UI 消息的 id 通过 `builder.last_modified(...)` 归属到对应 `ModelRequest`/`ModelResponse` 并 `set_ui_message_id`。

**`dump_messages` 的有损性**（docstring 明确列出）：`ModelRequest.metadata` 与顶层 `ModelResponse.provider_details` 丢失；`TextPart`/`ToolCallPart` 的 `id`/`provider_name`/`provider_details` 丢失；非法 JSON 的 args 被改写为 `{"INVALID_JSON":"<raw args>"}`；`tool_kind` 在 `<0.1.11` 丢失；错误/拒绝的返回值不保留 `tool_kind`；`RetryPromptPart` 变成 `ToolReturnPart`/`UserPromptPart`；`NativeToolReturnPart` 总是紧跟其 `NativeToolCallPart`；`CachePoint`/`UploadedFile` 在未开 `preserve_file_data` 时丢弃；`FilePart` 未开 `preserve_file_data` 时丢弃；`FileUrl.force_download` 在 `<0.1.15` 丢失；部分顺序在 text 跟随 tool call 时可能改变。多模态工具返回内容**始终**往返（内联在 `ToolMessage.content`）。

**`AGUIEventStream`**（[`ui/ag_ui/_event_stream.py`](../pydantic_ai_slim/pydantic_ai/ui/ag_ui/_event_stream.py)）

```python
@dataclass
class AGUIEventStream(UIEventStream[RunAgentInput, BaseEvent, AgentDepsT, OutputDataT]):
    ag_ui_version: str = DEFAULT_AG_UI_VERSION
    thread_id: str = field(default_factory=_generate_id)
    run_id: str = field(default_factory=_generate_id)
```

- `thread_id` / `run_id` 用 `_GeneratedID(str)` 标记「自生成」；`__post_init__` 中若给了 `run_input`，其身份**覆盖**显式传入值并发 `UserWarning`；两个 `_GeneratedID` 值不会触发告警。
- `_event_encoder` = `ag_ui.encoder.EventEncoder(accept=self.accept or SSE_CONTENT_TYPE)`；`content_type` 与 `encode_event` 都委托它。
- `handle_event` 覆盖：给所有 AG-UI 事件补 `timestamp`（毫秒）。
- `before_stream` 发 `RunStartedEvent(thread_id, run_id, timestamp)`。
- `before_response` 调 `new_message_id()`，避免后续 response 的 part 绑到上一个 response。
- `after_stream`：出错时不发；被取消时发无 outcome 的 `RunFinishedEvent`（AG-UI 无取消 outcome）；否则在 `HAS_INTERRUPTS` 时带 `outcome=self._build_outcome()`。
- `_build_outcome()`：版本 `< 0.1.19` 返回 `None`；结果为 `DeferredToolRequests` 且有 `approvals` 时返回 `RunFinishedInterruptOutcome(interrupts=[approval_to_interrupt(...)])`，否则 `RunFinishedSuccessOutcome()`。
- `handle_text_start`：`follows_text` 复用 `message_id`，否则新建并发 `TextMessageStartEvent`；`part.content` 非空时发 `TextMessageContentEvent`。
- `handle_thinking_start/delta/end`：按 `_use_reasoning`（`>= REASONING_VERSION`）懒加载 `_thinking_0_11` 或 `_thinking_0_10` 的实现；断言 `handle_thinking_start` 必须先于 delta/end。
- 工具调用：`handle_builtin_tool_call_start` 生成全局唯一内建 id 并记入 `_builtin_tool_call_ids`；`_handle_tool_call_start` 若当前 response 尚无 text（`_started_message_id != parent_message_id`），先发一对空的 `TextMessageStartEvent`+`TextMessageEndEvent` 公布 parent message id，再发 `ToolCallStartEvent`；`tool_kind` 通过 `ReasoningEncryptedValueEvent(subtype='tool-call', ...)` 承载；`ToolCallArgsEvent` 对 `str` args 原样发（可能是待拼接的 JSON 片段）。
- `_handle_tool_result` / `handle_builtin_tool_return` 用 `dump_tool_return_content` 序列化内容，并通过 `ReasoningEncryptedValueEvent(subtype='message', ...)` 承载非 `success` 的 outcome 与 `tool_kind`。
- `handle_tool_availability_delta` / `handle_compaction` 在版本 `< ACTIVITY_EVENTS_VERSION (0.1.19)` 时直接返回，否则发 `ActivitySnapshotEvent`。

**版本常量**（[`ui/ag_ui/_utils.py`](../pydantic_ai_slim/pydantic_ai/ui/ag_ui/_utils.py)）

| 常量 | 值 | 含义 |
|------|----|------|
| `ENCRYPTED_VALUE_VERSION` | `(0,1,11)` | `ToolCall`/`ToolMessage.encrypted_value` 字段 |
| `REASONING_VERSION` | `(0,1,11)` | `REASONING_*` 事件族 |
| `REASONING_MESSAGE_ROLE_VERSION` | `(0,1,14)` | `ReasoningMessageStartEvent.role` 从 `'assistant'` 改为 `'reasoning'` |
| `MULTIMODAL_VERSION` | `(0,1,15)` | 类型化多模态输入内容 |
| `ACTIVITY_EVENTS_VERSION` / `INTERRUPTS_VERSION` | `(0,1,19)` | activity 事件 / 中断生命周期 |

其他工具：`parse_ag_ui_version`（去预发布后缀）、`detect_ag_ui_version`（缺失时回落 `'0.1.10'`）、`DEFAULT_AG_UI_VERSION`、`REASONING_MESSAGE_ROLE`、`thinking_encrypted_metadata`、`tool_kind_encrypted_value` / `tool_kind_encrypted_value_kwargs`、`parse_encrypted_tool_kind` / `parse_encrypted_outcome`、`parse_builtin_tool_call_id`、`dump_tool_return_content` / `rehydrate_tool_return_content`。加密负载统一嵌套在顶层 `pydantic_ai` 键下（`_ENCRYPTED_VALUE_NAMESPACE`），避免把真正的 provider 加密 blob 误读为自身数据。

**`_forward_compat.py`**

`skip_unknown_tagged_items(body) -> tuple[JsonValue, frozenset[str]]`：当 `RunAgentInput.model_validate_json` 因「已安装版本不认识的判别标签」失败时，重读 body，剔除 `messages[]` 里未知 `role` 的消息、以及 user message 列表 `content` 里未知 `type` 的内容项。已知集合从已安装 union 运行时读出（`_known_tags`），不硬编码。未知 role 的消息只有同时满足「字符串 `id`」（`Message` union 共有契约）才被跳过，否则保留并继续报错。

**`_interrupt.py`**

- `HAS_INTERRUPTS`：门控 `ag-ui-protocol >= 0.1.19` 的中断类型（`Interrupt` / `ResumeEntry` / `RunFinishedInterruptOutcome` / `RunFinishedSuccessOutcome`），否则无操作 stub。
- `_ResumePayload`（camelCase alias，`StrictBool approved` 必填，可选 `edited_args` / `reason`）：审批恢复 payload 的**唯一真相**，其 JSON schema（`_RESUME_RESPONSE_SCHEMA`）出站广告在 `Interrupt.response_schema` 上，入站用它校验。
- `approval_to_interrupt(call, metadata) -> Interrupt`：id 为 `int-{tool_call_id}`（`INTERRUPT_ID_PREFIX='int-'`）。
- `interrupt_id_to_tool_call_id(interrupt_id)`：反向去前缀，前缀不符抛 `UserError`。
- `resume_entry_to_approval(entry)`：`cancelled` → `ToolDenied('Cancelled by user.')`；校验失败 → `ToolDenied()`；`approved` 且有 `edited_args` → `ToolApproved(override_args=...)`；否则按 `reason` 决定 `ToolDenied(message=...)` 或默认。

**`_multimodal.py`**：`media_url_to_multimodal` / `binary_to_multimodal` / `multimodal_input_to_content`，在 `InputContent` 的通用 `metadata` 里用 `vendor_metadata` / `force_download` 两个专用键存储，读回时只认自己的键。

---

### 1.7 Vercel AI 适配器（[`ui/vercel_ai/`](../pydantic_ai_slim/pydantic_ai/ui/vercel_ai/)）

包导出：`VercelAIAdapter`、`VercelAIEventStream`。转换自 Vercel AI SDK v5 的 `ui-messages.ts`。

**类型模型**（[`ui/vercel_ai/_models.py`](../pydantic_ai_slim/pydantic_ai/ui/vercel_ai/_models.py)）：`CamelBaseModel(BaseModel, ABC)`，`model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra='forbid')`。所有协议类型都继承它。

**请求类型**（[`ui/vercel_ai/request_types.py`](../pydantic_ai_slim/pydantic_ai/ui/vercel_ai/request_types.py)）：

- 消息 part：`TextUIPart`、`ReasoningUIPart`、`SourceUrlUIPart`、`SourceDocumentUIPart`、`FileUIPart`、`StepStartUIPart`、`DataUIPart`、`ToolInputStreamingPart` / `ToolInputAvailablePart` / `ToolOutputAvailablePart` / `ToolOutputErrorPart` / `ToolApprovalRequestedPart` / `ToolApprovalRespondedPart` / `ToolOutputDeniedPart` 及其 `DynamicTool*` 对应物；
- 审批：`ToolApprovalRequested` / `ToolApprovalResponded` / `ToolApproval = ToolApprovalRequested | ToolApprovalResponded`；
- 联合 alias：`ToolUIPart`、`DynamicToolUIPart`、`UIMessagePart`、`UIMessage`；
- 请求：`SubmitMessage` / `RegenerateMessage`，`RequestData = Annotated[SubmitMessage | RegenerateMessage, Discriminator('trigger')]`。

**响应类型**（[`ui/vercel_ai/response_types.py`](../pydantic_ai_slim/pydantic_ai/ui/vercel_ai/response_types.py)）：

- `FinishReason = Literal['stop','length','content-filter','tool-calls','error','other'] | None`；
- Chunk 族：`BaseChunk`（抽象），`TextStartChunk`/`TextDeltaChunk`/`TextEndChunk`、`ReasoningStartChunk`/`ReasoningDeltaChunk`/`ReasoningEndChunk`、`ErrorChunk`、`ToolInputStartChunk`/`ToolInputDeltaChunk`/`ToolInputAvailableChunk`/`ToolInputErrorChunk`、`ToolOutputAvailableChunk`/`ToolOutputErrorChunk`/`ToolOutputDeniedChunk`、`ToolApprovalRequestChunk`、`SourceUrlChunk`/`SourceDocumentChunk`/`FileChunk`/`DataChunk`、`StartStepChunk`/`FinishStepChunk`、`StartChunk`/`FinishChunk`、`AbortChunk`、`MessageMetadataChunk`、`DoneChunk`。

**`VercelAIAdapter`**

```python
@dataclass
class VercelAIAdapter(UIAdapter[RequestData, UIMessage, BaseChunk, AgentDepsT, OutputDataT]):
    sdk_version: Literal[5, 6, 7] = 5
    server_message_id: str | None = None
    preserve_file_data: InitVar[bool | None] = None  # 废弃别名 → allow_uploaded_files
```

- `sdk_version`：默认 5（向后兼容）；`6` 启用工具审批流式；`7` 与 6 同 wire（v7 的 data-stream 协议等于 v6）。
- `__post_init__(preserve_file_data)`：`stacklevel=4` 指向用户 `VercelAIAdapter(...)` 调用。
- `from_request` / `dispatch_request` 都接受 `sdk_version` / `server_message_id` / `preserve_file_data` 并转发。
- `deferred_tool_results`：仅在 `sdk_version >= 6` 时提取 `approval-responded` part；`approved` → `True`，有 `reason` → `ToolDenied(message=...)`，否则 `False`。
- `conversation_id`：请求体顶层 `id`（chat ID）。
- `messages`：`load_messages(self.run_input.messages)`。

**`VercelAIEventStream`**

```python
@dataclass
class VercelAIEventStream(UIEventStream[RequestData, BaseChunk, AgentDepsT, OutputDataT]):
    sdk_version: Literal[5, 6, 7] = 5
    server_message_id: str | None = None
```

- `VERCEL_AI_DSP_HEADERS = {'x-vercel-ai-ui-message-stream': 'v1'}`（`response_headers`）。
- `encode_event`：`f'data: {event.encode(self.sdk_version)}\n\n'`（SSE）。
- `before_stream` 发 `StartChunk`；`before_response` 若已开始 step 则先 `FinishStepChunk` 再 `StartStepChunk`；`after_stream` 发 `FinishStepChunk`，未取消时发 `FinishChunk(finish_reason=...)`，最后 `DoneChunk`。
- `handle_run_result`：映射 finish reason（`_FINISH_REASON_MAP`），发一个 `MessageMetadataChunk`，`sdk_version>=6` 且输出为 `DeferredToolRequests` 时发 `ToolApprovalRequestChunk`。
- `on_error` 会先为所有已流式但从未 announce 的工具调用补发 `ToolInputAvailableChunk`（把 `_streamed_call_parts` 冲掉），`_finish_reason='error'`，发 `ErrorChunk`。
- `on_cancelled` 发 `AbortChunk(reason='The agent run was cancelled.')`。
- v6 校验失败路径：`args_valid is False` 时把调用存入 `_invalidated_tool_calls` 并**抑制** `tool-input-available`，随后在 `_handle_tool_result` 里改发 `ToolInputErrorChunk`（v5 无此 chunk，仍发 `tool-input-available`）。
- `handle_file` → `FileChunk`；`handle_compaction` → `DataChunk(type=COMPACTION_DATA_TYPE)`；`handle_tool_availability_delta` → `DataChunk(type=TOOL_AVAILABILITY_DELTA_DATA_TYPE)`；`handle_custom_event` 对 `DATA_CHUNK_TYPES` 原样透传，否则 `DataChunk(type=f'data-{event.name}')`。

**`_utils.py` 关键项**：`TOOL_AVAILABILITY_DELTA_DATA_TYPE='data-tool-availability-delta'`、`COMPACTION_DATA_TYPE='data-compaction'`、`PROVIDER_METADATA_KEY='pydantic_ai'`、`_PydanticAIMessageMetadata`（只往返 `timestamp`）、`tool_return_output`、`load_provider_metadata` / `dump_provider_metadata` / `dump_message_metadata` / `apply_message_metadata`、`DATA_CHUNK_TYPES`、`iter_metadata_chunks`、`iter_tool_approval_responses`。

---

### 1.8 内置 Web 应用（[`ui/_web/`](../pydantic_ai_slim/pydantic_ai/ui/_web/)）

包导出：`create_web_app`、`ModelsParam`、`DEFAULT_HTML_URL`、`OFFLINE_HTML_URL`。

**`create_api_app`**（[`ui/_web/api.py`](../pydantic_ai_slim/pydantic_ai/ui/_web/api.py)）

```python
def create_api_app(
    agent: Agent[AgentDepsT, OutputDataT],
    models: ModelsParam = None,
    native_tools: Sequence[AbstractNativeTool] | None = None,
    deps: AgentDepsT = None,
    model_settings: ModelSettings | None = None,
    instructions: str | None = None,
    sdk_version: Literal[5, 6, 7] = BUNDLED_UI_SDK_VERSION,  # = 7
) -> Starlette
```

`ModelsParam = Sequence[Model | KnownModelName | str] | Mapping[str, Model | KnownModelName | str] | None`。

路由：

| 方法/路径 | 处理 |
|-----------|------|
| `OPTIONS /chat` | `options_chat`：答 CORS 预检但**不带**任何 `Access-Control-Allow-*` 头（拒绝跨域） |
| `POST /chat` | `post_chat`：要求 `Content-Type: application/json`（否则 415）；用 `VercelAIAdapter.from_request` 解析；校验 `extra_data`（model / builtin_tools）；派发 `dispatch_request` |
| `GET /configure` | `configure_frontend`：返回 `ConfigureFrontend(models, builtin_tools)` |
| `GET /health` | `health`：`{'ok': True}` |

模型/工具配置：Agent 自身 model 最先，其后是传入 `models`；`ModelInfo`/`BuiltinToolInfo` 用 camelCase 别名序列化。已经在 Agent 上配置的 native tool 会被过滤掉（不会重复作为 UI 选项）。

**`create_web_app`**（[`ui/_web/app.py`](../pydantic_ai_slim/pydantic_ai/ui/_web/app.py)）

```python
def create_web_app(
    agent, models=None, native_tools=None, deps=None, model_settings=None,
    instructions=None, html_source: str | Path | None = None,
    sdk_version: Literal[5, 6, 7] = BUNDLED_UI_SDK_VERSION,
    allowed_hosts: Sequence[str] | None = None,
) -> Starlette
```

- 把 `create_api_app(...)` 挂到 `/api`，外层加 `HostValidationMiddleware(allowed_hosts=...)`；
- `allowed_hosts` 在 `create_web_app` 内先归一化，使坏模式从本次调用而非首个请求报错（Starlette 懒建中间件栈）；
- `GET /` 与 `GET /{id}` 由 `index` 服务聊天 UI HTML（`Cache-Control: public, max-age=3600`）。

HTML 来源：`html_source=None` 时用 `DEFAULT_HTML_URL`（`@pydantic/ai-chat-ui@{CHAT_UI_VERSION}`，`CHAT_UI_VERSION='2.1.0'`），本地缓存于 XDG/LOCALAPPDATA 缓存目录；`OFFLINE_HTML_URL` 是全内联的单文件版本，供气隙部署。缓存读写走工作线程并串行化，写用同目录临时文件 + `os.replace` 原子替换。

**`HostValidationMiddleware`**（[`ui/_web/_hosts.py`](../pydantic_ai_slim/pydantic_ai/ui/_web/_hosts.py)）

- 只读 `Host` 头（从不读 `X-Forwarded-Host` / `Forwarded`）；
- 归一化：去端口、去单个尾点、非法字符 `@/?#` 判为不可解析；
- 允许：任意 IP 字面量、`localhost` 及 `.localhost` 子域、`allowed_hosts` 中的匹配（`*.example.com` 只匹配子域，`*` 全放行）；
- 不允许：HTTP 返回 421 `text/plain`（带 `x-content-type-options: nosniff`），WebSocket 发 `websocket.close` 1008；
- 防御 DNS rebinding：攻击者能把名字指向 `127.0.0.1`，但改不了浏览器发出的 `Host` 头。

`BUNDLED_UI_SDK_VERSION: Literal[7] = 7`：内置 UI 带 v7 SDK，故 `/chat` 目标 `sdk_version=7`（7 与 6 同 wire，含启用审批流式的 chunk）。

---

## 2. Realtime（语音）

### 2.1 分层与入口

包 docstring 与 [`realtime/AGENTS.md`](../pydantic_ai_slim/pydantic_ai/realtime/AGENTS.md) 描述的分层：

| 模块 | 职责 |
|------|------|
| [`realtime/model.py`](../pydantic_ai_slim/pydantic_ai/realtime/model.py) | `RealtimeModel` ABC 与 `infer_realtime_model` |
| [`realtime/settings.py`](../pydantic_ai_slim/pydantic_ai/realtime/settings.py) | 设置词汇 |
| [`realtime/profiles.py`](../pydantic_ai_slim/pydantic_ai/realtime/profiles.py) | `RealtimeModelProfile` |
| [`realtime/codec.py`](../pydantic_ai_slim/pydantic_ai/realtime/codec.py) | 低层连接（codec）词汇 |
| [`realtime/_session.py`](../pydantic_ai_slim/pydantic_ai/realtime/_session.py) | `RealtimeSession` |
| [`realtime/_lifecycle.py`](../pydantic_ai_slim/pydantic_ai/realtime/_lifecycle.py) / [`realtime/_core.py`](../pydantic_ai_slim/pydantic_ai/realtime/_core.py) | 生命周期 v2 契约与 `SessionCore`（内部） |
| `openai.py` / `azure.py` / `google.py` / `xai.py` / `openai_live.py` | 具体 provider |
| `_openai_protocol.py` / `_openai_webrtc.py` | OpenAI 协议与 WebRTC 共享层 |

包 `__all__` 导出会话/模型/设置/控制面事件等；低层 codec 词汇（`RealtimeConnection`、codec 事件、turn-control 动词、profile helper）在 `pydantic_ai.realtime.codec` 下。

高层入口：

```python
agent.realtime(model) -> AgentRealtime      # AbstractAgent.realtime（agent/abstract.py:1787）
  .session(...)  -> AsyncGenerator[RealtimeSession]  # AgentRealtime.session（:2371）
```

`AgentRealtime` 携带 Agent 的 realtime 配置；`session()` 打开并 `yield` 一个已进入的会话：

```python
async def session(
    self, *,
    audio_retention: AudioRetention = 'transcript_only',
    handle_barge_in: bool = False,
    retain_images_every_n: int = 1,
    retain_images_max: int | None = 100,
    retain_audio_max_seconds: float | None = 1800,
    provider_session: RealtimeProviderSession | None = None,
) -> AsyncGenerator[RealtimeSession]: ...
```

`AgentRealtime` 还提供 `answer_webrtc_offer(sdp_offer)`、`create_client_secret(*, expires_after_seconds=None)`、`hang_up(session)`。

**实时会话不经过 `iter()`（无 graph 运行）**。`Agent.realtime` 的文档明确：请求—响应 graph 专属参数（`output_type`、`retries`、`event_stream_handler`、`deferred_tool_results`）不适用；capabilities 在连接时跑一次 `for_run`，工具钩子（`prepare_tools` 与 `tool_validate`/`tool_execute` 的 `before`/`after`/`wrap`/`on_error`）逐工具调用运行，run 钩子围绕会话跑一次，event-stream 钩子包裹会话迭代器；graph/model-request/output 钩子不运行。

### 2.2 模型抽象（[`realtime/model.py`](../pydantic_ai_slim/pydantic_ai/realtime/model.py)）

- `RealtimeError(ModelAPIError)`：连接/协议失败（握手失败、provider 关闭会话、send 失败、重连放弃）。被拒绝的 WebSocket upgrade 例外——它带 HTTP 状态，抛 `ModelHTTPError`。
- `RealtimeClientSecret`（frozen dataclass）：`value`（`repr=False`）、`expires_at`、`provider_details`（`repr=False`）。
- `RealtimeProviderSession`（Protocol）：`provider_name` 与 `session_id`。
- `WebRTCSession`（frozen）：`provider_name` / `session_id` / `provider_details`，`call_id` 是 `session_id` 的别名。
- `WebRTCAnswer`（frozen）：`sdp` + `session`。

`RealtimeModel(AbstractModel)`：

| 成员 | 说明 |
|------|------|
| `settings: RealtimeModelSettings \| None` | 默认设置 |
| `_profile: RealtimeModelProfileSpec \| None` | 用户 `profile=` 覆盖，作为最后一层 |
| `__init__(*, settings=None, profile=None)` | 存设置/profile，并 `preload_pricing_data()` |
| `supported_native_tools()` | classmethod，返回该模型类实现的原生工具类型集合（默认空） |
| `connect(...)` | **抽象**；返回 `AbstractAsyncContextManager[RealtimeConnection]` |
| `connect_webrtc(session, *, messages, model_settings, model_request_parameters)` | 附加 sideband 控制连接；默认抛 `UserError` |
| `create_client_secret(*, instructions=None, tools=None, model_settings=None, expires_after_seconds=None)` | 铸造临时 token；默认抛 `UserError` |
| `answer_webrtc_offer(sdp_offer, *, instructions=None, tools=None, model_settings=None) -> WebRTCAnswer` | 代浏览器协商；默认抛 `UserError` |
| `hang_up(session)` | 结束 provider 侧通话；默认 `_check_hang_up` 抛 `UserError` |
| `model_name` | 抽象属性 |
| `base_url` | provider base URL |
| `profile` | 解析后的 `RealtimeModelProfile`（见下） |
| `context_window` | profile 的 `context_window` |
| `audio_input_sample_rate` / `audio_output_sample_rate` | 采样率（默认 `DEFAULT_AUDIO_SAMPLE_RATE`） |

**`profile` 解析顺序**（后层覆盖前层）：

1. `DEFAULT_REALTIME_PROFILE`（每个键的基线）；
2. provider 的 `realtime_model_profile(model_name)`；
3. genai-prices 尽力提供的 `context_window`（除非 provider 或用户 profile 显式设置该字段，包括设为 `None`）；
4. 模型类按实例能力做的调整（`_adjust_provider_profile`，如客户端面向的 API 表面）；
5. 用户 `profile=`（partial dict 合并，或 `(resolved) -> profile` 可调用体整体替换）。

随后 `supported_native_tools` 与该类实际实现取交集；废弃的 `supports_async_tool_calls` 从 `async_tool_call_mode` 派生。

**`infer_realtime_model(model, provider_factory=None)`**

- 解析 `provider:model`；无分隔符或无 model 名抛 `UserError`；
- `provider_factory` 收到**原样**前缀，其 provider 直接使用；否则字符串传给模型自行解析（如 `AzureRealtimeModel` 构造 `AzureProvider.for_realtime()`）；
- gateway 路由：`gateway/openai` / `gateway/google`，经 `normalize_gateway_provider`（`gateway/google` 折叠为 `google-cloud`），其他 gateway 前缀抛 `UserError`；
- 路由：
  - `openai`：**唯一按模型名分流**的地方——`gpt-live-*`（`is_openai_live_model`）→ `OpenAILiveModel`，其余 → `OpenAIRealtimeModel`；
  - `azure` → `AzureRealtimeModel`；
  - `xai` → `XaiRealtimeModel`；
  - `google` / `google-cloud` → `GoogleRealtimeModel`。

`KnownRealtimeModelName`（Literal）包含：`openai:gpt-live-1`、`openai:gpt-realtime`、`openai:gpt-realtime-2.1`、`openai:gpt-realtime-2.1-mini`、`azure:gpt-realtime`、`xai:grok-voice-latest`、`xai:grok-voice-think-fast-2.0`、`google:gemini-2.5-flash-native-audio-latest`、`google:gemini-3.1-flash-live-preview`、`google:gemini-3.8-live`、`google:gemini-3.8-live-extended-thinking`。

### 2.3 设置（[`realtime/settings.py`](../pydantic_ai_slim/pydantic_ai/realtime/settings.py)）

`RealtimeModelSettings(TypedDict, total=False)`：

| 键 | 类型 | 说明 |
|----|------|------|
| `max_tokens` | `int` | 每次响应最大 token（OpenAI/Azure/Gemini） |
| `parallel_tool_calls` | `bool` | 是否允许并行工具调用 |
| `async_tool_calls` | `bool \| None` | 工具运行期间模型是否继续对话（`'optional'` 模型才可选） |
| `tool_choice` | `ToolChoice` | 工具选择；会话无 output tool |
| `input_transcription_model` | `KnownRealtimeTranscriptionModelName \| str \| None` | 用户音频转写模型；`'auto'` 用 provider 推荐值，`None` 关闭 |
| `output_modality` | `Literal['audio','text']` | 单模态输出；不支持 text 的模型会抛 `UserError` |
| `thinking` | `ThinkingLevel` | 推理/思考配置 |
| `turn_detection` | `bool \| TurnDetection` | 自动 VAD/轮流控制 |
| `handshake_timeout` | `float` | 握手超时，默认 `30.0` |
| `reconnect` | `ReconnectPolicy` | 断线恢复策略 |

`TurnDetection(TypedDict, total=False)`：`sensitivity: Literal['low','medium','high']`、`prefix_padding_ms: int`、`silence_duration_ms: int`。provider 专属逃生舱：`openai_turn_detection` / `xai_turn_detection` / `google_vad`（存在时完全覆盖 `turn_detection`）。

`AudioRetention`：`Literal['transcript_only','input_audio','output_audio','all']`。保留的音频以 WAV `BinaryContent` 存在 `SpeechPart.audio`；live delta 仍是原始 PCM。

`ReconnectPolicy(TypedDict, total=False)`：`max_attempts`（默认 3，单次掉线的重连尝试上限）、`max_reconnects`（默认 50，整个会话的成功重连总上限）、`base_delay`（0.5）、`max_delay`（30.0）、`jitter`（True）。

`KnownRealtimeTranscriptionModelName`：`'auto','whisper-1','gpt-4o-transcribe','gpt-4o-mini-transcribe','gpt-realtime-whisper','gpt-live-transcribe','gpt-transcribe','grok-transcribe','azure-speech','mai-transcribe'`。

### 2.4 Profile（[`realtime/profiles.py`](../pydantic_ai_slim/pydantic_ai/realtime/profiles.py)）

`AsyncToolCallMode = Literal['never','optional','always']`。

`RealtimeModelProfile(TypedDict, total=False)` 的 `supports_*` / `emits_*` 标志（缺省布尔按 `False`，除了文档标注默认 `True` 的）：

| 键 | 默认 | 含义 |
|----|------|------|
| `supports_image_input` | False | 是否接受离散图像/视频帧 |
| `image_input_requires_response` | False | 图像只能作为响应起点（GPT-Live） |
| `supports_manual_turn_control` | False | 支持手动轮流（push-to-talk） |
| `supports_interruption` | False | 支持服务端打断 |
| `supports_output_truncation` | False | 支持输出截断（`played_ms`） |
| `supports_text_output` | True | 支持文本输出 |
| `supports_session_seeding` | False | 支持 `message_history` 播种 |
| `supports_webrtc` | False | 支持浏览器 WebRTC / 临时密钥 / sideband |
| `supports_seeding_images` / `supports_seeding_audio` | False | 播种时可含图像 / 保留音频 |
| `supports_thinking` | False | 支持 `thinking` |
| `async_tool_call_mode` | `'never'` | 见 `AsyncToolCallMode` |
| `supports_async_tool_calls` | — | 废弃，从 `async_tool_call_mode` 派生 |
| `supports_tool_return_schema` | False | 原生渲染工具 `return_schema`（Gemini Live） |
| `supported_native_tools` | `frozenset()` | 服务端原生工具 |
| `emits_input_speech_events` | False | 上报用户语音起止事件 |
| `synthesizes_turn_boundary` | False | 轮次边界由 Pydantic AI 推断（GPT-Live） |
| `responses_are_requests` | True | 每个记录的响应即一次请求（影响 `usage.requests` 与 request limit） |
| `response_usage_covers_context` | True | 响应用量覆盖整个上下文 |
| `audio_input_sample_rate` / `audio_output_sample_rate` | `24000` | 采样率 |
| `context_window` | `None` | 上下文窗口（未知） |

`DEFAULT_AUDIO_SAMPLE_RATE = 24000`；`DEFAULT_REALTIME_PROFILE` 是上表默认值的具体字典；`merge_realtime_profile(base, *overrides)` 逐层覆盖；`RealtimeModelProfileSpec = RealtimeModelProfile | Callable[[RealtimeModelProfile], RealtimeModelProfile]`。

### 2.5 Codec（[`realtime/codec.py`](../pydantic_ai_slim/pydantic_ai/realtime/codec.py)）

**输入类型**

- `RealtimeSessionInput = str | BinaryContent`：会话层 `send` 接受的类型。`str` 是一个完整文本轮，`BinaryContent` 携带图像帧、WAV 音频（流式前解包为 PCM）或原始 PCM chunk（`media_type='audio/pcm'`）。
- turn-control 动词：`CommitAudio`、`ClearAudio`、`CreateResponse`、`CancelResponse`、`TruncateOutput(audio_end_ms, *, item_id=None)`、`TextContext(text)`、`ToolResult(tool_call_id, *, output, content=None)`。
- `RealtimeInput`：连接层接受的 union（已归一化）——`str | TextContext | BinaryAudio | BinaryImage | CommitAudio | ClearAudio | CreateResponse | CancelResponse | TruncateOutput | ToolResult`。

**连接事件**（`RealtimeConnection.__aiter__` 产出）

| 事件 | 说明 |
|------|------|
| `AudioDelta(data, *, item_id=None, response_id=None)` | 模型音频 chunk（原始 PCM） |
| `OutputTranscript(text, *, is_final, output_text, item_id, response_id)` | 模型文本输出/音频转写 |
| `InputTranscript(text, *, is_final, item_id, cumulative)` | 用户音频转写 |
| `ToolCall(tool_call_id, *, tool_name, args, response_usage_follows, item_id, response_id)` | 工具调用请求 |
| `ToolCallCancelled(tool_call_ids)` | 模型取消进行中的调用 |
| `ResponseDone(*, interrupted, provider_response_id, finish_reason, provider_details, more_expected)` | 响应结束 |
| `SessionUsage(usage, *, provider_response_id, finish_reason, provider_details, response_scoped, context_window_used)` | 用量 |
| `ConversationCreated(conversation_id)` / `ConversationItemCreated(*, item_id, tool_call_id, replayed)` | OpenAI 协议会话/条目 |
| `InputRejected(input_index, *, refused)` | provider 拒绝了某个输入 |
| 共享事件 | `RealtimeInputSpeechStartEvent` / `...EndEvent`、`RealtimeOutputSpeechStart/EndEvent`、`RealtimeResponseInterruptedEvent`、`RealtimeInputTranscriptionErrorEvent`、`RealtimeSessionReconnectEvent`、`RealtimeSessionErrorEvent`、`PartStartEvent`、`PartEndEvent` |

`RealtimeCodecEvent` 是它们的 union。

**`RealtimeConnection(ABC)`**

| 成员 | 说明 |
|------|------|
| `transport_errors: ClassVar[tuple[type[Exception], ...]] = ()` | 传输层失败异常类型；会话把其映射为 `RealtimeError` |
| `send(content)` | **抽象**；喂入内容 |
| `__aiter__()` | **抽象**；迭代 codec 事件 |
| `_lifecycle_version: ClassVar[int] = 1` | 1 = 仅 codec；2 = 还产出 `_lifecycle_events()` |
| `_tagged_frames()` / `_lifecycle_events()` | 帧级/生命周期事件流（v2） |
| `_end_session()` | 关闭会话时产出尾部用量 |
| `model_name` | 服务端实际服务的模型 id（可能不同于请求 id，如 xAI 替换默认） |
| `set_message_history(callable)` | 让连接在丢状态重连时重放历史 |
| `input_transcription_enabled` | 是否产出 `InputTranscript`（默认 True） |
| `interrupts_response_on_speech` | 服务端 VAD 是否自行取消响应（默认 False） |
| `reconnect_restores_in_flight_state` | 重连是否延续进行中的响应/工具调用（默认 True） |
| `_can_reconnect` / `_answers_tool_calls_per_response` / `_take_merged_response_requests` / `_defers_audio_commit` / `_set_audio_commit_listener` | 内部（重连与回复计账） |

### 2.6 生命周期 v2 与 `SessionCore`（内部）

[`realtime/_lifecycle.py`](../pydantic_ai_slim/pydantic_ai/realtime/_lifecycle.py)：连接用 `_lifecycle_version = 2` 选择加入，事件带 id 直接陈述会话原本要从事件序列形状推断的事实：

- `ResponseStarted(response_id, answers, basis, user_turn_id)` / `ResponseEnded(response_id, status, finish_reason, provider_details)`——一个响应恰好被一对括起，`status ∈ {'completed','cancelled','failed','incomplete','lost'}`；
- `UserTurnStarted(turn_id)` / `UserTurnEnded(turn_id)` / `UserTurnDiscarded(turn_id)`；
- `InputAdded(input_id)` / `InputLost(...)` / `ResponseRequestRefused(...)`。

输入用「在该连接上第几次 `send()`」标识（`InputId = int`），与 `InputRejected.input_index` 一致。

[`realtime/_core.py`](../pydantic_ai_slim/pydantic_ai/realtime/_core.py)：`SessionCore` 通过单个同步 `apply()` 按发生顺序接收一切（v2 事件 + 会话命令），内部不 await，每次调用是一次原子迁移；按 id 保存响应/用户轮/输入/回复义务；历史是从实体**投影**出来的，从不编辑。`_CORE_MODE = 'legacy'`，`'shadow'` 让新核心与现核心并行以便对账（重构期内部用）。

### 2.7 会话（[`realtime/_session.py`](../pydantic_ai_slim/pydantic_ai/realtime/_session.py)）

`RealtimeEvent`（union，是 `AgentStreamEvent` 的严格子集）：`PartStartEvent | PartDeltaEvent | PartEndEvent | FunctionToolCallEvent | FunctionToolResultEvent | DeferredToolRequestsEvent | DeferredToolResultsEvent | EnqueuedMessagesEvent | RealtimeTurnCompleteEvent | RealtimeInputSpeechStartEvent | RealtimeResponseInterruptedEvent | RealtimeInputSpeechEndEvent | RealtimeOutputSpeechStartEvent | RealtimeOutputSpeechEndEvent | RealtimeInputTranscriptionErrorEvent | RealtimeSessionReconnectEvent | RealtimeSessionErrorEvent`。

`TranscriptUpdate(index, speaker, delta, transcript)`：`stream_transcripts(delta=True)` 产出的增量更新，按 `index` 分轮。

**构造**（仅列关键参数）：

```python
RealtimeSession(
    connection, *, model=None, tool_manager, instrumentation=None, agent_name=None,
    usage=None, usage_limits=None, audio_retention='transcript_only', handle_barge_in=False,
    retain_images_every_n=1, retain_images_max=100, retain_audio_max_seconds=1800,
    message_history=None, profile=None, owns_media=True, provider_session=None,
    conversation_id=None, run_id=None, instructions=None, metadata=None,
    agent_description=None, output_modality='audio', model_request_parameters=None,
    model_settings=None, wrap_event_stream=None,
)
```

**公开成员**

| 成员 | 说明 |
|------|------|
| `async with session` / `close()` | 上下文管理接收泵、后台工具任务、instrumentation span；`close()` 幂等 |
| `hang_up()` | WebRTC sideband 上真正结束通话（`close()` 只 detach）；自有连接上与 `close()` 等价 |
| `send(content, *, respond=None)` | 喂入文本/图像/音频；`str` 是完整文本轮，`respond=False` 作上下文 |
| `send_audio(data: bytes \| AsyncIterable[bytes])` | 流式单声道 PCM16 |
| `commit_audio()` / `clear_audio()` / `create_response()` | 手动轮流（push-to-talk），依赖 `supports_manual_turn_control` |
| `interrupt(*, played_ms=None)` / `interrupt(*, played_bytes)` | barge-in；`played_ms` 调用方自带计账，`played_bytes` 会话代为计账并返回是否真的打断 |
| `enqueue(*content, priority='asap' \| 'when_idle')` | 在合适轮次边界投递内容并触发响应 |
| `stream_audio() -> AsyncIterator[bytes]` | 播放用音频 chunk（每 iterator 缓冲最多 5 分钟） |
| `stream_transcripts(*, delta=False)` | 最终 `SpeechPart`（默认）或 `TranscriptUpdate` |
| `wait_for_playback()` / `wait_for_reply()` | 等待播放计账 / 等待模型应付的回复 |
| `all_messages()` / `new_messages()` | 历史快照（含/不含播种历史） |
| `conversation` | `Conversation(messages, usage, conversation_id)` |
| `profile` / `audio_input_sample_rate` / `audio_output_sample_rate` / `context_window_used` / `played_audio_bytes` / `usage` / `closed` / `result` | 只读访问 |

**不变量**：历史必须始终可作为 `Agent.run(message_history=...)` 的有效输入——在关闭/重连丢失时把所有东西结算（部分回复记为 interrupted response，运行中的工具给 cancelled return），使历史永不以悬空的 `ToolCallPart` 结束。工具结果在 `all_messages()` 中直接排在其调用所在的响应之后（请求—响应 API 要求这种相邻性）。策略在共享核心（`ToolManager`），会话只做翻译，**绝不重实现**——实时审批绕过曾正是一个安全 bug。

### 2.8 消息事件（[`messages.py`](../pydantic_ai_slim/pydantic_ai/messages.py)）

Realtime 控制面事件定义在 `pydantic_ai.messages`，不在 realtime 包内：

`RealtimeTurnCompleteEvent`（会话合成，生成与工具工作都完成）、`RealtimeInputSpeechStartEvent(item_id)`、`RealtimeInputSpeechEndEvent(item_id)`、`RealtimeOutputSpeechStartEvent`、`RealtimeOutputSpeechEndEvent`、`RealtimeResponseInterruptedEvent`（Gemini 服务端打断）、`RealtimeInputTranscriptionErrorEvent(message, *, type, code, item_id, content_index)`、`RealtimeSessionReconnectEvent(*, state_restored)`、`RealtimeSessionErrorEvent(message, *, type, code, recoverable)`。`RealtimeSessionEvent` 是它们的 `Annotated` union。

`SpeechPart` / `SpeechPartDelta` 也在 `messages.py`，是实时音频/转写的共享消息 part。

### 2.9 Provider 适配器差异

| 模块 | 主类 | 传输 | 关键差异 |
|------|------|------|----------|
| [`openai.py`](../pydantic_ai_slim/pydantic_ai/realtime/openai.py) | `OpenAIRealtimeModel` / `OpenAIRealtimeConnection` | `wss://api.openai.com/v1/realtime`（`websockets`） | 基准 OpenAI 协议；`interrupt_response` 服务端 VAD；`OpenAIRealtimeModelSettings`（`voice`、`noise_reduction`、`speed`、`openai_turn_detection`、截断策略等） |
| [`openai_live.py`](../pydantic_ai_slim/pydantic_ai/realtime/openai_live.py) | `OpenAILiveModel` / `OpenAILiveConnection` | `/live/sessions` | 与 Realtime API 不同协议；文本经 `session.commentary.append`/`thinking.append`（各限 500 token）作上下文；无 turn terminal，连接按静音时长合成 `ResponseDone`（`synthesizes_turn_boundary=True`）；工作委派（`client` / `responses` delegation） |
| [`azure.py`](../pydantic_ai_slim/pydantic_ai/realtime/azure.py) | `AzureRealtimeModel`（`OpenAIRealtimeModel` 子类）/ `AzureRealtimeConnection` | Azure GA `/openai/v1/realtime` 或 Azure AI Voice Live | `AzureRealtimeApi = Literal['azure_openai','voice_live']`；支持 Entra ID `credential`（`AzureTokenCredential`，scope `https://ai.azure.com/.default`）；`_VoiceLiveRealtimeConnection` |
| [`google.py`](../pydantic_ai_slim/pydantic_ai/realtime/google.py) | `GoogleRealtimeModel` / `GoogleRealtimeConnection` | `google-genai` SDK 托管 WebSocket | **16 kHz** 输入 / 24 kHz 输出；单一输出模态；原生接受视频帧（`BinaryImage`）；`GoogleRealtimeModelSettings`（含 `AutomaticVAD`、`MultiSpeaker`、`ContextCompression`）；`google-cloud` 走 Vertex |
| [`xai.py`](../pydantic_ai_slim/pydantic_ai/realtime/xai.py) | `XaiRealtimeModel` / `XaiRealtimeConnection`（`OpenAIRealtimeConnection` 子类） | `wss://api.x.ai/v1/realtime` | 复用 OpenAI codec，仅在差异处覆盖：`session.update` 形状、累计式输入转写快照、原生会话恢复（重用 `conversation.id`、抑制重放 burst）、无输出截断（`supports_output_truncation=False`）、无文本输出（`supports_text_output=False`） |

共享层：Azure 与 xAI 复用 [`_openai_protocol.py`](../pydantic_ai_slim/pydantic_ai/realtime/_openai_protocol.py)（事件映射、会话播种、工具转换、server-VAD 配置）与 [`_openai_webrtc.py`](../pydantic_ai_slim/pydantic_ai/realtime/_openai_webrtc.py)（WebRTC signaling / sideband helper）。能力差异一律经 `RealtimeModelProfile` 标志表达，**不靠** `isinstance`/provider 名判断。

Provider 的 `*_realtime_model_profile` helper 位于 `pydantic_ai/profiles/{google,openai,grok}.py`。

---

## 3. 安装 extra

[`pydantic_ai_slim/pyproject.toml`](../pydantic_ai_slim/pyproject.toml)：

| 功能 | extra | 依赖 |
|------|-------|------|
| UI（Starlette 适配，挂进你自己的应用） | `pydantic-ai-slim[ui]` | `starlette>=0.46.2` |
| AG-UI | `pydantic-ai-slim[ag-ui]` | `ag-ui-protocol>=0.1.10,<1`、`starlette>=0.46.2` |
| 内置 Web 应用（`Agent.to_web()` / `clai web`） | `pydantic-ai-slim[web]` | `starlette>=1.3.1`、`uvicorn>=0.38.0` |
| 实时语音（通用传输） | `pydantic-ai-slim[realtime]` | `websockets>=14.0` |
| OpenAI 实时 | `pydantic-ai-slim[openai-realtime]` | `pydantic-ai-slim[openai,realtime]` |
| Google 实时 | `pydantic-ai-slim[google-realtime]` | `pydantic-ai-slim[google,realtime]` |
| xAI 实时 | `pydantic-ai-slim[xai-realtime]` | `pydantic-ai-slim[xai,openai,realtime]` |

（根 `pyproject.toml` 的 `pydantic-ai[...]` 聚合包把这些 extra 一并转出。）

---

## 4. 需要注意的点

- Realtime 会话事件**不**流经 UI 事件流（`ui/_event_stream.py` 的 `handle_event` 显式忽略它们，`handle_part_start`/`handle_part_delta` 忽略 `SpeechPart`/`SpeechPartDelta`）。
- `run_stream_native` 只在 `manage_system_prompt='server'` 时注入 `ReinjectSystemPrompt`，且 `id=None` 以避免与用户 reinjector 的 id 冲突。
- `sanitize_messages` 只作用于从运行输入解析出的消息，`message_history` 需调用方自行处理。
- `dispatch_request` 把 `ValidationError` 转为 422；`_check_content_type` 的 415 由 Starlette 自身渲染。
- `AGUIEventStream` 用 `_GeneratedID` 区分「自生成 id」与「调用方显式传入 id」，只有后者在被 `run_input` 覆盖时才告警。
- `infer_realtime_model` 只在 `openai` 前缀内按模型名分流（`gpt-live-*`）。
