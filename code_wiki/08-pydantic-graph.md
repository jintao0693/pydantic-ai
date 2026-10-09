# 08 · Pydantic Graph

`pydantic-graph` 是类型提示驱动的图/状态机库，也是 **Pydantic AI Agent 循环的执行引擎**。它作为独立的叶子依赖存在（依赖 `anyio`、`logfire-api`、`pydantic`、`typing-inspection`）。

源代码目录：[pydantic_graph/pydantic_graph/](../pydantic_graph/pydantic_graph/)

---

## 1. 两种建模方式

Graph 支持两种等价入口，可混用：

1. **声明式节点**：子类化 `BaseNode`，实现 `async run(ctx) -> BaseNode | End`；返回类型提示在运行时被读取以确定/校验边。
2. **构建器 API**：用 `GraphBuilder` 以 `step` / `decision` / `join` / `map` 组合图。

Pydantic AI 的 Agent 循环使用**构建器 + `BaseNode` 子类**的组合（`GraphBuilder.node(UserPromptNode)` 等）。

---

## 2. 公共 API（`__init__.py`）

- 声明式：`BaseNode`、`End`、`GraphRunContext`、`Edge`。
- 构建/运行：`GraphBuilder`、`Graph`、`GraphRun`、`GraphTask`、`GraphTaskRequest`、`EndMarker`、`ErrorMarker`、`JoinItem`。
- 步骤/决策/汇合/拓扑节点：`Step`、`StepContext`、`StepNode`、`StartNode`、`EndNode`、`Fork`、`Decision`、`Join`、`JoinNode`、`ReducerContext`、`ReducerFunction`、`ReduceFirstValue`，以及 reducer `reduce_dict_update` / `reduce_list_append` / `reduce_list_extend` / `reduce_null` / `reduce_sum`、`TypeExpression`。
- 错误：`GraphSetupError`、`GraphRuntimeError`。
- 其他异常（`exceptions.py`）：`GraphBuildingError`、`GraphValidationError`、`UnsupportedEventLoopError`。

---

## 3. 关键类型

### 3.1 `basenode.py`

- `GraphRunContext(Generic[StateT, DepsT])`：dataclass(kw_only)，字段 `state`、`deps`——传给每个节点的上下文。
- `BaseNode(ABC, Generic[StateT, DepsT, NodeRunEndT])`：抽象 `async run(ctx) -> BaseNode | End`；`get_node_id()` 返回 `cls.__name__`。
- `End(Generic[RunEndT])`：终止哨兵，携带 `.data`。
- `Edge`：冻结 dataclass，作为边的 `label` 注解。

### 3.2 `step.py`（步骤 API）

- `StepContext`：携带 `state`、`deps`、`inputs`。
- `StepFunction` / `StreamFunction` 协议。
- `Step`：包装 `id`、`call`、`label`；`as_node(inputs)` → `StepNode`。
- `StepNode` / `NodeStep`：把 `BaseNode` 类型接入构建器。

### 3.3 `node.py` / `node_types.py`（拓扑）

- `StartNode`（id `'__start__'`）、`EndNode`（id `'__end__'`）。
- `Fork`：`is_map`（逐元素映射 vs 广播）、`downstream_join_id`。
- 类型别名：`MiddleNode = Step | Join | Fork`、`SourceNode`、`DestinationNode`、`AnyNode`；守卫 `is_source` / `is_destination`。

### 3.4 `decision.py`（条件分支）

- `Decision`：`id`、`branches: list[DecisionBranch]`、`note`；`branch(...)` 追加分支。
- `DecisionBranch`：`source`（匹配类型）、`matches`（谓词）、`path`、`destinations`。
- `DecisionBranchBuilder`：`GraphBuilder.match` 创建的流式构建器（`to` / `broadcast` / `transform` / `map` / `label`）。

### 3.5 `join.py`（并行汇合）

- `JoinState` / `ReducerContext`（提供 `cancel_sibling_tasks()` 以支持提前停止）。
- `ReducerFunction`：`(current, inputs) -> output` 或 `(ctx, current, inputs) -> output`。
- 内置 reducer 与 `ReduceFirstValue`（取消兄弟任务，保留首个）。
- `Join` / `JoinNode`：`parent_fork_id`、`preferred_parent_fork`（`'farthest'` / `'closest'`）。

### 3.6 `paths.py`（边/变换）

- 标记：`TransformMarker`、`MapMarker`、`BroadcastMarker`、`LabelMarker`、`DestinationMarker`。
- `Path` / `PathBuilder` / `EdgePath` / `EdgePathBuilder`。

---

## 4. 构建与运行

### 4.1 `GraphBuilder`（`graph_builder.py`）

字段：`name`、state/deps/input/output 类型、`auto_instrument`、内部节点/边表。方法：

- `start_node` / `end_node` 属性。
- `step(...)` / `stream(...)`：创建 `Step`。
- `join(reducer, *, initial | initial_factory, node_id, parent_fork_id, preferred_parent_fork)` → `Join`。
- `add(*edges)`、`add_edge(source, destination)`、`add_mapping_edge(...)`、`edge_from(*sources)`。
- `decision(...)`、`match(...)`、`match_node(...)`、`node(node_type)`（接入 `BaseNode` 子类，从其 `run` 的返回提示推断边，含 `End`、`StepNode`、`JoinNode`、union 返回时构建 `Decision`）。
- `build(validate_graph_structure=True) -> Graph`：替换占位 id、展平 path、规范化 fork、校验结构、收集 parent fork 与中间 join 节点。

模块 helper：`_validate_graph_structure`、`_replace_placeholder_node_ids`、`_flatten_paths`、`_normalize_forks`、`_collect_dominating_forks`、`_compute_intermediate_join_nodes`；Mermaid 渲染 helper。

### 4.2 `Graph`

dataclass，字段 `name`、`state_type`、`deps_type`、`input_type`、`output_type`、`auto_instrument`、`nodes`、`edges_by_source`、`parent_forks`、`intermediate_join_nodes`。方法：

- `async run(*, state, deps, inputs, span, infer_name) -> OutputT`：主入口，循环 `graph_run.next()` 直到 `EndMarker`。
- `run_sync(...)`：经 `_utils.run_until_complete` 的同步封装。
- `iter(...)`（asynccontextmanager）：产出可逐步执行的 `GraphRun`；创建自动 instrumentation span。
- `render(...)` → Mermaid 字符串；`__repr__` / `__str__` 也渲染 Mermaid。
- `get_parent_fork(join_id)`、`is_final_join(join_id)`。

### 4.3 `GraphRun`

管理单次执行：

- `__aenter__` / `__aexit__`（task group + 迭代器清理）、`__aiter__` / `__anext__`、`next(value)`（推进一步）、`override_next(value)`（在 `End` 或错误后重定向，供 Hook 系统使用）、`next_task`、`output`。
- 内部 `_GraphIterator` 执行调度与 fork-join 协调（`iter_graph`、`_run_task`、`_handle_decision`、`_handle_path`、`_handle_fork_edges`、`_cancel_sibling_tasks`），使用 AnyIO task group + memory-object stream。

---

## 5. 状态持久化

**`pydantic_graph` 自身不提供状态序列化/持久化。** 状态由调用方通过 `state` / `deps` 对象传入并持有；持久化是调用方的责任（Pydantic AI 的 durable execution 集成在其之外做检查点）。

---

## 6. 与 Pydantic AI 的关系

`pydantic_ai/_agent_graph.py` 导入 `BaseNode, End, Graph, GraphBuilder, GraphRunContext`：

- `build_agent_graph(...)`（缓存 `_build_agent_graph`）构建 `GraphBuilder(name=..., state_type=GraphAgentState, deps_type=GraphAgentDeps, input_type=UserPromptNode, output_type=FinalResult, auto_instrument=False)`，注册节点与边后 `g.build(validate_graph_structure=False)`。
- 节点 `AgentNode` / `UserPromptNode` / `ModelRequestNode` / `CallToolsNode` / `SetFinalResult` 均为 `BaseNode` 子类，`run(ctx: GraphRunContext[GraphAgentState, GraphAgentDeps[...]])` 返回下一个节点或 `End(FinalResult(...))`。
- `agent/__init__.py` 调用 `_agent_graph.build_agent_graph(...)`，运行经 `self.graph.iter(...)`。

因此：**`pydantic_graph` 是执行引擎，`pydantic_ai` 提供节点、状态（`GraphAgentState`）与依赖（`GraphAgentDeps`）**。
