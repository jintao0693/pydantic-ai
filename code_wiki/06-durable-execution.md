# 06 · Durable Execution

Durable Execution（持久化执行）让一次 Agent 运行在进程重启、失败与长时间等待后仍然存活：每一次模型调用、工具调用、workspace 操作与事件处理都成为一个「持久化单元」（activity / step / task），其结果被记录，重放时复用而不重新执行。

相关目录：[pydantic_ai_slim/pydantic_ai/durable_exec/](../pydantic_ai_slim/pydantic_ai/durable_exec/)

> 见 [durable_exec/AGENTS.md](../pydantic_ai_slim/pydantic_ai/durable_exec/AGENTS.md)：这些集成被视作**核心语义的兼容性测试**，而非外围适配器；新引擎应基于公开面（`BaseDurabilityCapability` + `DurableOperationBackend`）构建，不要复制 model/toolset/event/capability-operation 的管线代码。

---

## 0. 整体模型

```
Agent(capabilities=[TemporalDurability()])          # 或 DBOSDurability / PrefectDurability
  └─ for_agent(agent)                                # 绑定 agent：注册模型、工具集、操作
       ├─ _bind_models(agent)                        # 建立模型注册表（只有字符串能跨界）
       ├─ _register_toolsets(agent)                  # visit_and_replace 每个叶子 toolset -> Durable*Toolset
       ├─ _bind_capability_operations(agent)         # 收集 @durable_operation 并绑定
       └─ _bind_workspace_operation(agent)           # 若构造期有能力提供 workspace 则绑定
  └─ 运行期（在 workflow/flow 内）
       ├─ wrap_run                                  # 事件流、工具串行化、workspace ensure
       ├─ wrap_model_request                        # DurableModel：每段模型请求 = 一个 durable unit
       └─ DurableToolsetBase                        # get_tools / call_tool / validate_args = 各自 durable unit
```

核心抽象：

| 抽象 | 文件 | 角色 |
|------|------|------|
| `BaseDurabilityCapability` | [_base.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_base.py) | agent 面向的持久化能力基类，拥有模型注册表与全部装配逻辑 |
| `DurabilityEngineSpec` | [_spec.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_spec.py) | 声明式引擎配置 |
| `DurableOperation` / `DurableOperationId` | [_operation.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation.py) | 操作语义声明与类型化标识联合 |
| `DurableOperationBackend` 家族 | [_operation_backend.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation_backend.py) | 与引擎 SDK 的契约 |
| `DurableOperationNamer` / `JournalOperationNamer` | [_operation_names.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation_names.py) | 持久化命名（兼容数据） |
| `DurabilityCodec` / `IDENTITY_CODEC` / `JSON_CODEC` | [_codec.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_codec.py) | 每种持久化边界的序列化 |
| `DurableToolsetBase` 等 | [_toolset.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_toolset.py) | 工具集脚手架与生命周期 |
| `DurableWorkspace` | [_workspace.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_workspace.py) | workspace 调用作为持久化操作 |
| `durable_operation` 等 | [_capability_operation.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_capability_operation.py) | capability 方法的持久化操作 |
| `DurableModel` 等 | [_utils.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_utils.py) | 跨边界共享的内部分段/串流工具 |
| 运行期工具集校验 | [_runtime_toolsets.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_runtime_toolsets.py) | 拒绝无法在运行期持久包装的 toolset |

`durable_exec/__init__.py` 用惰性 `__getattr__` 导出引擎构建面（避免包初始化环）。

---

## 1. 公共抽象

### 1.1 `BaseDurabilityCapability(AbstractCapability[AgentDepsT])`

所有持久化能力的基类（[_base.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_base.py)）。

**类级**：

- `engine_spec: ClassVar[DurabilityEngineSpec]`（子类必须设置）。
- `_one_per_agent = 'durable execution engine'`：每个 agent 只允许一个（第二个不会增加持久性，只会接管分派）。

**构造参数**：

| 参数 | 说明 |
|------|------|
| `models: Mapping[str, Model] \| None` | 额外模型注册表，按 key 引用；agent 主模型恒注册为 `'default'` |
| `event_stream_handler` | 事件流处理器；非 `None` 时内部建一个 `ProcessEventStream` |
| `name: str \| None` | 持久化单元名中使用的唯一 agent 名；默认取 `agent.name` |

**属性/方法**：`engine_name`、`durable_unit_noun`、`durable_unit_plural`、`durable_container_noun`、`_cancellation_error_types`（取自 spec）、`agent`、`default_model_id`、`name`；`for_agent(agent)`、`get_durable_operation_backend()`（抽象）、`in_durable_context`（抽象 property）、`get_ordering()`（→ `'innermost'`）、`get_wrapper_toolset(toolset)`、`wrap_run`、`wrap_model_request`、`before_model_request`、`wrap_run_event_stream`、`resolve_model_id`、`from_agent(agent)`（classmethod，找回绑定副本）。

**`for_agent` 装配顺序**（`_bind_for_agent`）：

1. `_check_bindable()`；校验 agent 有唯一 `name`（否则 `UserError`）。
2. 深拷贝自身为 `bound`，重置 `_resolved_request_models` / 请求模型作用域。
3. `_bind_models(agent)`：建立模型注册表。
4. `_bind_to_agent(agent)`：子类建 operation backend。
5. 若 backend 是 `RegisteredOperationBackend`，先绑定四个模型操作（`_bound_model_operations`）。
6. `_bind_capability_operations(agent)`、`_bind_workspace_operation(agent)`。

若 `_companion_capabilities()` 非空（有 workspace 时返回 `[WorkspaceEnsurer()]`），`for_agent` 返回 `CombinedCapability([*companions, bound])`。

**模型跨界往返**（本类最重要的不变量）：

- 只有**字符串**能跨 durable 边界：`None`（agent 默认）、`models=` 注册表 key、或模型名串。
- 未注册的 `Model` 实例会被 workflow/flow 侧拒绝（`_find_model_id` 抛 `UserError`）——因为用它的 `model_id` 重建会得到「同名但不同端点/凭据」的另一个模型。
- `_find_model_id` 会逐层剥离 `WrapperModel`（`unwrap_model`），优先匹配最浅的已注册包装；已注册侧不再解包。
- `_model_id_for_request(ctx, request_context)` 优先用请求的原始 `model_id`（`ModelRequestContext.model_id`），若请求仍指向 run 的模型；否则回退到记录的解析结果或 `_find_model_id`。
- 活动/步骤/任务内用 `_resolve_model_for_request(model_id, run_context)` 重建：跑 agent 完整的 `resolve_model_id` 能力链（deps 感知的用户 `ResolveModelId` 优先），本能力的注册表作为 backstop，最后 `infer_model`。
- `_durable_model_scope(model_id, run_context)`：进入单元时同时（a）用 `guard_run_context` 保护 `ctx.enqueue()`/`ctx.cancel()`，（b）重建模型并 `managed_model_scope` 管理生命周期，把 `ctx.model` 设为活动模型。

**Workspace 路由**（`_bind_workspace_operation` / `_prepare_workspace`）：仅当构造期能力覆写 `get_workspace` 才绑定一个操作（`WORKSPACE_OPERATION_ID = CapabilityOperationId('workspace', operation='call')`），否则保持既有命名不变。容器内 `_prepare_workspace` 用 `DurableWorkspace` 包裹所选 workspace；`_claim_explicit_workspace` / `_check_construction_workspace` 拒绝无法在单元内重建的运行级 workspace。

**Capability 操作绑定**（`_bind_capability_operations`）：遍历 `leaf_capabilities(agent.root_capability)`，用 `collect_capability_operations` 收集 `@durable_operation` 声明；能力必须有显式 `id`（否则 `UserError`）；为每个操作构造 `DurableOperation(operation_id=CapabilityOperationId(capability_id, operation=name), ...)` 并让 backend 绑定，把结果缓存到 `_bound_capability_operations[(capability_id, name)]`。`_prepare_run_context` 把 dispatcher 注册到 `ctx._durable_operations`（先 `clear()`，不重新赋值，使 run 内共享同一映射）。

**工具集包装**（`_register_toolsets` / `_wrap_and_register_leaf`）：对 `agent.toolsets` 做 `visit_and_replace`，按叶子 id 包装并登记到 `_toolsets_by_id`；`DynamicToolset` 无 id 时抛 `UserError`。

**事件流**：`wrap_run_event_stream` 在容器外用 `ProcessEventStream` 转发；容器内逐个事件分派为 durable unit（`_dispatch_event_stream_event` + `_bind_event_operation`）。`_prepare_function_call_params` / `_tool_call_payload_errors` / `_tool_run_context_scope` / 各种 `*_parameter_transport` 是子类可覆写的引擎接缝。

### 1.2 `DurabilityEngineSpec`（全字段）

`@dataclass(frozen=True, kw_only=True)`（[_spec.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_spec.py)）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `engine_name` | `str` | 必填 | 错误信息中的引擎名（如 `'Temporal'`） |
| `durable_unit_noun` | `str` | 必填 | 持久化单元名词（`'activity'`/`'step'`/`'task'`） |
| `durable_container_noun` | `str` | 必填 | 容器名词（`'workflow'`/`'flow'`） |
| `durable_unit_plural` | `str \| None` | `None`（→ noun + `'s'`） | 复数名 |
| `codec` | `DurabilityCodec` | `IDENTITY_CODEC` | 每个持久化边界的序列化方式 |
| `serialization_failure` | `Callable[[Exception], BaseException] \| None` | `None` | 把确定性 codec 失败映射为引擎终态错误类型（JSON 日志引擎用） |
| `wrapped_toolset_kinds` | `frozenset[ToolsetKind]` | `{'function','mcp','dynamic'}` | 哪些叶子 toolset 种类被包装成 durable unit（DBOS 去掉 `'function'`） |
| `toolset_lifecycles` | `Mapping[ToolsetKind, Lifecycle]` | 见 §1.5 | 每种 toolset 的进出策略（强制显式声明） |
| `tool_call_result_upgrade_lenient` | `bool` | `False` | 宽松解码「控制流异常作为值」之前的旧记录（仅 DBOS/Prefect 等旧存储） |
| `journal_discovery` | `bool` | `True` | 工具发现（`get_tools`/`get_instructions`）是否单独成为 durable unit |
| `sequential_tools_in_durable_context` | `bool` | `False` | 容器内是否强制工具串行 |
| `unsupported_runtime_toolset_kinds` | `frozenset[RuntimeToolsetKind]` | `frozenset()` | 因绕过注册而被容器拒绝的运行期 toolset 种类 |
| `tool_config_key` | `str \| None` | `None` | 承载引擎专属 durable 配置的 tool metadata 键 |
| `cancellation_error_types` | `tuple[type[BaseException], ...]` | `()` | 容器取消时引擎用于中止运行的异常类型（非 `asyncio.CancelledError`） |

`__post_init__` 校验：名词非空、`wrapped_toolset_kinds` 的每种都有 lifecycle，否则抛 `UserError`。

**两大类引擎**：**对象传递型**（Temporal/DBOS/Prefect → `IDENTITY_CODEC`，把活 Python 对象交给引擎原语自己序列化）与 **JSON 日志型**（Restate/Lambda/Absurd → `JSON_CODEC`，先把值降为 JSON 兼容形状再写日志）。

### 1.3 `DurableOperation` 与 `DurableOperationId`

[_operation.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation.py)。

`DurableOperation(Generic[ParamsT, WireT, ResultT])`（`@dataclass(frozen=True, kw_only=True)`）：

| 字段 | 说明 |
|------|------|
| `operation_id: DurableOperationId` | 稳定类型化标识（用于命名与配置） |
| `handler: Callable[[ParamsT], Awaitable[ResultT]]` | 语义操作体 |
| `parameter_transport: ParameterTransport[ParamsT, WireT]` | 语义参数 ↔ 引擎线上参数 |
| `cache_identity: CacheIdentity[ParamsT]` | 供 hash-keyed 引擎使用的投影 |
| `result_codec: ResultCodec[ResultT]` | 结果编解码 |
| `config_role: OperationConfigRole` | 粗粒度配置类别（`'model'/'event'/'tool'/'capability'`） |
| `invocation_label: Callable[[ParamsT], str] \| None` | 可选每次调用标签 |

**`DurableOperationId`** 是可扩展联合（可在次版本新增变体，匹配需保留默认分支）：

| 变体 | 字段 |
|------|------|
| `ModelRequestId` | `model_id: str \| None`、`streaming: bool`、`model_name: str` |
| `ModelCompactMessagesId` | `model_id`、`model_name` |
| `ModelCancelSuspendedResponseId` | `model_id`、`model_name` |
| `CapabilityOperationId` | `capability_id: str`、`operation: str` |
| `EventStreamHandlerId` | （无字段） |
| `ToolsetGetToolsId` | `toolset_kind: ToolsetKind`、`toolset_id: str` |
| `ToolsetGetInstructionsId` | `toolset_id: str` |
| `ToolsetValidateToolArgumentsId` | `toolset_kind`、`toolset_id` |
| `ToolsetCallToolId` | `toolset_kind`、`toolset_id` |

`ToolsetKind = Literal['function', 'mcp', 'dynamic']`。参数 dataclass：`ModelRequestParams`、`ModelCancelSuspendedResponseParams`、`ModelCompactMessagesParams`、`EventStreamHandlerParams`、`ToolsetGetToolsParams`、`ToolsetCallToolParams`、`DynamicToolsetCallToolParams`。

协议：`ParameterTransport.dump/load`、`CacheIdentity.project`、`ResultCodec.dump/load`、`DurableOperationConfig.base/for_tool`。内建实现：`IdentityParameterTransport`、`NoCacheIdentity`、`TypedResultCodec(result_type, mode='json'|'identity')`。

### 1.4 后端与命名

**后端**（[_operation_backend.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation_backend.py)）：

| 类型 | 角色 |
|------|------|
| `DurableOperationBackend(ABC, Generic[ConfigT])` | 契约：`bind(operation)`、`config_for_tool(...)`、`registrations()` |
| `CallableOperationBackend` | 引擎接受异步回调：子类实现 `execute(*, operation_id, name, body, cache_key, config)`；基类拥有命名/配置/cache identity/结果编码，并 `durable_unit_scope()` 标记「当前在持久化单元内」 |
| `JournalCallableOperationBackend` | 使用标准 journal 命名约定的 `CallableOperationBackend`（`JournalOperationNamer`） |
| `RegisteredOperationBackend` | 处理器必须在 worker 启动前注册：子类实现 `register(operation, name, config)` 返回「绑定调用器 + 注册句柄」；基类收集 `registrations()` |
| `RoleBasedOperationConfig` | 从角色默认值 + 可选 per-tool resolver 解析配置 |
| `BoundDurableOperation` | 已绑定操作协议：`operation` 属性 + `async __call__(params, *, config=None)` |
| `in_durable_unit()` / `durable_unit_scope()` | 进程内引擎判断「是否在单元体内」（Prefect 的 `FlowRunContext` 在 task 内仍为真，故需此信号） |

**命名**（[_operation_names.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_operation_names.py)）：名字是**持久化的兼容数据，基本不可更改**（改动会搁浅在途 workflow 与已记录运行）。

- `DurableOperationNamer` 协议：`operation_name(operation_id)`、`invocation_name(operation_id, *, label)`；`DurableInvocationName(operation_name, display_name=None)`。
- `JournalOperationNamer(agent_name, *, default_model_id='default')`：约定形如
  - `{agent}__capability__{capability_id}.{operation}`
  - `{agent}__model.request[.model_id]` / `model.request_stream`
  - `{agent}__model.cancel_suspended_response` / `model.compact_messages`
  - `{agent}__event_stream_handler`
  - `{agent}__{kind_toolset|mcp_server}__{toolset_id}.get_tools` / `.validate_args` / `.call_tool`
  - `{agent}__mcp_server__{toolset_id}.get_instructions`
  - 非 MCP 的 `ToolsetCallToolId` 会附加 `:{label}`（每次工具调用一个单元）。
- `_toolset_prefix(kind)`：`'mcp'` → `'mcp_server'`，否则 `'{kind}_toolset'`。

**Codec**（[_codec.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_codec.py)）：`DurabilityCodec.dump(tp, value)` **在单元内**调用（不可序列化的 payload 在 step 内失败，生产与测试一致），`load(tp, payload)` **在单元外**调用。`IDENTITY_CODEC` 直通；`JSON_CODEC` 通过缓存的 `TypeAdapter(tp)` 做 JSON 往返。

### 1.5 Toolset 脚手架（[_toolset.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_toolset.py)）

- `Lifecycle: Literal['enter-outside-durable', 'enter-always', 'enter-never', 'enter-in-durable-unit']`：
  - `enter-always`：容器围绕 run 进入；
  - `enter-outside-durable`：只在容器外进入（Temporal 因 activity 可能在另一进程而需要）；
  - `enter-never`：谁都不进入；
  - `enter-in-durable-unit`：run 持有一个已进入的 toolset，第一个需要它的单元进入它——**只适用于单元在容器同进程内运行**的引擎；这让 `MCPToolset` 每 run 保持一个会话（与其 `cache_tools`）。
- `DurableToolsetBase`、`DurableFunctionToolset`、`DurableDynamicToolset`、`DurableMCPToolset`：按种类包装叶子 toolset；`CallToolOperation` 协议描述调用器；`resolve_tool_durable_config(...)` 从 `ToolsetTool.metadata[tool_config_key]` 解析引擎配置（`False` 可跳过包装）。
- `RunHeldToolset(id, toolset)`：惰性、加锁地在首个需要它的单元内进入 toolset，并把 `run_context` 传进去（`_run_held_toolset` / `toolset_for_unit`）。
- `EnqueueGuard` / `CancelGuard` / `guard_run_context(ctx, *, unit_noun, container_noun)`：把 `ctx.enqueue()` / `ctx.cancel()` 替换为「容器内不支持」的守卫（`PendingMessage` 结构，活控制器永不序列化）。
- **控制流即值**：`wrap_tool_call_result(coro)` 把工具调用的结果或控制流异常编码为可记录的 `CallToolResult`；`unwrap_tool_call_result` / `unwrap_recorded_tool_call_result` 反向解码。记录的判别类型：`_ApprovalRequired`、`_CallDeferred`、`_ModelRetry`、`_ValidationError`、`_ToolFailed`、`_ToolReturn`、`_ToolContentResult`。
- `DynamicToolsResult` / `DynamicToolInfo`：动态 toolset 的持久化发现（工具定义、`max_retries`、是否有 `args_validator`、指令），`get_dynamic_tools` / `call_dynamic_tool` / `validate_dynamic_tool_args` 走同一 durable 路径。
- `validation_context_from_agent(agent)`：worker 侧重建校验上下文（`RunContext` 重建后无活 `tool_manager` 时的兜底）。

### 1.6 Capability 操作（[_capability_operation.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_capability_operation.py)）

**`@durable_operation(name)`**：声明一个异步 capability 方法为持久化操作。

- 必须显式给 `name`（它是持久化兼容数据；Python 方法名可自由改）；空名 `ValueError`，非字符串 `ValueError`，同步方法 `TypeError`。
- 装饰后的包装：绑定调用以便无论调用风格都能看到 context 参数；从显式参数或环境 `get_current_run_context()` 取 `RunContext`（无环境则直通原方法）；若 context 上无 dispatcher（run 未准备 / 已在边界外重建）则内联调用；否则经 `ctx._durable_operations[(self.id, name)]` 分派。
- 若方法收 `ModelRequestContext`，worker 侧改动会经 `ModelRequestContextProjection` 写回活 context。

`base_hook_durable_operation(name)`：标记基类钩子，使每个覆写自动继承持久化执行。`collect_capability_operations(capability)`：两阶段 MRO 扫描（先「覆写的基类钩子」，再「直接标记的方法」），校验 never-durable 钩子与重名，产出 `CapabilityMethodDeclaration`。

**Never-durable 钩子**：所有 `wrap_*`（因接收 handler callable）、`wrap_run_event_stream`（接收活流）、`get_toolset` / `get_wrapper_toolset`（返回活 toolset）、`get_workspace`（返回活 backend）。

类型：`CapabilityOperationParams(run_context, *, arguments, model_id=None)`、`CapabilityOperationResult(value, *, usage_delta)`、`ModelRequestContextProjection`（把活 `Model` 换成 `model_id` 的可序列化投影）、`CapabilityCacheIdentity`（投影 `(model_id, arguments, run_context)`）、`bind_arguments` / `bind_declaration_body` / `call_declaration` / `recover_capability`。

### 1.7 Workspace（[_workspace.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_workspace.py)）

`WORKSPACE_OPERATION_ID = CapabilityOperationId('workspace', operation='call')`。容器内 `ctx.workspace` 是 `DurableWorkspace`：workflow/flow 代码里的每个 workspace 调用（含 `ensure`）都成为一个持久化操作（`WorkspaceCall`），重放时不重复；单元内（工具、`@durable_operation`）的调用直达底层 workspace。

- `WorkspaceCall(method, path, data, command, shell, env, timeout)` + `WorkspaceCallResult` + `WorkspaceCallError`：pydantic dataclass，`ser_json_bytes='base64'`（Temporal 按运行时类型序列化 `bytes`）。
- `WorkspaceCallCacheIdentity.project` = `(call, ref)`。
- **error-as-data**：`_EXPECTED_ERRORS` 列出的预期错误（`WorkspaceTimeoutError`、`WorkspaceUnavailableError`、`WorkspaceReadOnlyError`、`WorkspaceOutputLimitError`、`WorkspaceError`、`FileNotFoundError`、`NotADirectoryError`、`IsADirectoryError`、`PermissionError`、`FileExistsError`、`OSError`、`UnicodeEncodeError`、`NotImplementedError`、`UserError`、`TypeError`、`ValueError`）经 `error_as_data` 转成数据跨越边界，`raise_error` 以原始类型重抛；瞬时 OS 失败（`ConnectionError`/`TimeoutError`/`InterruptedError`/`BlockingIOError`）**不**转数据，留给引擎重试。
- `DurableWorkspace`：`_in_container()`（持久化上下文且不在单元内）；容器内每个方法走 `_call` → `_ensure`（加锁，只跑一次，并行首次调用共享同一环境）→ `_dispatch`；`backend` 属性在容器内抛 `UserError`。
- `WorkspaceEnsurer`（`outermost` 层）：`wrap_run` 前先 `ensure`（因 durability 能力是 `innermost`，否则第一个 workspace 调用会走惰性兜底）。
- `RejectWorkspaceInContainer`：供已弃用的 wrapper agent 在容器内拒绝 workspace（`_safe_at_runtime=True`）。

### 1.8 运行期工具集校验（[_runtime_toolsets.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_runtime_toolsets.py)）

`reject_unsupported_runtime_toolsets(toolsets, *, unsupported_kinds, engine, tool_config_key=None)`：运行期通过 `run(toolsets=...)` 传入的**执行型**叶子 toolset（`FunctionToolset` / `MCPToolset` / `DynamicToolset`）在构造期包装已完成后才到，故会在 workflow 内未检查点地运行 → 抛 `UserError`。非执行型 toolset（如 `ExternalToolset`）允许。若工具 metadata `{tool_config_key: False}`（且 toolset 所有工具都如此）可对 async 函数工具豁免。`reject_cancellation_token(...)` / `cancellation_token_unsupported_error(engine)`：拒绝同进程取消令牌跨 durable 边界。

### 1.9 跨边界共享工具（[_utils.py](../pydantic_ai_slim/pydantic_ai/durable_exec/_utils.py)）

- `managed_model_scope(model, *, owned)`：单元拥有模型时做 `__aenter__`/`__aexit__`（丢弃 `__aexit__` 返回值，不掩盖单元体异常）。
- `unwrap_model(model)`：剥离 `WrapperModel` 层（用于识别运行期确实换过模型，而 `model_id` 字符串比较太粗、直接比较实例又太严）。
- `StreamedActivityResult(response, events)`：跨边界携带最终 `ModelResponse` 与原始流事件。
- `DurableModel(WrapperModel)`：把每个模型请求分段（`request` / `request_stream` / `cancel_suspended_response` / `compact_messages`）经其各自的 durable unit 分派；由引擎能力在包装请求生命周期前安装。
- `capture_event_stream(...)`：在持久化边界内捕获活模型流（handler 消费后剩余事件被 drain 并捕获），把原始事件送回 workflow 重放。
- `disable_threads`：容器内禁用线程（Temporal 需要）。

---

## 2. 引擎适配

### 2.1 三引擎对比表（真实 `engine_spec` 值）

| 字段 | **Temporal** | **DBOS** | **Prefect** |
|------|--------------|----------|-------------|
| 能力类 | `TemporalDurability` | `DBOSDurability` | `PrefectDurability` |
| `engine_name` | `'Temporal'` | `'DBOS'` | `'Prefect'` |
| `durable_unit_noun` / plural | `activity` / `activities` | `step` / `steps` | `task` / `tasks` |
| `durable_container_noun` | `workflow` | `workflow` | `flow` |
| `codec` | `IDENTITY_CODEC` | `IDENTITY_CODEC` | `IDENTITY_CODEC`（对象传递，Prefect 内部序列化/缓存） |
| `wrapped_toolset_kinds` | `function, mcp, dynamic` | `mcp, dynamic`（函数工具内联为 `@DBOS.step`） | `function, mcp, dynamic` |
| `unsupported_runtime_toolset_kinds` | `function, mcp, dynamic` | `mcp, dynamic` | `function, mcp, dynamic` |
| `toolset_lifecycles` | `function: enter-outside-durable`、`mcp: enter-outside-durable`、`dynamic: enter-never` | `mcp: enter-in-durable-unit`、`dynamic: enter-never` | `function: enter-always`、`mcp: enter-in-durable-unit`、`dynamic: enter-never` |
| `tool_call_result_upgrade_lenient` | `False` | `True` | `True` |
| `journal_discovery` | `True` | `True` | `True` |
| `sequential_tools_in_durable_context` | `False` | `False` | `False` |
| `tool_config_key` | `'temporal'` | `None` | `'prefect'` |
| `cancellation_error_types` | `()` | `(DBOSWorkflowCancelledError, DBOSWorkflowConflictIDError)` | `()` |
| `in_durable_context` | `workflow.in_workflow()` | `DBOS.workflow_id is not None and DBOS.step_id is None` | `FlowRunContext.get() is not None` |
| 命名器 | `TemporalOperationNamer`（前缀 `agent__{name}`） | `DBOSOperationNamer`（继承 `JournalOperationNamer`，模型后缀为空） | `PrefectOperationNamer`（人类可读名） |

**Prefect 采用 hash-keyed（需 per-container 序列支持事件重放），其余为 sequence-keyed**；DBOS 步骤按启动顺序编号，故 workspace 场景必须串行。

### 2.2 Temporal

能力模块 [temporal/_durability.py](../pydantic_ai_slim/pydantic_ai/durable_exec/temporal/_durability.py)。

构造参数：`models`、`event_stream_handler`、`event_stream_topic`、`name`、`deps_type`、`activity_config`、`model_activity_config`、`event_stream_handler_activity_config`、`toolset_activity_config`、`run_context_type`。

- Activity 配置默认 60s `start_to_close_timeout`；模型 activity 额外带 `heartbeat_timeout=30s`（`_DEFAULT_MODEL_HEARTBEAT_TIMEOUT`）；所有 activity 后台心跳。配置用 `validate_activity_config` 归一化（未知键/非法值会在 workflow 内首次 `start_activity()` 时永久卡住 workflow task）。`with_non_retryable_errors` 给 retry policy 注入非重试错误。
- `_check_bindable()`：agent 必须在 workflow 外构造（以便 activity 在 workflow 运行前注册到 worker）。
- `_bind_to_agent`：建 `TemporalOperationBackend`，注册活动；`_register_activities` 绑定四个模型活动（`request_activity`/`request_stream_activity`/`compact_messages_activity`/`cancel_suspended_response_activity`），有事件流 handler 时再绑定 `event_stream_handler_activity`，然后包装 toolset。
- 通过 Workflow Stream 做工作流外事件流：`event_stream_topic` + `AgentEventStream` + `stream_agent_events()`；`_TerminalEventPublisher`（`outermost`）在 `after_run` 发布终结事件。
- `temporal_activities` 属性供 worker 注册；`PydanticAIPlugin` 走 `__pydantic_ai_agents__`，`AgentPlugin` 针对单个 agent。
- 沙箱：`PydanticAIPlugin` 的 `_workflow_runner` 把 `pydantic_ai`/`pydantic`/`httpx`/`openai`/`anthropic`/`google.auth`/`fastmcp`/`logfire` 等加入 passthrough，并安装 replay isolation guard。
- `workflow_failure_exception_types = [UserError, PydanticUserError, AgentRunError, UnsupportedEventLoopError, WorkspaceError]`（这些必须让 workflow 失败，而非让 workflow task 无限重试）。
- 图像输出不支持（`IMAGE_OUTPUT_UNSUPPORTED_MESSAGE`：图像会挤进受 2MB 限制的 activity payload）。
- `serialization_user_error`：把 activity 参数序列化失败映射为可读的 `UserError`（除 `deps` 外还可能是 `model_settings`、`RunContext.metadata`、`tool_call_metadata`、工具 metadata、`CustomEvent`/`CapabilityEvent` payload）。
- `_tool_run_context_scope` 直接 yield（Temporal 在反序列化 run context 时已应用守卫，重复包装会覆盖 activity 专属兼容状态）。

**`temporal/` 子包文件**：`__init__.py`、`_activity_execution.py`、`_agent.py`（`TemporalAgent`，已弃用）、`_durability.py`、`_dynamic_toolset.py`、`_event_stream.py`、`_function_toolset.py`、`_logfire.py`（`LogfirePlugin`）、`_mcp_toolset.py`、`_model.py`、`_model_errors.py`、`_operation_backend.py`、`_operation_names.py`、`_payload_converter.py`（`PydanticAIPayloadConverter`）、`_replay_safe_tracer_provider.py`、`_run_context.py`（`TemporalRunContext`、`deserialize_run_context`）、`_toolset.py`（`TemporalWrapperToolset`、`temporalize_toolset`、`with_non_retryable_errors`、`validate_activity_config`）、`_transports.py`、`_workflow.py`（`PydanticAIWorkflow`）。

`temporal/__init__.py` 公开：`TemporalAgent`（弃用）、`TemporalDurability`、`PydanticAIPlugin`、`LogfirePlugin`、`AgentPlugin`、`TemporalRunContext`、`TemporalWrapperToolset`、`TemporalOperationNamer`、`PydanticAIWorkflow`、`PydanticAIPayloadConverter`、`AgentEventStream`、`WorkflowStreamTopic`、`DurableAgentRunEvents`、`workflow_stream_event_handler`、`stream_agent_events`。

### 2.3 DBOS

能力模块 [dbos/_durability.py](../pydantic_ai_slim/pydantic_ai/durable_exec/dbos/_durability.py)。

构造参数：`models`、`event_stream_handler`、`name`、`model_step_config`、`event_stream_handler_step_config`、`mcp_step_config`、`parallel_execution_mode: DBOSParallelExecutionMode = 'parallel_ordered_events'`、`register_legacy_workflows: bool = False`。

- 无 `tool_config_key`：DBOS 不接受 per-tool 配置；工具调用 step 名刻意不带工具名（一个 toolset 的工具共用一个 step），故 per-tool 配置会是「首个工具决定」。
- `wrap_run` 对每个入口应用配置的执行模式；**容器内且 workspace 已附着时强制 `'sequential'`**（并行工具各自产生 workspace step 会把记录结果在恢复时重放到错误调用）。
- `_toolset_in_durable_context()` 返回 `True`（DBOS step 在 workflow 外退化为内联调用，保持 wrapper 时代生命周期）。
- `_durable_run_context` 用 `guard_enqueue_in_workflow`（只在真在 workflow 内守卫）。
- `register_legacy_workflows`：注册已弃用 `DBOSAgent` 用的 workflow 名，便于在途 wrapper 时代 workflow 迁移恢复；`_in_legacy_workflow` / `_legacy_run_event_stream_handler` 用 ContextVar 保存 legacy 状态。
- `cancellation_error_types` 见对比表；`DBOS.cancel_workflow()` 用这些 `BaseException` 而非 `CancelledError` 中止运行。

**`dbos/` 子包文件**：`__init__.py`、`_agent.py`（`DBOSAgent`、`DBOSParallelExecutionMode`，已弃用）、`_durability.py`、`_mcp_toolset.py`、`_model.py`（`DBOSModel`）、`_operation_backend.py`、`_operation_names.py`（`DBOSOperationNamer`）、`_utils.py`（`StepConfig`）。

公开：`DBOSAgent`（弃用）、`DBOSDurability`、`DBOSModel`、`DBOSOperationNamer`、`DBOSParallelExecutionMode`、`StepConfig`。

### 2.4 Prefect

能力模块 [prefect/_durability.py](../pydantic_ai_slim/pydantic_ai/durable_exec/prefect/_durability.py)。

构造参数：`models`、`event_stream_handler`、`name`、`event_stream_handler_task_config`、`model_task_config`、`mcp_task_config`、`tool_task_config`。

- 模型与事件 handler task 与工具 task 一样叠加 `with_non_retryable_errors`；`_normalize_unit_config` 也做同样处理。
- `get_durable_operation_backend()` 返回 `PrefectOperationBackend`，其中 `resolve_tool` 走 `_build_resolve_tool_config`，per-tool 覆盖通过 `metadata={'prefect': TaskConfig(...)}`（`False` 跳过 task 包装）；`event_sequence_key=f'pydantic_ai_event_sequence:{self.name}'`。
- `_default_run_id()`：从 `FlowRunContext` 的 `task_run_dynamic_keys['pydantic_ai:workspace_run_id']` 递增，返回 `f'{flow_run.id}:{sequence}'`。
- `_stamp_response` 用 `_stamp_response_provenance` 记录来源。
- Prefect 为 hash-keyed，需 per-container 序列支持事件重放。

**`prefect/` 子包文件**：`__init__.py`、`_agent.py`（`PrefectAgent`，弃用）、`_cache_policies.py`（`DEFAULT_PYDANTIC_AI_CACHE_POLICY`）、`_durability.py`、`_dynamic_toolset.py`、`_function_toolset.py`、`_mcp_toolset.py`、`_model.py`（`PrefectModel`）、`_operation_backend.py`、`_operation_names.py`（`PrefectOperationNamer`）、`_toolset.py`、`_types.py`（`TaskConfig`）。

公开：`PrefectAgent`（弃用）、`PrefectDurability`、`PrefectModel`、`PrefectOperationNamer`、`PrefectMCPToolset`（弃用）、`PrefectFunctionToolset`（弃用）、`TaskConfig`、`DEFAULT_PYDANTIC_AI_CACHE_POLICY`。

---

## 3. 使用示例

### 3.1 Temporal（完整形态）

```python
from temporalio import workflow

from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow, TemporalDurability

# agent 必须在 workflow 之外构造，activity 才能在 workflow 运行前注册到 worker
agent = Agent(
    'openai:gpt-5.6-sol',
    name='researcher',
    capabilities=[WebSearch(), TemporalDurability()],
)


@workflow.defn
class ResearchWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [agent]

    @workflow.run
    async def run(self, topic: str) -> str:
        result = await agent.run(f'Write a brief on: {topic}')
        return result.output
```

安装：`uv add "pydantic-ai[temporal]"`。Worker/客户端用 `PydanticAIPlugin` 注册（`Client.connect(..., plugins=[PydanticAIPlugin()])`、`Worker(..., plugins=[PydanticAIPlugin()])`），或对单个 agent 用 `AgentPlugin(agent)`；`PydanticAIPlugin` 会遍历 `__pydantic_ai_agents__` 收集各 agent 的 `temporal_activities`。

### 3.2 DBOS

```python
from dbos import DBOS

from pydantic_ai import Agent
from pydantic_ai.durable_exec.dbos import DBOSDurability

agent = Agent('openai:gpt-5.6-sol', name='researcher', capabilities=[DBOSDurability()])


@DBOS.workflow()
async def research(topic: str) -> str:
    # 在你的 @DBOS.workflow 内调用 agent.run()，这次运行即为持久化运行
    result = await agent.run(f'Write a brief on: {topic}')
    return result.output
```

安装：`uv add "pydantic-ai[dbos]"`。注意：`agent.run_stream()` 不能用于 DBOS workflow 内（应设置 `event_stream_handler=` 并改用 `agent.run()`）。

### 3.3 Prefect

```python
from prefect import flow

from pydantic_ai import Agent
from pydantic_ai.durable_exec.prefect import PrefectDurability

agent = Agent('openai:gpt-5.6-sol', name='researcher', capabilities=[PrefectDurability()])


@flow(name='research')
async def research(topic: str) -> str:
    # 在 flow 内调用 agent.run()，模型请求与工具调用成为 Prefect tasks
    result = await agent.run(f'Write a brief on: {topic}')
    return result.output
```

安装：`uv add "pydantic-ai[prefect]"`。

---

## 4. 重要约束

- **事件流缓冲区**：`GraphAgentState.event_stream_buffer` 是运行作用域的框架事件队列。在 **Temporal** 下，从工具或事件流处理函数 `emit` 会报错（它们在 activity 中运行，无法访问缓冲区）。在 DBOS/Prefect 下缓冲区在进程内可用，但在持久化单元内 `emit` 是执行副作用——**重放步骤或缓存任务不会再次 emit**。
- **durable 名字不可随意更改**：`@durable_operation(name=...)` 与 `JournalOperationNamer` / `TemporalOperationNamer` / `PrefectOperationNamer` 的输出是持久化兼容数据。改动前先用 [tests/durable_exec/test_durable_exec_compat.py](../tests/durable_exec/test_durable_exec_compat.py) 的模式把完整名字集 pin 住；除非有意并经过评审迁移在途执行，否则绝不因实现改名而更新这些 pin。
- **只有字符串能跨越模型边界**：未注册的 `Model` 实例会被拒绝。想用特定实例，要么在 `models=` 注册并按 key/实例引用，要么传模型名串让 `ResolveModelId` 在 worker 侧构建。
- **能力需能跨越持久化边界**：capability 在工具调用时从 `RunContext` 读取的任何内容都必须可序列化或随行携带。`id(...)` 或其它进程本地地址作为 key 的状态在 worker 侧会完全 miss（对象活在 workflow 进程）。Temporal 下 `_GUARDED_FIELDS` / `_NONE_UNLESS_ATTACHED` / `_DEFAULTED_UNLESS_CARRIED`（见 `temporal/_run_context.py`）界定每个字段的行为；`tool_manager` 读作 `None`，故 `ctx.tools` 变为 `{}` 而非报错。用 [tests/durable_exec/](../tests/durable_exec/) 中的测试证明行为。
- **每个叶子 toolset 需要稳定 `id`**：durable 引擎按 id 包装/命名 durable unit。函数工具集用 `FunctionToolset(id=...)`，MCP 用 `MCPToolset(..., id=...)`；能力贡献的 toolset 用能力 `id`（如 `WebSearch(local='duckduckgo', id='search')`、`MCP(url='...', id='...')`）。`DynamicToolset` 无 id 直接拒绝。
- **workspace 必须来自构造期能力**：单元会从构造期能力重建 workspace，故运行级 `workspace=` 只能传一个该能力认得的 `WorkspaceRef`（或 `result.workspace`）；调用方的策略包装（如 `ReadOnlyWorkspace`）必须改配到能力上。
- **运行期不得添加执行型 toolset**：`FunctionToolset` / `MCPToolset` / `DynamicToolset` 只能在构造期传入（见 §1.8）；同进程 `cancellation_token` 也不能跨边界。
- **durable unit 可能执行不止一次**：若进程在副作用之后、检查点提交之前失败，单元会重跑。要求幂等，或在可用处暴露引擎原生 at-most-once 选项。
- **workflow/flow 侧代码必须确定**：所有在容器内运行的工厂（`DynamicCapability` 的工厂、`DynamicToolset(per_run_step=False)` 的工厂）必须对同一 `deps` 给出相同结果；I/O 应放进单元内的 toolset 使用处。
- **资源不得逃出单元**：按 `engine_spec.toolset_lifecycles` 进出并关闭 toolset 资源（含失败与取消路径），验证单元内创建的资源不逃逸。
- **控制流即值**：工具抛出的 `ApprovalRequired`/`CallDeferred`/`ModelRetry`/`ToolFailed`/`ValidationError` 会作为数据记录并在反序列化侧重建（`_unwrap_tool_result`）；`tool_call_result_upgrade_lenient` 引擎还兼容旧的未包装记录，新引擎必须保持 `False`（宽松解码会把损坏/误解码的 payload 当成合法值传给模型）。

---

## 5. 测试与兼容性

- `tests/durable_exec/` 下按引擎分目录（`temporal/`）、并有跨引擎的通用用例：`test_durable_exec_compat.py`（名字 pin）、`test_capability_durable_operation.py`、`test_durable_operation.py`、`test_durable_workspace.py`、`test_dbos.py`、`test_dbos_workspace.py`、`test_prefect.py`、`test_prefect_workspace.py`、`test_temporal_skip_gating.py`、`workspace_scenarios.py`。
- Temporal 专项：`temporal/test_durability.py`、`test_toolsets.py`、`test_agent.py`、`test_workspace.py`、`test_workflow_streams.py`、`test_model_and_serialization.py`。
- 新引擎至少需覆盖：replay、teardown、控制流异常、持久化输出升级（legacy payload）、以及**在 durable 上下文之外**的行为（能力透明，普通 `Agent` 运行不受影响）。
