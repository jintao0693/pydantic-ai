# 06 · Durable Execution

Durable Execution（持久化执行）让一次 Agent 运行在进程重启、失败与长时间等待后仍然存活：每一次模型调用与工具调用都成为一个「持久化单元」，其结果被记录并可在重放时复用。

相关目录：[pydantic_ai_slim/pydantic_ai/durable_exec/](../pydantic_ai_slim/pydantic_ai/durable_exec/)

> 见 `durable_exec/AGENTS.md`：这些集成被视作**核心语义的兼容性测试**，而非外围适配器；新引擎应基于公开面（`BaseDurabilityCapability` + `DurableOperationBackend`）构建。

---

## 1. 共享抽象

### 1.1 `BaseDurabilityCapability`

`durable_exec/_base.py`：所有持久化能力的基类。

- 拥有模型注册表与跨持久化边界的模型往返（只有字符串能跨越边界；未注册的 `Model` 实例会被拒绝）。
- `engine_spec: ClassVar[DurabilityEngineSpec]`；`_one_per_agent = 'durable execution engine'`（每 Agent 只允许一个）。
- 构造参数：`models`、`event_stream_handler`、`name`。
- `for_agent(agent)`：返回绑定副本及伴随能力（`WorkspaceEnsurer`）。
- `get_ordering()` → `'innermost'`（最内层）。
- `get_wrapper_toolset(toolset)`：用 `visit_and_replace` 按 `id` 把叶子 toolset 替换为其持久化包装版本。
- 抽象 `get_durable_operation_backend()`。

### 1.2 `DurabilityEngineSpec`

`durable_exec/_spec.py`：声明式引擎配置——`engine_name`、`durable_unit_noun` / `container_noun`（如 activity/workflow）、`codec`、`wrapped_toolset_kinds`、`toolset_lifecycles`、`journal_discovery`、`sequential_tools_in_durable_context`、`tool_config_key`、`cancellation_error_types` 等。

引擎分为两类：**对象传递型**（Temporal/DBOS/Prefect → `IDENTITY_CODEC`）与 **JSON 日志型**（Restate/Lambda/Absurd → `JSON_CODEC`）。

### 1.3 `DurableOperation`

`durable_exec/_operation.py`：语义声明（`operation_id`、`handler`、`parameter_transport`、`cache_identity`、`result_codec`、`config_role`、`invocation_label`）。`DurableOperationId` 是可扩展的联合：`ModelRequestId`、`ModelCancelSuspendedResponseId`、`ModelCompactMessagesId`、`CapabilityOperationId`、`EventStreamHandlerId`、`ToolsetGetToolsId`、`ToolsetGetInstructionsId`、`ToolsetValidateToolArgumentsId`、`ToolsetCallToolId`。协议：`ParameterTransport`、`CacheIdentity`、`ResultCodec`。

### 1.4 后端与命名

- `_operation_backend.py`：`DurableOperationBackend(ABC)`、`CallableOperationBackend`（在命名持久化单元中执行异步回调）、`JournalCallableOperationBackend`、`RegisteredOperationBackend`。
- `_operation_names.py`：`DurableOperationNamer` 协议与 `JournalOperationNamer`——名字是**持久化的兼容数据，基本不可更改**。
- `_codec.py`：`DurabilityCodec` 协议；`IDENTITY_CODEC` / `JSON_CODEC`。

### 1.5 Toolset 脚手架

`_toolset.py`：`DurableToolsetBase`、`DurableFunctionToolset`、`DurableDynamicToolset`、`DurableMCPToolset`；`RunHeldToolset`；`guard_run_context` / `EnqueueGuard` / `CancelGuard`；`Lifecycle` 字面量（`enter-outside-durable` / `enter-always` / `enter-never` / `enter-in-durable-unit`）。

### 1.6 Capability 操作

`_capability_operation.py`：`@durable_operation(name)` 装饰器把异步 capability 方法声明为持久化操作；`collect_capability_operations`；`CapabilityOperationParams` / `CapabilityOperationResult`。

### 1.7 Workspace 与 Codec

`_workspace.py`：`DurableWorkspace`，`WORKSPACE_OPERATION_ID`；workspace 调用按次记录，`ensure` 每运行一次。

---

## 2. 引擎适配

| 引擎 | 模块 | 能力类 | durable 单元 / 容器 | codec | 包装的 toolset 种类 | `in_durable_context` |
|------|------|--------|---------------------|-------|---------------------|----------------------|
| **Temporal** | `temporal/_durability.py` | `TemporalDurability` | activity / workflow | `IDENTITY_CODEC` | function, mcp, dynamic | `workflow.in_workflow()` |
| **DBOS** | `dbos/_durability.py` | `DBOSDurability` | step / workflow | `IDENTITY_CODEC` | mcp, dynamic（函数工具内联为 `@DBOS.step`） | `DBOS.workflow_id is not None and DBOS.step_id is None` |
| **Prefect** | `prefect/_durability.py` | `PrefectDurability` | task / flow | `IDENTITY_CODEC` | function, mcp, dynamic | `FlowRunContext.get() is not None` |

各引擎的差异（见各自 `_durability.py`）：

- **Temporal**：`toolset_lifecycles` 为 `function: enter-outside-durable`、`mcp: enter-outside-durable`、`dynamic: enter-never`，`tool_config_key='temporal'`。向 worker 注册 activities（`PydanticAIPlugin` / `AgentPlugin`）；支持通过 Workflow Stream 做工作流外事件流（`stream_agent_events`）；拒绝图像输出（activity payload 大小）；追加 `_TerminalEventPublisher`。
- **DBOS**：`tool_config_key` 无；`toolset_lifecycles` 为 `mcp: enter-in-durable-unit`、`dynamic: enter-never`；`tool_call_result_upgrade_lenient=True`；`cancellation_error_types=(DBOSWorkflowCancelledError, DBOSWorkflowConflictIDError)`；含 `register_legacy_workflows` 用于迁移；有 workspace 时 `wrap_run` 强制并行执行模式为 `'sequential'`。
- **Prefect**：`tool_config_key='prefect'`；`toolset_lifecycles` 为 `function: enter-always`、`mcp: enter-in-durable-unit`、`dynamic: enter-never`；`tool_call_result_upgrade_lenient=True`。采用 hash-keyed（需 per-container 序列支持事件重放），而其他引擎为 sequence-keyed。

### Temporal 子包文件

`temporal/` 含 `_agent.py`、`_model.py`、`_toolset.py`、`_function_toolset.py`、`_dynamic_toolset.py`、`_mcp_toolset.py`、`_workflow.py`（`PydanticAIWorkflow`）、`_run_context.py`、`_event_stream.py`、`_payload_converter.py`、`_operation_backend.py`、`_durability.py`、`_logfire.py`、`_replay_safe_tracer_provider.py`、`_transports.py`。

DBOS 子包：`_agent.py`、`_model.py`、`_mcp_toolset.py`、`_operation_backend.py`、`_durability.py`、`_utils.py`、`_operation_names.py`。

Prefect 子包：`_agent.py`、`_model.py`、`_toolset.py`、`_function_toolset.py`、`_dynamic_toolset.py`、`_mcp_toolset.py`、`_operation_backend.py`、`_cache_policies.py`、`_types.py`、`_durability.py`、`_operation_names.py`。

---

## 3. 使用示例（Temporal）

```python
from temporalio import workflow

from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow, TemporalDurability

agent = Agent(
    'openai:gpt-6-sol',
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

安装：`uv add "pydantic-ai[temporal]"`。DBOS / Prefect 以同样方式附加（`pydantic-ai[dbos]` / `pydantic-ai[prefect]`）。

---

## 4. 重要约束

- **事件流缓冲区**：`GraphAgentState.event_stream_buffer` 是运行作用域的框架事件队列。在 **Temporal** 下，从工具或事件流处理函数 `emit` 会报错（它们在 activity 中运行，无法访问缓冲区）。在 DBOS/Prefect 下缓冲区在进程内可用，但在持久化单元内 `emit` 是执行副作用——**重放步骤或缓存任务不会再次 emit**。
- **durable 名字不可随意更改**：`@durable_operation(name=...)` 与 `JournalOperationNamer` 的输出是持久化兼容数据。
- **能力需能跨越持久化边界**：capability 在工具调用时从 `RunContext` 读取的任何内容都必须可序列化或随行携带。
