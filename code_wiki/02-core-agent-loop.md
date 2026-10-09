# 02 · 核心 Agent 循环

本篇覆盖 `pydantic_ai_slim/pydantic_ai/` 中最核心的执行路径：

- `Agent` 类与运行方法
- `_agent_graph.py` 的图与节点
- `RunContext`
- `AgentRun` / `AgentRunResult` / `StreamedRunResult`
- 异常体系

---

## 1. `Agent` 类

定义于 [agent/__init__.py](../pydantic_ai_slim/pydantic_ai/agent/__init__.py)，继承自 `AbstractAgent`（[agent/abstract.py](../pydantic_ai_slim/pydantic_ai/agent/abstract.py)）。

> 注意：这里是 `Agent` 包（`agent/`），`Agent` 类在 `agent/__init__.py`。

### 1.1 构造参数（`Agent.__init__`）

| 参数 | 说明 |
|------|------|
| `model` | 默认模型；`Model \| KnownModelName \| str \| None`，可省略并在每次运行时提供 |
| `output_type` | 输出校验类型，默认 `str` |
| `instructions` / `system_prompt` | 指令字符串 / 静态 system prompt（可用装饰器扩展） |
| `deps_type` | 仅用于静态类型参数化（依赖注入的类型标记） |
| `name` / `description` | 用于日志/可观测性；`description` 会写入运行 span 的 `gen_ai.agent.description` |
| `model_settings` | 静态设置或 `Callable[[RunContext], ModelSettings]` |
| `retries` | `int \| AgentRetries`；分类预算（`tools` / `output`），默认各 1 |
| `validation_context` | Pydantic 校验上下文，或 `Callable[[RunContext], ...]` |
| `tools` / `toolsets` | 函数工具与工具集（含 MCP server、toolset 工厂函数） |
| `defer_model_check` | 延迟到首次运行再解析命名模型 |
| `end_strategy` | `'early' \| 'graceful' \| 'exhaustive'`，默认 `'graceful'` |
| `metadata` | dict 或 `Callable[[RunContext], dict]` |
| `tool_timeout` | 默认单工具执行超时（秒） |
| `max_concurrency` | `int \| ConcurrencyLimit \| ConcurrencyLimiter \| None` |
| `capabilities` | Capability 列表，构造时经 `wrap_capability_funcs` 包装并自动注入 |

构造时还会：构建 `_root_capability = CombinedCapability(capabilities)`、校验 capability/toolset/native-tool id、两阶段绑定能力、构建 `_output_schema = OutputSchema.build(output_type)`、分离用户 toolset 与 `DynamicToolset` 工厂。

### 1.2 运行方法（定义在 `AbstractAgent`）

| 方法 | 返回 | 说明 |
|------|------|------|
| `run(...)` | `AgentRunResult[OutputDataT]` | 主要入口，驱动整张图直到结束 |
| `run_sync(...)` | 同上 | 同步封装 |
| `run_stream(...)` | `AsyncGenerator[StreamedRunResult[...]]` | 流式；在产出匹配 `output_type` 的首个输出处停止后续工具调用 |
| `run_stream_sync(...)` | 同上 | 同步封装 |
| `run_stream_events(...)` | 异步上下文管理器 | 后台运行 + 事件流 |
| `iter(...)` | `AsyncGenerator[AgentRun]` | **原语**：返回可逐步驱动的 `AgentRun` |

### 1.3 装饰器

| 装饰器 | 说明 |
|--------|------|
| `@agent.tool` | 注册工具，函数首个参数为 `RunContext`。支持 `name`/`description`/`retries`/`prepare`/`args_validator`/`strict`/`sequential`/`requires_approval`/`metadata`/`timeout`/`defer_loading` 等 |
| `@agent.tool_plain` | 同上，但函数不接收 `RunContext` |
| `@agent.instructions` | 注册指令函数（可含 `RunContext`）；`name=` 使其可被寻址为 `agent:<name>` |
| `@agent.system_prompt` | 注册 system prompt 函数；`dynamic=True` 时即使传入 `model_history` 也重新求值 |
| `@agent.output_validator` | 注册输出校验器，抛 `ModelRetry` 触发重试 |
| `@agent.on_event` | 注册事件 Hook（存入 `self._event_hooks`） |
| `@agent.toolset` | 注册 toolset 工厂函数 |

其他方法：`from_spec` / `from_file`（加载 YAML/JSON Agent Spec）、`instrument_all` / `instrument`、`system_prompt_parts`、`output_json_schema`、`override(...)`（临时覆盖 name/deps/model/toolsets/instructions/settings/retries/spec/workspace）、`to_web`、`to_cli` / `to_cli_sync`、`realtime(...)`。

### 1.4 与 capability / toolset / output 的关系

- Capability 被合并为单一 `root_capability: CombinedCapability`，贡献指令、工具集、native 工具、模型设置与 Hook。
- `Agent._get_toolset` 用 `PreparedToolset` 包裹，使 `prepare_tools` / `prepare_output_tools` 钩子烘焙进 `ToolManager.tools`。
- `_output_schema = OutputSchema.build(output_type)`；若含工具则另存 `_output_toolset`（kind 为 `'output'`）。

---

## 2. 执行循环：`_agent_graph.py`

文件：[pydantic_ai_slim/pydantic_ai/_agent_graph.py](../pydantic_ai_slim/pydantic_ai/_agent_graph.py)

### 2.1 图的构建

- `build_agent_graph(name, deps_type, output_type)` 委托给 `_build_agent_graph(name)`，后者带 `lru_cache(maxsize=128)`，**仅以 `name` 为键**——同一 Agent 的所有运行共享同一张图。
- 节点注册：`UserPromptNode`、`ModelRequestNode`、`CallToolsNode`、`SetFinalResult`；使用 `GraphBuilder(..., auto_instrument=False)` 构建，`validate_graph_structure=False`。

### 2.2 节点类

| 节点 | 职责 |
|------|------|
| `AgentNode` | 公共基类（`BaseNode[GraphAgentState, GraphAgentDeps, FinalResult]`） |
| `UserPromptNode` | 处理用户 prompt、指令、system prompt、延迟工具结果；清理历史、恢复挂起响应、重算动态 prompt，构建首个 `ModelRequest` |
| `ModelRequestNode` | 发起模型请求（含 `stream()` 上下文）；处理续段、重试、错误恢复 |
| `CallToolsNode` | 处理 `ModelResponse`，分类 part 并按策略派发工具调用，决定「结束 vs 继续」 |
| `SetFinalResult` | 立即以预算好的 `FinalResult` 结束图（流式已产出最终结果时使用） |

### 2.3 步骤序列

1. **构建 prompt**（`UserPromptNode.run` → `ModelRequestNode._prepare_request`）：清理历史（`_clean_message_history`）、修复中断尾部、追加请求、`run_step += 1`、选择模型（`_select_model`）、刷新能力/工具揭示状态、准备 `ToolManager`（`for_run_step`）、解析指令、构建请求参数、检查起始用量限制（`usage_limits.check_before_request`）。
2. **调用模型**（`ModelRequestNode.stream` → `_make_request`）：经 `wrap_model_request` / `before_model_request` / `after_model_request` 包裹；最终调用 `model_request` 或 `model_request_stream`。续段（如 Anthropic `pause_turn`、OpenAI background）会被解析并合并为单个响应、只提交一次用量。`SkipModelRequest` 短路；`ModelRetry` 构造重试节点（`_build_retry_node`）；其他错误走 `on_model_request_error` 恢复。
3. **处理工具/输出**（`CallToolsNode` → `process_tool_calls`，实现在 `_tool_execution.py`）：按 `ToolDefinition.kind` 分类，依 `end_strategy` 执行，应用 retry-wins，批量解析延迟调用。若输出了 `FinalResult`，`_handle_final_result` 追加尾部工具返回并返回 `End(final_result)`；否则返回新的 `ModelRequestNode` 进入下一轮。
4. **终结**：`End(FinalResult)` → `AgentRun.result` 依图输出与 `GraphAgentState` 构造 `AgentRunResult`。

### 2.4 状态与依赖

**`GraphAgentState`**（`@dataclass(kw_only=True)`）持有：`message_history`、`usage: RunUsage`、`output_retries_used`、`run_step`、`run_id`（每次运行新生成）、`conversation_id`、`metadata`、`last_max_tokens`、`last_model_request_parameters`、`pending_messages`（`PendingMessageQueue`）、`event_stream_buffer`（`EventStreamBuffer`）、`mcp_tool_defs_cache`。方法：

- `check_incomplete_tool_call()`：若上一条响应在工具调用中途被截断则抛 `IncompleteToolCall`。
- `consume_output_retry(max_output_retries, error=None)`：递增输出重试计数，超限抛 `UnexpectedModelBehavior`。

**`GraphAgentDeps`**（泛型 `DepsT, OutputDataT`）持有每次运行的配置与服务：`user_deps`、`prompt`、`model`、`model_selector`、`usage_limits`、`max_output_retries`、`end_strategy`、`output_schema`、`output_validators`、`root_capability`、`capabilities`、**按引用共享的** `loaded_capability_ids` 与 `discovered_tool_names`、`workspace`、`native_tools`、`tool_manager`、`tracer`、`instrumentation_settings`、`cancellation: RunCancellation`、`durable_operations` 等。

### 2.5 重试与用量限制

- **输出重试**：`consume_output_retry`；耗尽抛 `UnexpectedModelBehavior('Exceeded maximum output retries (N)')`。
- **工具重试**：`ToolManager._check_max_retries`；单个工具预算来自 `tool.max_retries`，回退到 `ctx.max_retries`（默认 1）。
- **用量限制**（`UsageLimits`）：在 `check_before_request`、`_enforce_usage_limits`（`check_tokens`/`check_cost`/`check_per_request_input_tokens`）、续段检查、工具调用数检查（`check_before_tool_call`）处执行。

### 2.6 运行上下文构建的不变量

`build_run_context(ctx)` 构造 `RunContext` 并 `replace(run_context, validation_context=...)`。重要约束：**只有 `validation_context` 可以经 `replace` 变更**；按引用传递的可变成员（`loaded_capability_ids`、`discovered_tool_names`、`pending_messages`、`_cancellation`、`_event_stream_buffer`、`_mcp_tool_defs_cache`、`_durable_operations`、`_run_capabilities_by_id`）绝不能被 fork，否则会破坏步内能力加载、工具揭示、消息入队、取消、事件投递与缓存。

---

## 3. `RunContext`

文件：[pydantic_ai_slim/pydantic_ai/_run_context.py](../pydantic_ai_slim/pydantic_ai/_run_context.py)（`RunContext` 为 `@dataclass(repr=False, kw_only=True)`）。

常用字段/属性：

- `deps`：依赖注入值。
- `model: AbstractModel`（实时会话中为 `RealtimeModel`）；`model_id` 属性。
- `usage: RunUsage`、`usage_limits: UsageLimits | None`。
- `agent`、`prompt`、`messages`、`validation_context`、`tracer`、`run_step`、`run_id`、`conversation_id`、`metadata`、`model_settings`、`workspace`。
- `retries: dict[str, int]`、`retry: int`、`max_retries: int`、`last_attempt`。
- 工具上下文：`tool_call_id`、`tool_name`、`tool_call_approved`、`tool_call_metadata`、`partial_output`。
- 能力状态：`root_capability`、`capabilities`、`loaded_capability_ids`、`capability_active`、`active_capability_ids`、`available_tool_names`、`is_tool_available(tool)`、`tools`、`discovered_tool_names`。
- `realtime` / `realtime_session`、`in_durable_context`、`context_window_used`、`tool_manager`、`pending_messages`。

方法：

- `async emit(event)`：把 `CustomEvent`（应用）/ `CapabilityEvent`（能力）投递进运行事件流，自动打上 `tool_call_id` / `tool_name`。
- `enqueue(*content, priority=...)`：注入待处理消息（pending messages）。
- `cancel()`：请求取消运行（在下一个 await 处投递；终态）。

---

## 4. `tool_manager.py` 与工具执行

文件：[tool_manager.py](../pydantic_ai_slim/pydantic_ai/tool_manager.py)、[_tool_execution.py](../pydantic_ai_slim/pydantic_ai/_tool_execution.py)。

**发现与生命周期**

- `ToolManager` 持有 `toolset`、`root_capability`、`ctx`、`tools`（缓存）、`failed_tools`、`succeeded_tools`、`availability_refused`、`default_max_retries`、`resolved_capability_ids`。
- `for_run_step(ctx)` 创建步级 manager：快照当前 active capability id、结转重试计数、重建 `tools = await toolset.get_tools(ctx)`。

**校验**

- `validate_tool_call(call, *, approved, metadata, wrap_validation_errors) -> ValidatedToolCall`：解析工具（`_resolve_tool`）、构建工具上下文、运行 `before_tool_validate` 等钩子，产出 `ValidatedToolCall`（含 `args_valid`、`validated_args`、`validation_error`、`deferral`）。
- 参数校验使用工具的 Pydantic `args_validator`（JSON 或 Python），可叠加自定义 `args_validator_func`。

**执行**

- `execute_tool_call(validated, ...)` → 内部 `_execute_tool_call_impl`：拒绝 `kind == 'external'`、运行 `before_tool_execute` 等钩子、经 `toolset.call_tool` 调用。
- 错误映射：`ValidationError` / `ModelRetry` → `ToolRetryError`（`RetryPromptPart`）；`ToolFailed` → `ToolFailedError`（`failed` 的 `ToolReturnPart`）。
- `SkipToolExecution` / `SkipToolValidation` 可短路替换执行或校验结果。

**输出工具的区别**

- 输出工具使用**输出钩子**（`validate_output_tool_call` / `execute_output_tool_call` → `run_output_validate_hooks` / `run_output_process_hooks`），用户面向的工具钩子不会对其触发。
- 输出校验器看到的是**全局**输出重试预算；输出函数看到的是 per-tool `max_retries`。

**延迟工具**

- `handle_call` 把声明式延迟（`ToolDefinition.defer`，如 `requires_approval=True`）转换为 `ApprovalRequired` / `CallDeferred`，统一经 `_resolve_single_deferred`，构建 `DeferredToolRequests` 并交由 `root_capability.handle_deferred_tool_calls`。

**编排（`_tool_execution.py`）**

- `process_tool_calls(...)` 按 kind 分类并委托策略处理器：`_EarlyProcessor` / `_GracefulProcessor` / `_ExhaustiveProcessor`（对应 `end_strategy`）。
- 不变量：**retry-wins**（函数/未知工具的 `RetryPromptPart` 抑制同批的最终结果；输出工具重试不抑制，"首个有效输出胜出"）；延迟调用在步骤末尾作为**单一批次**解析；可用性增量按产出顺序确定性剪枝。

---

## 5. 结果对象

### `AgentRun`（`run.py:169`）

有状态、可异步迭代（由 `agent.iter(...)` 获得）。

- 属性：`ctx`、`next_node`、`result -> AgentRunResult | None`、`usage`、`metadata`、`run_id`、`conversation_id`、`pending_messages`。
- 迭代：`__aiter__` / `__anext__` 产出节点；`next(node)` 手动驱动一步；`all_messages()` / `new_messages()`（及 JSON 变体）。
- `emit(event)`、`enqueue(...)`、`cancel()`（取消整个运行）。
- 节点生命周期钩子经 `_run_node_with_hooks` 派发（`wrap_node_run` / `before_node_run` / `on_node_run_error` / `after_node_run`）。

### `AgentRunResult`（`run.py:813`）

最终结果。公开 `output`、`workspace`、`all_messages(output_tool_return_content=...)`、`new_messages(...)`、`all_messages_json` / `new_messages_json`、`response`（最后一条 `ModelResponse`）、`usage`、`conversation`、`timestamp`、`metadata`、`run_id`、`conversation_id`。具有自定义 Pydantic 校验/序列化以保持私有 dataclass 字段（`_state`、`_new_message_index`、`_traceparent_value`）内部化。

### `StreamedRunResult`（`result.py:572`）与 `AgentStream`（`result.py:52`）

- `StreamedRunResult`：`is_complete`、`result`、`stream_output()`、`stream_text(delta=...)`、`stream_response()`、`get_output()`、`response`、`usage`、`all_messages` / `new_messages`、`validate_response_output`、`cancel()`（仅停止当前响应）。同步变体为 `StreamedRunResultSync`。
- `AgentStream`：底层流，提供 `stream_output`、`stream_response`（产出 `incomplete`→`complete`/`interrupted` 快照）、`stream_text`、`cancel`、`drain`、事件迭代等。
- `FinalResult`（`result.py:1196`）：图的标记输出，携带 `output`、`tool_name`、`tool_call_id`。

---

## 6. 异常体系

文件：[exceptions.py](../pydantic_ai_slim/pydantic_ai/exceptions.py)。

**控制流异常（非错误层级）**

| 异常 | 用途 |
|------|------|
| `ModelRetry` | 从工具/校验器/Hook 抛出，向模型发送重试提示 |
| `ToolFailed` | 终态、对模型可见的工具失败，不消耗重试预算 |
| `CallDeferred` | 延迟一个工具调用（携带 `metadata`） |
| `ApprovalRequired` | 标记工具调用需要人工审批 |
| `SkipModelRequest` / `SkipToolValidation` / `SkipToolExecution` | Hook 短路替换模型请求 / 校验 / 执行 |

**错误层级**

```
UserError(RuntimeError)                     # 应用开发者用法错误
  UndrainedPendingMessagesError             # 遗留（不再抛出）
AgentRunError(RuntimeError)                 # 运行错误基类
  RunCancelled                              # 应用请求的取消
  SuspendedResponseExpired                  # 恢复超过 provider 保留窗口的挂起响应
  UsageLimitExceeded                        # 超出 UsageLimits
  ConcurrencyLimitExceeded                  # 并发队列深度超限
  UnexpectedModelBehavior                   # 模型异常行为（含 body）
    ContentFilterError                      # 触发 provider 内容过滤器
    IncompleteToolCall                      # token 限制中断工具调用
  ModelAPIError                             # provider API 请求失败
    ModelHTTPError                          # 4xx/5xx（含 retry_after）
FallbackExceptionGroup(ExceptionGroup)      # 所有 fallback 模型均失败
ToolRetryError(Exception)                   # 应发送重试 RetryPromptPart
ToolFailedError(Exception)                  # 应发送失败 ToolReturnPart
MessageHistoryMutatedWarning(Warning)       # 检测到运行历史的原地修改
```

另从 `_warnings.py` 重导出：`CostCalculationFailedWarning`、`CostNotFoundWarning`、`PydanticAIDeprecationWarning`、`UsageExtractionFailedWarning`。

---

## 7. End strategy 语义

`EndStrategy`（见 `_agent_graph.py` 顶部注释）：

- `'early'`：输出工具按产出顺序运行，首个成功即结束；仅当所有输出失败时才运行函数工具。
- `'graceful'`（默认）：工具按产出顺序运行，首个成功输出胜出；函数工具的 `ModelRetry` 会抑制该输出。
- `'exhaustive'`：所有工具并行运行，按产出顺序取首个有效输出胜出。

纯非结构化文本（`str` / `TextOutput`）不会抢占工具调用。默认值在 v2 由 `'early'` 改为 `'graceful'`。
