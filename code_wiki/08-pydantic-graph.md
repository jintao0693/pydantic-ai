# 08 · Pydantic Graph

`pydantic-graph` 是类型提示驱动的图/状态机库，也是 **Pydantic AI Agent 循环的执行引擎**。它作为独立的叶子依赖存在，不依赖 `pydantic-ai`。

源代码目录：[`pydantic_graph/pydantic_graph/`](../pydantic_graph/pydantic_graph/)

依赖（[`pydantic_graph/pyproject.toml`](../pydantic_graph/pyproject.toml)）：`anyio>=4.7.0`、`logfire-api>=3.14.1`、`pydantic>=2.12`、`typing-inspection>=0.4.0`；`requires-python >= 3.11`。

> 注意：**没有 `graph.py`**。`Graph` / `GraphRun` / `GraphTask` / `GraphTaskRequest` / `EndMarker` / `ErrorMarker` / `JoinItem` 都定义在 [`graph_builder.py`](../pydantic_graph/pydantic_graph/graph_builder.py) 里；`__init__.py` 只是从各处汇总导出。

---

## 1. 两种建模方式

Graph 支持两种等价入口，可混用：

1. **声明式节点**：子类化 [`BaseNode`](../pydantic_graph/pydantic_graph/basenode.py)，实现 `async run(ctx) -> BaseNode | End`。`run` 的**返回类型提示**在运行时被读取，用来推断边并在运行时校验。
2. **构建器 API**：用 [`GraphBuilder`](../pydantic_graph/pydantic_graph/graph_builder.py) 以 `step` / `stream` / `decision` / `match` / `join` / `map` 组合图。

两者通过 `StepNode` / `JoinNode` / `NodeStep` 桥接：

- `BaseNode.run` 返回 `StepNode`（`my_step.as_node(inputs)`）或 `JoinNode`（`my_join.as_node(inputs)`）即可把控制权交给构建器节点；
- `GraphBuilder.add` 也会读取**普通 `Step` 函数**的返回提示，若返回的是 `BaseNode` 子类或 `End`，自动建边（经 `_edge_from_return_hint`）。因此「构建器步骤返回声明式节点」同样成立。

Pydantic AI 的 Agent 循环使用**构建器 + `BaseNode` 子类**的组合（`g.edge_from(g.start_node).to(UserPromptNode)` 加 `g.node(...)`）。

---

## 2. 公共 API（[`__init__.py`](../pydantic_graph/pydantic_graph/__init__.py)）

```python
from .basenode import BaseNode, Edge, End, GraphRunContext
from .decision import Decision
from .exceptions import GraphRuntimeError, GraphSetupError
from .graph_builder import (EndMarker, ErrorMarker, Graph, GraphBuilder,
                            GraphRun, GraphTask, GraphTaskRequest, JoinItem)
from .join import (Join, JoinNode, ReduceFirstValue, ReducerContext, ReducerFunction,
                   reduce_dict_update, reduce_list_append, reduce_list_extend,
                   reduce_null, reduce_sum)
from .node import EndNode, Fork, StartNode
from .step import Step, StepContext, StepNode
from .util import TypeExpression
```

`__all__` 即上述名字（另加 `GraphSetupError` / `GraphRuntimeError`）。

未被顶层导出、但可自行导入的类型：

- [`step.py`](../pydantic_graph/pydantic_graph/step.py)：`NodeStep`、`StepFunction`、`StreamFunction`、`AnyStepFunction`
- [`node_types.py`](../pydantic_graph/pydantic_graph/node_types.py)：`MiddleNode`、`SourceNode`、`DestinationNode`、`AnyNode`、`is_source`、`is_destination`
- [`paths.py`](../pydantic_graph/pydantic_graph/paths.py)：`Path`、`PathBuilder`、`EdgePath`、`EdgePathBuilder`、`TransformFunction`、`TransformMarker`、`MapMarker`、`BroadcastMarker`、`LabelMarker`、`DestinationMarker`、`PathItem`
- [`decision.py`](../pydantic_graph/pydantic_graph/decision.py)：`DecisionBranch`、`DecisionBranchBuilder`
- [`join.py`](../pydantic_graph/pydantic_graph/join.py)：`JoinState`、`SupportsSum`
- [`id_types.py`](../pydantic_graph/pydantic_graph/id_types.py)：`NodeID`、`NodeRunID`、`JoinID`、`ForkID`、`TaskID`、`ForkStackItem`、`ForkStack`、`generate_placeholder_node_id`、`replace_placeholder_id`
- [`parent_forks.py`](../pydantic_graph/pydantic_graph/parent_forks.py)：`ParentFork`、`ParentForkFinder`
- [`exceptions.py`](../pydantic_graph/pydantic_graph/exceptions.py)：`GraphBuildingError`、`GraphValidationError`、`UnsupportedEventLoopError`

---

## 3. 核心类型（`basenode.py`）

```python
StateT  = TypeVar('StateT', default=object)
RunEndT = TypeVar('RunEndT', covariant=True, default=object)
NodeRunEndT = TypeVar('NodeRunEndT', covariant=True, default=Never)
DepsT   = TypeVar('DepsT', default=object, contravariant=True)

@dataclass(kw_only=True)
class GraphRunContext(Generic[StateT, DepsT]):
    state: StateT
    deps: DepsT

class BaseNode(ABC, Generic[StateT, DepsT, NodeRunEndT]):
    @abstractmethod
    async def run(self, ctx: GraphRunContext[StateT, DepsT]) -> BaseNode[StateT, DepsT, Any] | End[NodeRunEndT]: ...
    @classmethod
    @cache
    def get_node_id(cls) -> str:
        return cls.__name__

@dataclass
class End(Generic[RunEndT]):
    data: RunEndT

@dataclass(frozen=True)
class Edge:
    label: str | None
```

| 类型 | 角色 |
|------|------|
| `GraphRunContext(state, deps)` | 传给每个节点 `run` 的上下文 |
| `BaseNode` | 节点基类；抽象 `run`；`get_node_id()`（`@cache`）返回 `cls.__name__` |
| `End(data)` | 终止哨兵，返回它以结束图 |
| `Edge(label)` | 冻结 dataclass，用作边的 `Annotated` 标签注解 |

`Edge` 只影响 Mermaid 渲染标签，不影响拓扑。

---

## 4. 步骤 API（[`step.py`](../pydantic_graph/pydantic_graph/step.py)）

```python
@dataclass(init=False)
class StepContext(Generic[StateT, DepsT, InputT]):
    _state: StateT; _deps: DepsT; _inputs: InputT
    def __init__(self, *, state, deps, inputs): ...
    @property
    def state(self) -> StateT: ...
    @property
    def deps(self) -> DepsT: ...
    @property
    def inputs(self) -> InputT: ...   # 必须是 property，以保证正确的方差行为
```

`inputs` 用 property 而非字段是为了类型方差；用户只读这三个属性。

协议：

```python
class StepFunction(Protocol[StateT, DepsT, InputT, OutputT]):
    def __call__(self, ctx: StepContext[StateT, DepsT, InputT]) -> Awaitable[OutputT]: ...

class StreamFunction(Protocol[StateT, DepsT, InputT, OutputT]):
    def __call__(self, ctx: StepContext[StateT, DepsT, InputT]) -> AsyncIterator[OutputT]: ...

AnyStepFunction = StepFunction[Any, Any, Any, Any]
```

`Step`：

```python
@dataclass(init=False)
class Step(Generic[StateT, DepsT, InputT, OutputT]):
    id: NodeID
    _call: StepFunction[StateT, DepsT, InputT, OutputT]   # 通过 .call property 暴露
    label: str | None
    def as_node(self, inputs: InputT | None = None) -> StepNode[StateT, DepsT]: ...
```

`StepNode` / `NodeStep`：

- `StepNode(step, inputs)`：`BaseNode` 子类，包装一个 `Step` 与其绑定的输入。`run` 恒抛 `NotImplementedError`——它不是用来直接运行的，而是让 `BaseNode.run` 通过返回它来指示「下一步交给这个构建器步骤」。
- `NodeStep(Step)`：把一个 `BaseNode` **类型**接入构建器。`__init__(node_type, *, id=None, label=None)` 用 `id or NodeID(node_type.get_node_id())` 作 id；`node_type` 经 `get_origin(...)` 解包（可能传入 `typing._GenericAlias`）。`_call_node` 校验 `ctx.inputs` 是对应实例后 `node.run(GraphRunContext(state, deps))`。

---

## 5. 拓扑节点（[`node.py`](../pydantic_graph/pydantic_graph/node.py)）

```python
class StartNode(Generic[OutputT]):
    id = NodeID('__start__')

class EndNode(Generic[InputT]):
    id = NodeID('__end__')

@dataclass
class Fork(Generic[InputT, OutputT]):
    id: ForkID
    is_map: bool                       # True：逐元素 map；False：广播同一份数据
    downstream_join_id: JoinID | None  # map 空可迭代时跳转到的下游 join
```

`StartNode` / `EndNode` 的 `_force_variance` 方法仅用于类型检查，调用会抛 `RuntimeError`。

**节点分类**（[`node_types.py`](../pydantic_graph/pydantic_graph/node_types.py)）：

| 类型别名 | 定义 |
|----------|------|
| `MiddleNode` | `Step | Join | Fork` |
| `SourceNode` | `MiddleNode | StartNode` |
| `DestinationNode` | `MiddleNode | Decision | EndNode` |
| `AnySourceNode` / `AnyDestinationNode` / `AnyNode` | 各自带 `Any` 参数化的版本 |

守卫：`is_source(node)` → `isinstance(node, StartNode | Step | Join)`；`is_destination(node)` → `isinstance(node, EndNode | Step | Join | Decision)`。

---

## 6. 条件分支（[`decision.py`](../pydantic_graph/pydantic_graph/decision.py)）

```python
@dataclass(kw_only=True)
class Decision(Generic[StateT, DepsT, HandledT]):
    id: NodeID
    branches: list[DecisionBranch[Any]]
    note: str | None
    def branch(self, branch: DecisionBranch[T]) -> Decision[StateT, DepsT, HandledT | T]: ...

@dataclass
class DecisionBranch(Generic[SourceT]):
    source: TypeOrTypeExpression[SourceT]
    matches: Callable[[Any], bool] | None
    path: Path
    destinations: list[AnyDestinationNode]
```

`DecisionBranch.source` 用于穷尽性检查；`matches` 为 `None` 时用默认匹配逻辑（运行时见 §9 `_handle_decision`）：

- `source` 为 `Any` 或 `object` → 总匹配；
- `source` 是 `Literal` → 值属于其参数集合时匹配；
- 其他类型 → `isinstance(inputs, source)`。

**`DecisionBranchBuilder`**（由 `GraphBuilder.match` 创建，不直接实例化）：

| 方法 | 说明 |
|------|------|
| `to(destination, *extra_destinations, fork_id=None)` | 指定目标（多个则建广播 fork）；类会被包成 `NodeStep` |
| `broadcast(get_forks, *, fork_id=None)` | 把分支展开为多个 `DecisionBranch` 路径 |
| `transform(func)` | 在路径中插入 `TransformFunction`（同步） |
| `map(*, fork_id=None, downstream_join_id=None)` | 展开可迭代输出为并行路径 |
| `label(label)` | 给当前路径点加 Mermaid 标签 |

---

## 7. 并行汇合（[`join.py`](../pydantic_graph/pydantic_graph/join.py)）

```python
@dataclass
class JoinState:
    current: Any
    downstream_fork_stack: ForkStack
    cancelled_sibling_tasks: bool = False

@dataclass(init=False)
class ReducerContext(Generic[StateT, DepsT]):
    def __init__(self, *, state, deps, join_state): ...
    @property
    def state(self) -> StateT: ...
    @property
    def deps(self) -> DepsT: ...
    def cancel_sibling_tasks(self):  # 提前停止
        self._join_state.cancelled_sibling_tasks = True
```

**Reducer 签名**（`Join.reduce` 用 `inspect.signature(reducer).parameters` 判定参数个数：2 → plain，否则 context）：

```python
PlainReducerFunction   = Callable[[OutputT, InputT], OutputT]
ContextReducerFunction = Callable[[ReducerContext[StateT, DepsT], OutputT, InputT], OutputT]
ReducerFunction        = ContextReducerFunction | PlainReducerFunction
```

**内置 reducer**：

| 函数 | 行为 |
|------|------|
| `reduce_null(current, inputs) -> None` | 丢弃所有输入，返回 `None` |
| `reduce_list_append(current, inputs)` | `current.append(inputs)` |
| `reduce_list_extend(current, inputs)` | `current.extend(inputs)` |
| `reduce_dict_update(current, inputs)` | `current.update(inputs)` |
| `reduce_sum(current, inputs)` | `current + inputs`（要求 `SupportsSum`） |
| `ReduceFirstValue()`（dataclass） | `__call__(ctx, current, inputs)`：`ctx.cancel_sibling_tasks()` 后返回 `inputs`（保留首个） |

**`Join` / `JoinNode`**：

```python
@dataclass(init=False)
class Join(Generic[StateT, DepsT, InputT, OutputT]):
    id: JoinID
    _reducer: ReducerFunction[...]          # .reducer property
    _initial_factory: Callable[[], OutputT] # .initial_factory property
    parent_fork_id: ForkID | None
    preferred_parent_fork: Literal['closest', 'farthest']
    def reduce(self, ctx, current, inputs) -> OutputT: ...
    def as_node(self, inputs: InputT | None = None) -> JoinNode[StateT, DepsT]: ...

@dataclass
class JoinNode(BaseNode[StateT, DepsT, Any]):
    join: Join[StateT, DepsT, Any, Any]
    inputs: Any
    async def run(self, ctx) -> ...:  # 恒抛 NotImplementedError
```

`preferred_parent_fork` 只在存在多个候选支配 fork 时生效（见 §11）。

---

## 8. 路径与边（[`paths.py`](../pydantic_graph/pydantic_graph/paths.py)）

**标记（marker）**：

| 标记 | 字段 | 含义 |
|------|------|------|
| `TransformMarker` | `transform: TransformFunction` | 路径中的同步变换 |
| `MapMarker` | `fork_id: ForkID`、`downstream_join_id: JoinID \| None` | 逐元素展开为并行 |
| `BroadcastMarker` | `paths: Sequence[Path]`、`fork_id: ForkID` | 广播到多条并行路径 |
| `LabelMarker` | `label: str` | Mermaid 标签 |
| `DestinationMarker` | `destination_id: NodeID` | 路径终点 |

`PathItem` 是上述五者的 union。

```python
@dataclass
class Path:
    items: list[PathItem]
    @property
    def last_fork(self) -> BroadcastMarker | MapMarker | None: ...
    @property
    def next_path(self) -> Path: ...   # 去掉首项
```

`PathBuilder`（流式构造路径）：

- `to(destination, *extra_destinations, fork_id=None)` → `Path`（多个目标时建 `BroadcastMarker`）；
- `broadcast(forks, *, fork_id=None)` → `Path`；
- `transform(func)` → 追加 `TransformMarker`；
- `map(*, fork_id=None, downstream_join_id=None)` → 追加 `MapMarker`；
- `label(label)` → 追加 `LabelMarker`。

`EdgePath(sources, path, destinations)` 是一条完整边；`EdgePathBuilder(sources, path_builder)` 是其流式构造器，方法同上（`to` / `broadcast` / `map` / `transform` / `label`）。注意 `EdgePathBuilder.map` 在 `len(sources) > 1` 时抛 `NotImplementedError`（多源 map 会重复执行）。

`TransformFunction` 与 `StepFunction` 类似，但**必须同步**（`def`，非 `async def`）。

---

## 9. `GraphBuilder`（[`graph_builder.py`](../pydantic_graph/pydantic_graph/graph_builder.py)）

```python
@dataclass(init=False)
class GraphBuilder(Generic[StateT, DepsT, GraphInputT, GraphOutputT]):
    name: str | None
    state_type / deps_type / input_type / output_type: TypeOrTypeExpression[...]
    auto_instrument: bool
```

构造：`GraphBuilder(*, name=None, state_type=type(None), deps_type=type(None), input_type=type(None), output_type=type(None), auto_instrument=True)`。内部持有 `_nodes`、`_edges_by_source`、`_decision_index`，以及自建 `StartNode[GraphInputT]` / `EndNode[GraphOutputT]`。

**嵌套类型别名**：`GraphBuilder.Source[OutputT] = SourceNode[...]`、`GraphBuilder.Destination[InputT] = DestinationNode[...]`。

**方法全表**

| 方法 | 说明 |
|------|------|
| `start_node` / `end_node`（property） | 起始/结束节点 |
| `step(call=None, *, node_id=None, label=None)` | 装饰器或直接调用，创建 `Step`；id 默认 `get_callable_name(call)` |
| `stream(call=None, *, node_id=None, label=None)` | 把返回 async iterator 的函数包成 `Step`（内部包一层 `await`） |
| `join(reducer, *, initial=UNSET, initial_factory=UNSET, node_id=None, parent_fork_id=None, preferred_parent_fork='farthest')` | 创建 `Join`；`initial_factory` 缺省时用 `lambda: initial` |
| `add(*edges)` | 加入边；自动为广播/映射创建 `Fork`；自动从 step 返回提示建边 |
| `add_edge(source, destination, *, label=None)` | 简单边 |
| `add_mapping_edge(source, map_to, *, pre_map_label=None, post_map_label=None, fork_id=None, downstream_join_id=None)` | map 边 |
| `edge_from(*sources)` | 返回 `EdgePathBuilder` |
| `decision(*, note=None, node_id=None)` | 新建无分支 `Decision` |
| `match(source, *, matches=None)` | 返回 `DecisionBranchBuilder` |
| `match_node(source, *, matches=None)` | 为 `BaseNode` 子类建 `DecisionBranch` |
| `node(node_type)` | 从 `BaseNode` 类建 `EdgePath`（读取 `run` 返回提示） |
| `build(validate_graph_structure=True)` | 生成 `Graph` |

**`add` 的自动建边规则**（重要）

`add` 遍历新增节点；对**非 `NodeStep` 的普通 `Step`**，读其 `call` 的返回类型提示，若可推断出边（返回 `BaseNode` 子类 / `End` / `StepNode` / `JoinNode`）则递归 `add(edge)`。因此：

- step 返回 `A | B`（都是 `BaseNode` 子类）→ 自动构建一个 `Decision`（分支用 `match(NoneType)`，仅用于 parent-fork 查找）；
- step 返回单节点 → 单条边；
- 返回非节点类型 → 不建边，需手工 `add`。

`_insert_node` 校验 id 唯一（同一对 `NodeStep` 同 `node_type` 例外）。

**`node(node_type)` 的返回提示推断**（`_edge_from_return_hint`）

`get_type_hints(node_type.run, localns=get_parent_namespace(...))` 取 `return`，对 union 每个成员（先 `unpack_annotated`）判断：

| 返回类型 | 处理 |
|----------|------|
| `End` | 目标为 `end_node` |
| 裸 `BaseNode` | 抛 `GraphSetupError`（要求显式列出每个子类） |
| `StepNode` | 从 `Annotated[...]` 中找 `Step` 注解；缺失则抛 `GraphSetupError` |
| `JoinNode` | 从 `Annotated[...]` 中找 `Join` 注解；缺失则抛 `GraphSetupError` |
| `BaseNode` 子类 | `NodeStep(return_type)` |

若 union 中**有任一成员不是节点**，整条边不创建（返回 `None`）。多个目标时构建 `Decision`。

**`build` 流水线**

```python
nodes, edges = _replace_placeholder_node_ids(nodes, edges)
nodes, edges = _flatten_paths(nodes, edges)
nodes, edges = _normalize_forks(nodes, edges)
if validate_graph_structure:
    _validate_graph_structure(nodes, edges)
parent_forks = _collect_dominating_forks(nodes, edges)
intermediate_join_nodes = _compute_intermediate_join_nodes(nodes, parent_forks)
return Graph(name, state_type, deps_type, input_type, output_type,
             nodes, edges, parent_forks, intermediate_join_nodes, auto_instrument)
```

各步骤：

| helper | 作用 |
|--------|------|
| `_replace_placeholder_node_ids` | 把 `__placeholder__:label:uuid` 形式的 id 换成稳定的 `label` / `label_2`… |
| `_flatten_paths` | 把路径在第一个 fork 处切分：上游以 `DestinationMarker(fork_id)` 结尾，下游路径挂到 fork 下 |
| `_normalize_forks` | 保证只有广播 fork 拥有多条出边；给多出边节点补 `{id}_broadcast_fork` |
| `_validate_graph_structure` | 5 项结构校验（见 §14） |
| `_collect_dominating_forks` | 为每个 `Join` 找支配 fork（`ParentForkFinder`） |
| `_compute_intermediate_join_nodes` | 计算每个 join 的「中间 join」集合，用于判定「final join」 |

---

## 10. `Graph`（[`graph_builder.py`](../pydantic_graph/pydantic_graph/graph_builder.py)）

```python
@dataclass(repr=False)
class Graph(Generic[StateT, DepsT, InputT, OutputT]):
    name: str | None
    state_type: type[StateT]
    deps_type: type[DepsT]
    input_type: type[InputT]
    output_type: type[OutputT]
    auto_instrument: bool
    nodes: dict[NodeID, AnyNode]
    edges_by_source: dict[NodeID, list[Path]]
    parent_forks: dict[JoinID, ParentFork[NodeID]]
    intermediate_join_nodes: dict[JoinID, set[JoinID]]
```

| 方法 | 说明 |
|------|------|
| `get_parent_fork(join_id)` | 返回该 join 的 `ParentFork`；缺失抛 `RuntimeError` |
| `is_final_join(join_id)` | 若该 join 出现在任何其他 join 的 `intermediate_join_nodes` 中则不 final |
| `async run(*, state=None, deps=None, inputs=None, span=None, infer_name=True) -> OutputT` | 主入口；`infer_name` 时用 `infer_obj_name(self, depth=2)` 推断名字；循环 `graph_run.next(event)` 直到 `StopAsyncIteration`，返回 `EndMarker.value` |
| `run_sync(*, state, deps, inputs, span, infer_name)` | 经 [`_utils.run_until_complete`](../pydantic_graph/pydantic_graph/_utils.py) 的同步封装；事件循环不支持 `run_until_complete()` 时抛 `UnsupportedEventLoopError` |
| `iter(*, state, deps, inputs, span, infer_name)` | `@asynccontextmanager`；产出可逐步执行的 `GraphRun`；`infer_name` 用 `depth=3`（`asynccontextmanager` 多一帧）；`auto_instrument` 时进 `run graph {name}` span 并通过 span 取得 `traceparent` |
| `render(*, title=None, direction=None)` | Mermaid 字符串 |
| `__repr__` / `__str__` | 也渲染 Mermaid |

---

## 11. `GraphRun` 与内部驱动

**`GraphRun`**（[`graph_builder.py`](../pydantic_graph/pydantic_graph/graph_builder.py)）：

```python
class GraphRun(Generic[StateT, DepsT, OutputT]):
    def __init__(self, graph, *, state, deps, inputs, traceparent): ...
    graph; state; deps; inputs
```

构造时即创建第一个 `GraphTask`（`node_id=StartNode.id`，初始 fork 栈含 `ForkStackItem(StartNode.id, node_run_id, 0)`），并建内部 task group 与 `_GraphIterator`。

| 成员 | 说明 |
|------|------|
| `__aenter__` / `__aexit__` | `_unwrap_exception_groups`、进入 task group 与迭代器上下文；退出时关闭 memory stream 并 `aclose` 迭代器 |
| `__aiter__` / `__anext__` | 直接驱动迭代器；遇到 `ErrorMarker` 时 `raise` 该错误（存于 `_next` 以便 `override_next` 恢复） |
| `next(value=None)` | 推进一步；必要时先 `anext(self)`（避免给刚启动的生成器发非 None 值） |
| `override_next(value)` | 在 `End` 或错误后重定向到新任务或 `EndMarker`（供 hook 系统用）；只能在两次迭代之间调用 |
| `next_task` | `_next or [self._first_task]` |
| `output` | `EndMarker` 时返回其 `value`，否则 `None` |
| `_get_next_task_id` / `_get_next_node_run_id` | 生成 `task:{n}` 形式的 id |

**`_GraphIterator`**（fork-join 协调核心）

按任务类型分派（`_run_task`）：

- `StartNode` / `Fork` → `_handle_edges`；
- `Step` → 进 `run node {id}` span，构造 `StepContext`，`await node.call(...)`；若是 `NodeStep` 则 `_handle_node(output, fork_stack)`，否则 `_handle_edges`；
- `Join` → 产出 `JoinItem(node_id, inputs, fork_stack)`（并交给消费者归约）；
- `Decision` → `_handle_decision`；
- `EndNode` → `EndMarker(inputs)`。

关键机制：

- **fork-join**：`_handle_fork_edges` 为 map 逐元素（或广播逐路径）生成带独立 `ForkStackItem` 的子任务；若 map 指定了 `downstream_join_id`，**预先**为该 join 建 `JoinState`，使空可迭代也能触发 join（以初始值）。
- **join 归约**：`iter_graph` 收到 `JoinItem` 时，用 `graph.get_parent_fork(join_id).fork_id` 与 `_resolve_join_fork_run` 定位 fork run，取（或新建）`JoinState`，调用 `join.reduce(ReducerContext(...), join_state.current, item.inputs)`；若 `cancelled_sibling_tasks` 被置位则 `_cancel_sibling_tasks(parent_fork_id, fork_run_id)`。
- **final vs 中间 join**：`_resolve_join_fork_run` 对 final join 会把 fork 栈裁到（含）其 parent fork，从而丢弃更深的 fork；非 final join 保留整个栈，以便下游 join 仍关联同一 fork run。
- **无活跃任务时**：遍历 `active_reducers`，跳过与同 parent fork 的中间 join 冲突的归约，其余以 reducer 结果 `_handle_non_fork_edges` 继续。
- **错误**：`_run_tracked_task` 把异常经 memory stream 以 `_GraphTaskResult(error=...)` 送出（不直接抛进 task group，避免被包成 `CancelledError` / `ExceptionGroup`），`iter_graph` 以 `ErrorMarker` yield，调用方可用 `override_next` 恢复。

**`EndMarker` / `ErrorMarker` / `JoinItem` / `GraphTaskRequest` / `GraphTask`**

| 类型 | 字段 |
|------|------|
| `EndMarker` | `_value`（property `value`） |
| `ErrorMarker` | `error: BaseException` |
| `JoinItem` | `join_id: JoinID`、`inputs: Any`、`fork_stack: ForkStack` |
| `GraphTaskRequest` | `node_id: NodeID`、`inputs: Any`、`fork_stack: ForkStack`（`repr=False`） |
| `GraphTask(GraphTaskRequest)` | 额外 `task_id: TaskID`（`repr=False`），`from_request(request, get_task_id)` |

**标识符**（[`id_types.py`](../pydantic_graph/pydantic_graph/id_types.py)）：`NodeID`、`NodeRunID`、`TaskID` 是 `NewType`；`JoinID = NodeID`、`ForkID = NodeID` 只是别名。`ForkStackItem(fork_id, node_run_id, thread_index)`；`ForkStack = tuple[ForkStackItem, ...]`。

---

## 12. 支配 fork 与死锁避免（[`parent_forks.py`](../pydantic_graph/pydantic_graph/parent_forks.py)）

```python
@dataclass
class ParentFork(Generic[T]):
    fork_id: T
    intermediate_nodes: set[T]
```

`ParentFork`：`fork_id` 是 join 的支配 fork；`intermediate_nodes` 是位于该 fork 与 join 之间的节点集（若其中没有属于先前 fork 的「walker」，则可安全越过 join）。

`ParentForkFinder(nodes, start_ids, fork_ids, edges)`：

- `find_parent_fork(join_id, *, parent_fork_id=None, prefer_closest=False)`：沿立即支配者链上溯找第一个既是 fork、又能通过「无旁路环」检验的支配 fork。`prefer_closest` 决定取最近还是最远（默认最远/最祖先）。手动指定 `parent_fork_id` 时仍验证它是合法支配 fork，否则抛 `GraphBuildingError`。
- `_predecessors` / `_dominators`（`cached_property`）：支配集用不动点迭代算法。
- `_immediate_dominator`：支配树中最近的支配者。
- `_get_upstream_nodes_if_parent`：若去掉 `fork_id` 后 join 仍处在一个环上（路径从 join 回到 join），返回 `None`（该 fork 不是合法 parent fork）。

`_collect_dominating_forks` 若某 join 无支配 fork，会抛 `GraphBuildingError`，并在消息里附带渲染好的 Mermaid 图。

---

## 13. Mermaid 渲染

`build_mermaid_graph(nodes, edges_by_source) -> MermaidGraph`，`MermaidGraph.render(direction=None, title=None, edge_labels=True)` 产出 `stateDiagram-v2`：

- `StartNode` / `EndNode` → 边上的 `[*]`；
- `Step` → 节点行（可带 `label`）；
- `Join` → `state {id} <<join>>`；
- `Fork` → `state {id} <<fork>>`（`is_map` 与广播同形）；
- `Decision` → `state {id} <<choice>>`，`note` 渲染为右侧 note；
- 边标签来自 `LabelMarker` / `Edge`。

`StateDiagramDirection = Literal['TB','LR','RL','BT']`；`NodeKind = Literal['broadcast','map','join','start','end','step','decision']`。渲染前按 BFS 深度做拓扑排序（`_topological_sort`），使图稳定易读。

```python
print(graph.render(direction='LR'))
```

---

## 14. `build` 的结构校验（`_validate_graph_structure`）

`build(validate_graph_structure=True)`（默认）会检查：

1. 存在从 `StartNode` 出发的边；
2. 存在指向 `EndNode` 的边；
3. 没有「死胡同」非 End 节点（既无出边、`Decision` 也无分支）；
4. `EndNode` 可从 `StartNode` 到达；
5. 所有节点都从 `StartNode` 可达。

任一项不满足抛 `GraphValidationError`（消息附 `how_to_suppress` 提示可传 `validate_graph_structure=False`）。Pydantic AI 的 Agent 图正因含不可达分支而用 `validate_graph_structure=False`。

---

## 15. 状态持久化边界

**`pydantic_graph` 自身不提供状态序列化/持久化。** 状态由调用方通过 `state` / `deps` 对象传入并由调用方持有；持久化是调用方的责任（Pydantic AI 的 durable execution 集成在其之外做检查点）。

`run_sync` 用 `loop.run_until_complete()`，因此不能在已有事件循环的 async 代码里调用；在 Temporal workflow 这类只能由自身运行时驱动的事件循环上会抛 `UnsupportedEventLoopError`。

---

## 16. 与 Pydantic AI 的对接

[`pydantic_ai/_agent_graph.py`](../pydantic_ai_slim/pydantic_ai/_agent_graph.py) 导入 `BaseNode, End, Graph, GraphBuilder, GraphRunContext`：

```python
def build_agent_graph(name, deps_type, output_type) -> Graph[GraphAgentState, GraphAgentDeps[DepsT, OutputT],
                                                            UserPromptNode[DepsT, OutputT], result.FinalResult[OutputT]]:
    return _build_agent_graph(name)

@lru_cache(maxsize=128)
def _build_agent_graph(name):
    g = GraphBuilder(name=name or 'Agent',
                     state_type=GraphAgentState,
                     deps_type=GraphAgentDeps[Any, Any],
                     input_type=UserPromptNode[Any, Any],
                     output_type=result.FinalResult[Any],
                     auto_instrument=False)
    g.add(
        g.edge_from(g.start_node).to(UserPromptNode[Any, Any]),
        g.node(UserPromptNode[Any, Any]),
        g.node(ModelRequestNode[Any, Any]),
        g.node(CallToolsNode[Any, Any]),
        g.node(SetFinalResult[Any, Any]),
    )
    return g.build(validate_graph_structure=False)
```

要点：

- 图**只依赖 `name`**（`lru_cache(maxsize=128)`），`deps_type` / `output_type` 只是绑定类型参数，因此每个名字构建一次、被所有 run 共享。
- `auto_instrument=False`：节点级 span 由 Agent 自己管理。
- 节点 `AgentNode` / `UserPromptNode` / `ModelRequestNode` / `CallToolsNode` / `SetFinalResult` 都是 `BaseNode` 子类，`run(ctx: GraphRunContext[GraphAgentState, GraphAgentDeps[...]])` 返回下一个节点或 `End(FinalResult(...))`。`AgentNode(BaseNode[GraphAgentState, GraphAgentDeps[DepsT, Any], result.FinalResult[NodeRunEndT]])`。
- 状态 `GraphAgentState` 字段：`message_history`、`usage`、`output_retries_used`、`run_step`、`run_id`、`conversation_id`、`metadata`、`last_max_tokens`、`last_model_request_parameters`、`pending_messages`、`event_stream_buffer`、`mcp_tool_defs_cache`。
- 依赖 `GraphAgentDeps[DepsT, OutputDataT]` 携带 `user_deps`、`prompt`、`model`、`model_selector`、`get_model_settings`、`usage_limits`、`max_output_retries`、`end_strategy`、`get_instructions`、`output_schema`、`output_validators`、`root_capability`、`capabilities` 等。
- [`pydantic_ai/agent/__init__.py`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py) 调用 `_agent_graph.build_agent_graph(self.name, self._deps_type, output_type_)`；运行经 `self.graph.iter(...)`。

因此：**`pydantic_graph` 是执行引擎，`pydantic_ai` 提供节点、状态（`GraphAgentState`）与依赖（`GraphAgentDeps`）**。

---

## 17. 可运行示例

### 17.1 声明式 + 构建器混合（来自 [README](../pydantic_graph/README.md)）

```python
from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext


@dataclass
class DivisibleBy5(BaseNode[None, object, int]):
    foo: int

    async def run(self, ctx: GraphRunContext[None]) -> Increment | End[int]:
        if self.foo % 5 == 0:
            return End(self.foo)
        else:
            return Increment(self.foo)


@dataclass
class Increment(BaseNode[None]):
    foo: int

    async def run(self, ctx: GraphRunContext[None]) -> DivisibleBy5:
        return DivisibleBy5(self.foo + 1)


g = GraphBuilder(input_type=int, output_type=int)


@g.step
async def start(ctx: StepContext[None, None, int]) -> DivisibleBy5:
    return DivisibleBy5(ctx.inputs)


g.add(
    g.node(DivisibleBy5),
    g.node(Increment),
    g.edge_from(g.start_node).to(start),
)

fives_graph = g.build()

# await fives_graph.run(inputs=4)  -> 5
```

`start` 的返回提示 `DivisibleBy5` 让 `add` 自动建边；`g.node(DivisibleBy5)` / `g.node(Increment)` 读取两者 `run` 的返回提示（`Increment | End[int]` 与 `DivisibleBy5`）建边。

### 17.2 map + join（并行）

```python
from pydantic_graph import GraphBuilder, StepContext, reduce_list_append

g = GraphBuilder(input_type=int, output_type=list[int])


@g.step
async def make_range(ctx: StepContext[None, None, int]) -> list[int]:
    return list(range(ctx.inputs))


@g.step
async def square_plus_one(ctx: StepContext[None, None, int]) -> int:
    return ctx.inputs * ctx.inputs + 1


join = g.join(reduce_list_append, initial=[])

g.add(
    g.edge_from(g.start_node).to(make_range),
    g.edge_from(make_range).map().to(square_plus_one),   # map：逐元素并行
    g.edge_from(square_plus_one).to(join),               # 汇合
    g.edge_from(join).to(g.end_node),
)

graph = g.build()

# await graph.run(inputs=3)  -> (1, 2, 5)，顺序按 join 完成次序
```

（等价写法：`g.add_mapping_edge(make_range, square_plus_one)`。）

### 17.3 手工 Decision

```python
from pydantic_graph import GraphBuilder, StepContext

g = GraphBuilder(input_type=int | str, output_type=str)


@g.step
async def start(ctx: StepContext[None, None, int | str]) -> int | str:
    return ctx.inputs


@g.step
async def handle_int(ctx: StepContext[None, None, int]) -> str:
    return f'int:{ctx.inputs}'


@g.step
async def handle_str(ctx: StepContext[None, None, str]) -> str:
    return f'str:{ctx.inputs}'


decision = g.decision(note='根据输入类型分流').branch(g.match(int).to(handle_int)).branch(g.match(str).to(handle_str))

g.add(
    g.edge_from(g.start_node).to(start),
    g.edge_from(start).to(decision),
    g.edge_from(handle_int).to(g.end_node),
    g.edge_from(handle_str).to(g.end_node),
)

graph = g.build()
```

分支按声明顺序测试，首个匹配者生效；都不匹配时运行期抛 `RuntimeError`。

### 17.4 渲染

```python
print(graph.render(title='demo', direction='LR'))
# stateDiagram-v2
#   direction LR
#   ...
```

---

## 18. 异常与工具（[`exceptions.py`](../pydantic_graph/pydantic_graph/exceptions.py) / [`util.py`](../pydantic_graph/pydantic_graph/util.py)）

| 异常 | 基类 | 用途 |
|------|------|------|
| `GraphSetupError` | `TypeError` | 图配置错误（如缺返回提示、裸 `BaseNode`） |
| `GraphBuildingError` | `ValueError` | 构建期错误（重复 id、缺支配 fork、空分支） |
| `GraphValidationError` | `ValueError` | 结构校验失败 |
| `GraphRuntimeError` | `RuntimeError` | 运行期错误（如缺 span） |
| `UnsupportedEventLoopError` | `RuntimeError` | 同步方法在当前事件循环上不可用 |

只有 `GraphSetupError` 与 `GraphRuntimeError` 在顶层导出。

[`util.py`](../pydantic_graph/pydantic_graph/util.py)：

- `TypeExpression[T]`：包装 `Any` / `Union[...]` / `Literal[...]` 等无法放进 `type[T]` 位置的类型表达式；`TypeOrTypeExpression[T] = type[TypeExpression[T]] | type[T]`；`unpack_type_expression(type_)` 解包。
- `Maybe[T] = Some[T] | None`（`Some` 是携带 `value` 的 dataclass），区分「无值」与「值为 None」。
- `get_callable_name(callable_)`：取 `__name__`，否则 `str(...)`。

[`_utils.py`](../pydantic_graph/pydantic_graph/_utils.py)（内部）：`run_until_complete`（含中断清理与异常链接）、`get_event_loop`、`get_traceparent`、`get_union_args`、`unpack_annotated`、`get_parent_namespace`、`infer_obj_name`、`UNSET` / `Unset`、`logfire_span`。

---

## 19. 注意事项

- `Graph` / `GraphRun` 等定义在 `graph_builder.py`，别去 `graph.py` 找。
- `StepNode` / `JoinNode` 的 `run` 恒抛 `NotImplementedError`——它们是「交接意图」，不是可直接运行的节点。
- `EdgePathBuilder.map` 不支持多源；需要时给每个源各建一条边。
- `Decision` 的 `source` 必须是能 `isinstance`（或 `Literal` / `Any` / `object`）的类型；`match(NoneType)` 是 `_edge_from_return_hint` 内部为多目标 union 建 `Decision` 时用的占位匹配。
- `auto_instrument=True` 才会自动创建节点/图 span；Pydantic AI 显式关掉。
- join 必须在「支配 fork」下游，否则 `build` 直接报错（并附 Mermaid 图）。
- `GraphBuilder` 的 `state_type` / `deps_type` / `input_type` / `output_type` 默认是 `type(None)`，不是 `object`。
