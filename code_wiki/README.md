# Pydantic AI Code Wiki

本 Wiki 是对 **Pydantic AI** 仓库（`/workspace`）的结构化源码导览，面向需要深入理解代码库的开发者与 Agent。

内容覆盖：项目整体架构、各主要模块职责、关键类与函数说明、依赖关系，以及构建 / 运行 / 测试方式。

> 本文档基于对仓库源码的静态阅读整理；所有结论均可在所引用的文件路径中核对。

---

## 一、项目是什么

Pydantic AI 是由 [Pydantic](https://pydantic.dev) 团队维护的、provider 无关的 Python AI Agent 框架。核心卖点：

- **任意模型、同一套 Python API**：通过 `'provider:model'` 字符串即可切换 OpenAI / Anthropic / Google / Bedrock / Groq / Mistral / xAI / Ollama 等数十个模型。
- **端到端类型安全**：结构化输出（structured output）、类型化依赖注入（dependency injection）、类型化工具（tools）。
- **可组合的 Capability 原语**：把工具、指令、Hook、模型设置打包为可复用的 `Capability`。
- **多界面**：CLI、内置 Web 聊天、实时语音（Realtime）、AG-UI / Vercel AI 事件流、GitHub Actions（gh-aw）。
- **Durable Execution**：Temporal / DBOS / Prefect / Restate / AWS Lambda / Kitaru / Airflow / Absurd。
- **OpenTelemetry 原生可观测性**（Logfire）。

---

## 二、文档目录

| # | 文档 | 内容 |
|---|------|------|
| 01 | [架构总览](01-architecture-overview.md) | 单仓库（monorepo）布局、包职责、依赖方向、整体数据流 |
| 02 | [核心 Agent 循环](02-core-agent-loop.md) | `Agent`、运行方法、`_agent_graph` 节点、`RunContext`、结果对象、异常体系 |
| 03 | [模型 / Provider / Profile](03-models-providers-profiles.md) | `Model` 抽象、`Provider`、`ModelProfile`、`ModelSettings`、Embeddings |
| 04 | [消息协议与输出](04-messages-and-output.md) | 规范化消息协议 `messages.py`、公开输出 API 与内部输出机制 |
| 05 | [工具 / Toolset / Capability](05-tools-toolsets-capabilities.md) | `ToolDefinition`、`AbstractToolset`、`AbstractCapability`、MCP、Workspace |
| 06 | [Durable Execution](06-durable-execution.md) | 共享持久化抽象与 Temporal / DBOS / Prefect 适配 |
| 07 | [UI 适配层与 Realtime](07-ui-and-realtime.md) | AG-UI / Vercel AI / Web 适配、实时语音会话 |
| 08 | [Pydantic Graph](08-pydantic-graph.md) | 类型化图执行引擎（Agent 循环的底层引擎） |
| 09 | [Pydantic Evals](09-pydantic-evals.md) | 数据集、评估器、报告、在线评估 |
| 10 | [Pydantic AI Harness](10-pydantic-ai-harness.md) | 官方 Capability 库（Coder、Researcher、记忆、规划等） |
| 11 | [CLI 与终端客户端](11-cli-clai-clai2.md) | `clai`、`pydantic-clai2`、`Agent.to_cli` |
| 12 | [开发工作流](12-development-workflow.md) | 构建 / 测试 / 类型检查 / CI / 发布 / 贡献流程 |

---

## 三、30 秒速览：一次 Agent 运行发生了什么

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

## 四、仓库顶层目录

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
| `.github/` | CI、发布、Agentic Workflow（gh-aw）定义与脚本 |
| `scripts/` | 开发/测试辅助脚本 |

---

## 五、约定与规范（阅读代码前应知）

- **编码规范**：见 [agent_docs/index.md](../agent_docs/index.md) 与各目录下的 `AGENTS.md`。
- **核心架构约束**：见 [agent_docs/pydantic-ai-slim.md](../agent_docs/pydantic-ai-slim.md)（定义 `Agent` / `_agent_graph` / `tool_manager` / `output` / `messages` / `models` / `providers` / `profiles` / `capabilities` / `durable_exec` / `ui` 的职责边界）。
- **类型安全**：Pyright `strict` 模式；避免 `Any` 与不必要的 `cast`。
- **测试**：`anyio_mode = "auto"`、覆盖率门禁 `fail_under = 100`、以 VCR 回放为主。
