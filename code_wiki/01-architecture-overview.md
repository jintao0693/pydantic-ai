# 01 · 架构总览

## 1. 单仓库（monorepo）结构

仓库使用 [`uv`](https://docs.astral.sh/uv/) workspace，根 [pyproject.toml](../pyproject.toml) 声明成员：

```toml
[tool.uv.workspace]
members = [
    "pydantic_ai_slim",   # pydantic-ai-slim  （核心）
    "pydantic_evals",     # pydantic-evals
    "pydantic_graph",     # pydantic-graph
    "clai",               # clai（CLI）
    "examples",           # pydantic-ai-examples
    "src/pydantic_ai_harness",  # pydantic-ai-harness
    "src/pydantic_clai2",       # pydantic-clai2
]
```

根包 `pydantic-ai` 本身不含业务代码，它只做「聚合安装」：

- `dependencies = ["pydantic-ai-slim[openai,anthropic,google,cli,mcp,evals,web,logfire]==<version>"]`
- 通过 `[tool.hatch.metadata.hooks.uv-dynamic-versioning.optional-dependencies]` 把 slim 的各个可选 extra ra 转发出去（如 `pydantic-ai[bedrock]`、`pydantic-ai[temporal]`）。

版本采用 `uv-dynamic-versioning`（基于 git tag，PEP 440）。harness 用 `0.{{ minor }}.{{ patch }}` 跟随 pydantic-ai 的 minor/patch（例如 pydantic-ai 2.51.0 ↔ harness 0.51.0）。

## 2. 包职责一览

| 包 | 目录 | 版本 | 职责 |
|----|------|------|------|
| **pydantic-ai-slim** | `pydantic_ai_slim/pydantic_ai/` | 2.x | 核心：Agent 循环、消息协议、模型适配、工具/Toolset、Capability、Workspace、Durable 适配、UI 适配、Realtime |
| **pydantic-graph** | `pydantic_graph/` | 独立 | 类型化图/状态机引擎，Agent 循环的执行底层 |
| **pydantic-evals** | `pydantic_evals/` | 独立 | 评估任意随机函数（含 LLM/Agent）的框架 |
| **pydantic-ai-harness** | `src/pydantic_ai_harness/` | 0.x | 官方 Capability 库：Coder、Researcher、记忆、规划、沙箱、Guardrail 等 |
| **pydantic-clai2** | `src/pydantic_clai2/` | 0.x | 能力感知的终端客户端（终端 UI + 插件系统） |
| **clai** | `clai/` | 独立 | 官方 CLI，是对核心 `pydantic_ai._cli` 的薄封装 |
| **pydantic-ai-examples** | `examples/` | - | 示例应用集合 |

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
       │               │                   │
┌──────▼──────┐  ┌─────▼───────┐   ┌───────▼─────────────┐
│pydantic-evals│  │    clai     │   │ pydantic-ai-harness │
│ (评估框架)   │  │ (薄封装 CLI) │   │   (Capability 库)    │
└──────────────┘  └─────────────┘   └───────────┬─────────┘
                                                 │
                                       ┌─────────▼─────────┐
                                       │ pydantic-clai2    │
                                       │ (终端客户端)       │
                                       └───────────────────┘
```

关键点：

- `pydantic-graph` 是**纯叶子依赖**，不依赖 pydantic-ai。
- `pydantic-ai-slim` 依赖 `pydantic-graph`、`pydantic>=2.12`、`anyio>=4.7`、`httpx2>=2.7`、`opentelemetry-api`、`typing-inspection`、`genai-prices`。
- `pydantic-evals` 依赖 `pydantic-ai-slim`（其 LLM-as-a-judge 直接用 `Agent` 实现）。
- `pydantic-ai-harness` 依赖 `pydantic-ai-slim`（版本精确对齐）。
- `pydantic-clai2` 依赖 `pydantic-ai-harness[coder]`。
- `clai` 只依赖 `pydantic-ai`（核心 CLI），**不依赖 harness**。

## 4. 核心包内部结构（`pydantic_ai/`）

依据 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md) 的职责划分：

| 模块 / 目录 | 职责 |
|-------------|------|
| `agent/` | 用户面向的构建与运行 API（`Agent`、`AbstractAgent`、`WrapperAgent`、`AgentSpec`） |
| `_agent_graph.py` | 循环编排：prompt 组装、模型请求、工具/输出处理、重试、用量检查、终结 |
| `tool_manager.py`、`tools.py`、`_tool_execution.py`、`toolsets/` | 工具发现、校验、执行、重试、审批/延迟、包装组合、稳定工具身份 |
| `output.py` / `_output.py` | 公开输出 API / 内部输出 schema 与处理器 |
| `messages.py` | 规范化消息协议（provider / UI / durable / 持久化历史都以此为往返格式） |
| `models/` | 规范化请求/响应 ↔ 各 provider 线上格式的映射 |
| `providers/` | 鉴权、客户端、base URL、HTTP 生命周期、provider 级 model/profile 推断 |
| `profiles/` | 模型家族事实（结构化输出默认值、schema 怪癖、native tool / thinking 支持等） |
| `capabilities/` | 可组合的横切行为（指令、设置、工具集、native 工具、wrapper toolset、各类 Hook） |
| `durable_exec/` | 将 agent/model/toolset 适配到持久化运行时 |
| `ui/` | 为前端协议翻译规范化消息/事件（AG-UI、Vercel AI、Web） |
| `realtime/` | 实时（双向语音）会话模型与 provider 适配 |
| `workspaces/` | Workspace 后端抽象（本地/只读/不可用等） |
| `embeddings/` | Embedding 模型抽象与 provider |
| `common_tools/` | 内置工具（DuckDuckGo、WebFetch、图像生成、Tavily、Exa 等） |
| `_cli/` | 内置 CLI（`clai` / `pai`）与 `clai web` |

## 5. 一次运行的端到端数据流

以 `Agent('openai:gpt-4o').run('...')` 为例：

1. **模型解析**：`infer_model('openai:gpt-4o')` → `infer_provider('openai')` → `OpenAIProvider` → 具体 `Model` 实例（如 `OpenAIResponsesModel`）。见 [03](03-models-providers-profiles.md)。
2. **构造运行**：`Agent.iter()` 返回 `AgentRun`，内部通过 `_prepare_run()` 把此次运行的输入解析为私有的 `_PreparedAgentRun`（负责资源进入、能力生命周期、恢复与清理）。
3. **图执行**（pydantic-graph）：注册的节点依次运行
   - `UserPromptNode`：清理/追加历史、解析指令与 system prompt、构建首个 `ModelRequest`。
   - `ModelRequestNode`：调用 `Model.request` / `request_stream`，走 `before/wrap/after_model_request` 钩子链；处理流式续段（continuation）、重试、用量限制。
   - `CallToolsNode`：按 `end_strategy` 分类并执行工具调用 / 输出工具；产出 `FinalResult` 或进入下一轮。
4. **终结**：`End(FinalResult)` → `AgentRunResult`（含 `output`、`all_messages()`、`usage`、`new_messages()` 等）。
5. **横切**：Capability 通过 Hook 在各步骤织入指令、设置、工具与行为；Instrumentation 生成 OTel span；Durable 适配把模型/工具调用变成持久化单元。
6. **结果对象**：`AgentRunResult`（最终）、`StreamedRunResult`（流式）、`AgentRun`（逐步）。

## 6. 关键设计不变量（跨模块）

- **Provider 事实只存在于 `providers/` / `profiles/` / `models/`**：不要在 graph/tool/output/ui 代码里散落 provider 名判断。
- **工具身份靠「是什么」而非「叫什么」**：用 `isinstance` 判断类型化 part（如 `ToolSearchCallPart`）或 `ToolDefinition.tool_kind`；工具名可被用户重命名/加前缀。
- **规范化协议往返**：provider 适配、UI 适配、durable 包装、持久化历史都应经 `messages.py` 的形状往返，不要把 provider 事实塞进字符串或临时字段。
- **Capability 优先于新增 `Agent` 构造参数**：能用 capability / toolset / model setting / profile 表达的行为，不应新增构造 kwarg。
- **向后兼容**：见 [docs/version-policy.md](../docs/version-policy.md)；默认值必须保持既有行为，新行为需显式 opt-in。

## 7. 延伸阅读

- 各目录的 `AGENTS.md`：`pydantic_ai_slim/pydantic_ai/AGENTS.md`、`.../capabilities/AGENTS.md`、`.../durable_exec/AGENTS.md`、`.../models/AGENTS.md`、`.../ui/AGENTS.md`、`.../toolsets/AGENTS.md` 等。
- 官方文档源在 [docs/](../docs/)，页面路由由 [docs/navigation.yml](../docs/navigation.yml) 管理。
