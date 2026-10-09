# 09 · Pydantic Evals

`pydantic-evals` 是评估「随机函数」（尤其是 LLM 调用与 Agent）的框架：创建/加载数据集、运行评估器、生成报告，并支持离线（数据集）与在线（生产流量）两种评估范式。

它**依赖** `pydantic-ai-slim`：LLM-as-a-judge 评估器直接用 `pydantic_ai.Agent` 实现，数据集序列化复用 `pydantic_ai._spec` 的注册表机制，重试语义复用 `pydantic_ai.retries`。

- 源代码目录：[pydantic_evals/pydantic_evals/](../pydantic_evals/pydantic_evals/)
- 顶层导出（[`__init__.py`](../pydantic_evals/pydantic_evals/__init__.py)）：`Case`、`CaseLifecycle`、`Dataset`、`increment_eval_metric`、`set_eval_attribute`。

```
pydantic_evals/
├── __init__.py
├── dataset.py            # Case / Dataset / evaluate / 序列化 / 任务执行
├── lifecycle.py          # CaseLifecycle 钩子
├── generation.py         # generate_dataset（LLM 合成数据集）
├── online.py             # 在线评估公开 API（evaluate 装饰器、OnlineEvaluator、Config）
├── online_capability.py  # OnlineEvaluation（附着到 Agent 的 capability）
├── _online.py            # 在线评估内部派发 / 采样 / sink 批处理
├── _otel_emit.py         # gen_ai.evaluation.result 事件发射
├── _task_run.py          # TaskRun 度量收集上下文
├── _utils.py             # run_until_complete / logfire_span / UNSET 等
├── evaluators/           # 评估器
├── reporting/            # 报告与渲染
└── otel/                 # SpanNode / SpanTree / SpanQuery
```

---

## 1. `dataset.py` — 数据集、用例与评估

### 1.1 `Case`（用例）

`Case(Generic[InputsT, OutputT, MetadataT])` 是 [`@dataclass(init=False)`](../pydantic_evals/pydantic_evals/dataset.py)（自定义 `__init__`），代表数据集中的一行。

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str \| None` | 用例标识；在报告中用于筛选。为 `None` 时加入数据集后得到 `Case {i}` 泛名 |
| `inputs` | `InputsT` | 传给被评估任务的输入 |
| `metadata` | `MetadataT \| None` | 供评估器使用的附加信息，默认 `None` |
| `expected_output` | `OutputT \| None` | 期望输出，供比较型评估器使用 |
| `evaluators` | `list[Evaluator[...]]` | **用例专属**评估器，与数据集级评估器叠加 |

构造签名（全部关键字参数）：

```python
Case(*, name=None, inputs, metadata=None, expected_output=None, evaluators=())
```

> 注：`evaluators` 参数刻意写成 `tuple | list` 而非 `Sequence`——用 `Sequence` 会让 pyright 为 `inputs` 推断出更窄的类型参数（如 `Literal`），从而与 `Dataset` 的类型参数不匹配。

### 1.2 `Dataset`（数据集）

`Dataset(BaseModel, Generic[...], extra='forbid', arbitrary_types_allowed=True)`：支持 YAML/JSON 加载保存，带有对所有用例生效的数据集级评估器与报告评估器。

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 数据集名 |
| `cases` | `list[Case[...]]` | 用例列表 |
| `evaluators` | `list[Evaluator[...]]` | 对所有用例生效的评估器，默认 `[]` |
| `report_evaluators` | `list[ReportEvaluator[...]]` | 作用于整份报告、产出实验级分析的评估器，默认 `[]` |

构造时 `__init__` 会校验**用例名不可重复**（`ValueError: Duplicate case name`）。

方法与序列化相关：

| 成员 | 说明 |
|------|------|
| `evaluate(...)` | 异步执行评估（见 1.3） |
| `evaluate_sync(...)` | `evaluate` 的同步封装，内部走 `_utils.run_until_complete` |
| `add_case(*, name=None, inputs, metadata=None, expected_output=None, evaluators=())` | 便捷新增用例；重名抛 `ValueError` |
| `add_evaluator(evaluator, specific_case=None)` | 加入数据集或某具名用例；用例不存在抛 `ValueError` |
| `from_file(path, fmt=None, custom_evaluator_types=(), custom_report_evaluator_types=())` | 从文件加载 |
| `from_text(contents, fmt='yaml', custom_evaluator_types=(), custom_report_evaluator_types=(), *, default_name=None)` | 从字符串加载 |
| `from_dict(data, custom_evaluator_types=(), custom_report_evaluator_types=(), *, default_name=None)` | 从字典加载 |
| `to_file(path, fmt=None, schema_path=DEFAULT_SCHEMA_PATH_TEMPLATE, ...)` | 保存到文件 |
| `model_json_schema_with_evaluators(custom_evaluator_types=(), custom_report_evaluator_types=())` | 生成含评估器枚举的 JSON Schema |
| `_build_tasks_to_run(repeat)` | 生成 `(case, report_case_name, source_case_name)` 列表 |
| `_params()` | 类方法，解析泛型参数 `(InputsT, OutputT, MetadataT)`（`functools.cache`） |

模块级常量：`DEFAULT_DATASET_PATH = './test_cases.yaml'`、`DEFAULT_SCHEMA_PATH_TEMPLATE = './{stem}_schema.json'`、`_YAML_SCHEMA_LINE_PREFIX = '# yaml-language-server: $schema='`。

泛型类型变量：`InputsT`、`OutputT`、`MetadataT`（均 `default=Any`）。

### 1.3 评估执行：`evaluate`

```python
async def evaluate(
    self,
    task: Callable[[InputsT], Awaitable[OutputT]] | Callable[[InputsT], OutputT],
    *,
    name: str | None = None,
    max_concurrency: int | None = None,
    progress: bool = True,
    retry_task: RetryConfig | None = None,
    retry_evaluators: RetryConfig | None = None,
    task_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    repeat: int = 1,
    lifecycle: (
        type[CaseLifecycle[...]]
        | Callable[[Case[...]], CaseLifecycle[...]]
        | None
    ) = None,
) -> EvaluationReport[InputsT, OutputT, MetadataT]
```

| 参数 | 语义 |
|------|------|
| `task` | 被评估的可调用对象（同步或异步，接受 `case.inputs`，返回输出） |
| `name` | 实验名，写入报告/日志遥测。缺省顺序：`name` → `task_name` → 任务函数名 |
| `max_concurrency` | 并发上限；`None` 表示全部并发。必须 `>= 1` |
| `progress` | 是否显示 rich 进度条（默认 `True`） |
| `retry_task` / `retry_evaluators` | 任务 / 评估器的 tenacity 重试配置 |
| `task_name` | 覆写任务名 |
| `metadata` | 实验级元数据字典 |
| `repeat` | 每个用例重复次数；必须 `>= 1`。`>1` 时报告名带 `[{run_idx}/{repeat}]`，并按原始用例名聚合 |
| `lifecycle` | 每用例生命周期类，或 `Case -> CaseLifecycle` 工厂；每用例新建实例 |

执行流程（关键不变量）：

1. 校验 `repeat >= 1`、`max_concurrency >= 1`。
2. 计算 `tasks_to_run`（见 `_build_tasks_to_run`），据 `max_concurrency` 建 `anyio.Semaphore`（否则用 `AsyncExitStack` 作为空限流器）。
3. 在 `logfire_span('evaluate {name}')` 下执行，span 属性含 `gen_ai.operation.name='experiment'`、`task_name`、`dataset_name`、`n_cases`；`repeat > 1` 时追加 `logfire.experiment.repeat`，有 `metadata` 时追加 `metadata`。
4. 对每个待运行项并发调用 `_run_task_and_evaluators(...)`（受 `Semaphore` 限制），结果按 `ReportCase` / `ReportCaseFailure` 分流。
5. 用 `eval_span.context` 派生出 `trace_id`（32 位十六进制）/ `span_id`（16 位），构造 `EvaluationReport`。
6. 若有 `report_evaluators`：构造 `ReportEvaluatorContext(name, report, experiment_metadata)` 并调用 `_run_report_evaluators`。
7. `_set_experiment_span_attributes`：写入 `logfire.experiment.metadata`（含 `n_cases`、`repeat`、`metadata`、`averages`）、`assertion_pass_rate`、`logfire.experiment.analyses`、`logfire.experiment.report_evaluator_failures`。

`evaluate_sync` 签名与 `evaluate` 完全一致，仅返回类型同步化，内部 `run_until_complete(self.evaluate(...))`。

### 1.4 内部 helper 与调用链

| 函数 | 职责 |
|------|------|
| `_run_task(task, case, retry=None) -> EvaluatorContext` | 在 `logfire_span('execute {task}')` + `context_subtree()` 内运行任务；设置 `_task_run.CURRENT_TASK_RUN`（禁止嵌套，已进入则 `RuntimeError`）；异步任务直接 await，同步任务经 `anyio.to_thread.run_sync` 后可能再 `await_maybe`；时长优先取 span 时长，回退 `perf_counter` 差值；最后 `extract_span_tree_metrics` 从 span 树推导度量 |
| `_run_task_and_evaluators(task, case, report_case_name, dataset_evaluators, retry_task, retry_evaluators, *, source_case_name=None, lifecycle=None)` | 每个用例一个 `case: {case_name}` span；依次 `lifecycle.setup()` → `_run_task` → `lifecycle.prepare_context` → 用例评估器 + 数据集评估器并发运行 → 分组为 `ReportCase`；异常捕获为 `ReportCaseFailure`；`finally` 调 `lifecycle.teardown(result)`（teardown 异常会向上抛出） |
| `_run_report_evaluators(report_evaluators, report_ctx)` | 逐个在 `report_evaluator: {name}` span 内运行，`list` 结果 extend，单结果 append；异常写入 `report.report_evaluator_failures` |
| `_group_evaluator_outputs_by_type(results)` | 按 `downcast` 拆成 `(assertions, scores, labels)` 三个字典；重名加 `_2`、`_3` 后缀去重 |
| `_get_evaluator_registry` / `_load_evaluator_from_registry` / `_build_evaluator_schema_types` | 基于 `pydantic_ai._spec` 的注册表构建/加载/建 schema；校验自定义评估器必须是 `Evaluator`/`ReportEvaluator` 子类且用 `@dataclass` 装饰 |

**span 树捕获**通过 `context_subtree()`（见 5.2）实现：它挂一个内存 span 导出器，把用例 span 收集为 `SpanTree`，作为 `EvaluatorContext._span_tree`。

### 1.5 度量辅助与序列化细节

- `set_eval_attribute(name, value)` / `increment_eval_metric(name, amount)`：写入 `CURRENT_TASK_RUN` 的 `attributes` / `metrics`（不在任务上下文中则为 no-op）。这两个函数由顶层 `__init__` 重导出。
- `to_file` 按扩展名推断格式（`.yaml`/`.yml`/`.json`，否则 `ValueError`）；`schema_path` 支持 `{stem}` 模板，YAML 写入 `# yaml-language-server: $schema=` 头，JSON 通过 `model_serializer(mode='wrap')`（`_add_json_schema`）注入 `$schema`。
- `_DatasetModel` 用 `alias='$schema'` 映射 `json_schema_path`，从而容忍文件里的 `$schema` 键；用例模型 `_CaseModel` 与数据集模型都设 `extra='forbid'`。
- `model_json_schema_with_evaluators` 内部**遮蔽**了 `Case`/`Dataset` 名字，构造只用于生成 schema 的临时模型，并把 `$schema` 声明为字符串属性。

---

## 2. `lifecycle.py` — `CaseLifecycle`

[`CaseLifecycle(Generic[InputsT, OutputT, MetadataT])`](../pydantic_evals/pydantic_evals/lifecycle.py)：每用例生命周期钩子。评估时**每个用例新建一个实例**，所有钩子默认 no-op。

评估单用例的钩子顺序：

1. `async setup()` — 任务执行前；
2. 任务运行；
3. `async prepare_context(ctx) -> ctx` — 任务后、评估器前，可丰富 `metrics` / `attributes`；
4. 评估器运行；
5. `async teardown(result)` — 评估器后，收到 `ReportCase`（成功）/`ReportCaseFailure`（失败）/`None`（如取消）。

| 成员 | 说明 |
|------|------|
| `__init__(case)` | 保存 `self._case` |
| `case`（property） | 返回被评估的 `Case` |
| `setup()` | 建资源（测试库、服务） |
| `prepare_context(ctx)` | 返回（可能修改过的）`EvaluatorContext` |
| `teardown(result)` | 清理；可按成功/失败区分行为 |

不变量：`setup()` / `prepare_context()` 抛错会被记为 `ReportCaseFailure`，且 `teardown()` 仍会执行；`teardown()` 抛错会向上传播、可能中断整次评估。

```python
class EnrichMetrics(CaseLifecycle):
    async def prepare_context(self, ctx: EvaluatorContext) -> EvaluatorContext:
        ctx.metrics['custom_metric'] = 42
        return ctx

dataset.evaluate_sync(lambda inputs: inputs.upper(), lifecycle=EnrichMetrics)
```

---

## 3. `evaluators/` — 评估器

### 3.1 基类与序列化（`_base.py` / `spec.py`）

- `_StrictABCMeta(ABCMeta)`：**在类定义时**（而非实例化时）就报出「继承自父类但未实现」的抽象方法——`TypeError: {name} must implement all abstract methods: ...`。类自身新声明的抽象方法不算（这是有意的抽象层）。
- `BaseEvaluator(metaclass=_StrictABCMeta)`：`@dataclass(repr=False)`，`__pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)`。方法：
  - `get_serialization_name() -> str`：默认返回类名；
  - `serialize(info)`：`@model_serializer(mode='plain')`，把 `as_spec()` 经 `to_jsonable_python` 转为 JSON 可序列化；
  - `as_spec() -> EvaluatorSpec`：由 `build_serialization_arguments()` 得到参数；0 个参数 → `None`；恰 1 个且非默认值且是首个 dataclass 字段 → 紧凑的 `(value,)` 元组形式；否则用 kwargs 字典；
  - `build_serialization_arguments()`：遍历 dataclass 字段，跳过等于默认值 / `default_factory()` 的字段（可在子类覆写）。
- `EvaluatorSpec = NamedSpec`：可序列化规格，支持三种写法——
  - `'MyEvaluator'`（无参）；
  - `{'MyEvaluator': first_arg}`（首个位置参数）；
  - `{'MyEvaluator': {k1: v1, k2: v2}}`（多个 kwargs）。

### 3.2 结果类型（`evaluator.py`）

| 名称 | 说明 |
|------|------|
| `EvaluationScalar` | `bool \| int \| Annotated[float, Field(allow_inf_nan=False)] \| str`。`int`/有限 `float` 视作分数，`str` 视作标签，`bool` 视作断言 |
| `EvaluationReason`（`@dataclass`） | `value: EvaluationScalar`、`reason: str \| None = None` |
| `EvaluatorOutput` | `EvaluationScalar \| EvaluationReason \| Mapping[str, EvaluationScalar \| EvaluationReason]` |
| `EvaluationResult(Generic[EvaluationScalarT])` | `@dataclass(kw_only=True)`：`name`、`value`、`reason`、`source: EvaluatorSpec`、`evaluator_version: str \| None = None`；方法 `downcast(*value_types) -> EvaluationResult[T] \| None`（`bool` 只匹配显式 `bool`） |
| `EvaluatorFailure` | `@dataclass(kw_only=True)`：`name`、`error_message`、`error_stacktrace`、`source: EvaluatorSpec`、`evaluator_version`、`error_type: str \| None` |

### 3.3 `Evaluator` 基类

`Evaluator(BaseEvaluator, Generic[InputsT, OutputT, MetadataT])`（`@dataclass(repr=False)`，参数为**逆变**类型变量）。子类必须实现 `evaluate`（可为 `def` 或 `async def`）。

| 方法 | 说明 |
|------|------|
| `get_default_evaluation_name() -> str` | 报告中的默认结果名，默认取 `get_serialization_name()`；返回 mapping 的评估器始终以 mapping 的键命名 |
| `get_evaluator_version() -> str \| None` | 版本标签，默认 `None`；下发到在线评估 sink，便于仪表盘按版本过滤 |
| `evaluate(ctx)`（abstract） | 返回 `EvaluatorOutput` 或 `Awaitable[EvaluatorOutput]` |
| `evaluate_sync(ctx)` | 同步运行，若是协程则 `run_until_complete` |
| `evaluate_async(ctx)` | 异步运行；同步实现直接返回。若要避免阻塞，可覆写为 `await anyio.to_thread.run_sync(self.evaluate, ctx)` |

### 3.4 `EvaluatorContext`（`context.py`）

`EvaluatorContext(Generic[...], @dataclass(kw_only=True))` 是**所有评估器的唯一输入**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str \| None` | 用例名 |
| `inputs` | `InputsT` | 任务输入 |
| `metadata` | `MetadataT \| None` | 用例元数据 |
| `expected_output` | `OutputT \| None` | 期望输出 |
| `output` | `OutputT` | 任务实际输出 |
| `duration` | `float` | 任务运行时长 |
| `_span_tree` | `SpanTree \| SpanTreeRecordingError` | 私有字段，`repr=False` |
| `attributes` | `dict[str, Any]` | 由 `set_eval_attribute` 写入 |
| `metrics` | `dict[str, int \| float]` | 由 `increment_eval_metric` 写入 |
| `span_tree`（property） | `SpanTree` | 若底层是 `SpanTreeRecordingError` 则抛出该异常 |

### 3.5 `run_evaluator`（`_run_evaluator.py`）

```python
async def run_evaluator(
    evaluator: Evaluator[InputsT, OutputT, MetadataT],
    ctx: EvaluatorContext[InputsT, OutputT, MetadataT],
    retry: RetryConfig | None = None,
) -> list[EvaluationResult] | EvaluatorFailure
```

语义：

- 取 `evaluator.evaluate_async`；有 `retry` 时用 `pydantic_ai.retries.retry(**retry)` 包裹（缺 tenacity 会给出明确 import 错误）。
- 在 `logfire_span('Calling evaluator: {evaluator_name}', _span_name='evaluator: {evaluator_name}')` 内运行（span 名保持稳定供已有查询使用）。
- 输出经 `_EVALUATOR_OUTPUT_ADAPTER`（`TypeAdapter(EvaluatorOutput, revalidate_instances='always')`，以对裸 dataclass 的 `EvaluationReason` 强制校验有限浮点约束）校验；类型非法抛 `ValueError`。
- `_convert_to_mapping` 把标量包成 `{evaluator_name: value}`；逐项把非 `EvaluationReason` 提升为 `EvaluationReason(value=...)`，产出 `EvaluationResult`（带 `source=as_spec()`、`evaluator_version`）。
- 任何异常 → 返回 `EvaluatorFailure`（含 `error_type=type(e).__name__`）。

### 3.6 内置评估器（`common.py`）

`DEFAULT_EVALUATORS` 是注册表元组：`Equals, EqualsExpected, Contains, IsInstance, MaxDuration, LLMJudge, HasMatchingSpan, ToolCorrectness, TrajectoryMatch, ArgumentCorrectness, MaxToolCalls, MaxModelRequests, GEval`。

| 评估器 | 关键字段 | 行为 |
|--------|----------|------|
| `Equals(value, evaluation_name=None)` | `value` | `ctx.output == value`，返回 `bool` |
| `EqualsExpected(evaluation_name=None)` | — | `expected_output` 为 `None` 时返回 `{}`（不比较）；否则 `output == expected_output` |
| `Contains(value, case_sensitive=True, as_strings=False, evaluation_name=None)` | `value` | 字符串子串 / 列表成员 / 字典键值（模型类对象先 dump 为 dict）；`case_sensitive` 仅在双方都是字符串时生效；返回 `EvaluationReason` 并带失败原因（失败原因做了 `_truncated_repr` 截断） |
| `IsInstance(type_name, evaluation_name=None)` | `type_name` | 遍历 `type(output).__mro__`，命中 `__name__` 或 `__qualname__` |
| `MaxDuration(seconds)` | `seconds: float \| timedelta` | `duration <= seconds` |
| `LLMJudge(rubric, model=None, include_input=False, include_expected_output=False, model_settings=None, score=False, assertion=OutputConfig(include_reason=True))` | 组合四个 judge agent | 按 `include_input` / `include_expected_output` 选择 judge；`score is not False` 时输出 `<name>_score`，`assertion is not False` 时输出 `<name>_pass`（仅一侧时名字退化为 `evaluation_name`） |
| `GEval(criteria, evaluation_steps, score_range=(1,5), include_input=False, model=None, model_settings=None, evaluation_name=None)` | G-Eval 链式思考 | `__post_init__` 校验 `min < max`、`evaluation_steps` 非空；返回 `EvaluationReason(score, reason)` |
| `HasMatchingSpan(query, evaluation_name=None)` | `SpanQuery` | `ctx.span_tree.any(query)`，返回 `bool` |

- `OutputConfig`（TypedDict）：`evaluation_name: str`、`include_reason: bool`。
- 模型字段序列化：`_serialize_model_as_string` 把 `models.Model` 实例替换为 `model.model_id` 字符串，使 spec 可往返。
- **`Python` 评估器已被移除**：`common.__getattr__('Python')` 与 `evaluators.__getattr__('Python')` 均抛 `ImportError`（安全原因，见 PR #2808）。

### 3.7 LLM-as-a-judge（`llm_as_a_judge.py`）

输出模型：

| 类型 | 字段 |
|------|------|
| `GradingOutput(BaseModel, populate_by_name=True)` | `reason: str`、`pass_: bool`（alias `pass`）、`score: float` |
| `GEvalOutput(BaseModel)` | `reason: str`、`score: int` |

内部 judge agent（均 `Agent`）：`_judge_output_agent`、`_judge_input_output_agent`、`_judge_input_output_expected_agent`、`_judge_output_expected_agent`、`_judge_g_eval_agent`、`_non_text_judge_agent`。

公开 helper（返回 `GradingOutput` 或 `GEvalOutput`）：

| 函数 | 签名要点 |
|------|----------|
| `judge_output` | `(output, rubric, model=None, model_settings=None)` |
| `judge_input_output` | `(inputs, output, rubric, model=None, model_settings=None)` |
| `judge_output_expected` | `(output, expected_output, rubric, model=None, model_settings=None)` |
| `judge_input_output_expected` | `(inputs, output, expected_output, rubric, model=None, model_settings=None)` |
| `judge_g_eval` | `(output, criteria, evaluation_steps, score_range=(1,5), inputs=None, model=None, model_settings=None)` |
| `set_default_judge_model(model)` | 覆写全局默认 judge 模型 |

关键机制：

- 默认模型 `_default_model = 'openai:gpt-5.2'`；`_MAX_G_EVAL_SCORE_LEVELS = 20`。
- `_build_prompt(output, rubric, inputs=None, expected_output=None)`：段落顺序固定为 **`Input → Output → ExpectedOutput → Rubric`**（与 few-shot 示例及 `judge_input_output_expected` 命名一致，指令放最后）；同时返回携带的上下文段落名，供无法写 reason 的 judge 在字段描述里点名。
- 无文本输出能力的 judge（`supports_text_output=False`，含 `FallbackModel`/`WrapperModel` 递归判断）：`LLMJudge`/`GEval` 走「无 reason 判定」路径（`allow_reasonless=True`），只返回类型化的 pass/fail（score 归一为 1.0/0.0）或归一化整数分；`_g_eval_output_type` 限制最多 20 档。公开 `judge_*` helper 该情形抛 `UserError`。
- 公开 helper 走 `allow_reasonless=False`，因此保证 `reason` 非空。

### 3.8 基于 span 的确定性评估器（`agentic.py`）

无需 LLM，读取 `ctx.span_tree`；缺少 span 树时优雅失败（返回失败原因 / 0.0）。

**计入规则（重要不变量）**：

- 只在**本地执行**的工具有 span；provider 原生/服务端内置工具（OpenAI file search、Anthropic web search 等）不计入。
- 默认只计成功调用；`include_failed=True` 计入所有尝试（异常或 `ModelRetry` 重试）；`MaxToolCalls` 例外，默认计入失败尝试（`include_failed=True`），因其仍消耗预算。
- 延迟调用（`ApprovalRequired` / `CallDeferred`）从**不**计入。
- 计入捕获树中所有匹配 span，含嵌套子 Agent（agent-as-tool 委派）。

私有常量：`_GEN_AI_TOOL_NAME_ATTR='gen_ai.tool.name'`、`_V2_TOOL_SPAN_NAME='running tool'`、`_V3_TOOL_SPAN_PREFIX='execute_tool '`、`_V2_TOOL_ARGUMENTS_ATTR='tool_arguments'`、`_V3_TOOL_ARGUMENTS_ATTR='gen_ai.tool.call.arguments'`、`_TOOL_DEFERRAL_NAME_ATTR='pydantic_ai.tool.deferral.name'`、`_GEN_AI_REQUEST_MODEL_ATTR='gen_ai.request.model'`、`_GEN_AI_OPERATION_NAME_ATTR='gen_ai.operation.name'`。

| 评估器 | 关键字段 | 语义 |
|--------|----------|------|
| `ToolCorrectness(expected_tools, allow_extra=False, include_failed=False, evaluation_name=None)` | 工具名 multiset | `Counter` 差集；缺工具 / 多余工具（`allow_extra=False` 时）判失败 |
| `TrajectoryMatch(expected_trajectory, order='in_order', include_failed=False, evaluation_name=None)` | `order = TrajectoryOrder` | `'exact'`：等则 1.0 否则 0.0；`'in_order'`：LCS 的 P/R 的 F1；`'any_order'`：multiset 交集的 P/R 的 F1。两侧都空 → 1.0，一侧空 → 0.0 |
| `ArgumentCorrectness(tool_name, expected_arguments, match_mode='subset', occurrence='first', include_failed=False, evaluation_name=None)` | `ArgumentMatchMode='exact'\|'subset'`、`ArgumentOccurrence='first'\|'last'` 或 0 基索引 | 选第 `occurrence` 次调用，解析 JSON 参数后比较；`'subset'` 只查顶层键，`'exact'` 还需无多余键 |
| `MaxToolCalls(max_calls, include_failed=True, evaluation_name=None)` | — | 计数 `<= max_calls`（默认含失败） |
| `MaxModelRequests(max_requests, evaluation_name=None)` | — | 优先 `ctx.metrics['requests']`，否则数 span 树中的 chat 请求 |

### 3.9 报告评估器（`report_evaluator.py` / `report_common.py`）

- `ReportEvaluatorContext(name, report, experiment_metadata)`（`@dataclass(kw_only=True)`）。
- `ReportEvaluator(BaseEvaluator, Generic[...])`：抽象 `evaluate(ctx) -> ReportAnalysis | list[ReportAnalysis] | Awaitable[...]`；`evaluate_async(ctx)` 处理同步/异步。
- 内置 `DEFAULT_REPORT_EVALUATORS`：`ConfusionMatrixEvaluator`、`KolmogorovSmirnovEvaluator`、`PrecisionRecallEvaluator`、`ROCAUCEvaluator`。

| 报告评估器 | 关键字段 | 产出 |
|-----------|----------|------|
| `ConfusionMatrixEvaluator(predicted_from='output', predicted_key=None, expected_from='expected_output', expected_key=None, title='Confusion Matrix')` | 四选一来源 `Literal['expected_output','output','metadata','labels']` | `ConfusionMatrix`（`matrix[expected][predicted]`） |
| `PrecisionRecallEvaluator(score_key, positive_from, positive_key=None, score_from='scores', title, n_thresholds=100)` | `score_from: 'scores'\|'metrics'`；`positive_from: 'expected_output'\|'assertions'\|'labels'` | `[PrecisionRecall, ScalarResult(AUC)]`（AUC 全分辨率、曲线降采样） |
| `ROCAUCEvaluator(score_key, positive_from, positive_key=None, score_from='scores', title='ROC Curve', n_thresholds=100)` | 同上 | `[LinePlot(ROC + 随机基线), ScalarResult(AUC)]` |
| `KolmogorovSmirnovEvaluator(...)` | 同上 | `[LinePlot(正/负 CDF), ScalarResult(KS 统计量)]` |

辅助函数：`_get_score`、`_get_positive`、`_extract_scored_cases`、`_iter_threshold_counts`、`_downsample`、`_trapezoidal_auc`。

---

## 4. `reporting/` — 报告

- `ReportCase`（`@dataclass(kw_only=True)`）：`name`、`inputs`、`metadata`、`expected_output`、`output`、`metrics`、`attributes`、`scores: dict[str, EvaluationResult[int\|float]]`、`labels`、`assertions: dict[str, EvaluationResult[bool]]`、`task_duration`、`total_duration`（含评估器耗时）、`source_case_name`（多轮实验的聚合键）、`trace_id`/`span_id`、`evaluator_failures`。
- `ReportCaseFailure`：任务执行抛错的用例（`error_message`、`error_stacktrace` 等）。
- `ReportCaseGroup`：同一用例多次运行的分组视图（`runs`、`failures`、`summary`），经 `EvaluationReport.case_groups()` 获得。
- `ReportCaseAggregate(BaseModel)`：`name`、`scores`、`labels`、`metrics`、`assertions: float \| None`、`task_duration`、`total_duration`；`average(cases)`（对量化属性取平均，`labels` 取分布）、`average_from_aggregates(aggregates)`（多轮聚合）。
- `EvaluationReport`（`@dataclass(kw_only=True)`）：`name`、`cases`、`failures`、`analyses`、`report_evaluator_failures`、`experiment_metadata`、`trace_id`、`span_id`。方法：
  - `case_groups()` → `list[ReportCaseGroup] | None`（无 `source_case_name` 时 `None`）；
  - `averages()` → `ReportCaseAggregate | None`；
  - `render(width=None, baseline=None, *, include_* ..., input_config/... )` → `str`；
  - `print(..., console=None)` → 打印到 rich `Console`；
  - `console_table(..., with_title=True, ascii_only=False)` → `RenderableType`；
  - `failures_table(...)` → 失败表格。
- 渲染配置：`RenderValueConfig`、`RenderNumberConfig`（`value_formatter`/`diff_formatter`/`diff_atol`/`diff_rtol`/`diff_increase_style`/`diff_decrease_style`），内部 `_ValueRenderer`、`_NumberRenderer`、`ReportCaseRenderer`、`EvaluationRenderer`（`build_table`、`build_diff_table`、`build_failures_table`）。ASCII 环境用 `v`/`x`/`->` 替代 `✔`/`✗`/`→`。
- 适配器：`EvaluationReportAdapter`、`ReportCaseAdapter`、`ReportCaseFailureAdapter`。
- `analyses.py`（均为 `BaseModel`，带 `type` 判别字段）：
  - `ConfusionMatrix(type='confusion_matrix', title, class_labels, matrix)`；
  - `PrecisionRecall(type='precision_recall', curves)` 及 `PrecisionRecallCurve(name, points, auc)`、`PrecisionRecallPoint(threshold, precision, recall)`；
  - `ScalarResult(type='scalar', title, value, unit)`；
  - `TableResult(type='table', title, columns, rows)`；
  - `LinePlot(type='line_plot', title, x_label, y_label, x_range, y_range, curves)` 及 `LinePlotCurve(name, points, style, step)`、`LinePlotPoint(x, y)`；
  - `ReportAnalysis = Annotated[ConfusionMatrix | PrecisionRecall | ScalarResult | TableResult | LinePlot, Discriminator('type')]`。

---

## 5. `otel/` — 基于 span 的评估

### 5.1 `span_tree.py`

**`SpanQuery`（TypedDict, total=False）** 字段（按实现顺序，便于对照）：

- 名称：`name_equals`、`name_contains`、`name_matches_regex`；
- 属性：`has_attributes: dict[str, Any]`（dict/list 值可与存成 JSON 字符串的属性匹配，list 亦可匹配 tuple）、`has_attribute_keys: list[str]`；
- 状态：`has_status: SpanStatus`；
- 时长：`min_duration`、`max_duration`（`timedelta | float`）；
- 逻辑组合：`not_`、`and_`、`or_`（`or_` 与同级其它条件不可共存，否则 `ValueError`）；
- 子节点：`min_child_count`、`max_child_count`、`some_child_has`、`all_children_have`、`no_child_has`；
- 递归控制：`stop_recursing_when`；
- 后代：`min_descendant_count`、`max_descendant_count`、`some_descendant_has`、`all_descendants_have`、`no_descendant_has`；
- 祖先：`min_depth`、`max_depth`、`some_ancestor_has`、`all_ancestors_have`、`no_ancestor_has`。

**`SpanNode`（`@dataclass(repr=False, kw_only=True)`）** 字段：`name`、`trace_id: int`、`span_id: int`、`parent_span_id: int | None`、`start_timestamp`、`end_timestamp`、`attributes: dict[str, AttributeValue]`、`status: SpanStatus = 'unset'`。属性：`duration`、`children`、`descendants`、`ancestors`、`node_key`（`{trace:032x}:{span:016x}`）、`parent_node_key`。方法：

- `from_readable_span(span)`（静态）：由 OTel `ReadableSpan` 构造；
- `add_child(child)`：断言 trace 与 parent 一致后挂接；
- 子/后代/祖先查询：`find_children` / `first_child` / `any_child`、`find_descendants` / `first_descendant` / `any_descendant`、`find_ancestors` / `first_ancestor` / `any_ancestor`（后两类支持 `stop_recursing_when`）；
- `matches(query)`：`query` 可为 `SpanQuery` 或谓词 `SpanPredicate = Callable[[SpanNode], bool]`；
- `repr_xml(include_children=True, include_trace_id=False, include_span_id=False, include_start_timestamp=False, include_duration=False)`。

**`SpanTree`（`@dataclass(repr=False, kw_only=True)`）** 字段：`roots: list[SpanNode]`、`nodes_by_id: dict[str, SpanNode]`。方法：`add_spans`、`add_readable_spans`、`find`、`first`、`any`、`__iter__`（DFS 遍历）、`repr_xml`；`_rebuild_tree` 按 `start_timestamp` 排序并重建父子与根集合。`SPAN_TREE_ADAPTER = TypeAdapter(SpanTree)` 支持 JSON 序列化。

类型：`SpanStatus = Literal['unset', 'ok', 'error']`；`AttributeValue` 同 OTel `AttributeValue`。

### 5.2 捕获机制

- `_context_subtree.py`：`context_subtree()` 上下文管理器；opentelemetry 缺失时回退实现 yield 一个 `SpanTreeRecordingError`（`__context__` 链到原始 `ImportError`）。
- `_context_in_memory_span_exporter.py`：真正的 `context_subtree()`——用 `_ContextInMemorySpanExporter`（按 `_EXPORTER_CONTEXT_ID` contextvar 分组收集 span）挂 `SimpleSpanProcessor`，退出上下文时把 `ReadableSpan` 列表灌入 `SpanTree`。导出器按 tracer provider 的 `id()` 缓存（弱引用，避免每次评估都挂新 processor）。
- `_errors.py`：`SpanTreeRecordingError(Exception)`，含 `message` 字段与 pydantic core schema（可序列化，序列化时丢弃 `__context__`/`__cause__`/traceback）。

---

## 6. `_task_run.py` — 任务运行的度量收集

- `TaskRun`（`@dataclass`）：`attributes`、`metrics`；方法 `record_metric`、`increment_metric`（0 → 0 时不记录）、`record_attribute`。
- `run_task()`（`@contextmanager`）→ 产出 `get_eval_context_kwargs` 可调用对象；设置 `CURRENT_TASK_RUN` contextvar，进入 `context_subtree()`，退出时计算时长、重置 contextvar、从 span 树提取度量。
- `extract_span_tree_metrics(task_run, span_tree)`：遍历节点，`gen_ai.request.model` 存在时——`gen_ai.operation.name == 'chat'` 计 `requests`；`operation.cost` 计 `cost`；`gen_ai.usage.details.*` 与 `gen_ai.usage.*` 前缀剥离后累加 token。
- `CURRENT_TASK_RUN = ContextVar[TaskRun | None]('CURRENT_TASK_RUN', default=None)`。

---

## 7. 在线评估（`online.py` / `online_capability.py` / `_online.py` / `_otel_emit.py`）

### 7.1 类型与配置

| 名称 | 说明 |
|------|------|
| `SamplingMode` | `Literal['independent', 'correlated']`。independent：每个评估器独立投币；correlated：每次调用共享一个随机种子，低采样率评估器的调用是高采样率的子集 |
| `SamplingContext`（`@dataclass(kw_only=True)`） | `evaluator`、`inputs`、`metadata`、`call_seed`（每次调用一次的 `[0,1)` 均匀随机值） |
| `SpanReference`（`@dataclass(kw_only=True)`） | `trace_id: str`、`span_id: str` |
| `OnlineEvaluator`（`@dataclass(kw_only=True)`） | `evaluator`、`sample_rate=None`、`max_concurrency=10`、`sink=None`、`on_max_concurrency=None`、`on_sampling_error=None`、`on_error=None`、`run_on_errors=False`；`__post_init__` 校验并发并建 `threading.Semaphore` |
| `OnlineEvalConfig`（`@dataclass(kw_only=True)`） | `default_sink=None`、`default_sample_rate=1.0`、`emit_otel_events=True`、`include_baggage=True`、`sampling_mode='independent'`、`enabled=True`、`metadata=None`、`on_max_concurrency=None`、`on_sampling_error=None`、`on_error=None`；方法 `evaluate(*evaluators, target=None, msg_template=None, span_name=None, extract_args=False, record_return=False)`（装饰器工厂）与 `should_evaluate()` |
| `OnErrorLocation` | `Literal['sink', 'on_max_concurrency']` |
| `EvaluatorContextSource`（Protocol） | `fetch(span)`、`fetch_many(spans)`，从存储轨迹重建 `EvaluatorContext` |

回调类型别名：`OnMaxConcurrencyCallback`、`OnSamplingErrorCallback`（必须同步）、`OnErrorCallback`、`SinkCallback = Callable[[Sequence[EvaluationResult], Sequence[EvaluatorFailure], EvaluatorContext], None | Awaitable[None]]`。

### 7.2 Sink 与派发（`_online.py`）

- `SinkPayload`（`@dataclass(kw_only=True, frozen=True)`）：`results`、`failures`、`context`、`span_reference`、`target`（禁止直接实例化）。
- `EvaluationSink`（`@runtime_checkable` Protocol）：`async submit(payload)`。
- `CallbackSink(callback)`：把 payload 的 `results`/`failures`/`context` 传给回调。
- 派发：`dispatch_async(coro)`（asyncio/trio）、`dispatch_in_background_thread(coro)`（同步场景，自带事件循环的线程）、`wait_for_evaluations(timeout=30.0)`。
- 采样：`sample_evaluators(online_evals, config, inputs)`，每个评估器按 `sampling_mode` 决定；异常交给 `on_sampling_error`，否则抛出。
- `dispatch_evaluators(...)`：按 sink 来源 `id()` 分组 → 并行运行评估器（每个用信号量 + 可选 `on_max_concurrency`，`build_parent_context` 把调用 span 设为 OTel 父上下文）→ 每组一次批量 `submit`。
- 短路：`emit_otel_events=False` 且无 sink 时评估器根本不运行。

### 7.3 OTel 事件发射（`_otel_emit.py`）

- 事件名 `gen_ai.evaluation.result`，OTel scope `pydantic-evals`。
- 属性：`gen_ai.evaluation.name`、`gen_ai.evaluation.score.value`、`gen_ai.evaluation.score.label`、`gen_ai.evaluation.explanation`、`error.type`（标准）；扩展 `gen_ai.evaluation.target`、`gen_ai.evaluation.evaluator.source`、`gen_ai.evaluation.evaluator.version`。
- 标量映射：`bool` → `score.value`（1.0/0.0）+ `score.label`（`pass`/`fail`）；`int`/`float` → 仅 `score.value`；`str` → 仅 `score.label`。
- `build_parent_context(span_reference)` 用 `NonRecordingSpan` 构造父上下文；`emit_otel_events(results, failures, target, include_baggage=True)`；失败事件带 `SeverityNumber.WARN`。

### 7.4 公开 API 与 `OnlineEvaluation` capability

- 模块级函数：`evaluate(...)`（= `DEFAULT_CONFIG.evaluate(...)`）、`configure(**kwargs)`（仅更新提供的项，`UNSET` 表示忽略，`None` 表示清空）、`run_evaluators(evaluators, context)`、`wait_for_evaluations(timeout=30.0)`、`disable_evaluation()`（context manager，临时关闭）。
- `DEFAULT_CONFIG = OnlineEvalConfig()` 是全局默认实例。
- `OnlineEvaluation(AbstractCapability[AgentDepsT])`（`online_capability.py`）：字段 `evaluators: Sequence[Evaluator | OnlineEvaluator]`、`config: OnlineEvalConfig | None`；`get_serialization_name()` 返回 `None`；实现 `wrap_run`——包装 `agent.run` / `run_stream` / `iter`，在运行完成后用 `_task_run.run_task()` 捕获 span 树，构造 `EvaluatorContext(name=ctx.run_id, ...)`，再异步 `dispatch_evaluators`；`_parse_traceparent(logfire_api.get_context().get('traceparent'))` 取 span 引用；`run_on_errors=True` 的评估器在异常路径以异常为 `output` 派发（异常仍继续抛出）。
- `extract_args` / `record_return` 需要安装 logfire，否则装饰时抛 `RuntimeError`。

---

## 8. 数据集生成（`generation.py`）

```python
async def generate_dataset(
    *,
    dataset_type: type[Dataset[InputsT, OutputT, MetadataT]],
    path: Path | str | None = None,
    custom_evaluator_types: Sequence[type[Evaluator[...]]] = (),
    model: models.Model | models.KnownModelName = 'openai:gpt-5.2',
    n_examples: int = 3,
    extra_instructions: str | None = None,
) -> Dataset[InputsT, OutputT, MetadataT]
```

实现：以 `dataset_type.model_json_schema_with_evaluators(...)` 为 schema，构造 `Agent(model, system_prompt=..., output_type=str)` 让 LLM 生成符合 schema 的 JSON；`strip_markdown_fences` 清洗输出后用 `dataset_type.from_text(..., fmt='json')` 解析；`path` 非空时 `to_file` 保存。`default_name` 取路径 stem 或 `'generated'`。

---

## 9. 与 Pydantic AI 的关系

- 传给 `Dataset.evaluate` 的 task 可以是 `Agent.run` 风格的可调用对象；其输出、时长与捕获的 OTel span 树共同构成 `EvaluatorContext`。
- `LLMJudge` / `GEval` 内部使用 `pydantic_ai.Agent`；judge 模型的 profile（`supports_text_output`）决定其走有理由还是无理由路径。
- 在线评估既能通过 `OnlineEvaluation` capability 附着到运行中的 Agent，也能通过 `@evaluate` 装饰器附着到任意函数（同步或异步）。
- 依赖方向：`pydantic-evals` **依赖** `pydantic-ai-slim`；而 `pydantic-graph` 是 `pydantic-ai` 的独立叶子依赖（见 [08](08-pydantic-graph.md)）。

---

## 10. 相关文件清单

| 文件 | 内容 |
|------|------|
| [`dataset.py`](../pydantic_evals/pydantic_evals/dataset.py) | `Case`、`Dataset`、`evaluate`、序列化、任务执行 |
| [`lifecycle.py`](../pydantic_evals/pydantic_evals/lifecycle.py) | `CaseLifecycle` |
| [`generation.py`](../pydantic_evals/pydantic_evals/generation.py) | `generate_dataset` |
| [`online.py`](../pydantic_evals/pydantic_evals/online.py) | 在线评估公开 API |
| [`online_capability.py`](../pydantic_evals/pydantic_evals/online_capability.py) | `OnlineEvaluation` |
| [`_online.py`](../pydantic_evals/pydantic_evals/_online.py) | 派发 / 采样 / sink 批处理 |
| [`_otel_emit.py`](../pydantic_evals/pydantic_evals/_otel_emit.py) | `gen_ai.evaluation.result` 发射 |
| [`_task_run.py`](../pydantic_evals/pydantic_evals/_task_run.py) | `TaskRun` / `run_task` / `extract_span_tree_metrics` |
| [`_utils.py`](../pydantic_evals/pydantic_evals/_utils.py) | `run_until_complete`、`logfire_span`、`task_group_gather`、`UNSET` |
| [`evaluators/`](../pydantic_evals/pydantic_evals/evaluators/) | 评估器基类、内置、agentic、judge、报告评估器 |
| [`reporting/`](../pydantic_evals/pydantic_evals/reporting/) | 报告结构、渲染、`analyses.py` |
| [`otel/`](../pydantic_evals/pydantic_evals/otel/) | `SpanNode`/`SpanTree`/`SpanQuery`、span 捕获 |
