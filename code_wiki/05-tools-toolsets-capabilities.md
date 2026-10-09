# 05 · 工具 / Toolset / Capability

本篇覆盖 Agent 的可组合扩展层——从「一个工具是什么」到「横切行为如何织入运行」：

- 工具定义与执行（[tools.py](../pydantic_ai_slim/pydantic_ai/tools.py)、[tool_manager.py](../pydantic_ai_slim/pydantic_ai/tool_manager.py)、[_tool_execution.py](../pydantic_ai_slim/pydantic_ai/_tool_execution.py)）
- Toolset 抽象与包装器（[toolsets/](../pydantic_ai_slim/pydantic_ai/toolsets/)）
- Capability 抽象、组合与钩子（[capabilities/](../pydantic_ai_slim/pydantic_ai/capabilities/)）
- MCP 集成、Workspace、内置工具

> 设计契约见 [toolsets/AGENTS.md](../pydantic_ai_slim/pydantic_ai/toolsets/AGENTS.md) 与 [capabilities/AGENTS.md](../pydantic_ai_slim/pydantic_ai/capabilities/AGENTS.md)。两条最重要的规则：**能用 Toolset/Capability 表达的横切行为，不要新增 `Agent` 构造参数**；**工具身份按「是什么」而非「叫什么」识别**。

---

## 0. 分层总览

```
Agent
 ├─ tools / toolsets / output_type
 │    └─ AbstractToolset ──(组合)── WrapperToolset ──> 具体 Toolset
 │           └─ get_tools() -> {name: ToolsetTool}      # ToolsetTool 包装 ToolDefinition
 ├─ capabilities=[...]
 │    └─ AbstractCapability ──(组合)── CombinedCapability / WrapperCapability
 │           └─ get_toolset() / get_wrapper_toolset() / get_instructions() / hooks
 └─ 运行期：ToolManager 负责本 step 的工具解析、参数校验、执行与重试
      └─ _tool_execution.process_tool_calls() 按 end_strategy 调度 output/function 工具
```

| 目录/文件 | 职责 |
|-----------|------|
| `tools.py` | `Tool`、`ToolDefinition`、各类工具函数类型别名、`ToolSelector` |
| `tool_manager.py` | `ToolManager`、`ValidatedToolCall`、校验/执行钩子编排、失败重试账本 |
| `_tool_execution.py` | `process_tool_calls` 与 early/graceful/exhaustive 三种调度器 |
| `toolsets/` | `AbstractToolset` + 全部具体 Toolset |
| `capabilities/` | `AbstractCapability` + 全部具体 Capability |
| `mcp.py` / `_mcp.py` | MCP 类型与 `MCPToolset` |
| `workspaces/` | Workspace 契约与实现 |
| `common_tools/` | 内置本地工具（DuckDuckGo / web fetch / Tavily / Exa / …） |

`ToolPartKind`（定义在 [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py)）= `Literal['tool-search', 'capability-load']`，用于把工具搜索结果提升为类型化 part。

---

## 1. 工具定义（`tools.py`）

### 1.1 类型别名

| 名称 | 定义 |
|------|------|
| `ToolParams` | `ParamSpec('ToolParams', default=...)` |
| `DocstringFormat` | `Literal['google', 'numpy', 'sphinx', 'auto']` |
| `SystemPromptFunc[DepsT]` | `Callable[[RunContext[DepsT]], str \| None]` 及其 async / 无 ctx 变体 |
| `ToolFuncContext[DepsT, P]` | `Callable[Concatenate[RunContext[DepsT], P], Any]` |
| `ToolFuncPlain[P]` | `Callable[P, Any]` |
| `ToolFuncEither[DepsT, P]` | 上两者的联合 |
| `ArgsValidatorFunc[DepsT, P]` | 校验函数：以 `RunContext` + 已转换参数为入参；同步或异步 |
| `ToolPrepareFunc[DepsT]` | `Callable[[RunContext[DepsT], ToolDefinition], ToolDefinition \| None \| Awaitable[...]]` |
| `ToolsPrepareFunc[DepsT]` | `Callable[[RunContext[DepsT], list[ToolDefinition]], list[ToolDefinition] \| Awaitable[...]]` |
| `NativeToolFunc[DepsT]` | `Callable[[RunContext[DepsT]], AbstractNativeTool \| None \| Awaitable[...]]` |
| `AgentNativeTool[DepsT]` | `AbstractNativeTool \| NativeToolFunc[DepsT]` |
| `ToolSelector[DepsT]` | `Literal['all'] \| Sequence[str] \| dict[str, Any] \| ToolSelectorFunc[DepsT]` |
| `ObjectJsonSchema` | `dict[str, Any]`（`{"type": "object"}` 形状） |
| `ToolKind` | `Literal['function', 'output', 'external', 'unapproved']` |

`ArgsValidatorFunc` 抛 `ModelRetry` 表示「让模型改正参数后重试」；抛 `ToolFailed` 表示终态失败（模型应改弦更张）；成功返回 `None`。

### 1.2 `Tool(Generic[ToolAgentDepsT])`

`@dataclass(init=False)`。字段一览：

| 字段 | 类型 | 说明 |
|------|------|------|
| `function` | `ToolFuncEither` | 被包装的函数 |
| `takes_ctx` | `bool` | 是否首参为 `RunContext`（未显式指定时由签名推断） |
| `max_retries` | `int \| None` | 该工具的重试上限；`None` 时继承 agent/工具集默认 |
| `name` | `str` | 工具名（默认函数 `__name__`） |
| `description` | `str \| None` | 描述（默认取 docstring） |
| `prepare` | `ToolPrepareFunc \| None` | 每 step 生成/改写工具定义，返回 `None` 则该 step 不注册 |
| `args_validator` | `ArgsValidatorFunc \| None` | schema 校验通过后、执行前的自定义校验 |
| `docstring_format` | `DocstringFormat` | 默认 `'auto'` |
| `require_parameter_descriptions` | `bool` | 缺参数描述是否报错 |
| `strict` | `bool \| None` | 供应商级严格 schema（OpenAI/Anthropic/Google/Bedrock） |
| `sequential` | `bool` | 是否为「屏障」工具（独占执行，不与其它工具重叠） |
| `requires_approval` | `bool` | 是否需要人类审批（HITL） |
| `metadata` | `dict[str, Any] \| None` | 不发给模型，用于过滤/行为定制 |
| `timeout` | `float \| None` | 执行超时秒数；超时向模型返回重试提示 |
| `defer_loading` | `bool` | 是否先对模型隐藏，待揭示后才可用 |
| `include_return_schema` | `bool \| None` | 是否把返回 schema 发给模型 |
| `function_schema` | `_function_schema.FunctionSchema` | 参数 JSON schema 的来源 |

构造参数比字段多一个 `schema_generator: type[GenerateJsonSchema] = GenerateToolJsonSchema`。校验器 `_validate_max_retries`（`>= 0`）与 `_validate_timeout`（`> 0`）会在构造时就拒绝非法值。

方法：

- `Tool.from_schema(function, name, description, json_schema, takes_ctx=False, sequential=False, args_validator=None)`：从既有 JSON schema 构造工具（**跳过** schema 校验，仅按关键字调用函数）。
- `tool_def`（property）：据此生成 `ToolDefinition`，`kind='unapproved' if requires_approval else 'function'`。
- `prepare_tool_def(ctx)`：返回 `tool_def`，或在设置了 `prepare` 时调用它（支持 async）。

### 1.3 `ToolDefinition`

`@dataclass(repr=False, kw_only=True)`，是发给模型的工具定义（函数工具与 output 工具共用）。

| 字段 | 默认 | 说明 |
|------|------|------|
| `name` | 必填 | 工具名 |
| `parameters_json_schema` | `{'type': 'object', 'properties': {}}` | 参数 schema |
| `description` | `None` | 描述 |
| `outer_typed_dict_key` | `None` | 非 `object` schema 的 output 工具在 TypedDict 中的键 |
| `strict` | `None` | 供应商严格模式；`None` 时按 provider 推断 |
| `sequential` | `False` | 屏障语义 |
| `kind` | `'function'` | `ToolKind` |
| `metadata` | `None` | 工具集/能力可写；MCP 工具携带 `meta`/`annotations` 与 `task` 标志 |
| `timeout` | `None` | 执行超时 |
| `defer_loading` | `False` | 作者意图：先隐藏；当前线格位置另行记录在 `ModelRequestParameters.tool_visibility` |
| `unless_native` | `None` | 设值即「当该 native 工具被支持时从线上移除」（本地回退标记）。兼容旧名 `prefer_native` / `prefer_builtin` |
| `with_native` | `None` | 设值即「属于某 native 工具管理的语料」，由该 native 的工具适配器决定线上格式 |
| `tool_kind` | `None` | 跨 provider 类型化 part 的判别符（如 `'tool-search'`） |
| `return_schema` | `None` | 返回值 schema（原生支持者作为结构化字段，否则以 JSON 文本注入描述） |
| `include_return_schema` | `None` | `True`/`False`/`None`（默认 `False`，除非用 `IncludeToolReturnSchemas`） |
| `toolset_id` | `None` | 来源 toolset 的 id，收集时自动设置，供 durable 配置使用 |
| `capability_id` | `None` | 贡献此工具的能力 id（延迟能力的门禁依据） |

> `unless_native` 与 `with_native` 是一对：前者 = 「支持 X 时把我丢掉（本地回退）」；后者 = 「我属于 X 的语料」。当 X 不被模型支持时 `with_native` 会被清除。

派生成员：`function_signature`（`cached_property`，懒算自 `parameters_json_schema` + `return_schema`）、`render_signature(body, **kwargs)`、`defer`（`property`，`kind in ('external', 'unapproved')`）。

### 1.4 `GenerateToolJsonSchema`

`GenerateToolJsonSchema(GenerateJsonSchema)` 是工具默认的 schema 生成器：

- `enum_schema`：把枚举成员的 docstring 渲染成 `anyOf: [{'const': ...}]`（需枚举类混入 `UseEnumMemberDocstrings`，`_utils.enum_member_docstrings` 解析）；`None` 成员会退回普通 `enum` 列表。
- `_named_required_fields_schema`：移除无用的属性 `title`。

### 1.5 工具选择器

`matches_tool_selector(selector, ctx, tool_def) -> bool` 统一判定：

- `'all'` → 总是匹配；
- 可调用 → 同步/异步谓词；
- `dict` → `_metadata_includes` 深包含检查（嵌套 dict 递归比较，工具 metadata 可有多余键）；
- `str` → 名字相等；
- `Sequence[str]` → 名字在集合中。

前三种（`'all'` / 序列 / dict）可序列化进 agent spec。

---

## 2. 工具执行（`tool_manager.py` / `_tool_execution.py`）

### 2.1 `ToolManager`

`@dataclass ToolManager(Generic[AgentDepsT])`——管理**一个 run step** 的工具解析、参数校验、执行与重试。

关键字段：

| 字段 | 说明 |
|------|------|
| `toolset` | 本 step 提供工具的 toolset |
| `root_capability` | 钩子调用根能力 |
| `ctx` | 本 step 的运行上下文 |
| `tools: dict[str, ToolsetTool]` | 缓存工具，键为模型调用的名字（`tool_def.name`） |
| `failed_tools` / `succeeded_tools` | 本 step 成败记录，用于下一步重试账本结转 |
| `availability_refused: set[str]` | 已用掉「一次免费可用性拒绝」的工具（跨整个 run，避免与重试预算混淆） |
| `default_max_retries: int = 1` | 工具重试默认值 |
| `resolved_capability_ids: frozenset \| None` | 解析工具时假设的「能力活动集」快照 |

方法/入口：

- `parallel_execution_mode(mode)`（classmethod + `@contextmanager`）：设置运行级模式 `'parallel' | 'sequential' | 'parallel_ordered_events'`（默认 `'parallel'`）。`'sequential'` 让每个工具都成为独立屏障；`'parallel_ordered_events'` 并行执行但事件在全部完成后按序发出。
- `for_run_step(ctx)`：为下一步重建 manager，结转重试账本：成功工具的重试计数清零；失败工具计数 +1。若 `resolved_capability_ids` 变了（能力在 step 中途变为可用），会在**同一步内**重新解析工具集（`DynamicToolset` 不重入，只重跑 `get_tools`）。最后把新 manager 挂到 `ctx.tool_manager`。
- `tool_defs` / `get_tool_def(name)` / `is_sequential(call)` / `get_parallel_execution_mode()`。

### 2.2 `ValidatedToolCall`

`@dataclass`，把「校验」与「执行」解耦：

| 字段 | 说明 |
|------|------|
| `call` | 原始 `ToolCallPart` |
| `tool: ToolsetTool \| None` | 解析出的工具；`None` 表示未知工具 |
| `ctx` | 该调用的运行上下文 |
| `args_valid` | schema + 自定义校验是否通过 |
| `validated_args: dict \| None` | 通过时的已校验参数（output 工具存放 `args_validator` 的产物） |
| `validation_error: ToolRetryError \| ToolFailedError \| None` | 校验失败时的模型可见结果 |
| `deferral: CallDeferred \| ApprovalRequired \| None` | 校验期间发生的 deferral（此时 `args_valid=True`） |

内部辅助：`_ToolUnavailable(ModelRetry)`（工具尚不可用时拒绝，携带该工具以按其自身预算计费）、`_ValidationDeferral`（校验期 deferral 的内部信号）。

### 2.3 执行顺序与调度

`_tool_execution.process_tool_calls(...)` 按 `end_strategy` 分类执行 output 与 function 工具：

- **`'early'`**：先按发出顺序逐个跑 output 工具，遇首个成功即停；function 工具**仅当所有 output 都失败**才运行（让模型下一轮纠正）。output 成功后，所有 function 工具记为「未执行」的桩返回。
- **`'graceful'`（默认）**：按模型发出顺序走；每个 output 工具之前先 flush 掉它前面的 function 工具批；output 顺序执行、遇首个成功即停，后续 output 被跳过（副作用不执行）；同一段内 function 工具并行。
- **`'exhaustive'`**：所有工具并行；按发出顺序的首个有效 output 成为最终结果，其余仍执行；只有 `sequential=True` 的工具（function，或 `ToolOutput(sequential=True)`）充当屏障。

`sequential=True` 屏障：其前面的工具先完成，它独占，之后的工具待其结束才启动（`_segment_by_barriers` 切分执行段）。运行级 `parallel_execution_mode('sequential')` 让每个工具都变成独立屏障。

**retry-wins 不变量**：在 `'graceful'`/`'exhaustive'` 下，只要有 function/unknown 工具产出 `RetryPromptPart`，`final_result` 会被抑制，模型下一轮先处理重试。output 工具的重试不触发它（「首个有效 output 获胜」）；`Agent.run_stream` 传入 `final_result` 或 `'early'` 模式下不适用。

三种调度实现于 [`_EarlyProcessor`](../pydantic_ai_slim/pydantic_ai/_tool_execution.py)、`_GracefulProcessor`、`_ExhaustiveProcessor`（共同基类 `_ToolCallProcessor`）。

### 2.4 校验与执行钩子链

`ToolManager` 把每个工具调用包进能力钩子链：`before_tool_validate` → 校验（含 `on_tool_validate_error` 恢复）→ `after_tool_validate` → `before_tool_execute` → 执行（含 `on_tool_execute_error` 恢复）→ `after_tool_execute`；`wrap_tool_validate` / `wrap_tool_execute` 分别包裹整个前后半程。

关键约束（源码 docstring 明示）：

- **deferral 只能在参数已校验后发生**。在 `before_tool_validate`（或校验失败路径）抛 `CallDeferred`/`ApprovalRequired` 是 `UserError`；合法的抛点：`after_tool_validate`、工具的 `args_validator`、`before_tool_execute`。
- `on_tool_execute_error` 不会被控制流异常（`SkipToolExecution`/`CallDeferred`/`ApprovalRequired`）、重试信号（`ToolRetryError`）或失败信号（`ToolFailedError`）触发——要拦截它们请用 `wrap_tool_execute`。

---

## 3. Toolset（`toolsets/`）

### 3.1 `AbstractToolset(ABC, Generic[AgentDepsT])`

职责：列出工具、校验参数、调用工具，并携带生命周期与指令。

**id 约定**（[toolsets/abstract.py](../pydantic_ai_slim/pydantic_ai/toolsets/abstract.py)）：

- `id`（抽象属性）在同一 agent 的所有 toolset 中唯一。**durable execution（Temporal 等）要求 id**，用于标识 toolset 的 activity/step。
- 框架保留 id：`AGENT_TOOLSET_ID = '<agent>'`（agent 自己的函数工具集）、`OUTPUT_TOOLSET_ID = '<output>'`（output 工具集）。尖括号形式表示「框架替用户填的角色」，自定义 toolset 不应返回这类 id。
- `label`（property）＝类名（有 id 时附 ` <id>`）；`tool_name_conflict_hint` 给出改名/加前缀建议。

生命周期方法：

| 方法 | 何时调用 | 语义 |
|------|----------|------|
| `for_run(ctx)` | 每 run 一次，`__aenter__` 之前 | 返回每 run 实例；默认返回 `self` |
| `for_run_step(ctx)` | 每 step 开始 | 返回 per-step 实例；若返回新实例，需自行管理内部 toolset 的进出 |
| `__aenter__` / `__aexit__` | toolset 装配期 | 建立/拆除连接 |

核心方法与树操作：

- `get_instructions(ctx) -> str \| InstructionPart \| Sequence[...] \| None`：默认 `None`；简单实现可返回纯 `str`（默认视为 dynamic），高级实现可返回 `InstructionPart`（声明 `name` / `dynamic`）。
- `get_tools(ctx) -> dict[str, ToolsetTool]`（抽象）
- `call_tool(name, tool_args, ctx, tool)`（抽象）
- `get_tool_for_tool_def(tool_def, ctx)`：默认 `(await self.get_tools(ctx))[tool_def.name]`；MCP 等可覆写以免二次网络往返（durable 边界重建工具时用）。
- `apply(visitor)` / `visit_and_replace(visitor)`：遍历/替换「叶子」toolset。

便利包装器（返回对应 wrapper）：`filtered(filter_func)`、`prefixed(prefix)`、`prepared(prepare_func)`、`renamed(name_map)`、`approval_required(func=...)`、`defer_loading(tool_names=None)`、`include_return_schemas()`、`with_metadata(**metadata)`。

**指令归因机制**（`_instruction_collection.py` + `_collect_instruction_contributions`）：收集指令时保留「谁写的」。`_authors_own_instructions()` 区分「自己发声」与「转发子节点」；`_instruction_source()` 在没有 id 或 id 含 `:` 时返回 `None`；`make_contribution` 会校验并格式化 `ToolsetInstructionSource(id)`，把冒用他人键的 part 重新归属；`flatten_instruction_contributions` 拒绝两个 toolset 用同一 id 且都贡献指令（`UserError`）。

### 3.2 `ToolsetTool(Generic[AgentDepsT])`

`@dataclass(kw_only=True)`，包裹一个工具定义并提供调用所需信息：

| 字段 | 说明 |
|------|------|
| `toolset` | 提供该工具的 toolset（用于错误信息） |
| `tool_def` | `ToolDefinition` |
| `max_retries` | 失败重试上限 |
| `args_validator` | Pydantic Core `SchemaValidator` / 兼容协议 |
| `args_validator_func` | 自定义校验函数（schema 校验后、执行前），默认 `None` |

### 3.3 `WrapperToolset`

`@dataclass`，持有 `wrapped: AbstractToolset` 并把所有方法委托出去。`id` 返回 `None`，`label` 形如 `WrapperToolset(<inner>)`。`apply` / `visit_and_replace` 直达 `wrapped`。**横切扩展的首选**（见 `pydantic_ai/AGENTS.md`：应扩展 `WrapperToolset` 而非改基类或具体实现）。

### 3.4 具体 Toolset 全表

| 模块 | 主要类 | 说明 |
|------|--------|------|
| [function.py](../pydantic_ai_slim/pydantic_ai/toolsets/function.py) | `FunctionToolset`、`FunctionToolsetTool` | Python 函数工具集；装饰器 `tool` / `tool_plain` / `instructions`，方法 `add_function` / `add_tool` |
| [combined.py](../pydantic_ai_slim/pydantic_ai/toolsets/combined.py) | `CombinedToolset`、`_CombinedToolsetTool` | 合并多个 toolset；重名抛 `UserError` |
| [filtered.py](../pydantic_ai_slim/pydantic_ai/toolsets/filtered.py) | `FilteredToolset` | 按 `filter_func(ctx, tool_def)` 过滤（同步/异步） |
| [prefixed.py](../pydantic_ai_slim/pydantic_ai/toolsets/prefixed.py) | `PrefixedToolset` | 名字加 `prefix_`，`call_tool` 时去前缀 |
| [renamed.py](../pydantic_ai_slim/pydantic_ai/toolsets/renamed.py) | `RenamedToolset` | 用 `name_map: dict[新名, 旧名]` 重命名 |
| [prepared.py](../pydantic_ai_slim/pydantic_ai/toolsets/prepared.py) | `PreparedToolset` | 用 `ToolsPrepareFunc` 批量改写定义；**禁止增删/改名工具** |
| [deferred_loading.py](../pydantic_ai_slim/pydantic_ai/toolsets/deferred_loading.py) | `DeferredLoadingToolset` | `PreparedToolset` 子类，把（选定）工具标 `defer_loading=True` |
| [approval_required.py](../pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py) | `ApprovalRequiredToolset` | 未审批时抛 `ApprovalRequired` |
| [external.py](../pydantic_ai_slim/pydantic_ai/toolsets/external.py) | `ExternalToolset` | `kind='external'`，`call_tool` 抛 `NotImplementedError`，`max_retries=0` |
| [_dynamic.py](../pydantic_ai_slim/pydantic_ai/toolsets/_dynamic.py) | `DynamicToolset`、`ToolsetFunc` | 用 `toolset_func(ctx)` 动态构建；`per_run_step` 决定重估时机 |
| [_tool_search.py](../pydantic_ai_slim/pydantic_ai/toolsets/_tool_search.py) | `ToolSearchToolset`、`_SearchTool` | 延迟工具发现（见 §3.6） |
| [_capability_owned.py](../pydantic_ai_slim/pydantic_ai/toolsets/_capability_owned.py) | `CapabilityOwnedToolset` | 把工具集绑定到所属能力（打 `capability_id` / `defer_loading`） |
| [include_return_schemas.py](../pydantic_ai_slim/pydantic_ai/toolsets/include_return_schemas.py) | `IncludeReturnSchemasToolset` | `PreparedToolset` 子类，置 `include_return_schema=True` |
| [set_metadata.py](../pydantic_ai_slim/pydantic_ai/toolsets/set_metadata.py) | `SetMetadataToolset` | `PreparedToolset` 子类，合并 metadata |
| [wrapper.py](../pydantic_ai_slim/pydantic_ai/toolsets/wrapper.py) | `WrapperToolset` | 通用包装器 |

`AgentToolset = Union[AbstractToolset[AgentDepsT], ToolsetFunc[AgentDepsT]]`（[toolsets/\_\_init\_\_.py](../pydantic_ai_slim/pydantic_ai/toolsets/__init__.py)；用运行时 `Union` 而非 `|`，因该模块无 `from __future__ import annotations`）。

**`FunctionToolset` 细节**：构造参数含 `max_retries`、`timeout`、`docstring_format`、`require_parameter_descriptions`、`schema_generator`、`strict`、`sequential`、`requires_approval`、`metadata`、`defer_loading`、`include_return_schema`、`id`、`instructions`。`add_function` 的 `None` 参数逐项回落 toolset 默认值；`add_tool` 时 `metadata` 会与 toolset metadata 合并（toolset 为底）。`get_tools` 为每个工具构造 `replace(ctx, tool_name=..., retry=..., max_retries=...)` 后调用 `prepare_tool_def`；重名/改名冲突抛 `UserError`。`call_tool` 优先用 `tool_def.timeout`，其次 toolset `timeout`，用 `anyio.fail_after` + `_utils.abandon_threads_on_cancel()`，超时抛 `ModelRetry(f'Timed out after {timeout} seconds.')`。`tool_for_tool_def(...)` 供 durable 从已记录定义重建工具，避免二次运行 `prepare`。

### 3.5 具体 Toolset 语义补充

- `DynamicToolset`：`for_run` 生成 per-run 副本；`per_run_step=False` 时在 `for_run` 求值工厂（唯一机会），`per_run_step=True` 时推迟到 `for_run_step`。在 `for_run_step` 中管理内部 toolset 的进出（先 detach 旧实例再尝试进入新实例，避免 `__aexit__` 退出未进入的对象）。
- `CombinedToolset.__aenter__` 用 `AsyncExitStack` 依次进入所有子 toolset；`get_tools` 并行收集后做重名检查，并把 `toolset_id` 写到 `ToolDefinition`。`call_tool` 用**传入**工具的 `tool_def`（而非 `get_tools` 时缓存的），以尊重外层 `PreparedToolset` 的改写。
- `DeferredLoadingToolset` 的 `prepare_func` 在 `__init__` 中固定为「把指定名字（或全部）标 `defer_loading=True`」。

### 3.6 延迟加载与工具发现

**`defer_loading` 语义**（见 [\_tool_search.py](../pydantic_ai_slim/pydantic_ai/toolsets/_tool_search.py) 模块 docstring）：一个延迟工具同时有两条独立属性：

- **揭示前隐藏**：每个延迟工具都有；由作者写的 `defer_loading` 承载（整 run 不变），当前可见性单独记录在 `ModelRequestParameters.revealed_tool_names` / `tool_visibility`。
- **是否属于可搜索语料**：由 `with_native='tool_search'` 承载，只标记**未被按需能力门禁**的延迟工具。被能力门禁的工具只能通过加载能力获得，永远不可搜索。

`Model.prepare_request`（按模型）决定隐藏工具如何上线上：支持「声明但扣留 schema」的 provider（Anthropic/OpenAI Responses `defer_loading`）把隐藏工具留在 `tools` 中、揭示时原位解锁；否则隐藏工具完全离线，揭示时才作为完整声明出现。

**`ToolSearchToolset`**（`WrapperToolset` 子类）配置：

| 字段 | 默认 | 说明 |
|------|------|------|
| `search_fn` | `None` | 自定义搜索函数；`None` 用默认关键词重叠算法 |
| `max_results` | `10` | 默认算法返回上限 |
| `tool_description` | `None` | `search_tools` 描述覆盖 |
| `parameter_description` | `None` | `queries` 参数描述覆盖 |
| `enable_fallback` | `True` | `False`（命名原生策略 `'bm25'`/`'regex'`）时不发本地 `search_tools` |
| `max_retries` | `None` | 本地 `search_tools` 重试预算 |

`get_tools` 把工具分 visible / deferred；deferred 中未被能力门禁者标 `with_native='tool_search'`（存入语料）。当语料非空且 `enable_fallback` 时，注入保留名 `search_tools`（`_SEARCH_TOOLS_NAME = TOOL_SEARCH_FUNCTION_TOOL_NAME`），其 `unless_native='tool_search'`（原生支持时被适配器丢弃）。若自定义 `search_fn` 存在则不设 `unless_native`（原生「客户端执行」路径仍需本地函数工具）。关键词算法用倒排表缓存，排序键为「未发现优先 → 相关度 → 语料序」，`max_results < 0` 表示按切片语义从尾部丢弃。

`parse_discovered_tools(messages)` / `discovered_tool_names_in_order(messages)` 从历史（仅 `post_compaction_window`）解析已发现工具名，兼容旧版 `metadata['discovered_tools']` 边带。

**`CapabilityOwnedToolset`** + `_capability_owned.py`：

- `resolve_capability_id(ctx, capability)`：在 `ctx.capabilities` 中按 identity 找回注册 id。
- `is_gated_by_deferred_capability(ctx, tool_def)`：`tool_def.capability_id` 对应能力存在且 `defer_loading is True`。
- `tool_defs_from_pre_definition_load_returns(...)`：为旧历史（load 返回未携带工具定义）重建定义。

**`DeferredCapabilityLoaderToolset`**（[toolsets/_deferred_capability_loader.py](../pydantic_ai_slim/pydantic_ai/toolsets/_deferred_capability_loader.py)）注入保留名工具 `load_capability`（`LOAD_CAPABILITY_TOOL_NAME`，`tool_kind='capability-load'`），参数为 `LoadCapabilityArgs`。加载逻辑：找不到 id / 已激活 / 同响应内重复加载 → `ModelRetry`；否则收集该能力的指令（`resolve_sourced_instructions` + 所有权 toolset 的指令）并入 `LoadCapabilityReturn`，并返回该能力拥有的工具名列表（`ToolReturn(return_value=result, tools=...)`）。

---

## 4. Capability（`capabilities/`）

Capability 是「可组合横切 Agent 行为」的所在地。见 [capabilities/AGENTS.md](../pydantic_ai_slim/pydantic_ai/capabilities/AGENTS.md)：**能在 capability 中表达的（贡献指令、设置、工具、native 工具、wrapper、生命周期钩子、事件/历史处理），不应新增 `Agent` 构造参数**；且任何在工具调用时从 `RunContext` 读取的内容都必须能穿过 durable 边界。

### 4.1 `AbstractCapability(ABC, Generic[AgentDepsT])`

`@dataclass(init=False)`。

**类级标记（`ClassVar`）**：

| 标记 | 默认 | 说明 |
|------|------|------|
| `_one_per_agent: str \| None` | `None` | 每个 agent 只允许一个该「种类」的能力（durable 引擎设置它）。框架在绑定任何东西前检查，被拒绝的配置不会注册 durable operation |
| `_safe_at_runtime: ClassVar[bool]` | `False` | 是否允许在绑定 durable 能力时按 run 添加（当前仅 `Instrumentation` 为 `True`） |

**实例字段**：

| 字段 | 说明 |
|------|------|
| `id: str \| None` | 运行内唯一的能力标识；`defer_loading=True` 时必需；否则由类名推导本地 id |
| `description: str \| None` | 描述（在 `load_capability` 目录中展示） |
| `defer_loading: bool` | 模型面向的工具与指令在显式加载前隐藏；模型设置与钩子在运行装配时注册、加载后生效 |

**取值（value-contribution）方法**（在 Agent 构造时调用；`for_run` 返回替换时重新提取）：

| 方法 | 返回/语义 |
|------|-----------|
| `get_instructions()` | `AgentInstructions \| None` |
| `get_description()` | `CapabilityDescription \| None`（默认返回静态 `description`） |
| `get_model_settings()` | `AgentModelSettings \| None`（可返回接收 `RunContext` 的 callable，逐 step 合并） |
| `get_model()` | `AgentModel \| None`：静态模型选择或 `ModelSelector`；**多个能力贡献模型时最后一个非 `None` 者胜出** |
| `resolve_model_id(ctx, *, model_id)` | 解析模型 id；返回 `None` 表示交棒；**首个返回模型者胜出**（与 `get_model` 相反） |
| `get_toolset()` | `AgentToolset \| None` |
| `get_native_tools()` | `Sequence[AgentNativeTool]`（默认 `[]`） |
| `get_workspace(ctx, *, ref)` / `_prepare_workspace(ctx, workspace, *, explicit)` | workspace 供给；`ref=None` 请求新环境；先问 run 层能力再问 agent 层，首个回答者胜出 |
| `get_wrapper_toolset(toolset)` | 包裹已装配的（非 output）toolset；**唯一每次运行都调用的取值方法**；多个提供者按中间件语义（列表中靠前者最外层） |

**生命周期元数据**：`for_agent(agent)`、`for_run(ctx)`、`apply(visitor)`、`visit_and_replace(visitor)`（返回 `None` 即移除该能力）、`combine(capabilities)`（同 id 合并，默认 `merge_capability_fields`）、`get_ordering()`、`get_serialization_name()` / `from_spec()`、`prefix_tools(prefix)`、`has_resolve_model_id` / `_has_get_workspace` / `_has_wrap_*` / `has_on_event` / `listens_to(event)`。

### 4.2 生命周期钩子全表

每个家族含 `before_*` / `after_*` / `wrap_*` / `on_*_error`，外加准备钩子与延迟工具处理：

| 家族 | 钩子（签名要点） |
|------|------------------|
| Run | `before_run(ctx)`；`after_run(ctx, *, result)`；`wrap_run(ctx, *, handler: WrapRunHandler)`；`on_run_error(ctx, *, error)` |
| Node run | `before_node_run(ctx, *, node)`；`after_node_run(ctx, *, node, result)`；`wrap_node_run(ctx, *, node, handler)`；`on_node_run_error(ctx, *, node, error)` |
| Event | `on_event(ctx, *, event)`；`wrap_run_event_stream(ctx, *, stream)` |
| Model request | `before_model_request(ctx, request_context)`；`after_model_request(ctx, *, request_context, response)`；`wrap_model_request(ctx, *, request_context, handler)`；`on_model_request_error(ctx, *, request_context, error)` |
| Tool 准备 | `prepare_tools(ctx, tool_defs)`（仅 function 工具）；`prepare_output_tools(ctx, tool_defs)`（仅 output 工具） |
| Tool validate | `before/after_tool_validate(ctx, *, call, tool_def, args)`；`wrap_tool_validate(..., handler)`；`on_tool_validate_error(..., error)` |
| Tool execute | `before/after_tool_execute(ctx, *, call, tool_def, args[, result])`；`wrap_tool_execute(..., handler)`；`on_tool_execute_error(..., error)` |
| Output validate | `before/after_output_validate(ctx, *, output_context, output)`；`wrap_output_validate(..., handler)`；`on_output_validate_error(..., error)` |
| Output process | `before/after_output_process(ctx, *, output_context, output)`；`wrap_output_process(..., handler)`；`on_output_process_error(..., error)` |
| 延迟工具 | `handle_deferred_tool_calls(ctx, *, requests) -> DeferredToolResults \| None`（累积式分派：每个能力处理剩余未决请求，结果合并后传给下一个） |

关键不变量（来自各处 docstring）：

- **只有 `wrap_*` 能修改流转**：`handler(...)` 内跑 `before_` → 核心 → `on_*_error` 恢复 → `after_`；不调用 `handler` 即跳过该内层生命周期；`wrap` 可多次调用 `handler`（重试）。
- **取消是终态**：钩子可观察与清理，但不能把已取消的运行恢复为成功；`after_*` 对取消节点不触发，把取消安全的清理放在 `wrap_*`（`try`/`finally` 包 `handler()`）。
- **`after_run` 的两种例外**：`wrap_run` 未调用 handler、或 `wrap_run` 自行恢复错误时，`after_run` 不调用；`on_run_error` 恢复 run-body 失败时 `after_run` 会调用。
- **`for_run` 不得获取资源**（它在任何运行钩子之前执行，若运行失败无人释放）；资源应在 `before_run`/`wrap_run` 获取并在 `wrap_run` 释放。
- `before_model_request` 的异常不进入 `on_model_request_error`（后者只覆盖核心模型调用）；抛 `ModelRetry` 会请求另一次模型尝试并计入 output 重试预算。
- `before_tool_validate`/`on_tool_validate_error` 抛 deferral 是 `UserError`（参数尚未校验）。
- `prepare_tools` 的结果同时流入模型请求参数与 `ToolManager.tools`，因此过滤也阻止执行。

### 4.3 组合：`CombinedCapability` 与中间件顺序

[combined.py](../pydantic_ai_slim/pydantic_ai/capabilities/combined.py) 的 `CombinedCapability(AbstractCapability)`：

- 构造时 `__normalize_capabilities()` 展平嵌套 `CombinedCapability`（叶子作为兄弟参与排序），并在任一叶子声明了 ordering 时用 `sort_capabilities` 拓扑排序。
- `_instruction_sources` 保留「容器本身」的组合视图（展平会丢掉容器级 `get_instructions` 覆写）；`_rebound(...)` 是**唯一受支持的替换子能力方式**（`replace()` 会重跑 `__post_init__` 从而按已展平列表重建 `_instruction_sources`，丢失容器覆写）。
- 钩子迭代顺序：`before_*` / `prepare_*` 正向；`after_*` / `wrap_*` / `on_*_error` **反向**（构成中间件链）——但 `get_wrapper_toolset` 与 `_prepare_workspace` 明确用 `reversed(self.capabilities)`（最后者先包，位于最内层）。
- `get_toolset()` 把每个子能力的 toolset 包进 `CapabilityOwnedToolset` 后合成 `CombinedToolset`；`get_native_tools()` 对延迟能力把 native 工具包成「仅加载后产出」的可调用。
- `bind_capabilities_tier(combined, agent, *, innermost)` 支持两阶段绑定：先绑定非 `innermost` 层，待其贡献的 toolset 出现在 `agent.toolsets` 后再绑定 `innermost` 层（durable 能力）。

**排序约束**（[capabilities/_ordering.py](../pydantic_ai_slim/pydantic_ai/capabilities/_ordering.py)）：`CapabilityOrdering(position: 'outermost'|'innermost'|None, wraps, wrapped_by, requires)`；`sort_capabilities` 用 `graphlib.TopologicalSorter`，`wraps=[X]` 表示「我在 X 外层」，类型引用按 `issubclass` 匹配、实例引用按 `is` 匹配；冲突（缺依赖 / 环 / 嵌套叶子位置矛盾）抛 `UserError`。

### 4.4 `WrapperCapability`

[wrapper.py](../pydantic_ai_slim/pydantic_ai/capabilities/wrapper.py)：包装单个 `wrapped` 并委托**全部**方法（与 `WrapperToolset` 类比）。

- `__adopt_wrapped_identity()`：自身无 `id` 时采纳被包装者的 `id` 与 `defer_loading`（因此能在延迟能力之上保持延迟语义）；`for_agent`/`for_run` 在重建副本上重跑它。
- `_emits_app_events` 透传 `wrapped._emits_app_events`（包装 `Hooks`/`ProcessEventStream` 不撤销用户的 `CustomEvent` 权限）。
- `get_serialization_name()` 返回 `None`（不参与 spec 序列化）。

### 4.5 `Capability` 便捷类

[capability.py](../pydantic_ai_slim/pydantic_ai/capabilities/capability.py) 的 `Capability(AbstractCapability)`：无需子类化即可打包 `instructions` / `toolsets` / `tools` / `id` / `description` / `defer_loading`。

- 内部 `_function_toolset = FunctionToolset(tools, id=id)`（把能力 id 盖到函数工具集，便于 durable 按 id 包装叶子 toolset）。
- 装饰器 `tool_plain()` / `tool()`（委托 `_function_toolset`）、`instructions(name=None)`（注册指令函数；`name` 键为 `'capability:<id>:<name>'`，需有 `id`）。
- `get_toolset()` 把 `toolsets=`（普通 toolset 原样、callable 包成带 `{id}_{index}` 的 `DynamicToolset`）与函数工具集合成；即使暂为空也返回函数工具集引用，以便构造后 `@cap.tool` 注册的工具可见。
- `get_serialization_name()` 返回 `None`（含函数工具/可调用描述，无法 spec 往返）。

### 4.6 `Hooks` 与 `@on_event`

**`Hooks`**（[hooks.py](../pydantic_ai_slim/pydantic_ai/capabilities/hooks.py)）：用装饰器或构造 kwargs 注册钩子函数，避免为几个钩子写子类。

- `hooks.on.<hook>` 装饰器命名空间（`_HookRegistration`）：支持裸装饰器与参数化形式；tool 类钩子额外支持 `tools=[...]` 过滤；`before_*`/`wrap_*`/`on_*_error` 等支持 `timeout`（超时抛 `HookTimeoutError(AgentRunError, TimeoutError)`；`wrap_run_event_stream` 不支持 timeout）。
- 构造 kwargs 名映射到内部 registry 键：如 `run=...` → `wrap_run`、`model_request=...` → `wrap_model_request`、`tool_execute=...` → `wrap_tool_execute`、`deferred_tool_calls=...` → `handle_deferred_tool_calls`；另收 `ordering` / `id` / `description` / `defer_loading`。
- 多条 `wrap_*` 钩子按注册顺序组建链（靠前者更外层）；同步函数会被放入线程执行（`_call_func` → `run_in_executor` + `await_maybe`），避免阻塞事件循环。
- `_emits_app_events = True`。
- 构造参数协议（`Protocol`）为每种钩子给出精确签名，且同时接受同步/异步。

**`@on_event(*event_types)`**（[capabilities/_on_event.py](../pydantic_ai_slim/pydantic_ai/capabilities/_on_event.py)）：把 async 方法标记为事件监听器；`_OnEventMethod` 是描述符（方法名不能叫 `on_event`）。`collect_on_event_methods(cls)`（`lru_cache`）按 MRO 定义顺序收集；`marked_listens_to(cls, event)` 判断「是否会因该事件被唤醒」。`on_event` 默认实现按定义顺序分派给被标记的方法，且 `listens_to` 让分派在进入能力前先过滤，避免每个事件都唤醒每个能力。

### 4.7 具体 Capability（按主题）

**模型设置类**

| 模块 | 类 | 关键构造参数 |
|------|-----|--------------|
| [thinking.py](../pydantic_ai_slim/pydantic_ai/capabilities/thinking.py) | `Thinking` | `effort: ThinkingLevel = True`（`'minimal'/'low'/'medium'/'high'/'xhigh'`/`True`/`False`）；`id='thinking'` |
| [caching.py](../pydantic_ai_slim/pydantic_ai/capabilities/caching.py) | `Caching` | `retention: bool \| CacheRetention = True`（`'5m'/'30m'/'1h'`）、`messages: bool = True`；`id='caching'` |
| [select_model.py](../pydantic_ai_slim/pydantic_ai/capabilities/select_model.py) | `SelectModel` | `selector: ModelSelector`（`get_model()` 返回它） |
| [resolve_model_id.py](../pydantic_ai_slim/pydantic_ai/capabilities/resolve_model_id.py) | `ResolveModelId`、`ModelIdResolver` | `resolver(ctx, model_id) -> Model \| None` |

**native / 本地回退类**

| 模块 | 类 | 说明 |
|------|-----|------|
| [native_tool.py](../pydantic_ai_slim/pydantic_ai/capabilities/native_tool.py) | `NativeTool` | 注册单个 native 工具；`from_spec` 支持扁平与显式两种 YAML 形式 |
| [native_or_local.py](../pydantic_ai_slim/pydantic_ai/capabilities/native_or_local.py) | `NativeOrLocalTool` | native + 本地回退的基类（下述） |
| [web_search.py](../pydantic_ai_slim/pydantic_ai/capabilities/web_search.py) | `WebSearch` | native `WebSearchTool` + `local='duckduckgo'`；`id='web_search'` |
| [web_fetch.py](../pydantic_ai_slim/pydantic_ai/capabilities/web_fetch.py) | `WebFetch` | native `WebFetchTool` + markdownify 回退；`id='web_fetch'` |
| [x_search.py](../pydantic_ai_slim/pydantic_ai/capabilities/x_search.py) | `XSearch` | native `XSearchTool`；非 xAI 模型需 `fallback_subagent_model`；`id='x_search'` |
| [image_generation.py](../pydantic_ai_slim/pydantic_ai/capabilities/image_generation.py) | `ImageGeneration` | native `ImageGenerationTool` + 三种互斥回退；`id='image_generation'` |
| [mcp.py](../pydantic_ai_slim/pydantic_ai/capabilities/mcp.py) | `MCP` | MCP 能力入口（`NativeOrLocalTool` 子类），见 §5 |

`NativeOrLocalTool` 是本组的核心：

- 字段：`native: AgentNativeTool | bool = True`（`True`=用默认、`False`=禁用、实例=指定、callable=每次运行动态生成，返回 `None` 则省略）、`local: str | Tool | Callable | AbstractToolset | bool | None = None`（`None`=自动、`True`/字符串=命名策略、`False`=禁用、可调用=包成 `Tool`）。
- `__post_init__` 记录 `_declared_native` / `_declared_local`（**声明**，非解析后值），解析 `native=True` → `_default_native()`，解析 `local`，并做多重一致性校验（`native=False` 且要求 native、`native=False` 且无本地回退、两者都 `False` 等 → `UserError`）。
- 子类钩子：`_default_native()`、`_native_unique_id()`、`_default_local()`、`_has_local_fallback()`、`_resolve_local_strategy(name)`、`_requires_native()`（如 `WebSearch` 的 `allowed_domains/max_uses/external_web_access=False` 需要 native）。
- `get_native_tools()` 在 `native is not False` 时返回 `[native]`；`get_toolset()` 在需要时用 `PreparedToolset` 给本地工具打 `unless_native=<native unique_id>`。
- `combine()`：先按字段合并，再用**合并后的声明**重建 native 工具（因为真正到达 provider 的是 native 工具而非能力本身），并重跑 `__post_init__` 校验。

`ImageGeneration` 额外：`fallback_subagent_model` / `fallback_image_model` / `local=ImageGenerator` 三者互斥；`dimensions` 是「一个值而非集合」（`combine` 取最后声明值）；直接生成器无法服务 `action='edit'`（构造/执行时 `UserError`）；native-only 与 direct-only 设置会在被丢弃时发 `UserWarning`（per-request 路由在 `models.resolve_request_tools`）。

**内容/事件/输出处理类**

| 模块 | 类 | 说明 |
|------|-----|------|
| [content_filter.py](../pydantic_ai_slim/pydantic_ai/capabilities/content_filter.py) | `RaiseContentFilterError` | `after_model_request` 里 `finish_reason=='content_filter'` 时抛 `ContentFilterError`（`body` 携带序列化的 `ModelResponse`） |
| [deferred_tool_handler.py](../pydantic_ai_slim/pydantic_ai/capabilities/deferred_tool_handler.py) | `HandleDeferredToolCalls` | 用 `handler(ctx, requests)` 内联解析延迟工具调用 |
| [process_history.py](../pydantic_ai_slim/pydantic_ai/capabilities/process_history.py) | `ProcessHistory` | `before_model_request` 中运行 `HistoryProcessor`（同步/异步、带/不带 ctx） |
| [process_event_stream.py](../pydantic_ai_slim/pydantic_ai/capabilities/process_event_stream.py) | `ProcessEventStream` | `wrap_run_event_stream`；handler 可为观测器（`async def` 返回 `None`）或处理器（异步生成器，替换下游流）。`_emits_app_events=True` |
| [instrumentation.py](../pydantic_ai_slim/pydantic_ai/capabilities/instrumentation.py) | `Instrumentation` | OTel/Logfire span；`_safe_at_runtime=True`；ordering=`outermost`（见下） |
| [reinject_system_prompt.py](../pydantic_ai_slim/pydantic_ai/capabilities/reinject_system_prompt.py) | `ReinjectSystemPrompt` | `replace_existing: bool = False`；把 agent 的 system prompt 重新注入首个 `ModelRequest`；`id='reinject_system_prompt'` |
| [thread_executor.py](../pydantic_ai_slim/pydantic_ai/capabilities/thread_executor.py) | `UseThreadExecutor` | `executor: Executor`；`wrap_run` 设置线程执行器上下文变量；`id='use_thread_executor'`（旧名 `ThreadExecutor` 已弃用） |

**工具/工具集修改类**

| 模块 | 类 | 说明 |
|------|-----|------|
| [toolset.py](../pydantic_ai_slim/pydantic_ai/capabilities/toolset.py) | `Toolset` | 包装普通 `AgentToolset`（`get_toolset()` 返回它） |
| [prefix_tools.py](../pydantic_ai_slim/pydantic_ai/capabilities/prefix_tools.py) | `PrefixTools` | `WrapperCapability` 子类；`get_toolset()` 用 `PrefixedToolset` 包裹（callable toolset 先包 `DynamicToolset`）；`from_spec(prefix, capability)` |
| [prepare_tools.py](../pydantic_ai_slim/pydantic_ai/capabilities/prepare_tools.py) | `PrepareTools` / `PrepareOutputTools` | 分别包装 `ToolsPrepareFunc` 到 `prepare_tools` / `prepare_output_tools`；结果经 `_utils.check_tools_prepare_func_result` |
| [set_tool_metadata.py](../pydantic_ai_slim/pydantic_ai/capabilities/set_tool_metadata.py) | `SetToolMetadata` | `tools: ToolSelector='all'` + `**metadata`；`get_wrapper_toolset` 用 `PreparedToolset` 按选择器合并 metadata |
| [include_return_schemas.py](../pydantic_ai_slim/pydantic_ai/capabilities/include_return_schemas.py) | `IncludeToolReturnSchemas` | `tools: ToolSelector='all'`；仅在 `include_return_schema is None` 时置 `True` |
| [_tool_search.py](../pydantic_ai_slim/pydantic_ai/capabilities/_tool_search.py) | `ToolSearch` | provider 自适应的延迟工具发现（见 §4.8） |
| [_dynamic.py](../pydantic_ai_slim/pydantic_ai/capabilities/_dynamic.py) | `DynamicCapability`、`ResolvedDynamicCapability`、`CapabilityFunc`、`wrap_capability_funcs` | 动态能力（见 §4.9） |
| [local_workspace.py](../pydantic_ai_slim/pydantic_ai/capabilities/local_workspace.py) | `LocalWorkspace` | 宿主文件系统/子进程 workspace（见 §6） |

`Instrumentation` 的排序为 `outermost`（`get_ordering()` 返回 `CapabilityOrdering(position='outermost')`），`for_run` 返回浅拷贝隔离每-run 状态。`PrefixTools` 的 `get_serialization_name()` 返回 `'PrefixTools'`，其余多数「持有 callable」的能力返回 `None`。

### 4.8 `ToolSearch` 能力

`ToolSearch(AbstractCapability)` 是工具发现的能力层入口（`id='tool_search'`，ordering=`outermost`）：

| 字段 | 默认 | 语义 |
|------|------|------|
| `strategy` | `None` | `None`=按 provider 自动；`'keywords'`=本地关键词；`'bm25'`/`'regex'`=强制 Anthropic 原生（不支持者报错）；callable=自定义搜索 |
| `max_results` | `10` | 本地搜索上限 |
| `tool_description` / `parameter_description` | `None` | 我方执行搜索时的描述覆盖 |

`get_native_tools()` 依策略注册 `ToolSearchTool`：`'keywords'`/callable → `strategy='custom', optional=True`；`None` → `optional=True`；命名原生策略 → `strategy=named, optional=False`。`get_wrapper_toolset()` 始终用 `ToolSearchToolset` 包裹，`enable_fallback = strategy not in ('bm25', 'regex')`。`before_model_request` 调 `record_loaded_capability_tools`（把已加载能力揭示的工具写入历史）。`function_tool_name = 'search_tools'`（保留名）。

### 4.9 动态能力与延迟加载目录

- **`DynamicCapability`**（[capabilities/_dynamic.py](../pydantic_ai_slim/pydantic_ai/capabilities/_dynamic.py)）：每 run 从工厂函数解析一次（`for_run`）。`defer_loading` 在 wrapper 上被拒绝（工厂可能返回 `None` 或自带加载状态的能力）；在 `__post_init__` 中即构建稳定的 `DynamicToolset(toolset_func=self._resolve_toolset, per_run_step=False, id=self.id)`，使 durable 引擎在构造期就登记到同一个 toolset 身份。`for_run` 返回 `ResolvedDynamicCapability`（`WrapperCapability` 子类），它用稳定的 dynamic toolset 替换被解析能力的 toolset 贡献。工厂在 workflow/flow 代码中运行，必须确定；I/O 留给返回的 toolset。
- `CapabilityFunc` 直接传入 `Agent(capabilities=[...])`/`agent.run(capabilities=[...])` 会被 `wrap_capability_funcs` 自动包成 `DynamicCapability`。
- **延迟加载目录**（[capabilities/_deferred_capability_loader.py](../pydantic_ai_slim/pydantic_ai/capabilities/_deferred_capability_loader.py)）：内部 `DeferredCapabilityLoader` 能力贡献一条**动态指令**（`_render_deferred_capability_catalog`），逐 turn 列出**全部**延迟能力（含已加载者，刻意不过滤以保持 prompt 前缀字节稳定），并用 `has_search_surface` 选择是否带上「先加载能力而非搜索其工具」的提示。它 `wrapped_by=[Instrumentation]`，并把 `DeferredCapabilityLoaderToolset` 装到工具集上。

### 4.10 同 id 合并（`combine` / `_merge`）

`AbstractCapability.combine` 默认走 [capabilities/_merge.py](../pydantic_ai_slim/pydantic_ai/capabilities/_merge.py) 的 `merge_capability_fields`：

- 合并规则：只一方声明→保留该值；双方都是 Mapping/Set/Sequence→并集（共享键后写者胜；序列保持首次出现位置）；其它→后值。`None` 读作「未声明」（使限制能与未提及它的配置共存）。
- 从**最后一个能力**起合并（不可调和字段与 `replace_no_init` 携带的子类状态取后者）。
- 声明了 `compare=False` 的字段按每-run 簿记处理，不合并。
- 枚举不到的属性（`set(vars(cap)) - fields - rebuildable - _NOT_CAPABILITY_STATE`）会**拒绝合并**并报 `UserError`（提示改成 dataclass 字段 / `cached_property` / 覆写 `combine`）。`cached_property` 会被丢弃以便按合并后字段重算；`__orig_class__` 被豁免。

`_combine_duplicate_capabilities`（[abstract.py](../pydantic_ai_slim/pydantic_ai/capabilities/abstract.py)）区分**层内**与**跨层**：层内同 id 是「同一配置说了两遍」，交给 `combine`；跨层是同 id 的**后者整体覆盖前者**（run 级能力替换 agent 级同名者，不合并）。`_declares_default_id`（读类属性是否有默认 `id`）与 `_reject_class_crossing_id`（不同类共用 id 一律拒绝）保证可判定。

`CAPABILITY_TYPES`（[capabilities/\_\_init\_\_.py](../pydantic_ai_slim/pydantic_ai/capabilities/__init__.py)）把有序列化名的能力类映射到名字（`NativeTool`、`Caching`、`RaiseContentFilterError`、`ImageGeneration`、`IncludeToolReturnSchemas`、`Instrumentation`、`LocalWorkspace`、`MCP`、`PrefixTools`、`PrepareTools`、`ProcessHistory`、`ReinjectSystemPrompt`、`SetToolMetadata`、`Thinking`、`ToolSearch`、`Toolset`、`WebFetch`、`WebSearch`、`XSearch`）。

---

## 5. MCP 集成

`mcp.py`（可选依赖 `mcp`）：

- **类型**：`MCPError`（含 `from_mcp_sdk`、`str`）、`ResourceAnnotations`、`Icon`、`BaseResource`/`Resource`/`ResourceTemplate`/`ResourceLink`、`PromptArgument`/`Prompt`/`PromptMessage`/`PromptResult`、`EmbeddedResource`、`ServerCapabilities`、`ContentBlock`、`CallToolFunc`（协议）、`ProcessToolCallback`、`ToolResult`。
- **`MCPToolset(client, *, ...)`**（推荐的 MCP 入口，基于 FastMCP `Client`）：

  Pydantic AI 层配置：`id`、`max_retries`、`tool_error_behavior: 'retry'|'error'|'failed'`（`'retry'` 抛 `ModelRetry`；`'error'` 传播底层 `ToolError`；`'failed'` 抛 `ToolFailed`）、`process_tool_call`、`prefer_tasks`、`cache_tools`、`cache_resources`、`cache_prompts`、`include_instructions`、`include_return_schema`、`sampling_model` / `sampling_handler`（互斥）。

  MCP 协议 kwargs（未传预建 client 时转发给默认 `fastmcp.Client`）：`elicitation_handler`、`log_handler`、`log_level`、`progress_handler`、`message_handler`、`client_info`、`init_timeout`（默认 5s）、`read_timeout`（默认 300s）、`roots`。

  HTTP 专属：`auth`（`httpx2.Auth` / `'oauth'` / bearer 字符串）、`verify`、`headers`、`http_client`（与 `headers` 互斥；预建 client 与上述 kwargs 互斥会抛 `ValueError`）。

  方法：`list_tools`、`get_tools`、`get_tool_for_tool_def` / `tool_for_tool_def`、`call_tool`、`list_prompts`/`get_prompt`、`list_resources`/`list_resource_templates`/`read_resource`、`set_sampling_model`；属性 `server_info`/`capabilities`/`instructions`/`is_running`/`label`/`id`。`__aenter__` 用 per-instance `anyio.Lock`（`_enter_lock`，懒建以适配 Temporal 沙箱）保证并发首次进入只连接一次。`load_mcp_toolsets(config_path)` 从多服务器 JSON 配置加载。
- `_mcp.py`：把 MCP sampling 消息映射为 Pydantic AI 消息（`map_from_mcp_params` 等）。
- **`MCP` capability**（[capabilities/mcp.py](../pydantic_ai_slim/pydantic_ai/capabilities/mcp.py)）：`NativeOrLocalTool` 子类，是 capability 层入口，包装 `MCPToolset` 并支持 native MCP 广告。默认 `native=False`（纯本地）；`native=True` 需要 `url=`。id 推导优先级：显式 `id` → native `MCPServerTool.id` → 由 `url` 的 host+末段 slug 推导（延迟能力不推导，须显式 id）。`allowed_tools` 通过 `toolset.filtered(...)` 施加。`from_spec(url, ...)` 限定可序列化子集。

---

## 6. Workspace（`workspaces/`）

[workspaces/protocol.py](../pydantic_ai_slim/pydantic_ai/workspaces/protocol.py) 的模块 docstring 定义后端契约：命令与文件操作针对**同一文件系统**；**报告真实退出码**（非零是结果而非异常）；**构建期无 I/O**（首次操作时才创建/附着）；ref 一旦 create 返回即记录且不再变更；**并发首次操作至多创建一个环境**（锁由后端持有，`Workspace` 不持锁）；**运行结束不销毁环境**；预期错误抛出特定类型，其它（SDK 瞬时错误）向上传播以便 durable 引擎重试。协议**冻结**：新增成员会静默破坏既有后端，改走新 `Supports*` 协议或具体类型。

- 异常：`WorkspaceError`、`WorkspaceOutputLimitError`（含 `limit`/`stdout`/`stderr`）、`WorkspaceUnavailableError`、`WorkspaceTimeoutError(WorkspaceError, TimeoutError)`、`WorkspaceReadOnlyError(WorkspaceError, PermissionError)`。
- 类型/协议：`CommandResult(exit_code, stdout, stderr)`、`FileEntry(name, path, is_dir, size)`、`SupportsCommands`（`run`）、`SupportsFilesystem`（`read_bytes`/`write_bytes`/`stat`/`list_dir`/`make_dir`/`remove`/`exists`）、`SupportsRealpath`（`realpath`）、`WorkspaceBackend`（`ref`、`working_dir`）、`WorkspaceCommand = str | Sequence[str]`。
- [workspace.py](../pydantic_ai_slim/pydantic_ai/workspaces/workspace.py)：`Workspace`（`ctx.workspace` 的 API：`run`/`working_dir`/`resolve`/`read_bytes`/`write_bytes`/`stat`/`list_dir`/`make_dir`/`remove`/`exists`/`realpath`/`read_text`/`write_text`，属性 `backend`/`read_only`/`attached`/`ref`/`durable_policy()`）、`WrapperWorkspace`。当后端只实现 `SupportsCommands` 时用 `_ShellFilesystem` 以 shell（`base64`/`dd`/`find`…）派生文件操作。
- [local.py](../pydantic_ai_slim/pydantic_ai/workspaces/local.py)：`LocalWorkspaceBackend`（宿主子进程 + 宿主文件系统，**不做隔离**）。
- [readonly.py](../pydantic_ai_slim/pydantic_ai/workspaces/readonly.py)：`ReadOnlyWorkspace`（读允许，命令与写操作抛 `WorkspaceReadOnlyError`，`read_only=True`）。
- [unavailable.py](../pydantic_ai_slim/pydantic_ai/workspaces/unavailable.py)：`UnavailableWorkspace(reason)`（所有操作抛 `WorkspaceUnavailableError`，`ref` 恒 `None`）、`NO_WORKSPACE`（无 workspace 的 run 所用后端）。
- [conformance.py](../pydantic_ai_slim/pydantic_ai/workspaces/conformance.py)：`WorkspaceBackendSuite`，后端应继承以证明契约。
- `workspace_layers(workspace)`：返回策略包装层的类型（最外层在前）。
- Capability 层：[capabilities/local_workspace.py](../pydantic_ai_slim/pydantic_ai/capabilities/local_workspace.py) 的 `LocalWorkspace(working_dir, *, read_only=False, env=None, id='local_workspace')`——`__post_init__` 把 `working_dir` 固定为 `LocalWorkspaceBackend(...).ref.id`（展开 `~`、把 `'.'` 定为当前目录）；`combine` 取最后者整体替换（避免早先的 `env`/`read_only` 泄露）；`get_workspace` 对别的目录的 ref 返回 `None`。

---

## 7. 内置工具（`common_tools/`）

| 模块 | 主要类 / 函数 | 工具名 |
|------|----------------|--------|
| [duckduckgo.py](../pydantic_ai_slim/pydantic_ai/common_tools/duckduckgo.py) | `DuckDuckGoSearchTool`、`duckduckgo_search_tool(client=None, max_results=None)` | `duckduckgo_search` |
| [web_fetch.py](../pydantic_ai_slim/pydantic_ai/common_tools/web_fetch.py) | `WebFetchLocalTool`、`web_fetch_tool(...)` | `web_fetch` |
| [image_generation.py](../pydantic_ai_slim/pydantic_ai/common_tools/image_generation.py) | `ImageGenerationSubagentTool`、`image_generation_tool(...)`（subagent 回退） | `generate_image` |
| [x_search.py](../pydantic_ai_slim/pydantic_ai/common_tools/x_search.py) | `XSearchSubagentTool`、`x_search_tool(...)` | `x_search` |
| [tavily.py](../pydantic_ai_slim/pydantic_ai/common_tools/tavily.py) | `TavilySearchTool`、`tavily_search_tool()` | `tavily_search` |
| [exa.py](../pydantic_ai_slim/pydantic_ai/common_tools/exa.py) | `ExaSearchTool`/`ExaFindSimilarTool`/`ExaGetContentsTool`/`ExaAnswerTool`、`ExaToolset`、各 `*_tool()` | `exa_search`/`exa_find_similar`/`exa_get_contents`/`exa_answer`（**已弃用**，v3 移除，改用 `pydantic_ai_harness.exa.ExaSearch`） |

- `web_fetch_tool(*, max_content_length=50_000, allow_local_urls=False, timeout=30, max_download_bytes=50MiB, allowed_domains=None, blocked_domains=None, headers=None)`：经 `_ssrf.safe_download` 做 SSRF 防护；默认发 `Accept: text/markdown`，否则 HTML→Markdown（`_MarkdownConverter` 对上游 `markdownify` 的若干超线性步骤做线性替换并设工作量预算，超预算抛 `ModelRetry('the document is too complex')`）；二进制内容返回 `BinaryContent`。域名过滤违反时 `ModelRetry`。
- `image_generation_tool` / `x_search_tool` 是各自能力在 native 不可用（或模型不支持）时经 `fallback_subagent_model` 运行子 agent 的本地回退；子 agent 携带对应 native 工具。
- 这些是 `WebSearch` / `WebFetch` / `ImageGeneration` / `XSearch` 能力在 native 工具不可用时解析出的本地回退。

---

## 8. 工具身份与设计的核心约束（来自 AGENTS.md）

- **按「是什么」而非「叫什么」识别工具**：工具名可被用户重命名或加前缀。用 `isinstance` 判断类型化 part（`ToolSearchCallPart`、`LoadCapabilityReturnPart` 等）或 `ToolDefinition.tool_kind`。唯一例外是 native 工具（其 `tool_name` 就是 native 工具的 `kind`）与读取旧版本历史时的名字判断（需在遗留历史代码中说明原因）。
- **本地工具与 provider 原生工具概念分离**：若某功能两者皆可，需显式化回退/选择行为，并测试其产生的消息历史（`unless_native`/`with_native` 是这条规则的机制）。
- **中间件顺序**：「列表中靠前者更外层」。capability 与 toolset 都遵循此规则；`after_*` / `wrap_*` 反向迭代。可用 `CapabilityOrdering`（`position` / `wraps` / `wrapped_by` / `requires`）强制顺序；`Instrumentation`、`ToolSearch`、`DeferredCapabilityLoader`、`WorkspaceEnsurer`、`_TerminalEventPublisher` 等以 `position` 定位。
- **durable 名字是兼容数据**：`@durable_operation(name=...)` 与 toolset `id` 一旦发布基本不可更改（详见 [06](06-durable-execution.md)）。
- **不要为单个调用方改共享协议/helper/toolset**：能用 wrapper 组合的行为就组合（`WrapperToolset` / `WrapperCapability`）。
- **spec 可序列化**：持有 callable 且无法 YAML/JSON 往返的实现（`Capability`、`Hooks`、`WrapperCapability`、`ProcessHistory`、`ProcessEventStream`、`PrepareTools`/`PrepareOutputTools`、`Toolset`、`UseThreadExecutor`、`SelectModel`、`ResolveModelId`、`HandleDeferredToolCalls`、`NativeOrLocalTool` 基类）`get_serialization_name()` 返回 `None`，不进 spec；可 spec 化的（`WebSearch`、`WebFetch`、`XSearch`、`ImageGeneration`、`MCP`、`Instrumentation`、`NativeTool`、`Thinking`、`Caching`、`SetToolMetadata`、`IncludeToolReturnSchemas`、`LocalWorkspace`、`PrefixTools`、`ToolSearch`…）提供 `from_spec`。
