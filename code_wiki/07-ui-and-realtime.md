# 07 · UI 适配层与 Realtime

本篇覆盖两个「非 graph 运行」的界面层：

- **UI 适配**：把规范化消息/事件翻译为前端协议（AG-UI、Vercel AI），并提供内置 Web 应用。
- **Realtime**：双向语音会话（OpenAI Realtime、Gemini Live、Azure、xAI Grok Voice）。

相关目录：[pydantic_ai_slim/pydantic_ai/ui/](../pydantic_ai_slim/pydantic_ai/ui/)、[realtime/](../pydantic_ai_slim/pydantic_ai/realtime/)

---

## 1. UI 适配层（`ui/`）

### 1.1 `UIAdapter`

`ui/_adapter.py` 的 `UIAdapter(ABC, Generic[...])`：把前端运行输入转换为 `Agent.run_stream_events()` 的参数，运行 Agent，再把 Pydantic AI 事件翻译为协议事件（借助 `UIEventStream`）。

**安全相关字段**（信任模型的关键）：

| 字段 | 说明 |
|------|------|
| `manage_system_prompt` | `'server'` / `'client'` |
| `allowed_file_url_schemes` | 允许的文件 URL scheme |
| `allowed_file_url_force_download` | 是否强制下载 |
| `allow_uploaded_files` | 是否允许客户端上传文件 |
| `strip_workspace_refs` | 是否剥离 workspace 引用 |

- `sanitize_messages()` 委托 `messages.sanitize_messages` 剥离不可信的客户端 part。
- `DEFAULT_ALLOWED_CONTENT_TYPES = frozenset({'application/json'})`，`_check_content_type` 作为 CSRF 控制。

可覆写方法：`build_run_input`、`load_messages`、`dump_messages`、`build_event_stream`、`messages`、`toolset`、`state`、`deferred_tool_results`、`conversation_id`。运行方法：`transform_stream`、`encode_stream`、`streaming_response`、`run_stream_native`、`run_stream`、`dispatch_request`。

`run_stream_native` 在 `manage_system_prompt='server'` 时注入 `ReinjectSystemPrompt(replace_existing=True, id=None)`。

`StateHandler` 协议 / `StateDeps` dataclass 把前端状态带入 deps；`MessagesBuilder` / `BuilderCheckpoint`（`ui/_messages_builder.py`）从请求/响应 part 构建 `ModelMessage`。

### 1.2 `UIEventStream`

`ui/_event_stream.py` 的 `UIEventStream(ABC, Generic[...])`。`NativeEvent = AgentStreamEvent | AgentRunResultEvent[Any]`。

- `transform_stream` 驱动请求/响应回合状态机，跟踪 `_open_part` / `_pending_tool_calls`，派发到类型化的 `handle_*` 方法，出错时先关闭未闭合 part 与挂起工具调用再调用 `on_error` / `on_cancelled`。
- 钩子：`before_stream` / `after_stream` / `on_error` / `on_cancelled`；`before_request` / `after_request` / `before_response` / `after_response`；`handle_part_start` / `handle_part_delta` / `handle_part_end`（扇出到 `handle_text_*`、`handle_thinking_*`、`handle_tool_call_*`、`handle_builtin_tool_call_*`、`handle_file`、`handle_compaction`）。
- `encode_event` / `encode_stream` / `streaming_response` 产出 SSE（`SSE_CONTENT_TYPE`）。
- `CustomEvent` 对未知事件类**失败关闭**（`UnknownCustomEvent`）。

### 1.3 协议适配器

| 协议 | 目录 | 主要类 |
|------|------|--------|
| **AG-UI** | `ui/ag_ui/` | `AGUIAdapter`、`AGUIEventStream`；辅助 `_forward_compat.py`（跳过未知判别标签）、`_interrupt.py`、`_multimodal.py`、`_thinking_0_10.py` / `_thinking_0_11.py` |
| **Vercel AI** | `ui/vercel_ai/` | `VercelAIAdapter`、`VercelAIEventStream`，`request_types.py` / `response_types.py` 转换 Vercel AI SDK v5 UI message 类型 |
| **Web** | `ui/_web/` | `create_web_app`、`create_api_app`、`DEFAULT_HTML_URL` / `OFFLINE_HTML_URL`；内置 Vercel AI SDK |

`ui/AGENTS.md` 规定：AG-UI 严格向后兼容（不升版本、用 feature-gate 添加新功能）。

`ui/__init__.py` 导出 `UIAdapter`、`UIEventStream`、`SSE_CONTENT_TYPE`、`DEFAULT_ALLOWED_CONTENT_TYPES`、`StateDeps`、`StateHandler`、`NativeEvent`、`MessagesBuilder`、`BuilderCheckpoint`。

> Realtime 事件**不**经过 UI 事件流（见 `ui/_event_stream.py` 注释）。

---

## 2. Realtime（语音）

### 2.1 分层

包 docstring 与 `realtime/AGENTS.md` 描述的分层：

- `model.py` — `RealtimeModel` + `infer_realtime_model`
- `settings.py` — 设置词汇
- `profiles.py` — profile 类型
- `codec.py` — 低层连接词汇
- `_session.py` — 会话
- 具体 provider — 各子模块

高层入口：`Agent.realtime(...)` → `AgentRealtime.session(...)` 打开 `RealtimeSession`。**实时会话不经过 `iter()`（无 graph 运行）**。

### 2.2 模型与推断

- `RealtimeError`（`ModelAPIError` 子类）、`RealtimeClientSecret`、`RealtimeProviderSession` 协议、`WebRTCSession` / `WebRTCAnswer`。
- `RealtimeModel(AbstractModel)`：`connect()`（抽象）、`connect_webrtc()`、`create_client_secret()`、`answer_webrtc_offer()`、`hang_up()`、`profile`、`audio_input_sample_rate` / `audio_output_sample_rate`、`supported_native_tools()`。
- `KnownRealtimeModelName` 与 `infer_realtime_model` 路由 `provider:model`（含 `gateway/openai`、`gateway/google`）。OpenAI 的 `gpt-live-*` 路由到 `OpenAILiveModel`。

### 2.3 会话与设置

- `_session.py` 的 `RealtimeSession`：包装 `RealtimeConnection`，把 codec 事件翻译成共享的消息/part 词汇、构建 `ModelMessage` 历史、通过同一套 `ToolManager` 核心并发运行工具。方法含 `send` / `send_audio` / `commit_audio` / `clear_audio` / `create_response` / `interrupt`、`stream_audio`、`stream_transcripts`、`all_messages` / `new_messages`、`close`、`hang_up`。
- **不变量**：「历史必须始终可作为 `Agent.run(message_history=...)` 的有效输入」；策略位于共享核心，绝不在会话中重实现（实时审批绕过曾是一个安全 bug）。
- `settings.py`：`RealtimeModelSettings(TypedDict)`（`max_tokens`、`parallel_tool_calls`、`async_tool_calls`、`tool_choice`、`input_transcription_model`、`output_modality`、`thinking` 等）、`TurnDetection`、`AudioRetention`。
- `profiles.py`：`RealtimeModelProfile` 的 `supports_*` 标志；provider 差异通过 profile 标志表达，**不靠 provider 名判断**。
- `codec.py`：`RealtimeConnection` 及词汇（`ToolResult`、`TextContext`、`CommitAudio`、`ClearAudio`、`CreateResponse`、`CancelResponse`、`TruncateOutput`）。

### 2.4 Provider 适配器

| 模块 | 类 | 说明 |
|------|----|------|
| `openai.py` | `OpenAIRealtimeModel` | OpenAI Realtime API（`websockets`） |
| `openai_live.py` | `OpenAILiveModel` | OpenAI GPT-Live（按模型名在 `openai` 前缀内路由） |
| `azure.py` | `AzureRealtimeModel`（`OpenAIRealtimeModel` 子类） | Azure GA `/openai/v1/realtime` 或 Azure AI Voice Live；支持 Entra ID `credential` |
| `google.py` | `GoogleRealtimeModel` | Gemini Live（Developer API `google`、Vertex `google-cloud`） |
| `xai.py` | `XaiRealtimeModel` | xAI Grok Voice（`wss://api.x.ai/v1/realtime`），支持原生会话恢复 |

Azure 与 xAI 复用 OpenAI codec（`_openai_protocol.py`）；能力差异经 `RealtimeModelProfile` 标志表达。

相关消息事件（`RealtimeInputSpeechStartEvent`、`RealtimeTurnCompleteEvent`、`RealtimeSessionErrorEvent` 等）定义在 `messages.py`，不在 realtime 包内。

---

## 3. 安装 extra

| 功能 | extra |
|------|-------|
| UI（Starlette 适配） | `pydantic-ai[ui]` |
| AG-UI | `pydantic-ai[ag-ui]` |
| 内置 Web 应用 | `pydantic-ai[web]` |
| 实时语音（通用） | `pydantic-ai[realtime]` |
| OpenAI / Google / xAI 实时 | `pydantic-ai[openai-realtime]` / `[google-realtime]` / `[xai-realtime]` |
