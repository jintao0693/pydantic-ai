# 10 · Pydantic AI Harness

`pydantic-ai-harness` 是 Pydantic AI 官方 Capability 库：把复杂、长时间运行任务所需的「电池」以 **Capability** 形式提供，可像搭积木一样附加到 Agent 上。它依赖 `pydantic-ai-slim`（版本精确对齐），版本号以 `0.{{ minor }}.{{ patch }}` 跟随 pydantic-ai。

源代码目录：[src/pydantic_ai_harness/pydantic_ai_harness/](../src/pydantic_ai_harness/pydantic_ai_harness/)

> 见 `src/pydantic_ai_harness/AGENTS.md`：每个 capability 是自包含子模块 `<name>/`，并从根 `__init__.py` 惰性重导出（`__getattr__`），因此导入 `pydantic_ai_harness` 不会拉入任何可选依赖。

---

## 1. 核心概念

- **Capability**：`AbstractCapability` 的子类，打包工具、Hook、指令与模型设置。
- **Hook**：生命周期方法（`before_model_request`、`wrap_run`、`after_tool_execute` 等）。
- **Toolset**：工具集合。
- **Guard**：校验输入/输出或控制工具访问。

`CombinedCapability` 的子类（如 `Coder`、`Researcher`）本身就是若干常规 capability 的组合——「拆开它们的方式和组装时一样」。

---

## 2. 完整 Agent Harness（可直接使用）

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `coder/` | `Coder(CombinedCapability)` | 完整编码 Agent 栈：六文件工具 + 持久 shell + 委派 + 上下文管理。另导出 `coder_agent`（无模型，供 CLI 以 `module:variable` 加载）与 `FILE_TOOL_NAMES` |
| `researcher/` | `Researcher(CombinedCapability)` | 完整 Web 研究栈：搜索 + 抓取 + 子研究员 + 有界工具输出。另导出 `researcher_agent` |

**`Coder` 的组成**（`coder/_capability.py`）：`RequireWorkspace('Coder')` → 携带说明的 `Capability` → `FileSystem(...)`（工具 `read_file`/`write_file`/`edit_file`/`list_files`/`grep`，`max_retries=5`）→ `Shell(...)` → 可选 `RepoContext` / `SubAgents` → `ClearToolResults`、`WarnNearLimits`、私有 `_BoundToolOutputs`（`ToolOutputLimits`，64,000 字符 Truncate）、`RepairToolArguments`。常量：`MAX_READ_CHARS = 50_000`、`MAX_OUTPUT_CHARS = 64000`。

---

## 3. Capability 模块（按主题）与对应 extra

### 3.1 执行环境 / Workspace

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `filesystem/` | `FileSystem`、`FileSystemToolset`、`FileReadEvent` 等 | 在 workspace 根下有界读/写/编辑/列目录/搜索；防路径穿越，敏感文件只读；可选 ripgrep |
| `shell/` | `Shell`、`ShellToolset`、`CommandStartedEvent` 等 | 带允许/拒绝列表、超时、凭证剥离、持久作业的命令执行 |
| `modal_sandbox/` | `ModalSandbox`、`ModalSandboxBackend` | 在隔离的 Modal 云沙箱中执行 |
| `e2b_sandbox/` | `E2BSandbox`、`E2BSandboxBackend` | 在隔离的 E2B 云沙箱中执行 |
| `sprites_sandbox/` | `SpritesSandbox`、`SpritesSandboxBackend` | 在持久 Fly.io Sprite 中执行 |
| `ssh_workspace/` | `SSHWorkspace`、`SSHWorkspaceBackend` | 经 `ssh` 远程 workspace（不隔离） |
| `bubblewrap_sandbox/` | `BubblewrapSandbox`、`BubblewrapWorkspace` | 包裹另一 workspace，使其命令在 Linux bubblewrap 沙箱中运行 |

### 3.2 工具与托管集成

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `github/` | `GitHub` | GitHub 托管 MCP：仓库、issue、PR |
| `slack/` | `Slack` | Slack 托管 MCP：消息、频道、画布 |
| `linear/` | `Linear` | Linear 托管 MCP：issue、项目、团队 |
| `notion/` | `Notion` | Notion 托管 MCP |
| `google_workspace/` | `GoogleWorkspace` | Google Workspace 托管 MCP（Gmail/Calendar/Drive） |
| `stackone/` | `StackOne` | 通过 StackOne 操作已连接的 SaaS 账号 |
| `ordinal/` `grain/` `day_ai/` `posthog/` `pylon/` | `Ordinal`、`Grain`、`DayAI`、`PostHog`、`Pylon` | 各类托管 MCP |
| `logfire_mcp/` | `LogfireMCP` | 查询 Logfire 遥测（工具前缀 `logfire_*`） |
| `localstack/` | `LocalStack` | 模拟 AWS 环境 + AWS CLI 工具 |
| `macroscope/` | `Macroscope`、`MacroscopeToolset` | 运行本地 Macroscope 代码审查并把发现交给 Agent |
| `exa/` | `ExaSearch`、`ExaAgent`、`ExaSearchToolset`、`ExaAgentToolset` | 基于 Exa 的 Web 研究 |
| `youdotcom/` | `YouSearch`、`YouResearch` | 基于 You.com 的搜索/研究 |
| `browser_use/` | `BrowserUse`、`BrowserUseToolset` | 委派开放式 Web 任务给自主浏览器 Agent |
| `playwright/` | `PlaywrightBrowser`、`PlaywrightBrowserToolset`、`EgressPolicy` | 驱动真实有状态 Chromium 页面 |

### 3.3 推理、规划与委派

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `planning/` | `Planning`、`PlanningToolset`；store `InMemoryPlanStore` / `SqlitePlanStore` / `PostgresPlanStore` / `RedisPlanStore` | 模型自持任务计划 + 缓存安全的实时提醒 |
| `subagents/` | `SubAgents`、`SubAgent`、`SubAgentToolset`、`DelegationTasks`、`DelegationReports` | 把独立任务委派给具名子 Agent |
| `dynamic_workflow/` | `DynamicWorkflow` | 用单个沙箱 Python 脚本编排子 Agent（带 `max_agent_calls` 预算） |
| `background_tools/` | `BackgroundTools` | 并发运行选定工具，结果以后续消息返回 |
| `advisor/` | `Advisor(NativeOrLocalTool)` | 让执行者在运行中途咨询更强的模型 |

### 3.4 上下文管理

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `compaction/` | `TieredCompaction`、`FallbackCompaction`、`SummarizingCompaction`、`SlidingWindowCompaction`、`ClearToolResults`、`DeduplicateFileReads`、`ClampOversizedMessages`、`ReportContextUsage`、`WarnNearLimits`；helper `compact_now`、`pin` / `reinject_pinned`、`estimate_token_count`、`resolve_context_window` | 把历史保持在上下文窗口内（LLM 摘要、滑窗裁剪、零成本清理工具结果、receipts、pin、实时用量报告） |
| `tool_output_limits/` | `ToolOutputLimits`、`Band` + 动作 `Passthrough` / `Truncate` / `Spill` / `Summarize`；store `OverflowStore` / `WorkspaceStore` / `LocalFileStore` | 在源头截断、落盘（可查询文件）或摘要过大的工具返回 |
| `warn_on_cache_busts/` | `WarnOnCacheBusts`、`CacheBustWarning`、`CacheNotEnabledWarning` | 依 provider 自身数字观察 prompt-cache 前缀坍缩 |
| `code_mode/` | `CodeMode`、`CodeModeToolset`、`EagerCodeModeToolset` | 模型写一个 Python 脚本在 Monty 沙箱中调用多个工具（一次往返，结果不进上下文） |
| `system_reminders/` | `SystemReminders`、`Reminder`、`GoalReanchor`、`LLMReminder` | 运行中途缓存安全地重注入指引，对抗指令衰减 |

### 3.5 知识与记忆

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `memory/` | `Memory`、`MemoryToolset`；store `MemoryStore` 协议、`InMemoryStore`、`FileStore`、`SqliteMemoryStore`、`PostgresMemoryStore` | 持久化、带命名空间的笔记本（有界注入 + 按需搜索） |
| `conversation_search/` | `ConversationSearch`、`ConversationSearchToolset`、`SnapshotHistorySource` | 对持久化步历史做 BM25 搜索（含被压缩丢弃的轮次） |
| `skills/` | `Skills`、`SkillDefinition` | 从 workspace 加载 Agent Skill（`SKILL.md`）为延迟能力 |
| `repo_context/` | `RepoContext`、`RepoContextToolset`、`AgentContextInventory`、`ContextFile` | 开局定向：读取 `AGENTS.md` / `CLAUDE.md` + 仓库结构 |
| `pydantic_ai_docs/` | `PydanticAIDocs` | 按需查询 Pydantic AI 文档 |

### 3.6 控制与安全

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `guardrails/` | `InputGuardrail`、`OutputGuardrail`、`ToolGuardrail`、`GuardrailResult`、`GuardrailError` / `InputBlocked` / `OutputBlocked`；`detectors.py`（`secret_data`、`personal_data`、`blocked_keywords`） | 校验/阻断/脱敏输入、工具调用、工具结果与输出 |
| `repair_tool_arguments/` | `RepairToolArguments` | schema 校验前修复畸形 JSON 工具参数（`json-repair`） |
| `prompt_injection_defender/` | `PromptInjectionDefender` | 对本地工具结果做间接提示注入分类，可扣留高风险结果 |
| `spend/` | `SpendLimits`；store `SpendStore` / `BatchSpendStore` / `InMemorySpendStore` / Redis | 跨窗口 USD/token 预算与按响应成本跟踪（按模型与租户） |
| `ask_user/` | `AskUser`、`AskUserToolset`、`AskUserRequest` / `AskUserAnswer` / `AskUserResponse`、`Answerer` 协议 | 模型中途向用户提出多选题，宿主提供回答者 |
| `tool_call_judge/` | `ToolCallJudge`、`ToolCallVerdict` | 第二个模型对每次工具调用回答一个风险问题，可在执行前阻断 |
| `trajectory_judge/` | `TrajectoryJudge`、`AllGood`、`Steer` | 第二个模型按节奏审查实时运行并在中途纠偏 |

### 3.7 运行时与持久化

| 模块 | 主要类 | 用途 |
|------|--------|------|
| `step_persistence/` | `StepPersistence`；store `StepStore` 协议、`InMemoryStepStore`、`FileStepStore`、`SqliteStepStore`、`MongoStepStore`、`PostgresStepStore`、`SqliteConversationStore`；类型 `StepEvent`、`ContinuableSnapshot`、`ToolEffectRecord`、`RunRecord` | 追加式事件日志 + 可继续快照，用于保存、恢复（`continue_run`）与分叉（`fork_run`）运行 |
| `absurd/` | `AbsurdDurability` | 把模型请求、MCP 调用、工具调用检查点到 PostgreSQL 上的 Absurd step |
| `aws_lambda/` | 能力（`_capability.py`） | 在 AWS Lambda durable functions 上持久化执行 |
| `media/` | `MediaStore` 协议；`DiskMediaStore`、`SqliteMediaStore`、`S3MediaStore`、`PostgresMediaStore`、`MongoMediaStore`；`externalize_media` / `restore_media` | 内容寻址媒体存储，把大二进制/文本 part 从快照中外部化 |
| `logfire/` | `ManagedPrompt` | 用 Logfire 托管 prompt 承载指令（版本化/灰度，无需重新部署） |

### 3.8 实验性与废弃 shim

- `experimental/acp/`：`PydanticAIACPAgent`、`run_acp_stdio`、`AcpSession`（Agent Client Protocol，编辑器集成）。
- 废弃 shim（仍可导入并告警）：`cache_stability/`→`warn_on_cache_busts`、`context/`→`repo_context`、`docs/`→`pydantic_ai_docs`、`overflowing_tool_output/`→`tool_output_limits`、`runtime_authoring/`→`capability_creation`。

### 3.9 内部 helper（下划线前缀，非 capability）

`_combine.py`、`_durable.py`、`_events.py`、`_mcp.py`、`_monty_exec.py`、`_output.py`、`_usage.py`、`_warn.py`、`_web_search.py`、`_workspace.py`（`RequireWorkspace`、`workspace_path`、`raise_tool_failure`）、`_workspace_provider.py`。

---

## 4. 使用示例

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai_harness import Advisor, Coder

agent = Agent(
    'anthropic:claude-fable-5-1',
    capabilities=[
        Coder(),          # 文件、shell、仓库上下文、子 Agent、上下文管理
        WebSearch(),      # 联网查文档与报错
        Advisor('openai:gpt-6-sol'),  # 卡住时咨询另一个模型
    ],
)
agent.to_cli_sync()
```

`Coder` 是普通的组合 Capability，可整体使用，也可拆开用其内部积木（二者等价）：

```python
capabilities = [
    FileSystem('.'), Shell(cwd='.'), RepoContext(), SubAgents(...),
    ClearToolResults(), WarnNearLimits(), ToolOutputLimits(), RepairToolArguments(),
]
```

---

## 5. extra 与依赖

`pydantic-ai-harness` 声明约 30 个可选 extra（`coder`、`researcher`、`code-mode`、`modal`、`e2b`、`sprites`、`browser-use`、`playwright`、`exa`、`youdotcom`、`github`、`slack`、`linear`、`notion`、`google-workspace`、`stackone`、`ordinal`、`grain`、`day-ai`、`posthog`、`pylon`、`logfire`、`absurd`、`aws-lambda`、`skills`、`acp`、`mongodb`、`prompt-injection-defender` 等）。

基础依赖：`genai-prices`、`httpx`、`json-repair`、`pydantic-ai-slim==<对齐版本>`。

示例运行：

```bash
uvx --with pydantic-ai-harness clai -a pydantic_ai_harness.coder:coder_agent -m anthropic:claude-fable-5
```

---

## 6. 相关资源

- `pydantic_ai_harness/.agents/skills/`：内置两个 Agent Skill——`pydantic-ai-harness` 与 `migrating-deep-agents-to-pydantic-ai-harness`。
- `agent_docs/`：capability 编写、测试、并发、核心边界等规范。
- `examples/`：`coding_agent.py`、`research_agent.py`。
- `gh-aw/`：GitHub Agentic Workflows 引擎定义。
- `integration_tests/`：localstack / mongodb / postgres / redis 的实时测试。
