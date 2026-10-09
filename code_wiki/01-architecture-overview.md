# 01 · 架构总览

本篇给出仓库的宏观结构：单仓库布局与版本策略、各包职责、依赖方向、核心包 `pydantic_ai/` 的内部目录职责、一次运行的端到端数据流（精确到函数）、跨模块设计不变量、以及仓库约定。

> 所有结论均可在所引用的文件路径中核对；行号区间使用 `#Lx-Ly` 形式指向源码。

---

## 1. 单仓库（monorepo）结构

仓库是 [`uv`](https://docs.astral.sh/uv/) workspace。根 [pyproject.toml](../pyproject.toml#L106-L115) 声明成员：

```toml
[tool.uv.workspace]
members = [
    "pydantic_ai_slim",         # pydantic-ai-slim  （核心框架）
    "pydantic_evals",           # pydantic-evals
    "pydantic_graph",           # pydantic-graph
    "clai",                     # clai（CLI）
    "examples",                 # pydantic-ai-examples
    "src/pydantic_ai_harness",  # pydantic-ai-harness
    "src/pydantic_clai2",       # pydantic-clai2
]
```

各成员通过 [tool.uv.sources](../pyproject.toml#L97-L104) 中 `{ workspace = true }` 互相引用，因此本地开发始终用工作区内的源码，而非 PyPI 上的已发布版本。

### 1.1 根包 `pydantic-ai` 只做「聚合安装」

根 [pyproject.toml](../pyproject.toml) 的 `name = "pydantic-ai"` 不含业务代码，它把核心与一组默认后端聚合为一次安装：

- [dependencies](../pyproject.toml#L49-L52)：
  ```toml
  pydantic-ai-slim[openai,anthropic,google,cli,mcp,evals,web,logfire]=={{ version }}
  ```
- 其余 extra（`bedrock`、`xai`、`temporal`、`ag-ui`、`realtime`、`spec` 等）通过 [tool.hatch.metadata.hooks.uv-dynamic-versioning.optional-dependencies](../pyproject.toml#L54-L86) 用 `pydantic-ai-slim[<name>]=={{ version }}` 原样转发，因此 `pydantic-ai[bedrock]` 与 `pydantic-ai-slim[bedrock]` 指向同一份可选依赖。

slim 包自身的必装依赖见 [pydantic_ai_slim/pyproject.toml](../pydantic_ai_slim/pyproject.toml#L56-L68)；可选依赖分组（每个 provider / integration 一组）见 [同文件 L70-L162](../pydantic_ai_slim/pyproject.toml#L70-L162)。新增可选分组时须同步更新 `docs/install.md`（源码注释即此约定）。

### 1.2 版本策略

- 版本由 `uv-dynamic-versioning` 基于 git tag 生成，PEP 440 风格，见 [pyproject.toml L5-L11](../pyproject.toml#L5-L11)。
- slim、evals、graph 通过 `pydantic-graph=={{ version }}` / `pydantic-evals=={{ version }}` 精确对齐同一版本号（[pydantic_ai_slim/pyproject.toml L64](../pydantic_ai_slim/pyproject.toml#L64)）。
- `pydantic-ai-harness` 与 `pydantic-clai2` 采用 `0.{{ minor }}.{{ patch }}` 跟随 pydantic-ai 的 minor/patch（例如 pydantic-ai 2.51.0 ↔ harness 0.51.0）。
- 向后兼容约束见 [docs/version-policy.md](../docs/version-policy.md)：**默认值必须保持既有行为，新行为需显式 opt-in**。

---

## 2. 包职责一览

| 包 | 目录 | 版本 | 职责 |
|----|------|------|------|
| **pydantic-ai-slim** | [pydantic_ai_slim/pydantic_ai/](../pydantic_ai_slim/pydantic_ai/) | 2.x | 核心：Agent 循环、消息协议、模型适配、工具/Toolset、Capability、Workspace、Durable 适配、UI 适配、Realtime |
| **pydantic-graph** | `pydantic_graph/` | 独立 | 类型化图/状态机引擎，Agent 循环的执行底层（叶子依赖） |
| **pydantic-evals** | [pydantic_evals/pydantic_evals/](../pydantic_evals/pydantic_evals/) | 独立 | 评估任意随机函数（含 LLM/Agent）的框架 |
| **pydantic-ai-harness** | [src/pydantic_ai_harness/](../src/pydantic_ai_harness/) | 0.x | 官方 Capability 库：Coder、Researcher、记忆、规划、沙箱、Guardrail、Workspace 后端等 |
| **pydantic-clai2** | [src/pydantic_clai2/](../src/pydantic_clai2/) | 0.x | 能力感知的终端客户端（终端 UI + 插件系统），基于 harness |
| **clai** | `clai/clai/` | 独立 | 官方 CLI，是对核心 `pydantic_ai._cli` 的薄封装 |
| **pydantic-ai-examples** | [examples/pydantic_ai_examples/](../examples/pydantic_ai_examples/) | - | 示例应用集合 |

> 说明：`pydantic-graph` 的包源码位于 `pydantic_graph/pydantic_graph/`（工作区成员目录 `pydantic_graph/` 另有 `pyproject.toml`/`README.md`），其对外 API 在 `pydantic_graph/pydantic_graph/__init__.py`；Agent 循环只依赖其中的 `Graph` / `GraphRun` / `BaseNode` / `GraphBuilder` / `End` / `GraphRunContext` 等基元。

---

## 3. 依赖方向

```
                ┌──────────────────────┐
                │   pydantic-graph     │  (叶子依赖：anyio, logfire-api, pydantic)
                └──────────┬───────────┘
                           │
   ┌───────────────────────▼────────────────────────┐
   │            pydantic-ai-slim (core)             │
   │  agent · models · messages · tools · caps ...  │
   └───┬───────────────┬───────────────────┬────────┘
       │               │                   │
┌──────▼──────┐  ┌─────▼───────┐   ┌───────▼─────────────┐
│pydantic-evals│  │    clai     │   │ pydantic-ai-harness │
│ (评估框架)   │  │(薄封装 CLI) │   │   (Capability 库)    │
└──────────────┘  └─────────────┘   └───────────┬─────────┘
                                                 │
                                       ┌─────────▼─────────┐
                                       │ pydantic-clai2    │
                                       │ (终端客户端)       │
                                       └───────────────────┘
```

关键点：

- `pydantic-graph` 是**纯叶子依赖**，不依赖 pydantic-ai。
- `pydantic-ai-slim` 必装依赖（[pydantic_ai_slim/pyproject.toml L57-L68](../pydantic_ai_slim/pyproject.toml#L57-L68)）：`anyio>=4.7.0`、`griffelib>=2.0`、`httpx2>=2.7`、`pydantic>=2.12`、`pydantic-graph=={{ version }}`、`opentelemetry-api>=1.28.0`、`typing-inspection>=0.4.0`、`genai-prices>=0.1.9`。所有 provider SDK 均为可选 extra，核心包在未安装时仍可导入。
- `pydantic-evals` 依赖 `pydantic-ai-slim`（LLM-as-a-judge 直接用 `Agent` 实现）。
- `pydantic-ai-harness` 依赖 `pydantic-ai-slim`，版本精确对齐。
- `pydantic-clai2` 依赖 `pydantic-ai-harness[coder]`。
- `clai` 只依赖 `pydantic-ai`（核心 CLI），**不依赖 harness**。
- 根 `pydantic-ai` 给终端用户提供「全量安装」，依赖方向上是聚合，不是被核心依赖。

---

## 4. 核心包内部结构（`pydantic_ai/`）

职责边界以 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md) 为准，目录事实以 [pydantic_ai_slim/pydantic_ai/](../pydantic_ai_slim/pydantic_ai/) 为准。

### 4.1 顶层模块（单文件）

| 模块 | 职责 |
|------|------|
| [agent/](../pydantic_ai_slim/pydantic_ai/agent/) | 用户面向的构建与运行 API（`Agent`、`AbstractAgent`、`WrapperAgent`、`AgentSpec`） |
| [_agent_graph.py](../pydantic_ai_slim/pydantic_ai/_agent_graph.py) | 循环编排：prompt 组装、模型请求、工具/输出处理、重试、用量检查、终结 |
| [tool_manager.py](../pydantic_ai_slim/pydantic_ai/tool_manager.py) | 工具发现/校验/执行/重试/审批延迟；`ToolManager` |
| [_tool_execution.py](../pydantic_ai_slim/pydantic_ai/_tool_execution.py) | 单步工具调用编排：`process_tool_calls` 与三个 `end_strategy` 处理器 |
| [output.py](../pydantic_ai_slim/pydantic_ai/output.py) | 公开输出 API（`ToolOutput` / `NativeOutput` / `PromptedOutput` / `TextOutput` / `Choices` 等） |
| [_output.py](../pydantic_ai_slim/pydantic_ai/_output.py) | 内部输出 schema（`OutputSchema.build`）、处理器（`*OutputProcessor`）、输出工具集 `OutputToolset` |
| [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py) | 规范化消息协议（provider / UI / durable / 持久化历史都以此为往返格式） |
| [run.py](../pydantic_ai_slim/pydantic_ai/run.py) | `AgentRun`、`AgentRunResult`、`EnqueueContent` 等运行态对象 |
| [result.py](../pydantic_ai_slim/pydantic_ai/result.py) | `AgentStream`、`StreamedRunResult`、`StreamedRunResultSync`、`FinalResult` |
| [_run_context.py](../pydantic_ai_slim/pydantic_ai/_run_context.py) | `RunContext`、`EventStreamBuffer`、事件派发与锚定证据 |
| [exceptions.py](../pydantic_ai_slim/pydantic_ai/exceptions.py) | 全部公开异常与警告 |
| [usage.py](../pydantic_ai_slim/pydantic_ai/usage.py) | `RunUsage` / `RequestUsage` / `UsageLimits` |
| [settings.py](../pydantic_ai_slim/pydantic_ai/settings.py) | `ModelSettings`（provider 相关 knob 用 `{provider}_` 前缀） |
| [concurrency.py](../pydantic_ai_slim/pydantic_ai/concurrency.py) | `ConcurrencyLimit` / `ConcurrencyLimiter` / `AbstractConcurrencyLimiter` |
| [retries.py](../pydantic_ai_slim/pydantic_ai/retries.py) | 基于 tenacity 的 HTTP 传输重试（`RetryConfig`、`*TenacityTransport`、`wait_retry_after`） |
| [conversation.py](../pydantic_ai_slim/pydantic_ai/conversation.py) | `Conversation`（message_history + usage + conversation_id 的聚合） |
| [mcp.py](../pydantic_ai_slim/pydantic_ai/mcp.py) | MCP 客户端封装（`MCPServer` 等） |
| [tools.py](../pydantic_ai_slim/pydantic_ai/tools.py) | `Tool`、`ToolDefinition`、`DeferredToolResults`、`ToolDenied`、`RunContext` 的重导出 |
| [template.py](../pydantic_ai_slim/pydantic_ai/template.py) | `TemplateStr`（可渲染模板字符串） |
| [direct.py](../pydantic_ai_slim/pydantic_ai/direct.py) | 直接调用模型（绕过 Agent 循环）的便捷入口 |
| [prices.py](../pydantic_ai_slim/pydantic_ai/prices.py) / [_genai_prices.py](../pydantic_ai_slim/pydantic_ai/_genai_prices.py) | 成本计算（genai-prices 集成） |
| [format_prompt.py](../pydantic_ai_slim/pydantic_ai/format_prompt.py) | `format_as_xml` |
| [function_signature.py](../pydantic_ai_slim/pydantic_ai/function_signature.py) / [_function_schema.py](../pydantic_ai_slim/pydantic_ai/_function_schema.py) | 由函数签名/文档串生成工具 JSON schema |
| [_system_prompt.py](../pydantic_ai_slim/pydantic_ai/_system_prompt.py) / [_instructions.py](../pydantic_ai_slim/pydantic_ai/_instructions.py) | system prompt 运行器 / 指令归一化与来源标注 |
| [_cancel.py](../pydantic_ai_slim/pydantic_ai/_cancel.py) | `CancellationToken`、`RunCancellation`（第一方取消控制器） |
| [_enqueue.py](../pydantic_ai_slim/pydantic_ai/_enqueue.py) | `PendingMessage` / `PendingMessageQueue`（`enqueue` 队列） |
| [_instrumentation.py](../pydantic_ai_slim/pydantic_ai/_instrumentation.py) / [_otel_messages.py](../pydantic_ai_slim/pydantic_ai/_otel_messages.py) | OTel 埋点与语义约定消息映射 |
| [_event_registry.py](../pydantic_ai_slim/pydantic_ai/_event_registry.py) | `AgentStreamEvent` 联合类型的运行时注册表 |
| [_history_processor.py](../pydantic_ai_slim/pydantic_ai/_history_processor.py) / [_history_mirroring.py](../pydantic_ai_slim/pydantic_ai/_history_mirroring.py) | 历史处理器 / 历史镜像 |
| [_spec.py](../pydantic_ai_slim/pydantic_ai/_spec.py) | Agent Spec（YAML/JSON）构建与能力注册表 |
| [_utils.py](../pydantic_ai_slim/pydantic_ai/_utils.py) | 跨模块共享工具与 typeguard 的规范归属地 |
| [_warnings.py](../pydantic_ai_slim/pydantic_ai/_warnings.py) | 警告类型（经 `exceptions.py` 重导出） |

### 4.2 子包/目录

| 目录 | 职责 |
|------|------|
| [agent/](../pydantic_ai_slim/pydantic_ai/agent/) | `Agent`（`__init__.py`）、`AbstractAgent`（`abstract.py`）、`WrapperAgent`（`wrapper.py`）、`AgentSpec`（`spec.py`） |
| [models/](../pydantic_ai_slim/pydantic_ai/models/) | 规范化请求/响应 ↔ 各 provider 线上格式的映射；`Model` 抽象在 `_abstract.py`；`instrumented.py`、`fallback.py`、`function.py`、`test.py` 为横切/测试模型 |
| [providers/](../pydantic_ai_slim/pydantic_ai/providers/) | 鉴权、客户端、base URL、HTTP 生命周期、provider 级 model/profile 推断 |
| [profiles/](../pydantic_ai_slim/pydantic_ai/profiles/) | 模型家族事实（结构化输出默认值、schema 怪癖、native tool / thinking 支持等） |
| [capabilities/](../pydantic_ai_slim/pydantic_ai/capabilities/) | 可组合的横切行为（指令、设置、工具集、native 工具、wrapper toolset、各类 Hook）；`abstract.py` 为基类，`combined.py`/`wrapper.py` 为容器与包装器 |
| [toolsets/](../pydantic_ai_slim/pydantic_ai/toolsets/) | `AbstractToolset` 与各实现（`function.py`、`combined.py`、`prepared.py`、`filtered.py`、`renamed.py`、`prefixed.py`、`_dynamic.py`、`external.py`、`approval_required.py`、`_tool_search.py` 等） |
| [native_tools/](../pydantic_ai_slim/pydantic_ai/native_tools/) | provider-native 工具抽象与工具搜索 |
| [durable_exec/](../pydantic_ai_slim/pydantic_ai/durable_exec/) | 将 agent/model/toolset 适配到持久化运行时（`temporal/`、`dbos/`、`prefect/`） |
| [ui/](../pydantic_ai_slim/pydantic_ai/ui/) | 为前端协议翻译规范化消息/事件（`ag_ui/`、`vercel_ai/`、`_web/`） |
| [realtime/](../pydantic_ai_slim/pydantic_ai/realtime/) | 实时（双向语音）会话模型与 provider 适配 |
| [workspaces/](../pydantic_ai_slim/pydantic_ai/workspaces/) | Workspace 后端抽象（`local.py`、`readonly.py`、`unavailable.py`、`protocol.py`、`conformance.py`） |
| [embeddings/](../pydantic_ai_slim/pydantic_ai/embeddings/) | Embedding 模型抽象与 provider |
| [images/](../pydantic_ai_slim/pydantic_ai/images/) | 图像生成模型抽象与 provider |
| [common_tools/](../pydantic_ai_slim/pydantic_ai/common_tools/) | 内置工具（DuckDuckGo、WebFetch、图像生成、Tavily、Exa、X Search） |
| [_cli/](../pydantic_ai_slim/pydantic_ai/_cli/) | 内置 CLI（`clai` / `pai`）与 `clai web` |
| [.agents/skills/](../pydantic_ai_slim/pydantic_ai/.agents/skills/) | 随包分发的 Agent 技能（构建 Pydantic AI agents、各框架迁移指南） |
| [ext/](../pydantic_ai_slim/pydantic_ai/ext/) | 第三方互操作（如 LangChain 转换） |

`pydantic_ai/__init__.py`（共 436 行，[L1-L120](../pydantic_ai_slim/pydantic_ai/__init__.py#L1-L120) 起）是公开命名空间的唯一定义点，把上述模块的公开符号集中重导出（`Agent`、`AgentRunEvents`、`EndStrategy`、异常、`messages` 类型、`Concurrency*`、`AgentSpec`、`Capability` 等）。新增公开符号应先落在职责模块，再在此重导出。

---

## 5. 一次运行的端到端数据流

以 `await agent.run('hi')`（模型为字符串 `'openai:gpt-5.2'`）为例，逐层下钻：

### 步骤 1 · 运行方法 → `iter()`

`AbstractAgent.run()`（[agent/abstract.py#L532-L681](../pydantic_ai_slim/pydantic_ai/agent/abstract.py#L532-L681)）本身不实现循环，它：

1. 若 `infer_name` 且 `Agent.name is None`，调用 `self._infer_name(inspect.currentframe())`；
2. `event_stream_handler = event_stream_handler or self.event_stream_handler`；
3. `async with self.iter(...) as agent_run:` 拿到 `AgentRun`；
4. 用 `while not isinstance(node, End)` 驱动：`node = await agent_run.next(node)`（传了 `event_stream_handler` 时改走 `_run_node_with_hooks(node, _stream_and_advance)`，让 handler 看到 `ModelRequestNode` / `CallToolsNode` 的 `node.stream()` 事件）；
5. `assert agent_run.result is not None; return agent_run.result`。

`Agent.iter()`（[agent/__init__.py#L1313-L1468](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1313-L1468)）先 `_agent_graph.resolve_conversation(...)` 归一 `message_history` / `usage` / `conversation_id`，再 `await self._prepare_run(...)` 得到 `_PreparedAgentRun`，最后 `async with prepared.open() as agent_run: yield agent_run`。

### 步骤 2 · 运行准备：`_prepare_run` → `_PreparedAgentRun`

`Agent._prepare_run()`（[agent/__init__.py#L1470-L2064](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L1470-L2064)）解析本次运行的全部输入：

- `binding = take_run_binding()`（消费 `run_stream_events` 的绑定）、构造 `RunCancellation`；
- 归一 `retries`（`_normalize_agent_retry_overrides`）；解析 `spec`（`_resolve_spec`，运行期 spec 是**增量**的，优先级 `run 参数 > spec > agent 默认`）；
- 解析 root capability（[`_base_run_capability`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3088)）、模型选择（[`_resolve_model_selection`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3118)）、运行期 capability（[`_resolve_run_capabilities`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3218)）、工具集列表（[`_build_toolset_list`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3484)）、输出 schema（[`_prepare_output_schema`](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L3539)）；
- 组装 `GraphAgentState` 与 `GraphAgentDeps`，返回 `_PreparedAgentRun`（[agent/__init__.py#L4325-L4350](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L4325-L4350)）。

`_PreparedAgentRun.open()`（[agent/__init__.py#L4353-L4596](../pydantic_ai_slim/pydantic_ai/agent/__init__.py#L4353-L4596)）用 `AsyncExitStack` 管资源生命周期：进入取消翻译（`_translate_cancellation`）、绑定取消控制器（`cancellation.bind()`）、并发限流上下文（`get_concurrency_context`）、模型进入（`enter_model`），然后 `self.graph.iter(...)` 得到 `GraphRun` 并包成 `AgentRun`。

### 步骤 3 · 图执行（pydantic-graph）

图由 `_agent_graph.build_agent_graph(name, deps_type, output_type)`（[_agent_graph.py#L2956-L2971](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2956-L2971)）委托 `_build_agent_graph(name)`（带 `@lru_cache(maxsize=128)`，**仅以 `name` 为键**，见 [L2974-L2999](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2974-L2999)）构建。`deps_type` / `output_type` 只绑定类型参数。

注册节点：`UserPromptNode`、`ModelRequestNode`、`CallToolsNode`、`SetFinalResult`；入口 `UserPromptNode`；输出类型 `FinalResult`。`GraphBuilder(..., auto_instrument=False)`，`build(validate_graph_structure=False)`。

节点序列（正常单轮工具/文本场景）：

1. **`UserPromptNode.run`**（[L660-L772](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L660-L772)）：取/建 `capture_run_messages` 列表，`_clean_message_history` 清理历史，`_repair_interrupted_tail` 修复中断尾巴，追加用户 prompt 与 system prompt，产出 `ModelRequestNode`。若历史末尾有未处理工具调用或 `deferred_tool_results`，直接转 `CallToolsNode`。
2. **`ModelRequestNode.run` / `.stream`**（[L1374-L1385](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1374-L1385) / [L1387+](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1387)）：`_prepare_request`（[L1746-L1808](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1746-L1808)）追加请求、`run_step += 1`、`_select_model`、刷新能力/工具揭示状态、`ToolManager.for_run_step`、解析指令、构建 `ModelRequestContext`、`usage_limits.check_before_request`；随后 `_make_request` 经 `wrap_model_request` / `before_model_request` / `after_model_request` 调用 `model_request` / `model_request_stream`。
3. **`CallToolsNode.run` / `.stream`**（[L2190](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2190) / [L2205](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2205)）→ `_handle_tool_calls`（[L2443-L2558](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2443-L2558)）：刷新揭示状态、`ToolManager.for_run_step`、在 `end_strategy='early'` 下判断响应文本/图像能否直接成为结果，然后 `process_tool_calls(...)`（[_tool_execution.py#L307-L374](../pydantic_ai_slim/pydantic_ai/_tool_execution.py#L307-L374)）执行工具；产出 `FinalResult` 时走 `_handle_final_result`（[L2652-L2672](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2652-L2672)）返回 `End(final_result)`，否则返回新的 `ModelRequestNode` 进入下一轮。
4. **终结**：`End(FinalResult)` → `AgentRun.result` 依据图输出与 `GraphAgentState` 构造 `AgentRunResult`。

> 流式场景下，若流已产出最终结果，`SetFinalResult.run`（[L2683-L2686](../pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2683-L2686)）可立即 `End`。

### 步骤 4 · 结果对象

- [`AgentRun`](../pydantic_ai_slim/pydantic_ai/run.py#L170)（逐步驱动 + 状态访问）；
- [`AgentRunResult`](../pydantic_ai_slim/pydantic_ai/run.py#L814)（最终结果，`output` / `all_messages()` / `usage` / `metadata` / `run_id` / `conversation_id` / `workspace` 等）；
- [`StreamedRunResult`](../pydantic_ai_slim/pydantic_ai/result.py#L572) / [`StreamedRunResultSync`](../pydantic_ai_slim/pydantic_ai/result.py#L972)（流式）；
- [`FinalResult`](../pydantic_ai_slim/pydantic_ai/result.py#L1197)（图标记输出，携带 `output` / `tool_name` / `tool_call_id`）。

### 步骤 5 · 横切

- **Capability** 通过 Hook 在各阶段织入指令、设置、工具与行为（详见 [02](02-core-agent-loop.md) 与 [05](05-tools-toolsets-capabilities.md)）。
- **Instrumentation** 生成 OTel span（`gen_ai.*` 属性），由 `Instrumentation` capability 的 `wrap_run` / `wrap_model_request` / `wrap_tool_execute` 实现。
- **Durable 适配**把模型/工具调用转成持久化单元（[06](06-durable-execution.md)）。

---

## 6. 关键设计不变量（跨模块）

以下约束来自 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md) 与源码注释，是改动时必须维护的：

- **Provider 事实只存在于 `providers/` / `profiles/` / `models/`**：不要在 graph/tool/output/ui 代码里散落 provider 名判断（`rule` 明令）。profile 的布尔开关（如 `bedrock_supports_prompt_caching`）优于内联 `isinstance()`。
- **工具身份靠「是什么」而非「叫什么」**：用 `isinstance` 判断类型化 part（如 `ToolSearchCallPart`）或 `ToolDefinition.tool_kind`；工具名可被用户重命名/加前缀。唯一例外是按 `kind` 命名的 native tool part，以及识别旧版本写下的历史（须在 legacy 处理代码内并注明原因）。
- **规范化协议往返**：provider 适配、UI 适配、durable 包装、持久化历史都应经 [messages.py](../pydantic_ai_slim/pydantic_ai/messages.py) 的形状往返，不要把 provider 事实塞进字符串或临时字段（如 `id` / `content` / `args`）；provider 特有数据放 `provider_details` / `provider_metadata`。
- **Capability 优先于新增 `Agent` 构造参数**：能用 capability / toolset / model setting / profile 表达的行为，不应新增构造 kwarg。跨切面工具集行为应扩展 `WrapperToolset`，而非改基类或各实现。
- **共享可变成员按引用传递**：`GraphAgentDeps.loaded_capability_ids`、`discovered_tool_names`、`durable_operations`、`run_capabilities_by_id` 与 `GraphAgentState` 的 `pending_messages`、`event_stream_buffer`、`mcp_tool_defs_cache`、以及 `cancellation` 都被**按引用**共享进每个 `RunContext`，只就地 mutate、绝不重新赋值（详见 [02 §6](02-core-agent-loop.md)）。
- **向后兼容**：默认值必须保持既有行为；新行为需显式 opt-in（见 [docs/version-policy.md](../docs/version-policy.md)）。删除公开 API 属于 major 版本范畴，但旧序列化历史仍需能反序列化。
- **本地工具与 provider-native 工具概念分离**：若二者可互为替代，须显式选择/回退并把产生的 message history 纳入测试。
- **类型安全**：Pyright `strict`；避免 `Any` 与不必要的 `cast`/`# type: ignore`；用 `assert_never` 收束联合类型穷尽分支。

---

## 7. 顶层目录清单

| 目录 / 文件 | 说明 |
|------|------|
| [pydantic_ai_slim/](../pydantic_ai_slim/) | 核心框架包 `pydantic-ai-slim`（`pydantic_ai/`） |
| [pydantic_graph/](../pydantic_graph/) | 图/状态机库 `pydantic-graph` |
| [pydantic_evals/](../pydantic_evals/) | 评估框架 `pydantic-evals` |
| [clai/](../clai/) | 官方 CLI 包 `clai` |
| [examples/](../examples/) | `pydantic-ai-examples` 示例应用 |
| [src/pydantic_ai_harness/](../src/pydantic_ai_harness/) | `pydantic-ai-harness` 官方 Capability 库 |
| [src/pydantic_clai2/](../src/pydantic_clai2/) | `pydantic-clai2` 能力感知的终端客户端 |
| [tests/](../tests/) | 测试套件（`models/`、`providers/`、`profiles/`、`harness/`、`durable_exec/`、`realtime/`、`evals/` 等子目录） |
| [docs/](../docs/) | 发布到 pydantic.dev 的文档源；路由由 [docs/navigation.yml](../docs/navigation.yml) 管理 |
| [agent_docs/](../agent_docs/) | 面向 Agent 的编码规范与架构说明（[index.md](../agent_docs/index.md)、[pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md)、主题指南） |
| [.github/](../.github/) | CI、发布、Agentic Workflow 定义与脚本 |
| [scripts/](../scripts/) | 开发/测试辅助脚本（含 `typecheck_changed.py`） |
| [code_wiki/](../code_wiki/) | 本套源码导览文档 |

---

## 8. 约定与规范

- **编码规范**：入口 [agent_docs/index.md](../agent_docs/index.md)；每个目录另有 `AGENTS.md`（如 [pydantic_ai/AGENTS.md](../pydantic_ai_slim/pydantic_ai/AGENTS.md)、`capabilities/`、`models/`、`providers/`、`profiles/`、`toolsets/`、`ui/`、`durable_exec/`、`native_tools/`、`realtime/`、`tests/`、`src/pydantic_ai_harness/AGENTS.md`）。
- **Lint / 格式化**：`ruff`（[pyproject.toml L304-L408](../pyproject.toml#L304-L408)），`line-length = 120`、单引号、`google` docstring 约定；ban `typing.TypedDict`（用 `typing_extensions.TypedDict`）与 `asyncio.Lock`（用 `anyio.Lock`）。
- **类型检查**：Pyright `strict`（[pyproject.toml L410-L450](../pyproject.toml#L410-L450)）；`# pyright: ignore` 需带 error code 与理由。单文件检查：`PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright path/to/file.py`。
- **测试**：`pytest` + `inline-snapshot` + `cassetter`（录制/回放 provider 请求）；`anyio_mode = "auto"`（`async def` 测试无需标记）；覆盖率门禁 `fail_under = 100`（[pyproject.toml L574-L601](../pyproject.toml#L574-L601)）。单测：`uv run pytest path/to/test.py::test_name`。
- **开发命令**：`make install`（装依赖与 hooks）、`make lint`、`make format`、`make typecheck`、`make test`。迭代期只做针对性的单文件/单测检查，仓库级 pyright/pytest/覆盖率交给 CI。
- **提交与 PR**：Conventional Commits 作 commit subject；PR 标题是祈使句且不用 `fix:`/`docs:` 前缀，代码标识符用反引号；不留 `Co-Authored-By`。详见根 [AGENTS.md](../AGENTS.md)。
- **文档**：官方文档源在 [docs/](../docs/)，页面路由在 [docs/navigation.yml](../docs/navigation.yml)；`tests/test_examples.py` 会执行文档中的代码示例。

---

## 9. 延伸阅读

- 各目录 `AGENTS.md`，尤其 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md)（职责边界、兼容性检查清单、Run Event Stream / Cancellation 内部机制）。
- 主题指南：[docs/](../docs/) 下的 `agent.md`、`tools.md`、`output.md`、`message-history.md`、`dependencies.md`、`capabilities/`、`durable-exec/` 等。
- 后续篇章：[02 核心 Agent 循环](02-core-agent-loop.md)、[03 模型/Provider/Profile](03-models-providers-profiles.md)、[04 消息协议与输出](04-messages-and-output.md)、[05 工具/Toolset/Capability](05-tools-toolsets-capabilities.md)。
