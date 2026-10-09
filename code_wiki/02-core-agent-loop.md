# 02 · 核心 Agent 循环

本篇覆盖 `pydantic_ai_slim/pydantic_ai/` 中最核心的执行路径，精确到类/函数签名与字段：

- [`Agent`](#1-agent-类) 类与运行方法
- [`AbstractAgent` / `WrapperAgent` / `AgentSpec`](#2-abstractagent--wrapperagent--agentspec)
- [`_agent_graph.py`](#3-执行循环-agent_graphpy) 的图与节点、`GraphAgentState` / `GraphAgentDeps`
- [`RunContext`](#4-runcontext)
- [`ToolManager` 与 `_tool_execution`](#5-toolmanager-与-tool_execution)
- [结果对象](#6-结果对象)
- [异常体系](#7-异常体系)
- [`end_strategy` 语义](#8-end-strategy-语义)、[重试与用量限制](#9-重试与用量限制)、[运行上下文构建不变量](#10-运行上下文构建的不变量)

> 行号区间使用 `#Lx-Ly` 形式指向源码，路径相对 `code_wiki/` 目录。

---

## 1. `Agent` 类

`Agent` 定义于 [agent/__init__.py#L475-L882](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L475-L882)，继承自 `AbstractAgent`（[agent/abstract.py](../pydantic_ai_slim/pydantic_ai/agent/abstract.py)）。泛型参数为 `Agent[AgentDepsT, OutputDataT]`，两者默认未指定时为 `Agent[object, str]`。

> 注意：这里 `Agent` 类住在 `agent/` **包**的 `__init__.py`，而非某个 `agent.py` 模块。

### 1.1 构造参数（`Agent.__init__`，[L616-L882](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L616-L882)）

`__init__` 有一对刻意相同的 `@overload`（[L568-L614](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L568-L614)），仅为了让旧版 Pyright 把类联合 `output_type`（`Foo | Bar`）解析为 `type[Foo | Bar]`。

```python
def __init__(
    self,
    model: models.Model | models.KnownModelName | str | None = None,
    *,
    output_type: OutputSpec[OutputDataT] = str,
    instructions: AgentInstructions[AgentDepsT] = None,
    system_prompt: str | Sequence[str] = (),
    deps_type: type[AgentDepsT] | TypeForm[AgentDepsT] = object,
    name: str | None = None,
    description: TemplateStr[AgentDepsT] | str | None = None,
    model_settings: AgentModelSettings[AgentDepsT] | None = None,
    retries: int | AgentRetries | None = None,
    validation_context: Any | Callable[[RunContext[AgentDepsT]], Any] = None,
    tools: Sequence[Tool[AgentDepsT] | ToolFuncEither[AgentDepsT, ...]] = (),
    toolsets: Sequence[AgentToolset[AgentDepsT]] | None = None,
    defer_model_check: bool = False,
    end_strategy: EndStrategy = 'graceful',
    metadata: AgentMetadata[AgentDepsT] | None = None,
    tool_timeout: float | None = None,
    max_concurrency: _concurrency.AnyConcurrencyLimit = None,
    capabilities: Sequence[AgentCapability[AgentDepsT]] | None = None,
) -> None: ...
```

逐项语义（取自 [docstring L638-L705](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L638-L705)）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `model` | `None` | 默认模型；可用 `'provider:model'` 字符串（运行时才解析），若省略则必须在每次运行时提供 |
| `output_type` | `str` | 输出类型；见 [04](04-messages-and-output.md) 与 `_output.OutputSchema.build` |
| `instructions` | `None` | 指令（字符串 / `TemplateStr` / 函数 / 序列）；也可用 `@agent.instructions` 追加 |
| `system_prompt` | `()` | 静态 system prompt（单个字符串或序列） |
| `deps_type` | `object` | **仅**用于静态类型参数化（依赖注入的类型标记） |
| `name` | `None` | 名称，用于日志/可观测性；`None` 时在首次运行时从调用栈推断 |
| `description` | `None` | 人类可读描述，写入运行 span 的 `gen_ai.agent.description` |
| `model_settings` | `None` | 静态 `ModelSettings` 或 `Callable[[RunContext], ModelSettings]`（每步动态求值） |
| `retries` | `None` | `int` 或 `AgentRetries`；分类预算（`tools` / `output`），默认各 1 |
| `validation_context` | `None` | Pydantic 校验上下文，或 `Callable[[RunContext], ...]` |
| `tools` | `()` | 函数工具（`Tool(fn)` 或裸函数） |
| `toolsets` | `None` | 工具集（含 MCP server、返回 toolset 的工厂函数） |
| `defer_model_check` | `False` | `True` 时把具名模型的解析推迟到首次运行（便于测试时 `override`） |
| `end_strategy` | `'graceful'` | 与终结结果并发的函数工具调用处理策略，见 [§8](#8-end-strategy-语义) |
| `metadata` | `None` | dict 或 `Callable[[RunContext], dict]`；运行开始解析、成功结束后重算 |
| `tool_timeout` | `None` | 默认单工具执行超时（秒）；`<= 0` 时抛 `UserError` |
| `max_concurrency` | `None` | `int \| ConcurrencyLimit \| ConcurrencyLimiter \| None` |
| `capabilities` | `None` | Capability 列表；函数形式会经 `wrap_capability_funcs` 包装为 `DynamicCapability` |

`AgentRetries`（[agent/abstract.py#L112-L132](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L112-L132)）是 `TypedDict(total=False)`，仅含两个键：

- `tools: int`：工具调用（函数/输出/MCP）的默认重试次数，除非 tool/toolset 另有更具体限制；
- `output: int`：输出校验重试；文本路径上是**全局**预算，工具路径上是每个输出工具的默认 `max_retries`（可被 `ToolOutput(max_retries=...)` 覆盖）。

裸 `int` 是同时设置两者的简写；字典则只覆盖指定键（如 `retries={'tools': 3}`）。

### 1.2 构造期做了什么（[L706-L882](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L706-L882)）

构造不只是赋值，按顺序完成：

1. `capabilities = wrap_capability_funcs(capabilities)` → `_inject_auto_capabilities(...)`；
2. `self._event_hooks = Hooks()`（`@agent.on_event` 的监听器独立存放，使其能像 `@agent.tool` 一样在 `override(toolsets=...)` 后存活）；
3. `self._root_capability = CombinedCapability(capabilities)`；`_validate_capability_ids(...)`；`_combine_duplicate_capabilities(...)` 合并同 id 的两个能力；`_validate_instruction_source_ids(...)`；
4. 记录 `self._model = model`（**不**在此急切解析字符串模型——能力可能自行解释 model id）；
5. `self._output_schema = _output.OutputSchema[OutputDataT].build(output_type)`；`self._output_validators = []`；
6. 归一 `retries`（`_normalize_agent_retry_overrides` / `_normalize_agent_retries`）→ `_max_tool_retries` / `_max_output_retries`；校验 `tool_timeout > 0`；
7. 构建 `self._function_toolset = _AgentFunctionToolset(tools, max_retries=None, timeout=self._tool_timeout, output_schema=...)`——刻意让 `max_retries=None`，使 agent 级默认在每步经 `ToolManager.default_max_retries -> RunContext.max_retries` 解析；
8. 拆分 `toolsets`：非 `AbstractToolset` 的工厂函数进 `_dynamic_toolsets`（`DynamicToolset`），其余进 `_user_toolsets`；
9. `self._concurrency_limiter = _concurrency.normalize_to_limiter(max_concurrency)`；
10. 初始化一组 `ContextVar` 覆盖槽（`_override_name` / `_override_deps` / `_override_model` / `_override_toolsets` / `_override_tools` / `_override_native_tools` / `_override_instructions` / `_override_metadata` / `_override_model_settings` / `_override_output_retries` / `_override_tool_retries` / `_override_workspace` / `_override_root_capability`）供 `override(...)` 使用；
11. **两阶段能力绑定**：`bind_capabilities_tier(self._root_capability, self, innermost=False)` → 抽取 `get_toolset()` → `bind_capabilities_tier(..., innermost=True)`。`innermost`（即 durability 能力）会在其他能力之后绑定，因而能包裹全部 agent 工具集，代价是它自身不能贡献工具集；
12. 若 `model is not None and not defer_model_check and not self._root_capability.has_resolve_model_id`：`self._model = models.infer_model(model)`；
13. 绑定后再次校验（`_validate_capability_ids` / `_validate_instruction_source_ids`），抽取能力贡献：`_cap_instructions`、`_cap_native_tools`（`_validate_native_tool_ids`）、`_cap_model_settings`；
14. `CombinedToolset(self.toolsets)`——在注册期即校验稳定的 toolset 身份。

### 1.3 运行方法矩阵

六个运行方法都定义在 `AbstractAgent`（[agent/abstract.py](../pydantic_ai_slim/pydantic_ai/agent/abstract.py)）上，`Agent` 只实现其中的 `iter`：

| 方法 | 定义处 | 返回 | 语义 |
|------|--------|------|------|
| `run(...)` | [abstract.py#L532](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L532) | `AgentRunResult[OutputDataT]` | 主要入口：驱动整张图直到结束 |
| `run_sync(...)` | [abstract.py#L739](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L739) | `AgentRunResult[OutputDataT]` | 同步封装（`loop.run_until_complete`）；不能在 async 代码或运行中的同步工具里调用 |
| `run_stream(...)` | [abstract.py#L910](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L910) | `AsyncGenerator[StreamedRunResult[...]]` | 流式；以**首个匹配 `output_type` 的输出**为终点，此后不再执行模型发出的工具调用 |
| `run_stream_sync(...)` | [abstract.py#L1238](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1238) | `StreamedRunResultSync[...]` | 同步流式封装 |
| `run_stream_events(...)` | [abstract.py#L1422](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1422) | `AbstractAsyncContextManager[AgentRunEvents]` | 后台运行 + 类型化事件流；**首次迭代才启动后台 run** |
| `iter(...)` | [agent/__init__.py#L1313](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1313)、抽象签名 [abstract.py#L1613](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1613) | `AsyncGenerator[AgentRun]` | **原语**：返回可逐步驱动的 `AgentRun`（`run` 就是它的消费者） |

`run` 的参数（[abstract.py#L532-L610](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L532-L610)，与 `iter` / `run_stream` / `run_stream_events` 基本一致）：

| 参数 | 说明 |
|------|------|
| `user_prompt` | 起始/续接对话的用户输入（`str` 或多模态内容序列） |
| `output_type` | 本次运行的输出类型（仅当 agent 无 output validator 时可用） |
| `conversation` | 直接续接的 `Conversation`（替代分别传 `message_history` / `usage` / `conversation_id`；同时传两者抛 `UserError`） |
| `message_history` | 迄今对话历史 |
| `deferred_tool_results` | 历史中延迟工具调用的结果 |
| `conversation_id` | 对话 ID；传 `'new'` 强制新对话；省略时取历史中最近的值或新 UUID7 |
| `run_id` | 本次运行 ID；**从不**从历史继承；空串或与历史冲突抛 `UserError` |
| `model` | 本次运行的模型（agent 未设 model 时必填） |
| `instructions` | 本次运行的附加指令 |
| `deps` | 依赖注入值 |
| `model_settings` | 本次请求设置，或 `Callable[[RunContext], ModelSettings]` |
| `usage_limits` | `UsageLimits`（请求数 / token / 成本） |
| `cancellation_token` | 跨任务/线程取消用；**一次性**——复用一个已取消 token 会阻止运行启动 |
| `usage` | 起始用量（续接对话或嵌套 agent 时用） |
| `metadata` | 本次运行元数据（dict 或 callable），与 agent 级元数据合并 |
| `retries` | 覆盖 agent 级重试预算（`int` 或 `AgentRetries`） |
| `infer_name` | 未设 name 时是否从调用栈推断 |
| `toolsets` | 本次运行的附加工具集 |
| `event_stream_handler` | 本次运行的事件处理器（durability 下在 workflow 侧运行） |
| `capabilities` | 本次运行的附加能力（与 agent 能力合并） |
| `workspace` | 本次运行的 workspace（backend / `WorkspaceRef` / `'new'`） |
| `spec` | 本次运行的 agent spec（运行期 spec 值**增量**叠加） |

### 1.4 装饰器家族

| 装饰器 | 定义处 | 说明 |
|--------|--------|------|
| `@agent.tool` | [L2722](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2722) | 注册工具，函数首个参数为 `RunContext`。参数：`name` / `description` / `retries` / `prepare` / `args_validator` / `docstring_format` / `require_parameter_descriptions` / `schema_generator` / `strict` / `sequential` / `requires_approval` / `metadata` / `timeout` / `defer_loading` / `include_return_schema` |
| `@agent.tool_plain` | [L2860](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2860) | 同上，但函数**不**接收 `RunContext` |
| `@agent.instructions` | [L2364](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2364) | 注册指令函数（可含 `RunContext`；`name=` 使其可被寻址为能力 `agent` 下的具名 part） |
| `@agent.system_prompt` | [L2503](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2503) | 注册 system prompt 函数；仅一个 kwarg `dynamic=False`，`dynamic=True` 时即使传入 `model_history` 也重新求值（键为其 `__qualname__`） |
| `@agent.output_validator` | [L2578](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2578) | 注册输出校验器；抛 `ModelRetry` 触发重试 |
| `@agent.on_event` | [L2625](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2625) | 注册事件监听器（存入 `self._event_hooks`）；可传 `*event_types` 收窄，及 `timeout=` |
| `@agent.toolset` | [L2984](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2984) | 注册 toolset 工厂函数（进 `_dynamic_toolsets`） |

示例（取自 `@agent.tool` docstring）：

```python
from pydantic_ai import Agent, RunContext

agent = Agent('test', deps_type=int)

@agent.tool
def foobar(ctx: RunContext[int], x: int) -> int:
    return ctx.deps + x

@agent.tool(retries=2)
async def spam(ctx: RunContext[int], y: float) -> float:
    return ctx.deps + y
```

### 1.5 其他方法与入口

| 方法 | 定义处 | 说明 |
|------|--------|------|
| `from_spec(...)` | [L938](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L938) | 从 dict / `AgentSpec` 构造 agent |
| `from_file(...)` | [L1103](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1103) | 从 YAML/JSON 文件构造 agent |
| `instrument_all(...)` / `instrument` | [L1164](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1164) / [L1169](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1169) | 全局/实例级埋点开关 |
| `system_prompt_parts(...)` | [L2419](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2419) | 解析出的 system prompt part 列表 |
| `output_json_schema(...)` | abstract.py [L454](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L454) | 输出 JSON schema |
| `override(...)` | [L2147](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L2147) | 临时覆盖（经上面那些 `ContextVar`）：name/deps/model/toolsets/tools/native_tools/instructions/settings/retries/spec/workspace/root capability |
| `to_web(...)` | [L4223](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L4223) | 挂载 Web 聊天 UI |
| `to_cli(...)` / `to_cli_sync(...)` | abstract.py [L2100](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L2100) / [L2146](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L2146) | CLI 界面 |
| `realtime(...)` | abstract.py [L1787](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1787) | 打开实时（双向语音）会话 |

### 1.6 与 capability / toolset / output 的关系

- `self._root_capability: CombinedCapability` 汇总指令、工具集、native 工具、模型设置与 Hook。`Agent` 的 `root_capability` 属性（[L3463](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3463)）与 `toolsets` 属性（[L3468](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3468)）暴露合并视图。
- `Agent._get_toolset()`（[L3392](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3392)）用 `PreparedToolset` 包裹，把 `prepare_tools` / `prepare_output_tools` 钩子烘焙进 `ToolManager.tools`。
- `self._output_schema = OutputSchema.build(output_type)`；若含工具则 `self._output_toolset = self._output_schema.toolset`（kind 为 `'output'`），并在 `max_retries is None` 时填入 `_max_output_retries`。

---

## 2. `AbstractAgent` / `WrapperAgent` / `AgentSpec`

### 2.1 `AbstractAgent`（[agent/abstract.py#L349-L2184](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L349-L2184)）

`AbstractAgent(Generic[AgentDepsT, OutputDataT], ABC)` 定义所有 agent 的公共契约。抽象成员（子类必须实现）：`model`（属性+setter）、`name`、`description`、`deps_type`、`output_type`、`event_stream_handler`、`toolsets`、`iter(...)`、`override(...)`、`__aenter__` / `__aexit__`。具体方法包括 `run` / `run_sync` / `run_stream` / `run_stream_sync` / `run_stream_events`、`system_prompt_parts`、`output_json_schema`、`realtime`、`to_cli` / `to_cli_sync`。

静态工具方法：`parallel_tool_call_execution_mode(mode)`（[L1991](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1991)）、`using_thread_executor(executor)`（[L2005](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L2005)）、`using_sleep(sleep_func)`（[L2041](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L2041)）；节点判别辅助 `is_model_request_node` / `is_call_tools_node` / `is_user_prompt_node` / `is_end_node`（[L2053-L2090](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L2053-L2090)）。

`run_stream_events` 返回的 [`AgentRunEvents`](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L139-L311) 是一个**手写**的迭代器类（而非 `async def` 生成器，以避免 Python 3.11 下 `GeneratorExit` 在错误 Context 中恢复帧；见其 docstring）。它在首次 `__anext__` 时经 `_ensure_started` 启动后台 run（零缓冲 `anyio` memory stream，天然背压），末尾产出单个 `AgentRunResultEvent`。`_RunStreamEventsContext` 保证早退时也能清理。

### 2.2 `WrapperAgent`（[agent/wrapper.py#L55](../pydantic_ai_slim/pydantic_ai/agent/wrapper.py#L55)）

`WrapperAgent(AbstractAgent[AgentDepsT, OutputDataT])`：包裹另一个 agent 的基类，自身不做任何事（`def __init__(self, wrapped: AbstractAgent[...])`，把被包装者存为 `self.wrapped`）。`model` / `name` / `description` / `deps_type` / `output_type` / `event_stream_handler` / `root_capability` / `toolsets` / `__aenter__` / `__aexit__` / `output_json_schema` / `system_prompt_parts` / `iter` / `override` / realtime 相关方法等全部转发给 `self.wrapped`。Durable 适配（Temporal/DBOS/Prefect）以它为基类扩展。

### 2.3 `AgentSpec`（[agent/spec.py#L33](../pydantic_ai_slim/pydantic_ai/agent/spec.py#L33)）

`AgentSpec(BaseModel)`：从 dict/YAML/JSON 构造 agent 的规格。字段：

```python
json_schema_path: str | None = Field(default=None, alias='$schema')
model: str | None = None
name: str | None = None
description: TemplateStr[Any] | str | None = None
instructions: TemplateStr[Any] | str | list[TemplateStr[Any] | str] | None = None
deps_schema: dict[str, Any] | None = None
output_schema: dict[str, Any] | None = None
model_settings: dict[str, Any] | None = None
retries: int | AgentRetries | None = None
end_strategy: EndStrategy = 'graceful'
tool_timeout: float | None = None
metadata: dict[str, Any] | None = None
capabilities: list[CapabilitySpec] = []
```

类方法：`from_file(path, fmt=None)`、`from_text(text, fmt='yaml')`、`from_dict(data)`；实例方法 `to_file(...)`、`to_dict(...)`（见 [spec.py](../pydantic_ai_slim/pydantic_ai/agent/spec.py)）。`capabilities` 用 `CapabilitySpec`，可序列化能力由 `CAPABILITY_TYPES` 注册表（[capabilities/__init__.py#L76-L100](../pydantic_ai_slim/pydantic_ai/capabilities/__init__.py#L76-L100)）解析。

---

## 3. 执行循环：`_agent_graph.py`

文件：[pydantic_ai_slim/pydantic_ai/_agent_graph.py](../pydantic_ai_slim/pydantic_ai/_agent_graph.py)。模块 `__all__` 导出 `GraphAgentState`、`GraphAgentDeps`、`UserPromptNode`、`ModelRequestNode`、`CallToolsNode`、`build_run_context`、`capture_run_messages`、`HistoryProcessor`、`resolve_conversation`、`resolve_conversation_id`、`process_tool_calls`、`resolve_run_id`。

### 3.1 图的构建

```python
def build_agent_graph(
    name: str | None,
    deps_type: type[DepsT],
    output_type: OutputSpec[OutputT],
) -> Graph[GraphAgentState, GraphAgentDeps[DepsT, OutputT], UserPromptNode[DepsT, OutputT], result.FinalResult[OutputT]]:
    return _build_agent_graph(name)
```

- `build_agent_graph`（[L2956-L2971](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2956-L2971)）只委托；`deps_type` / `output_type` 仅绑定类型参数。
- `_build_agent_graph(name)`（[L2974-L2999](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2974-L2999)）带 `@lru_cache(maxsize=128)`，**仅以 `name` 为键**——同一 agent（同名）的所有运行共享同一张图。用 `GraphBuilder(name=name or 'Agent', state_type=GraphAgentState, deps_type=GraphAgentDeps[Any, Any], input_type=UserPromptNode[Any, Any], output_type=result.FinalResult[Any], auto_instrument=False)`，注册 `UserPromptNode` / `ModelRequestNode` / `CallToolsNode` / `SetFinalResult`，`build(validate_graph_structure=False)`。

### 3.2 节点类

| 节点 | 定义处 | 职责 |
|------|--------|------|
| `AgentNode` | [L579](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L579) | 公共基类（`BaseNode[GraphAgentState, GraphAgentDeps[DepsT, Any], FinalResult[NodeRunEndT]]`） |
| `UserPromptNode` | [L637-L861](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L637-L861) | 处理用户 prompt、指令、system prompt、延迟工具结果；清理历史、恢复挂起响应、重算动态 prompt，构建首个 `ModelRequest` |
| `ModelRequestNode` | [L1352-L2164](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1352-L2164) | 发起模型请求（含 `.stream()` 上下文）；处理续段、重试、错误恢复 |
| `CallToolsNode` | [L2166-L2674](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2166-L2674) | 处理 `ModelResponse`：分类 part、按 `end_strategy` 派发工具调用，决定「结束 vs 继续」 |
| `SetFinalResult` | [L2677-L2686](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2677-L2686) | 立即以预算好的 `FinalResult` 结束图（流式已产出最终结果时使用） |

`UserPromptNode` 字段：`user_prompt: str | Sequence[UserContent] | None`；KW_ONLY 的 `deferred_tool_results` / `instructions` / `instructions_functions` / `system_prompts` / `system_prompt_functions` / `system_prompt_dynamic_functions`。

`ModelRequestNode` 字段：`request: ModelRequest`；`is_resuming_without_prompt: bool = False`；私有 `_resume_suspended`（恢复挂起的 provider 轮次）、`_result`、`_did_stream`、`last_request_context`。

### 3.3 步骤序列（函数级细节）

**步骤 1 · 构建 prompt**（`UserPromptNode.run`，[L660-L772](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L660-L772)）：

1. 取 `capture_run_messages()` 的列表（或在 `LookupError` 时新建）；
2. `messages[:] = _clean_message_history(ctx.state.message_history)` 清理并复用该列表作为历史；`ctx.deps.new_message_index = len(messages)`；
3. 若 `deferred_tool_results is not None` → `_handle_deferred_tool_results(...)`（[L774-L826](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L774-L826)）直接转 `CallToolsNode`（跳过模型请求）；
4. `_repair_interrupted_tail(messages, has_new_prompt=...)`（[L864](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L864)）为中断的尾巴合成工具返回；
5. 处理历史末尾：末条若是 `ModelRequest` 且无新 prompt，则 pop 出来复用其 parts 作为 `next_message`（`is_resuming_without_prompt=True`）；末条若是 `ModelResponse`：`suspended` 且无 prompt 时返回 `_resume_suspended` 的 `ModelRequestNode`；无 prompt 且有未处理工具调用时转 `CallToolsNode`；
6. `_reevaluate_dynamic_prompts(...)` 重算动态 system prompt；
7. 生成含 system prompt 与 `UserPromptPart` 的 `next_message`，返回 `ModelRequestNode(request=next_message, is_resuming_without_prompt=...)`。

**步骤 2 · 调用模型**（`ModelRequestNode.run` → `_make_request` / `.stream` → `_prepare_request`）：

- `_prepare_request`（[L1746-L1808](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1746-L1808)）：设置 `request.timestamp` / run 元数据 → 追加 request → `ctx.state.run_step += 1` → `_select_model(ctx)`（[L2689](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2689)）→ `_refresh_loaded_capability_ids` / `_refresh_discovered_tool_names` → `build_run_context` 并覆盖 `retry` / `max_retries` → `ToolManager.for_run_step` → `_display_first_run_banner` → `_get_instructions` 并写入 `request.instructions` → 校验非空 → `_prepare_request_parameters` → `get_model_settings` → 构建 `ModelRequestContext` → `ctx.deps.usage_limits.check_before_request(ctx.state.usage)`。
- `_make_request`（[L1665-L1744](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1665-L1744)）：经 `root_capability.wrap_model_request(...)`（若无则直接 `model_handler`）包裹；`model_handler` 内先 `_apply_before_model_request`，再 `model_request(...)`（或流式的 `model_request_stream`），捕获 `ModelRequestContext` 的 span context，最后 `after_model_request`。续段（Anthropic `pause_turn`、OpenAI background）由 `model_request` 解析合并为单个响应、用量只提交一次。
- 错误处理：`SkipModelRequest` 用其 `response` 短路；`ModelRetry` → `_build_retry_node(ctx, e)`（[L2148](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2148)）；其他异常 → `_recover_model_request_error`（[L2086](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2086)）。

**步骤 3 · 处理工具/输出**（`CallToolsNode.run`，[L2190](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2190) → `_handle_tool_calls`，[L2443-L2558](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2443-L2558)）：

1. `_refresh_discovered_tool_names(ctx)` 依据响应后的历史重新计算揭示；
2. `build_run_context` 并把响应侧的 `AnchoredEvidence` 装到 `run_context` / `tool_manager.ctx`；
3. `ToolManager.for_run_step`（通常同一步返回原 manager，保留已累积重试）；
4. 若 `end_strategy='early'` 且响应携带非工具输出、且所有并发调用都是普通函数工具：`_process_response_output` 先算出 `final_result`（图像优先于 schema 校验文本）；
5. `process_tool_calls(...)`（[_tool_execution.py#L307](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L307)）执行；产出 `output_final_result` 时 `_handle_final_result`（[L2652](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2652)）追加尾部工具返回并返回 `End(final_result)`；
6. 否则构造新的 `ModelRequestNode(parts=output_parts)` 进入下一轮；
7. 异常路径会把已收集的 `output_parts` 以 `state='interrupted'` 的 `ModelRequest` 落盘，供恢复路径合成 `'interrupted'` 返回。

**步骤 4 · 终结**：`End(FinalResult)` → `AgentRun.result`（[run.py#L284](../pydantic_ai_slim/pydantic_ai/run.py#L284)）依图输出与 `GraphAgentState` 构造 `AgentRunResult`。

### 3.4 `GraphAgentState`（`@dataclass(kw_only=True)`，[L379-L462](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L379-L462)）

| 字段 | 默认 | 说明 |
|------|------|------|
| `message_history: list[ModelMessage]` | `[]` | 迄今消息历史 |
| `usage: RunUsage` | `RunUsage()` | 运行用量 |
| `output_retries_used: int` | `0` | 已消耗的输出重试 |
| `run_step: int` | `0` | 当前步序号 |
| `run_id: str` | UUID7 | 本次运行 ID；**从不**从历史继承 |
| `conversation_id: str` | UUID7 | 对话 ID（运行参数 / 历史最近值 / 新 UUID7） |
| `metadata: dict | None` | `None` | 运行元数据 |
| `last_max_tokens: int | None` | `None` | 最近解析的 `max_tokens`（仅用于错误信息） |
| `last_model_request_parameters: ModelRequestParameters | None` | `None` | 最近请求参数（用于 OTel span 属性） |
| `pending_messages: list[PendingMessage]` | `[]` | `enqueue` 队列（`__post_init__` 转为 `PendingMessageQueue`） |
| `event_stream_buffer: list[AgentStreamEvent]` | `EventStreamBuffer()` | 运行事件缓冲（按引用进 `RunContext._event_stream_buffer`） |
| `mcp_tool_defs_cache: dict[str, dict[str, ToolDefinition]]` | `{}` | durable MCP toolset 定义缓存（按 `id`） |

方法：

- `check_incomplete_tool_call()`（[L429](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L429)）：若最后一条响应 `finish_reason == 'length'` 且末尾 `ToolCallPart` 参数不完整，抛 `IncompleteToolCall`。
- `consume_output_retry(max_output_retries, error=None)`（[L445](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L445)）：`output_retries_used += 1`，超限时先 `check_incomplete_tool_call()` 再抛 `UnexpectedModelBehavior(f'Exceeded maximum output retries ({max_output_retries})')`。

### 3.5 `GraphAgentDeps`（`Generic[DepsT, OutputDataT]`，[L465-L577](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L465-L577)）

| 字段 | 说明 |
|------|------|
| `user_deps: DepsT` | 用户依赖 |
| `prompt` / `new_message_index` / `resumed_request` / `resumed_request_index` | 本次输入与恢复锚点 |
| `model: models.Model` / `model_selector` / `model_selected_for_step` / `evaluate_model_selector` / `enter_model` | 模型与（可选）每步选择器 |
| `get_model_settings: Callable[[RunContext], ModelSettings | None]` | 每步解析设置 |
| `usage_limits: UsageLimits` | 本次运行限制 |
| `max_output_retries: int` | 输出重试预算 |
| `end_strategy: EndStrategy` | 终结策略 |
| `get_instructions: Callable[[RunContext], Awaitable[list[InstructionPart] | None]]` | 解析指令 |
| `output_schema: OutputSchema[OutputDataT]` / `output_validators` / `validation_context` | 输出与校验 |
| `root_capability: AbstractCapability` / `capabilities: dict[str, AbstractCapability]` | 能力图 |
| `loaded_capability_ids: set[str]` / `discovered_tool_names: set[str]` | **按引用共享**、只就地 mutate（见 §10） |
| `workspace: Workspace` / `carried_workspace_ref` / `adopted_response` | workspace 与响应 ref 记录 |
| `native_tools: list[AgentNativeTool]` / `tool_manager: ToolManager` | 工具 |
| `tracer: Tracer` / `instrumentation_settings: InstrumentationSettings | None` | 埋点 |
| `display_banner: BannerDisplay` | 首次运行 banner |
| `agent: Agent | None` | 反向引用 |
| `cancellation: RunCancellation` | 第一方取消控制器（运行期） |
| `pending_immediate_dispatches` / `event_stream_replacements` | 立即派发结算信号 / legacy 事件替换（运行期、按 `id`） |
| `durable_operations: dict[tuple[str, str], Callable]` | durable 操作派发表（**按引用共享**） |
| `run_capabilities_by_id: dict[str, AbstractCapability]` | durable 恢复用（**按引用共享**） |
| `model_id: str | None` | `model` 由字符串解析出的 id（供 durable 往返） |

属性 `workspace_ref`（[L517](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L517)）返回本次响应记录的 ref。

---

## 4. `RunContext`

文件：[pydantic_ai_slim/pydantic_ai/_run_context.py](../pydantic_ai_slim/pydantic_ai/_run_context.py)，`@dataclasses.dataclass(repr=False, kw_only=True)`（[L168](../pydantic_ai_slim/pydantic_ai/_run_context.py#L168)）。

### 4.1 字段

| 字段 | 默认 | 说明 |
|------|------|------|
| `deps: RunContextAgentDepsT` | — | 依赖注入值 |
| `model: AbstractModel` | — | 当前模型（实时会话中为 `RealtimeModel`）；`model_id` 属性读 `_model_id` |
| `usage: RunUsage` | — | 运行用量 |
| `usage_limits: UsageLimits \| None` | `None` | 运行限制；运行中恒非空（无限制时是默认 `UsageLimits()`，`request_limit=50`） |
| `agent` | `None` | 运行该上下文的 agent |
| `prompt` | `None` | 原始用户 prompt |
| `messages: list[ModelMessage]` | `[]` | 会话历史；改写它会改写运行历史 |
| `validation_context` | `None` | Pydantic 校验上下文 |
| `tracer` / `trace_include_content` / `instrumentation_version` | `NoOpTracer` / `False` / 默认版本 | 埋点 |
| `retries: dict[str, int]` | `{}` | 各工具已用重试数 |
| `tool_call_id` / `tool_name` | `None` | 当前工具调用 |
| `retry: int` / `max_retries: int` | `0` / `0` | 重试与上限（工具调用或输出校验语义不同） |
| `run_step` | `0` | 当前步 |
| `tool_call_approved` / `tool_call_metadata` | `False` / `None` | 审批与元数据 |
| `partial_output` | `False` | 传给输出校验器的输出是否部分 |
| `run_id` / `conversation_id` | `None` | 运行/对话 ID |
| `metadata` | `None` | 运行元数据 |
| `model_settings` | `None` | 当前步解析后的模型设置（工具 Hook/输出校验器/构造期为 `None`） |
| `workspace: Workspace` | `no_workspace()` | 运行 workspace |
| `pending_messages: list[PendingMessage] \| None` | `None` | `enqueue` 队列（合成上下文为 `None`） |
| `_cancellation` | `None` | 取消控制器（私有、运行期） |
| `_event_stream_buffer` | `None` | 运行事件缓冲（私有、按引用） |
| `_pending_immediate_dispatches` / `_event_stream_replacements` | `{}` / `{}` | 私有 |
| `_durable_operations` / `_run_capabilities_by_id` / `_run_held_toolsets` | `None` | 私有、按引用 |
| `_mcp_tool_defs_cache` | `{}` | 私有、按引用 |
| `tool_manager: ToolManager \| None` | `None` | 当前步工具管理器 |
| `realtime_session: RealtimeSession \| None` | `None` | 会话连接后的 `RealtimeSession` |
| `root_capability` / `capabilities: dict[str, AbstractCapability]` | `None` / `{}` | 能力 |
| `loaded_capability_ids: set[str]` / `discovered_tool_names: set[str]` | `set()` | 能力加载 / 工具揭示集合（按引用） |
| `capability_active: bool \| None` | `None` | 当前（或目标）能力是否 active |
| `_anchored_evidence: AnchoredEvidence` | `AnchoredEvidence()` | 响应侧证据 |
| `_capability` | `None` | 当前执行 hook 的能力（事件归属用） |

只读属性：`model_id`（[L443](../pydantic_ai_slim/pydantic_ai/_run_context.py#L443)）、`capability_loaded`（[L457](../pydantic_ai_slim/pydantic_ai/_run_context.py#L457)）、`realtime`（[L477](../pydantic_ai_slim/pydantic_ai/_run_context.py#L477)）、`in_durable_context`（[L490](../pydantic_ai_slim/pydantic_ai/_run_context.py#L490)）、`last_attempt`（[L507](../pydantic_ai_slim/pydantic_ai/_run_context.py#L507)）、`context_window_used`（[L512](../pydantic_ai_slim/pydantic_ai/_run_context.py#L512)）、`active_capability_ids`（[L551](../pydantic_ai_slim/pydantic_ai/_run_context.py#L551)）、`available_capability_ids`（[L617](../pydantic_ai_slim/pydantic_ai/_run_context.py#L617)）、`available_tool_names`（[L637](../pydantic_ai_slim/pydantic_ai/_run_context.py#L637)）、`tools`（[L717](../pydantic_ai_slim/pydantic_ai/_run_context.py#L717)）；判别方法 `is_tool_available(tool)`（[L654](../pydantic_ai_slim/pydantic_ai/_run_context.py#L654)）。

### 4.2 方法

- **`async emit(event)`**（[L729-L829](../pydantic_ai_slim/pydantic_ai/_run_context.py#L729-L829)）：把 `CustomEvent`（应用）或 `CapabilityEvent`（能力）投递进运行事件流；自动打上 `tool_call_id` / `tool_name`。**必为 await**（供 async 工具/hook 使用）；无事件流的合成上下文会抛 `UserError`。
- `enqueue(*content, priority='asap')`（[L831-L882](../pydantic_ai_slim/pydantic_ai/_run_context.py#L831-L882)）：注入待处理消息；`priority` 取 `'asap'` / `'when_idle'`；返回 `enqueue_id`（空调用返回 `None`）。
- `cancel()`（[L883](../pydantic_ai_slim/pydantic_ai/_run_context.py#L883)）：请求取消运行（在下一个 await 处投递；终态）。

`capability_active` 是当前字段，`capability_loaded` 属性（[L457](../pydantic_ai_slim/pydantic_ai/_run_context.py#L457)，含 [setter L471](../pydantic_ai_slim/pydantic_ai/_run_context.py#L471)）是它的（过渡期）别名；模块用 `_run_context_init_with_capability_loaded`（[L922-L934](../pydantic_ai_slim/pydantic_ai/_run_context.py#L922-L934)）包装生成的 `__init__`，使 `capability_loaded=` 构造参数仍可用但发出 `PydanticAIDeprecationWarning`。

---

## 5. `ToolManager` 与 `_tool_execution`

文件：[tool_manager.py](../pydantic_ai_slim/pydantic_ai/tool_manager.py)、[_tool_execution.py](../pydantic_ai_slim/pydantic_ai/_tool_execution.py)。

### 5.1 生命周期与字段（`ToolManager`，[tool_manager.py#L147-L191](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L147-L191)）

| 字段 | 说明 |
|------|------|
| `toolset: AbstractToolset` | 本步提供工具的 toolset |
| `root_capability: AbstractCapability \| None` | hook 调用入口 |
| `ctx: RunContext \| None` | 本步上下文 |
| `tools: dict[str, ToolsetTool] \| None` | 缓存，键为模型调用的 `tool_def.name` |
| `failed_tools` / `succeeded_tools: set[str]` | 本步失败/成功的工具名 |
| `availability_refused: set[str]` | 本 run 已用掉一次「可用性拒绝」的工具名（与重试预算分开） |
| `default_max_retries: int` | 默认重试数（默认 1） |
| `resolved_capability_ids: frozenset[str] \| None` | 缓存 `tools` 时所依据的能力活动集（可用性变化时触发重解析） |

`for_run_step(ctx)`（[L210-L266](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L210-L266)）：创建步级 manager——同一步且 `resolved_capability_ids` 未变则直接返回自身；否则结转重试计数（失败者 +1）、`await toolset.for_run_step(ctx)`、`tools = await toolset.get_tools(ctx)`，并把新 manager 写回 `ctx.tool_manager`。

### 5.2 校验

`async validate_tool_call(call, *, approved=False, metadata=None, wrap_validation_errors=True) -> ValidatedToolCall`（[L658-L741](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L658-L741)）：

- 解析工具（`_resolve_tool`）、构建工具上下文（`_build_tool_context`）、运行 `before_tool_validate` / `wrap_tool_validate` / `after_tool_validate` 钩子链（`_run_validate_hooks`）；
- 参数校验用工具的 Pydantic `args_validator`（JSON 或 Python），可叠加自定义 `args_validator_func`；
- `SkipToolValidation` → 用其 `validated_args` 作为成功结果；
- `_ValidationDeferral`（校验阶段抛出的 `ApprovalRequired` / `CallDeferred`）→ 返回 `args_valid=True` 且携带 `deferral`（不消耗重试）；
- `ValidationError` / `ModelRetry` → `ToolRetryError`（消耗重试预算、进 `failed_tools`）；`ToolFailed` → `ToolFailedError`（**不**消耗预算）；`wrap_validation_errors=False` 时原样抛出、不触碰预算状态。

`ValidatedToolCall`（[L60](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L60)）携带 `args_valid`、`validated_args`、`validation_error`、`deferral`。

### 5.3 执行

`async execute_tool_call(validated, *, wrap_validation_errors=True)`（[L743-L783](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L743-L783)）→ `_execute_tool_call_impl`（[L987](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L987)）：

- 拒绝 `kind == 'external'`（抛 `RuntimeError`）；
- 运行 `before_tool_execute` / `wrap_tool_execute` / `after_tool_execute` 钩子链；
- 经 `toolset.call_tool` 调用；错误映射见 §5.2；
- `SkipToolExecution` 用其 `result` 短路替换执行结果。

`handle_call(...)`（[L1078-L1182](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L1078-L1182)）是校验+执行的便捷组合，并把声明式延迟（`ToolDefinition.defer`，如 `requires_approval=True` → `kind='unapproved'`；external → `kind='external'`）统一转成 `CallDeferred` / `ApprovalRequired` 走 `_resolve_single_deferred`。`resolve_deferred_tool_calls(requests)`（[L1184](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L1184)）批量调用 `root_capability.handle_deferred_tool_calls`。

### 5.4 输出工具的区别

- 输出工具使用**输出钩子**（`validate_output_tool_call` [L787](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L787) / `execute_output_tool_call` [L858](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L858) → `run_output_validate_hooks` / `run_output_process_hooks`），**不会**触发用户面向的工具钩子；
- `validate_output_tool_call` 把 `OutputContext`（含 `schema`、`mode='tool'`、`tool_call`、`tool_def`）交给钩子，钩子看到的是**语义值**（模型被要求产出的东西），而非内部 dict 包装；
- 输出校验器看到**全局**输出重试预算（`_build_output_run_context` 用 `max_output_retries`）；输出函数看到 per-tool `max_retries`。

### 5.5 延迟工具

`handle_call` 把**声明式**延迟（`ToolDefinition.defer`）与**抛出式**延迟（`CallDeferred` / `ApprovalRequired`）统一经 `_resolve_single_deferred`（[L1202](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L1202)）处理，构建 `DeferredToolRequests` 交 `root_capability.handle_deferred_tool_calls`。

### 5.6 编排（`_tool_execution.py`）

```python
async def process_tool_calls(
    tool_manager: ToolManager[DepsT],
    *,
    tool_calls: list[ToolCallPart],
    tool_call_results: dict[str, DeferredToolResult | Literal['skip']] | None,
    tool_call_metadata: dict[str, dict[str, Any]] | None,
    final_result: FinalResult[NodeRunEndT] | None,
    ctx: GraphRunContext[GraphAgentState, GraphAgentDeps[DepsT, NodeRunEndT]],
    output_parts: list[ModelRequestPart],
    output_final_result: deque[FinalResult[NodeRunEndT]] | None = None,
) -> AsyncIterator[AgentStreamEvent]:
```

`process_tool_calls`（[L307-L374](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L307-L374)）按 `ctx.deps.end_strategy` 选择处理器（`_EarlyProcessor` / `_GracefulProcessor` / `_ExhaustiveProcessor`，`assert_never` 收束），`async for event in processor.run()` 产出事件，最后把 `processor.final_result` 写入 `output_final_result`。

`_ToolCallProcessor`（[L378-L514](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L378-L514)）在 `__post_init__` 中按 `tool_def.kind` 分类每个调用（`output` / `function` / `external` / `unapproved` / `unknown`），并计算 `function_indices` / `output_indices`。`run()` 先做工具调用数用量检查（`check_before_tool_call`），再 `_run_strategy()`，随后 `_apply_retry_wins()`，最后 `_finalize_deferred()`。

不变量（[docstring L338-L347](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L338-L347)）：

- **retry-wins**：函数/未知工具产出 `RetryPromptPart` 会抑制同批的 `final_result`；输出工具的重试**不**触发（"首个有效输出胜出"）；当 `final_result` 是外部传入（`Agent.run_stream` 已提交流式输出）或不适用 `'early'` 时，retry-wins 不生效；
- 延迟调用（`external` / `unapproved` 且无结果）在步末作为**单一批次**解析；
- `sequential=True` 工具是屏障（之前的先完成、其独自运行、之后的才开始）；`parallel_execution_mode('sequential')` 让每个工具都成为屏障；
- 可用性增量按产出顺序确定性剪枝。

---

## 6. 结果对象

### `AgentRun`（[run.py#L170](../pydantic_ai_slim/pydantic_ai/run.py#L170)）

有状态、可异步迭代（由 `agent.iter(...)` 获得）。

- 属性：`ctx`（[L264](../pydantic_ai_slim/pydantic_ai/run.py#L264)）、`next_node`（[L271](../pydantic_ai_slim/pydantic_ai/run.py#L271)）、`result -> AgentRunResult | None`（[L284](../pydantic_ai_slim/pydantic_ai/run.py#L284)）、`usage`（[L673](../pydantic_ai_slim/pydantic_ai/run.py#L673)）、`metadata`（[L678](../pydantic_ai_slim/pydantic_ai/run.py#L678)）、`run_id`（[L683](../pydantic_ai_slim/pydantic_ai/run.py#L683)）、`conversation_id`（[L688](../pydantic_ai_slim/pydantic_ai/run.py#L688)）、`pending_messages`（[L693](../pydantic_ai_slim/pydantic_ai/run.py#L693)）。
- 迭代：`__aiter__` / `__anext__`（[L337](../pydantic_ai_slim/pydantic_ai/run.py#L337)）产出节点；`next(node)`（[L579](../pydantic_ai_slim/pydantic_ai/run.py#L579)）手动驱动一步；`all_messages()` / `new_messages()`（[L307](../pydantic_ai_slim/pydantic_ai/run.py#L307) / [L322](../pydantic_ai_slim/pydantic_ai/run.py#L322)）及 JSON 变体。
- `emit(event)`（[L700](../pydantic_ai_slim/pydantic_ai/run.py#L700)）、`enqueue(...)`（[L736](../pydantic_ai_slim/pydantic_ai/run.py#L736)）、`cancel()`（[L776](../pydantic_ai_slim/pydantic_ai/run.py#L776)）（取消整个运行）。
- 节点生命周期钩子经 `_run_node_with_hooks`（[L522](../pydantic_ai_slim/pydantic_ai/run.py#L522)）派发：`wrap_node_run` / `before_node_run` / `on_node_run_error` / `after_node_run`。

### `AgentRunResult`（[run.py#L814](../pydantic_ai_slim/pydantic_ai/run.py#L814)）

最终结果。公开 `output`、`workspace`（[L828](../pydantic_ai_slim/pydantic_ai/run.py#L828)）、`all_messages(output_tool_return_content=...)`（[L960](../pydantic_ai_slim/pydantic_ai/run.py#L960)）、`new_messages(...)`（[L993](../pydantic_ai_slim/pydantic_ai/run.py#L993)）、`all_messages_json` / `new_messages_json`、`response`（最后一条 `ModelResponse`，[L1026](../pydantic_ai_slim/pydantic_ai/run.py#L1026)）、`usage`（[L1035](../pydantic_ai_slim/pydantic_ai/run.py#L1035)）、`conversation`（[L1040](../pydantic_ai_slim/pydantic_ai/run.py#L1040)）、`timestamp`（[L1065](../pydantic_ai_slim/pydantic_ai/run.py#L1065)）、`metadata`（[L1070](../pydantic_ai_slim/pydantic_ai/run.py#L1070)）、`run_id`（[L1075](../pydantic_ai_slim/pydantic_ai/run.py#L1075)）、`conversation_id`（[L1080](../pydantic_ai_slim/pydantic_ai/run.py#L1080)）。私有字段 `_output_tool_name` / `_state` / `_new_message_index` / `_traceparent_value` 通过自定义 `model_validator(mode='before')`（[L858](../pydantic_ai_slim/pydantic_ai/run.py#L858)）与 `model_serializer(mode='wrap')`（[L905](../pydantic_ai_slim/pydantic_ai/run.py#L905)）在公开序列化形状（`messages` / `usage` / `run_id` / `conversation_id` / `metadata` / `output`）与内部字段之间往返；`__getstate__`（[L837](../pydantic_ai_slim/pydantic_ai/run.py#L837)）在 pickle 时剔除 live workspace。

### `StreamedRunResult`（[result.py#L572](../pydantic_ai_slim/pydantic_ai/result.py#L572)）与 `AgentStream`（[result.py#L52](../pydantic_ai_slim/pydantic_ai/result.py#L52)）

- `StreamedRunResult`：`is_complete`（[L1184](../pydantic_ai_slim/pydantic_ai/result.py#L1184)）、`result`（[L632](../pydantic_ai_slim/pydantic_ai/result.py#L632)）、`stream_output()`（[L739](../pydantic_ai_slim/pydantic_ai/result.py#L739)）、`stream_text(delta=False)`（[L764](../pydantic_ai_slim/pydantic_ai/result.py#L764)）、`stream_response()`（[L794](../pydantic_ai_slim/pydantic_ai/result.py#L794)）、`get_output()`（[L825](../pydantic_ai_slim/pydantic_ai/result.py#L825)）、`response`（[L839](../pydantic_ai_slim/pydantic_ai/result.py#L839)）、`usage`（[L869](../pydantic_ai_slim/pydantic_ai/result.py#L869)）、`all_messages` / `new_messages`、`validate_response_output`（[L912](../pydantic_ai_slim/pydantic_ai/result.py#L912)）、`cancel()`（[L942](../pydantic_ai_slim/pydantic_ai/result.py#L942)，**仅停止当前响应**）。同步变体为 `StreamedRunResultSync`（[L972](../pydantic_ai_slim/pydantic_ai/result.py#L972)）。
- `AgentStream`：底层流，提供 `stream_output`（[L90](../pydantic_ai_slim/pydantic_ai/result.py#L90)）、`stream_response`（[L119](../pydantic_ai_slim/pydantic_ai/result.py#L119)，产出 `incomplete` → `complete` / `interrupted` 快照）、`stream_text`（[L140](../pydantic_ai_slim/pydantic_ai/result.py#L140)）、`cancel`（[L174](../pydantic_ai_slim/pydantic_ai/result.py#L174)）、`drain`（[L185](../pydantic_ai_slim/pydantic_ai/result.py#L185)）、`get_output`（[L249](../pydantic_ai_slim/pydantic_ai/result.py#L249)）、`validate_response_output`（[L281](../pydantic_ai_slim/pydantic_ai/result.py#L281)）、事件迭代 `__aiter__`（[L433](../pydantic_ai_slim/pydantic_ai/result.py#L433)）。

### `FinalResult`（[result.py#L1197](../pydantic_ai_slim/pydantic_ai/result.py#L1197)）

图的标记输出，`@dataclass(repr=False)`：

```python
output: OutputDataT
tool_name: str | None = None        # 最终输出工具的 name；纯文本输出时为 None
tool_call_id: str | None = None     # 产出最终输出的 tool call id；纯文本输出时为 None
```

---

## 7. 异常体系

文件：[exceptions.py](../pydantic_ai_slim/pydantic_ai/exceptions.py)。`__all__`（[L23-L49](../pydantic_ai_slim/pydantic_ai/exceptions.py#L23-L49)）列出全部公开符号。

### 控制流异常（非错误层级）

| 异常 | 定义处 | 用途 |
|------|--------|------|
| `ModelRetry(message)` | [L52](../pydantic_ai_slim/pydantic_ai/exceptions.py#L52) | 从工具/校验器/Hook 抛出，向模型发送重试提示 |
| `ToolFailed(message)` | [L95](../pydantic_ai_slim/pydantic_ai/exceptions.py#L95) | 终态、对模型可见的工具失败，**不**消耗重试预算 |
| `CallDeferred(metadata=None)` | [L145](../pydantic_ai_slim/pydantic_ai/exceptions.py#L145) | 延迟一个工具调用（携带 `metadata`） |
| `ApprovalRequired(metadata=None)` | [L163](../pydantic_ai_slim/pydantic_ai/exceptions.py#L163) | 标记工具调用需要人工审批 |
| `SkipModelRequest(response)` | [L181](../pydantic_ai_slim/pydantic_ai/exceptions.py#L181) | 用给定 `response` 短路模型请求 |
| `SkipToolValidation(validated_args)` | [L198](../pydantic_ai_slim/pydantic_ai/exceptions.py#L198) | 用给定 args 短路校验 |
| `SkipToolExecution(result)` | [L211](../pydantic_ai_slim/pydantic_ai/exceptions.py#L211) | 用给定 result 短路执行 |

### 错误层级

```
UserError(RuntimeError)                        # 应用开发者用法错误
  UndrainedPendingMessagesError                # 遗留（不再抛出，保留兼容 except）
AgentRunError(RuntimeError)                    # 运行错误基类
  RunCancelled                                 # 应用请求的取消（携带可恢复的历史快照）
  SuspendedResponseExpired                     # 恢复超出 provider 保留窗口的挂起响应
  UsageLimitExceeded                           # 超出 UsageLimits
  ConcurrencyLimitExceeded                     # 并发队列深度超过 max_queued
  UnexpectedModelBehavior                      # 模型异常行为（含 body）
    ContentFilterError                         # provider 内容过滤器
    IncompleteToolCall                         # token 限制中断工具调用
  ModelAPIError                                # provider API 请求失败
    ModelHTTPError                             # 4xx/5xx（含 retry_after 属性）
FallbackExceptionGroup(ExceptionGroup)         # 所有 fallback 模型均失败
ToolRetryError(Exception)                       # 应发送重试 RetryPromptPart
ToolFailedError(Exception)                      # 应发送失败 ToolReturnPart
MessageHistoryMutatedWarning(Warning)           # 检测到运行历史的原地修改
```

要点：

- `RunCancelled`（[L263](../pydantic_ai_slim/pydantic_ai/exceptions.py#L263)）携带 `all_messages()` / `new_messages()` / `response` / `usage` / `metadata` / `run_id` / `conversation_id`；`from_cancellation(exc)`（[L315](../pydantic_ai_slim/pydantic_ai/exceptions.py#L315)）可从外部 `CancelledError` / `TimeoutError` / `KeyboardInterrupt` 的异常链中取出运行状态。第一方取消是终态、可捕获；外部取消保持 `CancelledError` 传播。
- `ModelHTTPError.retry_after`（[exceptions.py#L572](../pydantic_ai_slim/pydantic_ai/exceptions.py#L572)）从 `Retry-After` 头解析秒数（先整数、再 HTTP-date）。
- `UsageLimitExceeded` 在消息末尾追加固定 `_HINT`（幂等，见 [L454-L463](../pydantic_ai_slim/pydantic_ai/exceptions.py#L454-L463)）。
- `MessageHistoryMutatedWarning`（[L660](../pydantic_ai_slim/pydantic_ai/exceptions.py#L660)）在成功运行结束时尽力检测历史原地修改。

另从 [_warnings.py](../pydantic_ai_slim/pydantic_ai/_warnings.py) 重导出：`CostCalculationFailedWarning`、`CostNotFoundWarning`、`PydanticAIDeprecationWarning`、`UsageExtractionFailedWarning`。

---

## 8. End strategy 语义

`EndStrategy = Literal['early', 'graceful', 'exhaustive']`（[_agent_graph.py#L131-L162](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L131-L162)）：

- **`'early'`**：输出工具按产出顺序运行，首个成功即结束；**函数工具不执行**。仅当所有输出工具都失败时才运行函数工具（让模型下一轮修正）。若响应在函数工具调用旁携带有效结构化输出（`NativeOutput`/`PromptedOutput` 文本，或图像），该输出终结运行、函数工具被跳过。纯非结构化文本（`str` / `TextOutput`）**不会**这样抢占工具调用。
- **`'graceful'`**（默认）：工具按产出顺序运行——排在输出工具之前的函数工具先完成。输出工具按序运行，首个成功胜出，后续输出工具被跳过（副作用不执行）。若函数工具抛 `ModelRetry`，则**抑制**该输出结果、把重试抛给模型（retry-wins）。天然文本/图像输出**不会**提前结束运行。
- **`'exhaustive'`**：所有工具并行运行（`sequential=True` 工具是屏障），按产出顺序的首个有效输出成为最终结果而其余仍执行；`ModelRetry` 同样抑制输出。

纯非结构化文本（`str` / `TextOutput`）不会抢占工具调用。默认值在 v2 由 `'early'` 改为 `'graceful'`；`end_strategy='early'` 可恢复 v1 行为。三种策略分别在 [_EarlyProcessor](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L1115) / [_GracefulProcessor](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L1143) / [_ExhaustiveProcessor](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L1172) 中实现。

---

## 9. 重试与用量限制

### 重试

- **输出重试**：`GraphAgentState.consume_output_retry(max_output_retries, error=...)`（[_agent_graph.py#L445](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L445)）递增 `output_retries_used`；耗尽时先 `check_incomplete_tool_call()` 再抛 `UnexpectedModelBehavior('Exceeded maximum output retries (N)')`。文本路径由输出校验器的 `ModelRetry` 触发；工具路径由输出工具派发/空响应的 `ToolRetryError` 触发。
- **工具重试**：`ToolManager._check_max_retries(name, max_retries, error)`（[tool_manager.py#L302](../pydantic_ai_slim/pydantic_ai/tool_manager.py#L302)）；单个工具预算来自 `tool.max_retries`，回退链为 `tool.max_retries -> toolset.max_retries -> ctx.max_retries`（`default_max_retries`，默认 1）。
- **模型请求重试**：`ModelRetry` 触发 `_build_retry_node`（[_agent_graph.py#L2148](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2148)）；HTTP 传输级重试见 [retries.py](../pydantic_ai_slim/pydantic_ai/retries.py)（tenacity）。
- agent 级默认经 `Agent(retries=...)`；每次运行可 `agent.run(retries=...)` 覆盖。

### 用量限制（`UsageLimits`，[usage.py#L470-L543](../pydantic_ai_slim/pydantic_ai/usage.py#L470-L543)）

| 字段 | 默认 | 说明 |
|------|------|------|
| `request_limit` | `50` | 模型请求次数上限（请求前检查） |
| `cost_limit` | `None` | 成本上限（USD） |
| `tool_calls_limit` | `None` | 成功工具调用数上限 |
| `input_tokens_limit` / `output_tokens_limit` / `total_tokens_limit` | `None` | token 上限（累加，响应后检查） |
| `per_request_input_tokens_limit` | `None` | 单次请求输入 token 上限（累计 `input_tokens_limit` 的补充，可 `count_tokens_before_request=True` 提前强制） |
| `count_tokens_before_request` | `False` | 发送前先做一次 token 计数以提前强制输入上限 |

检查点：`check_before_request`（[_agent_graph.py#L1807](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1807)）、`_enforce_usage_limits`（[_agent_graph.py#L2133](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2133)）、`check_tokens` / `check_cost` / `check_per_request_input_tokens`（续段与流式响应迭代，[result.py#L1212-L1228](../pydantic_ai_slim/pydantic_ai/result.py#L1212)）、`check_before_tool_call`（[_tool_execution.py#L498-L502](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L498-L502)）。

---

## 10. 运行上下文构建的不变量

`build_run_context(ctx)`（[_agent_graph.py#L2714-L2760](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2714-L2760)）从 `GraphRunContext` 构造 `RunContext`，然后：

```python
validation_context = build_validation_context(ctx.deps.validation_context, run_context)
run_context = replace(run_context, validation_context=validation_context)
```

**唯一允许经 `replace` 变更的就是 `validation_context`。** 下列成员按引用共享进每个 `RunContext`，`replace` 是浅拷贝、因此身份得以保留；**绝不**把它们作为 `replace` 的 kwarg，否则会 fork 对象、静默破坏：

- `loaded_capability_ids` / `discovered_tool_names`：破坏步内能力加载与工具揭示；
- `pending_messages`：破坏消息入队；
- `_cancellation`：破坏取消；
- `_event_stream_buffer`：破坏事件投递；
- `_mcp_tool_defs_cache`：破坏 MCP tool 定义缓存；
- `_durable_operations` / `_run_capabilities_by_id`：破坏 durable 操作派发与 worker 侧恢复。

相关辅助：

- `_refresh_loaded_capability_ids` / `_refresh_discovered_tool_names`（[L2778-L2803](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2778-L2803)）就地 `clear()` + `update()`（不重新赋值），以保持所有 `RunContext` 副本同步；
- `build_validation_context`（[L2868](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2868)）在 `validation_ctx` 为 callable 时以 `run_context` 调用之；
- `_build_output_run_context`（[L2880](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2880)）在 `tool_manager.ctx` 基础上把 `retry` / `max_retries` 覆盖为**输出**预算（`max_output_retries`），供输出校验使用；
- `run_cancelled_snapshot(message, state, deps)`（[L2763](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2763)）构造携带运行状态快照的 `RunCancelled`。

取消相关的三条关键簿记（详见 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md) 的 "Cancellation Internals"）：发放计数（`RunCancellation.resolve()` 消费自己发放的取消）、`bind()` 处的重新发放（使第一方取消对吞掉/uncancel 的 hook 保持粘性）、翻译边界 `finally` 中的 `release_issued()`（防止泄漏的 `Task.cancelling()` 计数误伤后续无关工作）。

---

## 11. 代码示例

### 逐步驱动（`iter`）

```python
from pydantic_ai import Agent

agent = Agent('openai:gpt-5.2')

async def main():
    nodes = []
    async with agent.iter('What is the capital of France?') as agent_run:
        async for node in agent_run:
            nodes.append(node)
    assert agent_run.result is not None
    print(agent_run.result.output)
    #> The capital of France is Paris.
```

### 事件流（`run_stream_events`）

```python
from pydantic_ai import Agent, AgentRunResultEvent, AgentStreamEvent

agent = Agent('openai:gpt-5.2')

async def main():
    collected: list[AgentStreamEvent | AgentRunResultEvent] = []
    async with agent.run_stream_events('What is the capital of France?') as events:
        async for event in events:
            collected.append(event)
```

### 从文件构造（Agent Spec）

```python
from pydantic_ai import Agent

agent = Agent.from_file('agent.yaml')   # 需安装 pydantic-ai-slim[spec]
result = agent.run_sync('hello')
```

---

相关篇章：[01 架构总览](01-architecture-overview.md)、[03 模型/Provider/Profile](03-models-providers-profiles.md)、[04 消息协议与输出](04-messages-and-output.md)、[05 工具/Toolset/Capability](05-tools-toolsets-capabilities.md)、[06 Durable Execution](06-durable-execution.md)。
