# 05 · 工具 / Toolset / Capability

本篇覆盖 Agent 的可组合扩展层：

- 工具定义与执行（`tools.py`、`tool_manager.py`）
- Toolset 抽象与包装器（`toolsets/`）
- Capability 抽象、组合与钩子（`capabilities/`）
- MCP 集成、Workspace、内置工具

---

## 1. 工具（`tools.py`）

| 类/类型 | 说明 |
|---------|------|
| `Tool(Generic[ToolAgentDepsT])` | 被装饰函数的工具包装。字段：`function`、`takes_ctx`、`max_retries`、`name`、`description`、`prepare`、`args_validator`、`docstring_format`、`strict`、`sequential`、`requires_approval`、`metadata`、`timeout`、`defer_loading`、`include_return_schema` |
| `ToolDefinition` | 序列化到模型的工具定义（见 [04](04-messages-and-output.md) §7） |
| `ToolPrepareFunc` / `ToolsPrepareFunc` | 单工具 / 整批工具的「准备」函数类型 |
| `ToolSelector` / `matches_tool_selector` | 按 `'all'`、名称、metadata 深包含或谓词选择工具 |
| `GenerateToolJsonSchema` | 工具使用的 schema 生成器 |

`ToolPartKind`（定义在 `messages.py`）= `Literal['tool-search', 'capability-load']`。

---

## 2. Toolset（`toolsets/`）

### 2.1 `AbstractToolset`

`toolsets/abstract.py`：工具的可复用集合，带生命周期、指令与执行边界。

- `id`（抽象属性，需在 Agent 内唯一；durable execution 必需）。框架保留 id：`'<agent>'`（`AGENT_TOOLSET_ID`）、`'<output>'`（`OUTPUT_TOOLSET_ID`）。
- 生命周期：`for_run(ctx)`（每运行一次）、`for_run_step(ctx)`（每步）、`__aenter__` / `__aexit__`。
- 指令：`get_instructions(ctx)` 及 `_collect_instruction_contributions` 机制（保留 per-toolset 归因）。
- 核心：`get_tools(ctx) -> dict[str, ToolsetTool]`（抽象）、`call_tool(name, tool_args, ctx, tool)`（抽象）、`get_tool_for_tool_def(tool_def, ctx)`（供 durable 从已记录定义重建工具）。
- 树操作：`apply(visitor)`、`visit_and_replace(visitor)`。
- 便利包装：`filtered()`、`prefixed()`、`prepared()`、`renamed()`、`approval_required()`、`defer_loading(tool_names)`、`include_return_schemas()`、`with_metadata(**metadata)`。

`ToolsetTool`：包装 `toolset`、`tool_def`、`max_retries`、`args_validator`、`args_validator_func`。

### 2.2 `WrapperToolset`

`toolsets/wrapper.py`：持有 `wrapped` 并委托全部方法，是横切扩展的首选（见 `pydantic_ai/AGENTS.md`：应扩展 `WrapperToolset` 而非修改基类或具体实现）。

### 2.3 具体 Toolset

| 模块 | 主要类 |
|------|--------|
| `function.py` | `FunctionToolset`、`FunctionToolsetTool`（装饰器 `tool`/`tool_plain`/`instructions`；`call_tool` 用 `anyio.fail_after` 施加超时） |
| `combined.py` | `CombinedToolset`（名称冲突抛 `UserError`） |
| `filtered.py` / `prefixed.py` / `renamed.py` / `prepared.py` | `FilteredToolset` / `PrefixedToolset` / `RenamedToolset` / `PreparedToolset` |
| `deferred_loading.py` | `DeferredLoadingToolset`（`PreparedToolset` 子类） |
| `approval_required.py` | `ApprovalRequiredToolset`（未审批则抛 `ApprovalRequired`） |
| `external.py` | `ExternalToolset`（`kind='external'`，`call_tool` 抛错） |
| `_dynamic.py` | `DynamicToolset`、`ToolsetFunc` |
| `_tool_search.py` | `ToolSearchToolset` |
| `_capability_owned.py` | `CapabilityOwnedToolset` |

`AgentToolset = Union[AbstractToolset, ToolsetFunc]`。

---

## 3. Capability（`capabilities/`）

Capability 是「可组合的横切 Agent 行为」的所在地。见 `capabilities/AGENTS.md`：能用 capability 表达的（贡献指令、设置、工具、native 工具、wrapper、生命周期钩子、事件/历史处理），不应新增 `Agent` 构造参数。

### 3.1 `AbstractCapability`

`capabilities/abstract.py` 的 `AbstractCapability(ABC, Generic[AgentDepsT])`。

关键字段：

| 字段 | 说明 |
|------|------|
| `id: str \| None` | 运行内唯一；`defer_loading=True` 时必需；否则由类名推导 |
| `description: str \| None` | 描述 |
| `defer_loading: bool = False` | 模型面向的工具与指令在模型显式 `load_capability` 前隐藏；模型设置与生命周期钩子在运行装配时注册，但仅在加载后生效 |

类级标记：`_one_per_agent`（每 Agent 只允许一个该类能力；durable 能力设置它）、`_safe_at_runtime`（仅 `Instrumentation` 为 `True`）、`_cancellation_error_types`、`_emits_app_events`。

**取值（value-contribution）方法**（在 Agent 构造时调用；`for_run` 返回替换时重新提取）：

- `get_instructions()`、`get_description()`、`get_model_settings()`、`get_model()`（最后非 None 者胜出）、`resolve_model_id()`、`get_toolset()`、`get_native_tools()`、`get_workspace()` / `_prepare_workspace()`、`get_wrapper_toolset(toolset)`（唯一每次运行都调用的取值方法）。

**生命周期元数据**：`for_agent(agent)`、`for_run(ctx)`、`apply(visitor)`、`visit_and_replace(visitor)`、`combine(capabilities)`、`get_ordering()`、`get_serialization_name()` / `from_spec()`。

### 3.2 生命周期钩子

每个家族含 `before_*` / `after_*` / `wrap_*` / `on_*_error`：

| 家族 | 钩子 |
|------|------|
| Run | `before_run`、`after_run`、`wrap_run`、`on_run_error` |
| Node run | `before_node_run`、`after_node_run`、`wrap_node_run`、`on_node_run_error` |
| Event | `on_event`（由 `@on_event` 标记的方法分派）、`wrap_run_event_stream` |
| Model request | `before/after/wrap_model_request`、`on_model_request_error` |
| Tool validate | `before/after/wrap_tool_validate`、`on_tool_validate_error` |
| Tool execute | `before/after/wrap_tool_execute`、`on_tool_execute_error` |
| Output validate | `before/after/wrap_output_validate`、`on_output_validate_error` |
| Output process | `before/after/wrap_output_process`、`on_output_process_error` |
| Tool 准备 | `prepare_tools`、`prepare_output_tools` |
| 延迟工具 | `handle_deferred_tool_calls(requests)` |

不变量：

- 只有 `wrap_*` 允许通过 handler 修改；`before_*` / `after_*` 是观察/转换点。
- **取消是终态**：钩子可观察与清理，但不能把已取消的运行恢复为成功。
- `for_run` 不得获取资源（它在任何运行钩子之前执行，若运行失败无人释放）。

### 3.3 组合

- **`CombinedCapability`**（`combined.py`）：持有子能力序列，展平嵌套并（在声明了 ordering 时）拓扑排序；每个钩子按顺序迭代子能力（`before_*` 正向，`after_*`/`wrap_*` 反向，构成中间件链）。`_rebound(...)` 是唯一受支持的替换子能力方式。`bind_capabilities_tier` 支持两阶段绑定。
- **`WrapperCapability`**（`wrapper.py`）：包装单个 `wrapped` 并委托全部方法；当自身无 `id`/`defer_loading` 时会采纳被包装者的身份，从而能在延迟能力之上保持延迟语义。

### 3.4 `Capability` 便捷类

`capabilities/capability.py` 的 `Capability(AbstractCapability)`：无需子类化即可打包 `toolsets`、`tools`、`description`、`defer_loading`、`id`。装饰器 `tool_plain()` / `tool()` 注册函数工具，`instructions()` 注册指令（键为 `'capability:<id>:<name>'`）。

### 3.5 `Hooks` 与 `@on_event`

- `capabilities/hooks.py`：`Hooks` 能力通过 `hooks.on.<hook>` 装饰器或构造 kwargs 注册钩子函数；`_HookRegistration` 支持 `timeout` 与工具名过滤器；`HookTimeoutError`。`_emits_app_events = True`。
- `capabilities/_on_event.py`：`@on_event(*event_types)` 标记异步方法为监听器；`collect_on_event_methods` / `marked_listens_to` 驱动分派过滤，使能力「不会为每个事件都被唤醒」。

### 3.6 具体 Capability（按主题）

| 模块 | 类 / 用途 |
|------|-----------|
| `native_tool.py` | `NativeTool`：注册单个 native 工具 |
| `native_or_local.py` | `NativeOrLocalTool`：native 工具 + 本地回退；模型不支持 native 时本地工具被启用 |
| `mcp.py` | `MCP`：MCP 能力入口（`NativeOrLocalTool` 子类），由 URL host+slug 推导稳定 id |
| `web_search.py` | `WebSearch`：native `WebSearchTool`，本地 `duckduckgo` 回退 |
| `web_fetch.py` | `WebFetch`：native `WebFetchTool`，本地 markdownify 回退 |
| `image_generation.py` | `ImageGeneration`：native `ImageGenerationTool`，三种互斥回退（local / fallback_image_model / fallback_subagent_model） |
| `x_search.py` | `XSearch`：native `XSearchTool` |
| `thinking.py` / `caching.py` | `Thinking` / `Caching`：通过 `get_model_settings` 设置思考/缓存 |
| `content_filter.py` | `RaiseContentFilterError`：`finish_reason='content_filter'` 时抛错 |
| `deferred_tool_handler.py` | `HandleDeferredToolCalls`：解析 `DeferredToolRequests` |
| `process_history.py` | `ProcessHistory`：在 `before_model_request` 运行 `HistoryProcessor` |
| `process_event_stream.py` | `ProcessEventStream`：事件流处理器（`wrap_run_event_stream`） |
| `instrumentation.py` | `Instrumentation`：OTel/Logfire span（`_safe_at_runtime=True`，ordering 为 outermost） |
| `local_workspace.py` | `LocalWorkspace`：宿主文件系统/子进程 workspace |
| `select_model.py` / `resolve_model_id.py` | `SelectModel` / `ResolveModelId` |
| `prefix_tools.py` | `PrefixTools`（`WrapperCapability` 子类，用 `PrefixedToolset` 包裹） |
| `prepare_tools.py` | `PrepareTools` / `PrepareOutputTools` |
| `set_tool_metadata.py` | `SetToolMetadata` |
| `include_return_schemas.py` | `IncludeToolReturnSchemas` |
| `reinject_system_prompt.py` | `ReinjectSystemPrompt`（UI 适配在 `manage_system_prompt='server'` 时自动添加） |
| `thread_executor.py` | `UseThreadExecutor` |
| `toolset.py` | `Toolset`：包装普通 `AgentToolset` |
| `_tool_search.py` | `ToolSearch`：provider 自适应的延迟工具发现 |
| `_dynamic.py` | `DynamicCapability`、`wrap_capability_funcs` |

注册表 `CAPABILITY_TYPES`（`capabilities/__init__.py`）把序列化名映射到类。

### 3.7 `defer_loading` 语义

- 模型面向的工具与指令保持隐藏，直到模型调用 `load_capability`（或 tool search / 其它工具通过 `ToolReturn.tools` 揭示）。
- 需要稳定 `id`。
- 模型设置与生命周期钩子已注册，但仅在加载后生效。
- `CapabilityOwnedToolset` 为工具打上 `capability_id` / `defer_loading`；`is_gated_by_deferred_capability` 区分「揭示前隐藏」与「可搜索语料成员」。

---

## 4. MCP 集成

- `mcp.py`：定义 `MCPError`、`BaseResource` / `Resource` / `ResourceTemplate` / `ResourceLink`、`Prompt` / `PromptResult`、`Icon`、`ServerCapabilities`、`CallToolFunc`，以及 **`MCPToolset`**（基于 FastMCP `Client` 的推荐 MCP 入口）。
- `MCPToolset` 配置：`tool_error_behavior`（`'retry'|'error'|'failed'`）、`max_retries`、`prefer_tasks`、`cache_tools`/`cache_resources`/`cache_prompts`、`include_instructions`、`process_tool_call`、`sampling_model`/`sampling_handler`。方法含 `list_tools`、`get_tools`、`get_tool_for_tool_def`、`call_tool`、`list_prompts`/`get_prompt`、`list_resources`/`read_resource`、`set_sampling_model`。
- `_mcp.py`：把 MCP sampling 消息转换为 Pydantic AI 消息（`map_from_mcp_params`）。
- **`MCP` capability**（`capabilities/mcp.py`）是 capability 层入口，包装 `MCPToolset` 并支持 native MCP 广告。

---

## 5. Workspace（`workspaces/`）

`workspaces/protocol.py` docstring 定义后端契约：对同一文件系统执行命令与文件操作、报告真实退出码、构建期无 I/O、并发首次使用至多创建一个环境、运行结束不销毁环境、抛出特定错误类型。

- 异常：`WorkspaceError`、`WorkspaceOutputLimitError`、`WorkspaceUnavailableError`、`WorkspaceTimeoutError`、`WorkspaceReadOnlyError`。
- 类型/协议：`CommandResult`、`FileEntry`、`SupportsCommands`、`SupportsFilesystem`、`WorkspaceBackend`、`WorkspaceCommand`。
- 实现：`workspace.py`（`Workspace`、`WrapperWorkspace`）、`local.py`（`LocalWorkspaceBackend`，宿主子进程，「不做隔离」）、`readonly.py`（`ReadOnlyWorkspace`）、`unavailable.py`（`UnavailableWorkspace`、`NO_WORKSPACE`）、`conformance.py`（`WorkspaceBackendSuite`，后端需继承以证明契约）。
- Capability 层：`capabilities/local_workspace.py` 的 `LocalWorkspace`。

---

## 6. 内置工具（`common_tools/`）

| 模块 | 主要类 / 函数 |
|------|----------------|
| `duckduckgo.py` | `DuckDuckGoSearchTool`、`duckduckgo_search_tool()`（工具名 `duckduckgo_search`） |
| `web_fetch.py` | `WebFetchLocalTool`、`web_fetch_tool()`（经 `_ssrf.safe_download` 做 SSRF 防护，HTML→Markdown） |
| `image_generation.py` | `image_generation_tool()`（subagent 回退） |
| `x_search.py` | `x_search_tool()` |
| `tavily.py` | `TavilySearchTool`、`tavily_search_tool()` |
| `exa.py` | 已废弃（`ExaToolset` 等；改用 `pydantic_ai_harness.exa`） |

这些是 `WebSearch` / `WebFetch` / `ImageGeneration` / `XSearch` 能力在 native 工具不可用时解析出的本地回退。

---

## 7. 工具身份与设计的核心约束（来自 AGENTS.md）

- **按「是什么」而非「叫什么」识别工具**：工具名可被用户重命名或加前缀。用 `isinstance` 判断类型化 part（`ToolSearchCallPart`、`LoadCapabilityReturnPart` 等）或 `ToolDefinition.tool_kind`。唯一例外是 native 工具（其 `tool_name` 就是 native 工具的 `kind`）与读取旧版本历史时的名字判断（需在遗留历史代码中说明原因）。
- **本地工具与 provider 原生工具概念分离**：若某功能两者皆可，需显式化回退/选择行为并测试其产生的消息历史。
- **中间件顺序**：「列表中靠前者更外层」。capability 与 toolset 都遵循此规则；`after_*` / `wrap_*` 反向迭代。可通过 `CapabilityOrdering`（`position`、`wraps`、`wrapped_by`、`requires`）强制顺序。
- **durable 名字是兼容数据**：`@durable_operation(name=...)` 与 toolset `id` 一旦发布基本不可更改。
