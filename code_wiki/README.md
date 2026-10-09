# Pydantic AI Code Wiki

本 Wiki 是对 **Pydantic AI** 仓库（`/workspace`）的深度源码导览，面向需要深入理解、修改或扩展该代码库的开发者与编码 Agent。

内容覆盖：项目整体架构、各主要模块职责、关键类与函数签名、数据结构、调用链、设计不变量与扩展点，以及构建 / 运行 / 测试 / 发布方式。

> 本文档基于对仓库源码的静态阅读整理；所有结论均可通过文中给出的文件链接（相对 `code_wiki/` 目录）在所引用的源码中逐条核对。行号基于当前工作树。

---

## 一、项目是什么

Pydantic AI 是由 [Pydantic](https://pydantic.dev) 团队维护的、provider 无关的 Python AI Agent 框架。核心卖点：

- **任意模型、同一套 Python API**：通过 `'provider:model'` 字符串即可切换 OpenAI / Anthropic / Google / Bedrock / Groq / Mistral / xAI / Ollama 等数十个模型。
- **端到端类型安全**：结构化输出（structured output）、类型化依赖注入（dependency injection）、类型化工具（tools）。
- **可组合的 Capability 原语**：把工具、指令、Hook、模型设置打包为可复用的 `Capability`，可在不修改 `Agent` 构造参数的前提下织入横切行为。
- **多界面**：CLI、内置 Web 聊天、实时语音（Realtime）、AG-UI / Vercel AI 事件流、GitHub Actions（gh-aw）。
- **Durable Execution**：Temporal / DBOS / Prefect / Restate / AWS Lambda / Kitaru / Airflow / Absurd。
- **OpenTelemetry 原生可观测性**（Logfire）。

---

## 二、如何使用本 Wiki

- **想快速建立整体认知**：先读 [01 架构总览](01-architecture-overview.md)，再看 [README §四 30 秒速览](#四30-秒速览一次-agent-运行发生了什么)。
- **想理解一次运行的内部机制**：读 [02 核心 Agent 循环](02-core-agent-loop.md)，它把 `Agent.run` 到 `AgentRunResult` 的每一步落到具体函数。
- **想接入新模型 / Provider**：读 [03 模型 / Provider / Profile](03-models-providers-profiles.md)，重点是三层职责边界与 `ModelProfile`。
- **想写工具 / Capability / MCP 集成**：读 [05 工具 / Toolset / Capability](05-tools-toolsets-capabilities.md)。
- **想接持久化运行时**：读 [06 Durable Execution](06-durable-execution.md)。
- **想接前端 / 语音**：读 [07 UI 适配层与 Realtime](07-ui-and-realtime.md)。
- **想扩展图引擎或理解节点调度**：读 [08 Pydantic Graph](08-pydantic-graph.md)。
- **想写评估 / 数据集**：读 [09 Pydantic Evals](09-pydantic-evals.md)。
- **想用现成的能力积木（编码、研究、记忆、沙箱等）**：读 [10 Pydantic AI Harness](10-pydantic-ai-harness.md)。
- **想跑 CLI / 终端客户端**：读 [11 CLI 与终端客户端](11-cli-clai-clai2.md)。
- **想贡献代码 / 理解 CI**：读 [12 开发工作流](12-development-workflow.md)。

---

## 三、文档目录

| # | 文档 | 主要内容 |
|---|------|----------|
| 01 | [架构总览](01-architecture-overview.md) | monorepo 与 uv workspace 布局、各包职责与版本策略、依赖方向、核心包内部目录职责、一次运行的端到端数据流、跨模块设计不变量 |
| 02 | [核心 Agent 循环](02-core-agent-loop.md) | `Agent`/`AbstractAgent`/`WrapperAgent`/`AgentSpec`、运行方法矩阵与装饰器、`_agent_graph.py` 节点与步骤序列、`RunContext`、`ToolManager` 与工具执行、结果对象、异常体系、end_strategy、重试与用量限制 |
| 03 | [模型 / Provider / Profile](03-models-providers-profiles.md) | `Model` 抽象与 `prepare_request`/`prepare_messages`、`ModelRequestParameters`、`StreamedResponse`、模型字符串推断、`Provider` 注册表、`ModelProfile` 全字段、`ModelSettings`、用量与成本、Embeddings、函数签名→JSON Schema |
| 04 | [消息协议与输出](04-messages-and-output.md) | 规范化消息协议 `messages.py`（请求/响应 part 全表、`tool_kind` 判别）、媒体类型、流式事件、运行事件、历史维护 helper、公开输出 API 与内部输出机制（`OutputSchema`/`OutputProcessor`/`OutputToolset`） |
| 05 | [工具 / Toolset / Capability](05-tools-toolsets-capabilities.md) | `Tool`/`ToolDefinition`、工具执行流程与 end_strategy、`AbstractToolset` 与全部具体 Toolset、`AbstractCapability` 取值方法与全部生命周期钩子、`CombinedCapability` 组合与顺序、全部具体 Capability、`defer_loading`、MCP 集成、Workspace、内置工具 |
| 06 | [Durable Execution](06-durable-execution.md) | 共享抽象（`BaseDurabilityCapability`/`DurabilityEngineSpec`/`DurableOperation`/后端与 codec/`@durable_operation`）、Temporal / DBOS / Prefect 三引擎对比与子包清单、可运行示例、事件流缓冲区与名字兼容性等约束 |
| 07 | [UI 适配层与 Realtime](07-ui-and-realtime.md) | `UIAdapter` 信任模型与安全字段、`UIEventStream` 状态机与钩子、AG-UI / Vercel AI / Web 适配器、内置 Web 应用；Realtime 分层、`RealtimeModel` 与推断、`RealtimeSession` 与不变量、各 provider 适配器 |
| 08 | [Pydantic Graph](08-pydantic-graph.md) | 两种建模方式、公共 API、`BaseNode`/`End`/`Step`/`Decision`/`Join`/paths、`GraphBuilder` 方法全表与结构校验、`Graph`/`GraphRun` 的 fork-join 调度、Mermaid 渲染、与 Pydantic AI 的对接 |
| 09 | [Pydantic Evals](09-pydantic-evals.md) | `Case`/`Dataset` 与 `evaluate` 流程、`CaseLifecycle`、评估器体系（内置 / LLM-as-a-judge / 基于 span 的确定性评估器 / 报告评估器）、报告与图表、span 树 API、任务运行度量、在线评估、数据集生成 |
| 10 | [Pydantic AI Harness](10-pydantic-ai-harness.md) | 惰性导入机制、`Coder`/`Researcher` 完整栈、按主题分组的全部官方 Capability（执行环境、托管集成、规划委派、上下文管理、知识记忆、控制安全、运行时持久化、实验性）、extra 与依赖、可运行示例 |
| 11 | [CLI 与终端客户端](11-cli-clai-clai2.md) | 内置 `_cli`（`clai`/`pai`）命令行与 slash 命令、`Agent.to_cli`、`clai` 薄封装包、`pydantic-clai2` 分层与源码布局、插件 API、`examples` 示例集合 |
| 12 | [开发工作流](12-development-workflow.md) | uv 环境与 Makefile 目标、Ruff/Pyright/mypy/pytest/coverage 配置与门禁、pre-commit 钩子、测试约定（VCR、fixture）、CI 作业全表与 Agentic Workflows、发布流程、辅助脚本、文档与提交规范 |

---

## 四、30 秒速览：一次 Agent 运行发生了什么

```
Agent('openai:gpt-4o').run('hi')
  └─ infer_model('openai:gpt-4o')            # 字符串 -> 具体 Model 实例
  └─ Agent.iter()                            # 基于 pydantic_graph 的图运行
       └─ UserPromptNode    # 组装 prompt / 指令 / system prompt / 历史
       └─ ModelRequestNode  # 调用模型（含流式、重试、用量限制）
       └─ CallToolsNode     # 处理模型返回的 tool calls / 输出
             └─ (无工具调用则) End(FinalResult) -> AgentRunResult
```

- 引擎：[pydantic_graph](08-pydantic-graph.md) 提供 `Graph` / `GraphRun`；节点定义在 `_agent_graph.py`。
- 模型层：[models/](03-models-providers-profiles.md) 把规范化请求翻译成各 provider 的线上格式。
- 协议层：[messages.py](04-messages-and-output.md) 是 provider 无关的规范化消息协议。
- 扩展层：[capabilities/](05-tools-toolsets-capabilities.md) 通过 Hook 在运行各处织入横切行为。

---

## 五、仓库顶层目录

| 目录 | 说明 |
|------|------|
| `pydantic_ai_slim/pydantic_ai/` | 核心框架包 `pydantic-ai-slim`（Agent 循环、模型、工具、能力等） |
| `pydantic_graph/` | 图/状态机库 `pydantic-graph` |
| `pydantic_evals/` | 评估框架 `pydantic-evals` |
| `clai/` | 官方 CLI 包 `clai`（`pydantic_ai._cli` 的薄封装） |
| `examples/` | `pydantic-ai-examples` 示例应用 |
| `src/pydantic_ai_harness/` | `pydantic-ai-harness` 官方 Capability 库 |
| `src/pydantic_clai2/` | `pydantic-clai2` 能力感知的终端客户端 |
| `tests/` | 测试套件（含模型、provider、能力、持久化等子目录） |
| `docs/` | 发布到 pydantic.dev 的文档源 |
| `agent_docs/` | 面向 Agent 的编码规范与架构说明 |
| `code_wiki/` | 本源码导览（你可正在阅读的目录） |
| `.github/` | CI、发布、Agentic Workflow（gh-aw）定义与脚本 |
| `scripts/` | 开发/测试辅助脚本 |

---

## 六、核心概念速查

| 术语 | 一句话说明 | 详见 |
|------|-----------|------|
| `Agent` | 用户面向的构建与运行入口，组合模型 / 工具 / 能力 / 输出类型 | [02](02-core-agent-loop.md) |
| `RunContext` | 单次运行上下文，承载 deps、使用量、能力状态、工具上下文等 | [02](02-core-agent-loop.md) |
| `ModelProfile` | 模型家族固有事实（是否支持结构化输出、缓存、思考、native 工具等） | [03](03-models-providers-profiles.md) |
| `ModelMessage` | 规范化消息协议，provider / UI / durable / 历史持久化的统一往返格式 | [04](04-messages-and-output.md) |
| `ToolDefinition` | 序列化到线上的工具定义；`kind` 表功能角色，`tool_kind` 表类型化 part 形状 | [04](04-messages-and-output.md)/[05](05-tools-toolsets-capabilities.md) |
| `AbstractToolset` | 带生命周期、指令与执行边界的可复用工具集合 | [05](05-tools-toolsets-capabilities.md) |
| `AbstractCapability` | 可组合的横切行为（指令、设置、工具、Hook）的统一载体 | [05](05-tools-toolsets-capabilities.md) |
| Durable Execution | 把模型/工具调用变成可记录与重放的持久化单元 | [06](06-durable-execution.md) |
| `UIAdapter` | 把前端协议输入翻译为 Agent 运行、把运行事件翻译为协议事件 | [07](07-ui-and-realtime.md) |
| `BaseNode` / `Graph` | `pydantic-graph` 的声明式节点与执行引擎，Agent 循环的底层 | [08](08-pydantic-graph.md) |
| `Dataset` / `Evaluator` | 评估用例集合与评估器，评估任意随机函数（含 LLM/Agent） | [09](09-pydantic-evals.md) |
| `Capability`（harness） | 官方能力积木，打包复杂能力（编码、研究、记忆、沙箱等） | [10](10-pydantic-ai-harness.md) |

---

## 七、约定与规范（阅读代码前应知）

- **编码规范**：见 [agent_docs/index.md](../agent_docs/index.md) 与各目录下的 `AGENTS.md`。
- **核心架构约束**：见 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md)（定义 `Agent` / `_agent_graph` / `tool_manager` / `output` / `messages` / `models` / `providers` / `profiles` / `capabilities` / `durable_exec` / `ui` 的职责边界）。
- **类型安全**：Pyright `strict` 模式；避免 `Any` 与不必要的 `cast`。
- **测试**：`anyio_mode = "auto"`、覆盖率门禁 `fail_under = 100`、以 VCR 回放为主。
- **向后兼容**：默认值必须保持既有行为，新行为需显式 opt-in（见 [docs/version-policy.md](../docs/version-policy.md)）。

---

## 八、维护说明

- 各文档顶部标注了覆盖范围与相关源码目录；修改核心代码时请同步更新对应文档。
- 文档内链接统一使用相对 `code_wiki/` 目录的路径（如 `../pydantic_ai_slim/pydantic_ai/agent/__init__.py`），便于在本地与 IDE 中直接跳转。
- 如需新增主题，请在 [§三 文档目录](#三文档目录) 中登记编号并保持既有命名风格 `<NN>-<kebab-topic>.md`。
