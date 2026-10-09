# 09 · Pydantic Evals

`pydantic-evals` 是评估「随机函数」（尤其是 LLM 调用与 Agent）的框架：创建/加载数据集、运行评估器、生成报告，并支持在线评估。它依赖 `pydantic-ai-slim`（其 LLM-as-a-judge 评估器直接用 `Agent` 实现）。

源代码目录：[pydantic_evals/pydantic_evals/](../pydantic_evals/pydantic_evals/)

顶层导出：`Case`、`CaseLifecycle`、`Dataset`、`increment_eval_metric`、`set_eval_attribute`。

---

## 1. `dataset.py` — 数据集、用例与评估

### 1.1 核心类型

- `Case(Generic[InputsT, OutputT, MetadataT])`：`name`、`inputs`、`metadata`、`expected_output`、`evaluators`（用例专属）。
- `Dataset(BaseModel, Generic[...])`：`name`、`cases`、`evaluators`（对所有用例生效）、`report_evaluators`。

### 1.2 评估执行

- `async evaluate(task, *, name, max_concurrency, progress, retry_task, retry_evaluators, task_name, metadata, repeat, lifecycle) -> EvaluationReport`：在 `logfire_span('evaluate {name}')` 下逐用例运行任务 + 评估器，汇总为 `EvaluationReport`，再运行报告评估器。
- `evaluate_sync(...)`：经 `run_until_complete` 的同步封装。
- `add_case(...)`、`add_evaluator(evaluator, specific_case=None)`。
- 序列化：`from_file` / `from_text` / `from_dict` / `to_file`、`model_json_schema_with_evaluators`；格式推断与写入 `$schema`。

内部 helper：`_run_task(task, case, retry)`（在 `logfire_span` + `context_subtree()` 内运行，捕获 span 树，产出 `EvaluatorContext`）；`_run_task_and_evaluators(...)`（每用例一个 `case:` span，运行生命周期 `setup()` / `prepare_context()`，并发运行用例与数据集评估器）；`_run_report_evaluators(...)`。

其他模块级函数：`set_eval_attribute(name, value)`、`increment_eval_metric(name, amount)`。

### 1.3 生命周期

`lifecycle.py` 的 `CaseLifecycle`：每用例创建新实例，钩子 `async setup()`、`async prepare_context(ctx) -> ctx`、`async teardown(result)`；`self.case` 暴露 `Case`。

---

## 2. `evaluators/` — 评估器

### 2.1 基类与结果

- `_base.py`：`_StrictABCMeta`（类定义时即报未实现抽象方法）、`BaseEvaluator`（`as_spec()` / `serialize` / `get_serialization_name`）。
- `spec.py`：`EvaluatorSpec = NamedSpec`（可序列化，支持 `'Name'`、`{'Name': arg}`、`{'Name': {kwargs}}`）。
- `evaluator.py`：
  - `EvaluationScalar = bool | int | finite float | str`。
  - `EvaluationReason(value, reason=None)`。
  - `EvaluationResult`：`name`、`value`、`reason`、`source`、`evaluator_version`；`downcast(*types)`。
  - `EvaluatorFailure`：错误信息与堆栈。
  - `Evaluator(BaseEvaluator, ...)`：抽象 `evaluate(ctx)`；`evaluate_sync` / `evaluate_async`。
- `context.py`：`EvaluatorContext` —— 评估器的唯一输入：`name`、`inputs`、`metadata`、`expected_output`、`output`、`duration`、`span_tree`、`attributes`、`metrics`。
- `_run_evaluator.py`：`async run_evaluator(evaluator, ctx, retry)`（可选 tenacity 重试，包裹 logfire span，异常转为 `EvaluatorFailure`）。

### 2.2 内置评估器（`common.py`）

| 评估器 | 说明 |
|--------|------|
| `Equals(value)` | 输出等于给定值 |
| `EqualsExpected()` | 输出等于期望输出 |
| `Contains(value, case_sensitive, as_strings)` | 包含（str/list/dict/类模型对象） |
| `IsInstance(type_name)` | 输出的 MRO 命中某类型名 |
| `MaxDuration(seconds)` | 时长约束 |
| `LLMJudge(rubric, model, ...)` | LLM-as-a-judge，产出分数和/或通过 |
| `GEval(criteria, evaluation_steps, score_range, ...)` | 简化版 G-Eval 链式思考整数评分 |
| `HasMatchingSpan(query)` | span 树查询匹配 |

`DEFAULT_EVALUATORS` 为注册表。

### 2.3 LLM-as-a-judge（`llm_as_a_judge.py`）

- 输出模型：`GradingOutput(reason, pass_, score)`、`GEvalOutput(reason, score)`。
- 内部 judge agent：`_judge_output_agent`、`_judge_input_output_agent`、`_judge_input_output_expected_agent`、`_judge_output_expected_agent`、`_judge_g_eval_agent`、`_non_text_judge_agent`。
- 公开 helper：`judge_output`、`judge_input_output`、`judge_output_expected`、`judge_input_output_expected`、`judge_g_eval`；`set_default_judge_model(model)`。默认模型 `'openai:gpt-5.2'`。
- 提示词按 `Input → Output → ExpectedOutput → Rubric` 构造。

### 2.4 基于 span 的确定性评估器（`agentic.py`）

无需 LLM，读取 `ctx.span_tree`（缺少 span 时优雅失败）：

| 评估器 | 说明 |
|--------|------|
| `ToolCorrectness(expected_tools, allow_extra, include_failed)` | 调用工具的 multiset |
| `TrajectoryMatch(expected_trajectory, order, include_failed)` | `order = 'exact' \| 'in_order' \| 'any_order'`（LCS / multiset F1） |
| `ArgumentCorrectness(tool_name, expected_arguments, match_mode, occurrence, include_failed)` | `match_mode = 'exact' \| 'subset'` |
| `MaxToolCalls(max_calls, ...)` / `MaxModelRequests(max_requests)` | 调用/请求次数上限 |

### 2.5 报告评估器（`report_evaluator.py` / `report_common.py`）

- `ReportEvaluatorContext(name, report, experiment_metadata)`；`ReportEvaluator(BaseEvaluator)` 抽象 `evaluate(ctx)`。
- 内置 `DEFAULT_REPORT_EVALUATORS = (ConfusionMatrixEvaluator, KolmogorovSmirnovEvaluator, PrecisionRecallEvaluator, ROCAUCEvaluator)`。

---

## 3. `reporting/` — 报告

- `ReportCase`：单用例记录（`name`、`inputs`、`output`、`expected_output`、`scores` / `labels` / `assertions`、`task_duration` / `total_duration`、`span_id`、`trace_id`、`evaluator_failures`）。
- `ReportCaseFailure`：任务执行抛错的用例。
- `ReportCaseGroup`、`ReportCaseAggregate`（`average` / `average_from_aggregates`）。
- `EvaluationReport`：`name`、`cases`、`failures`、`analyses`、`experiment_metadata`；方法 `case_groups()`、`averages()`、`render(...)`、`print(...)`、`console_table(...)`、`failures_table(...)`。
- `analyses.py`：`ConfusionMatrix`、`PrecisionRecall`（含曲线/点）、`ScalarResult`、`TableResult`、`LinePlot`；`ReportAnalysis` 为按 `type` 判别的联合。

---

## 4. `otel/` — 基于 span 的评估

- `span_tree.py`：
  - `SpanQuery`（TypedDict，可序列化过滤条件）。
  - `SpanNode`：`name`、`trace_id`、`span_id`、`parent_span_id`、时间戳、`attributes`、`status`；`duration`、`children`、`descendants`、`ancestors`；`find_children` / `find_descendants` / `find_ancestors` / `matches`。
  - `SpanTree`：由 span 构建父子层级；`find` / `first` / `any`、DFS 迭代、`repr_xml`。
- `_context_subtree.py`：`context_subtree()` 捕获用例 span。
- `_errors.py`：`SpanTreeRecordingError`；`_context_in_memory_span_exporter.py`：内存导出器。

---

## 5. `_task_run.py` — 任务运行的度量收集

- `TaskRun`：累加 `attributes` 与 `metrics`（`record_metric` / `increment_metric` / `record_attribute`）。
- `run_task()` 上下文管理器：设置 `CURRENT_TASK_RUN` contextvar，捕获 span 树。
- `extract_span_tree_metrics(task_run, span_tree)`：从 `gen_ai.*` span 属性推导 `requests`、`cost` 与 token 用量。

---

## 6. 在线评估（`online.py` / `online_capability.py`）

- `SamplingContext`、`SamplingMode = 'independent' | 'correlated'`、`SpanReference(trace_id, span_id)`。
- `OnlineEvaluator`：包装 `Evaluator`，带 `sample_rate`、`max_concurrency`、`sink`、错误回调。
- `OnlineEvalConfig`：`default_sink`、`default_sample_rate`、`emit_otel_events`、`include_baggage`、`sampling_mode`、`enabled`、`metadata` 等；提供 `evaluate(...)` 装饰器工厂。
- 模块函数：`evaluate(...)`（装饰器，使用 `DEFAULT_CONFIG`）、`configure(...)`、`run_evaluators`、`wait_for_evaluations`、`disable_evaluation`。
- Sink：`_online.py` 的 `SinkPayload`、`EvaluationSink` 协议、`CallbackSink`、异步/后台线程派发、采样。
- `online_capability.py`：`OnlineEvaluation(AbstractCapability)` —— 通过 `wrap_run` 在每次运行完成后异步派发评估器（包装 `agent.run` / `run_stream` / `iter`）。

---

## 7. 数据集生成（`generation.py`）

- `async generate_dataset(*, dataset_type, path, custom_evaluator_types, model='openai:gpt-5.2', n_examples, extra_instructions) -> Dataset`：用 LLM `Agent` 合成符合 `dataset_type.model_json_schema_with_evaluators(...)` 的数据集。

---

## 8. 与 Pydantic AI 的关系

- 传入 `Dataset.evaluate` 的 task 可以是 `Agent.run` 风格的可调用对象；其输出、时长与捕获的 OTel span 树构成 `EvaluatorContext`。
- LLM-as-a-judge 内部使用 `pydantic_ai.Agent`。
- 在线评估既能通过 `OnlineEvaluation` 能力附着到运行中的 Agent，也能通过 `@evaluate` 装饰器附着到任意函数。
- `pydantic-evals` **依赖** `pydantic-ai-slim`；而 `pydantic-graph` 是 `pydantic-ai` 的独立叶子依赖（见 [08](08-pydantic-graph.md)）。
