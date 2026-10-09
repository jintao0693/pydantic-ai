# 10 · Pydantic AI Harness

`pydantic-ai-harness` 是 Pydantic AI 的**官方 Capability 库**：把复杂、长时间运行任务所需的「电池」以 **Capability** 形式提供，可像搭积木一样附加到 Agent 上（`Agent(capabilities=[...])`）。

- 源代码目录：[src/pydantic_ai_harness/](../src/pydantic_ai_harness/)
- 包实现：[src/pydantic_ai_harness/pydantic_ai_harness/](../src/pydantic_ai_harness/pydantic_ai_harness/)
- 版本策略：始终 `0.x`，且跟随 pydantic-ai 的 minor/patch——`pydantic-ai 2.51.0` 对应 `pydantic-ai-harness 0.51.0`（见 [`pyproject.toml`](../src/pydantic_ai_harness/pyproject.toml) 的 `format-jinja = "0.{{ minor }}.{{ patch }}..."`）。
- 依赖方向：**依赖** `pydantic-ai-slim`（精确对齐同版本）；基础依赖 `genai-prices>=0.0.71`、`httpx>=0.28.1`、`json-repair>=0.58.0`。

### 与 core 的边界

| 归属 | 拥有什么 |
|------|----------|
| Pydantic AI core | 运行时原语：Agent 循环语义、规范化消息、Model/Provider/Profile 行为、工具执行语义、Durable Execution 原语、通用 capability 钩子；以及需要模型或框架支持的 capability（`Thinking`、`WebSearch`、`WebFetch`、`MCP`、`ToolSearch`、`LocalWorkspace`、provider 原生 compaction 等） |
| Harness | 由上述原语组合出的可选、开箱即用能力：编码 Agent 工具、护栏、记忆、上下文管理、仓库工具、验证循环、Skills、规划、子 Agent 等 |

> 见 [`AGENTS.md`](../src/pydantic_ai_harness/AGENTS.md)：**边界是机械的，不是成熟度分层**。当某项变更需要新的 core 语义时，应停下并在 core 提议变更，而不是在 harness 里重造 core 行为。

---

## 1. 目录布局与惰性导入机制

每个 capability 是一个**自包含子模块** `<name>/`（模块名 = capability 名，一个模块对应一个 capability 或策略），测试在 `tests/harness/<name>/`，并维护两份手写文档：代码旁的 `README.md`（GitHub/PyPI）与 `docs/harness/<name>.md`（文档站）。

约定（见 [`agent_docs/index.md`](../src/pydantic_ai_harness/agent_docs/index.md) 与其 `code_mode` 范例）：

- `_capability.py`：公开的 capability 类
- `_toolset.py`：实现用的 toolset
- `__init__.py`：包级重导出
- 由根基 [`__init__.py`](../src/pydantic_ai_harness/pydantic_ai_harness/__init__.py) **惰性重导出**

根的 `__init__.py` 采用「惰性重导出」：`__all__` 列全名，实际解析由 `__getattr__(name)` 完成，映射表分三组：

- `_CAPABILITY_EXPORTS`：capability 名字 → 子模块（如 `'Coder': 'coder'`、`'Memory': 'memory'`、`'ToolGuardrail': 'guardrails'`）；
- `_CONSTANT_EXPORTS`：常量/辅助类型 —— `'SubAgent'`、`'BubblewrapWorkspace'`、`'DEFAULT_RESEARCHER_INSTRUCTIONS'`、`'E2BSandboxBackend'`、`'ModalSandboxBackend'`、`'SpritesSandboxBackend'`、`'SSHWorkspaceBackend'`、`'LLM_API_KEY_ENV_PATTERNS'`、`'READ_ONLY_TOOL_NAMES'`，各映射到其子模块；
- `_GUARDRAIL_EXPORTS`：guardrail 相关名称（含改名前的旧名 `GuardResult`/`InputGuard`/`OutputGuard` 等）。

`__getattr__` 只会 `import_module(f'.{module_name}', __name__)` 并 `getattr`——因此**导入 `pydantic_ai_harness` 本身不会拉入任何可选依赖**。唯一在根处急切导入的是 [`_warn.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_warn.py) 的 `HarnessDeprecationWarning`、`MCPReadOnlyNoToolsWarning`。

废弃 shim 模块（`cache_stability/`、`context/`、`docs/`、`overflowing_tool_output/`、`runtime_authoring/` 等）仍是可导入的包，导入时经 `warn_module_renamed(...)` 发出 `DeprecationWarning`。

---

## 2. 核心概念

| 术语 | 说明 |
|------|------|
| **Capability** | `AbstractCapability` 的子类，把工具、钩子、指令与模型设置打包为可复用单元——harness 的核心抽象 |
| **Hook** | `AbstractCapability` 上的生命周期方法，在 Agent 图执行各处拦截（`before_run`、`wrap_run`、`before_model_request`、`after_tool_execute` 等） |
| **Toolset** | capability 提供给 Agent 的工具集合 |
| **Guard** | 校验输入/输出或控制工具访问的一类 capability（如 `InputGuardrail`、`OutputGuardrail`） |
| **CombinedCapability** | 若干常规 capability 的组合；`Coder`、`Researcher` 即其子类，可整体使用，也可拆开用内部积木 |
| **AICA** | AI Code Assistant，实现 issue、审查计划、处理 PR 反馈的自动化 Agent（仓库开发流程用语） |

### Workspace 前置检查：`RequireWorkspace`

[`_workspace.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_workspace.py) 提供私有 helper，并被多数「需要 workspace」的能力复用以在运行开始时即失败：

| 名称 | 说明 |
|------|------|
| `RequireWorkspace(owner)` | capability，在 `before_run` 调 `require_workspace(...)`；作为组合能力的第一项，消息里点名 harness 名 |
| `require_workspace(workspace, owner, messages)` | 无 workspace 时抛 `UserError`，并给出附着指引；若 `messages` 续在某个 workspace，则点名它 |
| `workspace_path(path)` | 返回路径的 sandbox 拼写；拒绝 `~` 前缀 |
| `raise_tool_failure(error)` | 把 `WorkspaceError` 转成 `ToolFailed`（`WorkspaceUnavailableError` 直接重抛以结束运行） |
| `supports_commands` / `innermost_backend` / `is_unattached` | workspace 内省 |
| `secondary_workspace(value, owner)` | 校验能力自带的 `workspace=` 参数（须是 backend 而非 capability） |
| `METADATA_DIR` / `metadata_dir(workspace, name)` | `.pydantic-ai-harness/<name>` 目录（自动加 `.gitignore`） |

> harness 自有的文件（Shell 后台作业日志、工具输出溢出等）默认落在 workspace 工作目录下的 `.pydantic-ai-harness/`（已 git-ignore）。

---

## 3. 完整 Agent Harness（`Coder` / `Researcher`）

### 3.1 `Coder`（编码 Agent 栈）

`Coder(CombinedCapability[AgentDepsT])`（[`coder/_capability.py`](../src/pydantic_ai_harness/pydantic_ai_harness/coder/_capability.py)）：

```python
Coder(
    workspace: str | Path | None = None,   # 已弃用，传值会 warn_argument_ignored
    *,
    instructions: str | None = None,       # 追加到默认指导之后
    unrestricted_filesystem: bool = False, # 文件工具可达整个 workspace 文件系统
    repo_context: bool = True,             # 是否包含 RepoContext
    sub_agents: bool = True,               # 是否加入 delegate_task
    agent_folders: str | Sequence[str | Path] | None = None,
)
```

组成（按加入顺序）：

| 组成 | 参数 |
|------|------|
| `RequireWorkspace('Coder')` | — |
| `Capability(id='coder_instructions', instructions=[...])` | `INSTRUCTIONS`（+ 追加的 `instructions`）；`repo_context=True` 时追加 `project_instructions(unrestricted=...)` |
| `_file_system(unrestricted)` | `FileSystem(content_hashes=False, max_read_chars=MAX_READ_CHARS, tools=FILE_TOOL_NAMES, max_retries=MAX_FILE_TOOL_RETRIES)`；`unrestricted=True` 时 `replace(root_dir='/', read_only_patterns=[])` |
| `Shell(denied_commands=[], default_timeout=MAX_FOREGROUND_WAIT, allow_interactive=True, tools=['shell'])` | 持久 `shell` 工具 |
| `RepoContext(expose_inventory_tool=False)` | 仅 `repo_context=True` |
| `SubAgents(include_self=True, agent_folders=agent_folders)` | 仅 `sub_agents=True` |
| `ClearToolResults(max_fraction=0.7)` | — |
| `WarnNearLimits(max_context_fraction=0.9)` | — |
| `_BoundToolOutputs(id='coder_tool_output_limits', bands=[Band(over=MAX_OUTPUT_CHARS, action=Truncate(max_chars=MAX_OUTPUT_CHARS))])` | 私有 `ToolOutputLimits` 子类，`get_toolset()` 返回 `None`（无 spill 检索工具） |
| `RepairToolArguments()` | — |

常量与导出：

| 名称 | 值/含义 |
|------|---------|
| `FILE_TOOL_NAMES` | `('read_file', 'write_file', 'edit_file', 'list_files', 'grep')` |
| `MAX_READ_CHARS` | `50_000`（单次 `read_file` 的完整行字符上限） |
| `MAX_OUTPUT_CHARS` | `64000`（任何工具结果保留的字符数） |
| `MAX_FILE_TOOL_RETRIES` | `5`（每个文件工具允许的连续重试次数） |
| `coder_agent` | 无模型的 `Agent(name='coder', capabilities=[LocalWorkspace('.'), Coder()])`（非 POSIX 平台会退化为无 workspace），供 CLI 以 `module:variable` 加载 |

`FILE_TOOL_NAMES` 只有 5 个（`shell` 覆盖目录创建、文件元数据等）；README 所说的「六文件工具」含来自 `Shell` 的 `shell`。

### 3.2 `Researcher`（Web 研究栈）

`Researcher(CombinedCapability[AgentDepsT])`（[`researcher/_capability.py`](../src/pydantic_ai_harness/pydantic_ai_harness/researcher/_capability.py)）：

```python
Researcher(
    *,
    instructions: str | None = DEFAULT_RESEARCHER_INSTRUCTIONS,  # None 表示不带默认指令
    subagents: Sequence[SubAgent[AgentDepsT]] | None = None,
    store: OverflowStore | WorkspaceStore | None = None,
)
```

组成：`store is None` 或 `store` 是 `WorkspaceStore` 时先加 `RequireWorkspace('Researcher')`；`instructions` 非 `None` 时加 `Capability(instructions=...)`；随后 `WebSearch(local=True)`、`WebFetch(local=True)`、`SubAgents(agents=delegates, agent_folders=None)`（有 delegate 时）、`ToolOutputLimits(store=store)`。

默认子研究员 `_researcher(store)`：`Agent(name='researcher', description='Research a focused sub-question on the web and report back with findings and source links', capabilities=[WebSearch(local=True), WebFetch(local=True), ToolOutputLimits(store=store)])`，包进 `SubAgent(...)`。

导出：`DEFAULT_RESEARCHER_INSTRUCTIONS`（5 条默认指导：先广泛搜索、读支撑来源、优先一手权威来源、每个事实断言附直接链接、区分事实与推断）、`researcher_agent`（`Agent(name='researcher', capabilities=[LocalWorkspace('.'), Researcher()])`，非 POSIX 时退化为 `Researcher(store=LocalFileStore())`）。

---

## 4. Capability 全表（按主题）

### 4.1 执行环境 / Workspace

Workspace 提供 Agent 操作的文件与命令；harness 不替你选，须显式附着（否则运行开始时失败并提示）。

| 模块 | 主要类（真实导出） | 用途 |
|------|--------------------|------|
| [`filesystem/`](../src/pydantic_ai_harness/pydantic_ai_harness/filesystem/) | `FileSystem`、`FileSystemToolset`、`Replacement`、事件 `FileReadEvent`/`FileWrittenEvent`/`FileEditedEvent`/`DirectoryListedEvent`/`DirectoryCreatedEvent`/`FilesSearchedEvent`/`FileChangeRequestEvent`、`FileOperation`、`SearchKind`；常量 `DEFAULT_TOOL_NAMES`、`FILE_SYSTEM_TOOL_NAMES`、`READ_ONLY_TOOL_NAMES`、`RIPGREP_TOOL_NAMES`、`FILE_SYSTEM_EVENTS`、`MAX_DIFF_SOURCE_CHARS`、`MAX_EVENT_DIFF_CHARS` | 在 workspace 中 `root_dir` 下有界读/写/编辑/列目录/搜索；路径穿越校验、敏感文件默认只读（`read_only_patterns`）、可选 ripgrep（`list_files`/`grep`）；`content_hashes` 提供乐观并发控制 |
| [`shell/`](../src/pydantic_ai_harness/pydantic_ai_harness/shell/) | `Shell`、`ShellToolset`、事件 `CommandStartedEvent`/`CommandOutputEvent`/`CommandFinishedEvent`、常量 `SHELL_TOOL_NAMES`、`RUN_SCOPED_TOOL_NAMES`、`MAX_FOREGROUND_WAIT`、`LLM_API_KEY_ENV_PATTERNS`、`SHELL_EVENTS` | 带允许/拒绝列表、超时、凭证剥离（`denied_env_patterns`）、持久作业的命令执行；`tools=['shell']` 注册持久 shell 工具 |
| [`modal_sandbox/`](../src/pydantic_ai_harness/pydantic_ai_harness/modal_sandbox/) | `ModalSandbox`、`ModalSandboxBackend`、`ModalSandboxNoToolsWarning` | 隔离的 Modal 云沙箱作为 workspace；构造参数含 `image`、`app_name`、`sandbox_timeout`、`idle_timeout`、`working_dir`、`env`、`warn_if_no_tools` |
| [`e2b_sandbox/`](../src/pydantic_ai_harness/pydantic_ai_harness/e2b_sandbox/) | `E2BSandbox`、`E2BSandboxBackend` | 隔离的 E2B 云沙箱；构造参数含 `template`、`allow_internet_access`、`sandbox_timeout`、`working_dir`、`env` |
| [`sprites_sandbox/`](../src/pydantic_ai_harness/pydantic_ai_harness/sprites_sandbox/) | `SpritesSandbox`、`SpritesSandboxBackend` | 持久 Fly.io Sprite；构造参数含 `client`、`runtime`、`working_dir`、`env` |
| [`ssh_workspace/`](../src/pydantic_ai_harness/pydantic_ai_harness/ssh_workspace/) | `SSHWorkspace`、`SSHWorkspaceBackend` | 经 `ssh` 客户端在远端宿主机执行（不隔离）；构造参数 `destination`、`working_dir`、`read_only`、`env`、`ssh_args` |
| [`bubblewrap_sandbox/`](../src/pydantic_ai_harness/pydantic_ai_harness/bubblewrap_sandbox/) | `BubblewrapSandbox`、`BubblewrapWorkspace` | 包裹另一 workspace capability，使其命令在该 workspace 宿主机上以 Linux bubblewrap 沙箱运行（`network=False` 时 seccomp 过滤阻断套接字） |

> `LocalWorkspace` 属于 core（`from pydantic_ai.capabilities import LocalWorkspace`），是本地机器上的默认 workspace。`ModalSandbox`/`E2BSandbox`/`SpritesSandbox` 会拒绝 `defer_loading=True`（workspace 在延迟能力加载前就被选定）。

### 4.2 工具与托管集成

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`github/`](../src/pydantic_ai_harness/pydantic_ai_harness/github/) | `GitHub`、`GITHUB_MCP_URL` | GitHub 托管 MCP：仓库、issue、PR |
| [`slack/`](../src/pydantic_ai_harness/pydantic_ai_harness/slack/) | `Slack` | Slack 托管 MCP：消息、频道、画布 |
| [`linear/`](../src/pydantic_ai_harness/pydantic_ai_harness/linear/) | `Linear` | Linear 托管 MCP：issue、项目、团队 |
| [`notion/`](../src/pydantic_ai_harness/pydantic_ai_harness/notion/) | `Notion` | Notion 托管 MCP |
| [`google_workspace/`](../src/pydantic_ai_harness/pydantic_ai_harness/google_workspace/) | `GoogleWorkspace`、`GoogleWorkspaceService` | Google Workspace（Gmail/Calendar/Drive 等） |
| [`stackone/`](../src/pydantic_ai_harness/pydantic_ai_harness/stackone/) | `StackOne`、`StackOneToolset`、`ToolMode`、`STACKONE_API_KEY_ENV`、`STACKONE_BASE_URL` | 通过 StackOne 操作已连接的 SaaS 账号（HRIS、ATS、CRM…） |
| [`ordinal/`](../src/pydantic_ai_harness/pydantic_ai_harness/ordinal/) | `Ordinal` | Ordinal 托管 MCP：社交内容草拟/排期/分析 |
| [`grain/`](../src/pydantic_ai_harness/pydantic_ai_harness/grain/) | `Grain` | Grain 托管 MCP：会议、逐字稿、笔记 |
| [`day_ai/`](../src/pydantic_ai_harness/pydantic_ai_harness/day_ai/) | `DayAI` | Day AI 托管 MCP：CRM 记录与会议上下文 |
| [`posthog/`](../src/pydantic_ai_harness/pydantic_ai_harness/posthog/) | `PostHog` | PostHog 托管 MCP：产品分析、feature flag、实验、仪表盘 |
| [`pylon/`](../src/pydantic_ai_harness/pydantic_ai_harness/pylon/) | `Pylon` | Pylon 托管 MCP：支持工单、账号、联系人 |
| [`logfire_mcp/`](../src/pydantic_ai_harness/pydantic_ai_harness/logfire_mcp/) | `LogfireMCP`、`LOGFIRE_EU_MCP_URL`、`LOGFIRE_US_MCP_URL` | 查询 Logfire 遥测、管理可观测性资源 |
| [`localstack/`](../src/pydantic_ai_harness/pydantic_ai_harness/localstack/) | `LocalStack`、`LocalStackContainer`、`LocalStackToolset`、`LocalStackError` | 模拟 AWS 环境 + AWS CLI 工具 |
| [`macroscope/`](../src/pydantic_ai_harness/pydantic_ai_harness/macroscope/) | `Macroscope`、`MacroscopeToolset`、`MacroscopeIssue`、`MacroscopeReview`、`parse_macroscope_stream` | 运行本地 `macroscope codereview` 并把发现交给 Agent |

### 4.3 Web 与研究

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`exa/`](../src/pydantic_ai_harness/pydantic_ai_harness/exa/) | `ExaSearch`、`ExaAgent`、`ExaClient`、`ExaSearchToolset`、`ExaAgentToolset`、`ExaAgentRuns`、`ExaSource`、`RUN_ID_METADATA_KEY`、`agent_run_result` | 基于 Exa 的检索（带页面内容）与开放式研究委派 |
| [`youdotcom/`](../src/pydantic_ai_harness/pydantic_ai_harness/youdotcom/) | `YouSearch`、`YouResearch`、`YouSearchToolset`、`YouResearchToolset`、`YouClient`、`YouSource`、`ExtractionModeName`、`ResearchEffortName`、`FinanceEffortName` | 基于 You.com 的搜索/阅读与带引用的多步研究 |
| [`browser_use/`](../src/pydantic_ai_harness/pydantic_ai_harness/browser_use/) | `BrowserUse`、`BrowserUseToolset`、`BrowserAgent`、`BrowserAgentFactory`、`BrowserAgentHistory`、`BrowserAgentSettings`、`BrowserTask`、`default_browser_agent`、`ChatModelInput`、`PydanticAIChatModel`、`resolve_chat_model` | 把开放式 Web 任务委派给自主 browser-use Agent |
| [`playwright/`](../src/pydantic_ai_harness/pydantic_ai_harness/playwright/) | `PlaywrightBrowser`、`PlaywrightBrowserToolset`、`PlaywrightBrowserSession`、`EgressPolicy`、`EgressRequest`、`RequestKind`、`BrowserEvent`、`BrowserUnavailableError`、`BrowserUnavailableWarning`（及 `DEFAULT_*` 常量） | 驱动真实有状态 Chromium 页面：导航、点击、输入、读取、检查页面行为；`EgressPolicy` 控制出网 |

### 4.4 推理、规划与委派

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`planning/`](../src/pydantic_ai_harness/pydantic_ai_harness/planning/) | `Planning`、`PlanningToolset`、`render_plan`；store `PlanStore` / `InMemoryPlanStore` / `SqlitePlanStore` / `PostgresPlanStore` / `RedisPlanStore`；类型 `PlanItem`、`PlanStatusUpdate`、`TaskStatus`；事件 `PlanEventEmitter`、`PlanEvent`、`PlanCreatedEvent`、`PlanUpdatedEvent`、`PlanCompletedEvent`、`PlanDeletedEvent`、`PlanStatusChangedEvent`、`PlanEventType`、`PLANNING_EVENTS` | 模型自持任务计划 + **缓存安全**的实时提醒。工具面 `write_plan`/`read_plan`/`add_task`/`update_task_status`/`update_task_statuses`/`remove_task`，`enable_subtasks=True` 时加 `add_subtask`/`set_dependency`/`get_available_tasks`；`tools` 可收窄；当前计划作为**临时尾提醒**注入 |
| [`subagents/`](../src/pydantic_ai_harness/pydantic_ai_harness/subagents/) | `SubAgents`、`SubAgent`、`SubAgentToolset`、`ToolResolver`、`AgentOverride`、`ModelOption`、`DelegationTasks`、`DelegationTask`、`DelegationReports`、`DelegationStartEvent`/`DelegationEndEvent`/`DelegationOutcome`、`MAX_EVENT_TEXT_CHARS`、`SUB_AGENTS_EVENTS` | 把独立任务委派给具名子 Agent（单个 `delegate_task(agent_name, task)` 工具）；支持从 `agent_folders` 加载磁盘定义（Markdown/TOML）、`models` 菜单路由、`include_self`、`shared_capabilities`、`event_stream_handler` |
| [`dynamic_workflow/`](../src/pydantic_ai_harness/pydantic_ai_harness/dynamic_workflow/) | `DynamicWorkflow`、`DynamicWorkflowToolset`、`WorkflowAgent`、`WorkflowResourceLimits` | 模型写一个沙箱 Python 脚本（Monty）编排子 Agent（fan-out/chain/vote），带硬性 `max_agent_calls`（默认 50）预算；`tool_name='run_workflow'`，`forward_usage=True` 时共享 usage |
| [`background_tools/`](../src/pydantic_ai_harness/pydantic_ai_harness/background_tools/) | `BackgroundTools` | 并发运行选定工具，结果以后续消息返回；默认选择 `metadata={'background': True}` 的工具，`'optional'` 时由模型逐次决定 |
| [`advisor/`](../src/pydantic_ai_harness/pydantic_ai_harness/advisor/) | `Advisor`（`NativeOrLocalTool` 子类） | 让执行者在运行中途咨询更强的模型；`Advisor(model, *, mode='auto'\|'native'\|'local', output_type=str, max_uses=None, max_tokens=None, caching='5m'\|'1h'\|None, forward_history=False)` |

### 4.5 上下文管理

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`compaction/`](../src/pydantic_ai_harness/pydantic_ai_harness/compaction/) | `TieredCompaction`、`FallbackCompaction`、`SummarizingCompaction`、`SlidingWindowCompaction`、`ClearToolResults`、`DeduplicateFileReads`、`ClampOversizedMessages`、`ReportContextUsage`、`WarnNearLimits`；helper `compact_now`、`pin`/`reinject_pinned`/`is_pinned`、`estimate_token_count`、`estimate_context_tokens`、`resolve_context_window`、`DEFAULT_CONTEXT_WINDOW`、`CompactionStrategy`、`SupportsFocus`、`drain_summary_events`、`TranscriptHandleProvider`、`WarningKind`、`ContextUsage`、`ContextUsageEvent`、`REPORT_CONTEXT_USAGE_EVENTS` | 把历史保持在上下文窗口内：LLM 摘要、滑窗裁剪、零成本清理工具结果、去重文件读取、钳制超大消息、pin、实时用量报告 |
| [`tool_output_limits/`](../src/pydantic_ai_harness/pydantic_ai_harness/tool_output_limits/) | `ToolOutputLimits`、`Band`、动作 `Passthrough`/`Truncate`/`Spill`/`Summarize`、`Action`、`SummarizeFunc`、`Serializer`、`TruncationStrategy`、`indented_json`、`json_lines`；store `OverflowStore`/`WorkspaceStore`/`LocalFileStore`；常量 `READ_TOOL_NAME` | 在源头截断、落盘（可 `read_tool_result` 查询）或摘要过大的工具返回；默认 `Spill(then=Truncate())`；`ModelRetry` 等错误从不经过该钩子 |
| [`warn_on_cache_busts/`](../src/pydantic_ai_harness/pydantic_ai_harness/warn_on_cache_busts/) | `WarnOnCacheBusts`、`CacheBustWarning`、`CacheNotEnabledWarning` | 依 provider 自身数字观察 prompt-cache 前缀坍缩 |
| [`code_mode/`](../src/pydantic_ai_harness/pydantic_ai_harness/code_mode/) | `CodeMode`、`CodeModeToolset`、`CodeModeMount`、`CodeModeOS`、`CodeModeOSCallback`、`CodeModeResourceLimits`、`CodeModeReturnSchemaWarning`、`SpeculationStats`；事件 `CODE_MODE_EVENTS` 及 `Speculative*Event` | 模型写一个 Python 脚本在 Monty 沙箱中调用多个工具（一次往返，中间结果不进上下文）；`tools='all'` 默认全部沙箱化，`mount`/`os_access` 可控地开放宿主资源 |
| [`system_reminders/`](../src/pydantic_ai_harness/pydantic_ai_harness/system_reminders/) | `SystemReminders`、`Reminder`、`DynamicReminder`、`AsyncDynamicReminder`、`GoalReanchor`、`LLMReminder`、`ReminderFiredEvent`、`SYSTEM_REMINDERS_EVENTS` | 运行中途缓存安全地重注入指引，对抗指令衰减（提醒作为 `wrap_model_request` 中的临时 `UserPromptPart` 置于 `CachePoint` 之后） |

### 4.6 知识与记忆

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`memory/`](../src/pydantic_ai_harness/pydantic_ai_harness/memory/) | `Memory`、`MemoryToolset`；store `MemoryStore`（协议）、`SearchableMemoryStore`、`InMemoryStore`、`FileStore`、`SqliteMemoryStore`、`PostgresMemoryStore`；类型 `MemoryFile`、`MemoryMutation`、`MemoryOperation`、`MemorySearchResult`、`MemorySearchMatch`；异常 `MemoryConflictError`、`MemoryOperationConflictError` | 持久化、带命名空间的笔记本：`MEMORY.md` 作为 user-role 上下文注入，`read_memory`/`search_memory` 按需读取 |
| [`conversation_search/`](../src/pydantic_ai_harness/pydantic_ai_harness/conversation_search/) | `ConversationSearch`、`ConversationSearchToolset`、`HistorySource`（协议）、`SnapshotHistorySource`、`SnapshotStore`、`SearchScope` | 对持久化步历史做 BM25 搜索（含被压缩丢弃的轮次）；默认 `scope='conversation'` |
| [`skills/`](../src/pydantic_ai_harness/pydantic_ai_harness/skills/) | `Skills`、`SkillDefinition`；解析函数 `parse_skill`、`load_skill_libraries` | 从 workspace 加载 Agent Skill（`SKILL.md`）为**延迟**能力；`Skills(directories, *, include=None, exclude=None, workspace=None)` |
| [`repo_context/`](../src/pydantic_ai_harness/pydantic_ai_harness/repo_context/) | `RepoContext`、`RepoContextToolset`、`AgentContextInventory`、`AssetRoot`、`ContextFile` | 开局定向：走上层 `CLAUDE.md`/`AGENTS.md` 注入系统指令、可选的资产清单工具、遍历时按需提示 |
| [`pydantic_ai_docs/`](../src/pydantic_ai_harness/pydantic_ai_harness/pydantic_ai_docs/) | `PydanticAIDocs`、`PydanticAIDocsToolset`、`PydanticAIDocsTopic` | 按需查询 Pydantic AI 文档 |

### 4.7 控制与安全

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`guardrails/`](../src/pydantic_ai_harness/pydantic_ai_harness/guardrails/) | `InputGuardrail`、`OutputGuardrail`、`ToolGuardrail`、`GuardrailResult`、`GuardrailError`、`InputBlocked`、`OutputBlocked`、`ToolBlocked`、`InputGuardrailFunc`、`OutputGuardrailFunc`、`ToolGuardrailFunc`、`ToolResultGuardrailFunc`、`ToolCallInfo`、`ToolResultInfo`；[`detectors.py`](../src/pydantic_ai_harness/pydantic_ai_harness/guardrails/detectors.py) 提供开箱检查（含 `redact_secrets`、`blocked_keywords`、`personal_data`、`for_text` 等） | 校验/阻断/脱敏输入、工具调用、工具结果与输出；支持 guard 链、`RunContext` 首参、`parallel=True` 并行守卫 |
| [`repair_tool_arguments/`](../src/pydantic_ai_harness/pydantic_ai_harness/repair_tool_arguments/) | `RepairToolArguments` | schema 校验前修复畸形 JSON 工具参数（`json-repair`） |
| [`prompt_injection_defender/`](../src/pydantic_ai_harness/pydantic_ai_harness/prompt_injection_defender/) | `PromptInjectionDefender`、`OnDetection` | 对本地工具结果做间接提示注入分类，可扣留高风险结果；`block_high_risk`、`semantic_detection`、`tool_filter`、`on_detection`、`blocked_message` |
| [`spend/`](../src/pydantic_ai_harness/pydantic_ai_harness/spend/) | `SpendLimits`、`Budget`、`BudgetSpec`、`Window`、`PriceFunc`、`SpendCallback`；store `SpendStore`/`BatchSpendStore`/`InMemorySpendStore`/`RedisSpendStore`/`RedisClient`；类型 `SpendSnapshot`、`Spent`、`BudgetStatus`、`SpendEntry`；事件 `SPEND_LIMITS_EVENTS`、`SpendRecordedEvent`、`SpendBudgetStatus`；异常 `SpendLimitExceeded`、`UnpricedModelError`、`UnpricedModelWarning` | 跨窗口 USD/token 预算与按响应成本跟踪（按模型与租户）；`expose_tools=True` 时提供 `get_spend` 工具 |
| [`ask_user/`](../src/pydantic_ai_harness/pydantic_ai_harness/ask_user/) | `AskUser`、`AskUserToolset`、`AskUserRequest`、`AskUserAnswer`、`AskUserResponse`、`Answerer`（协议）、`Question`、`QuestionOption`、`ask_user_result`、`check_response`、`MAX_QUESTIONS`、`DECLINED`、`TIMED_OUT`、`TOOL_NAME` | 模型中途向用户提出多选题；`answerer=None` 时改为延迟工具调用（`DeferredToolRequests`） |
| [`tool_call_judge/`](../src/pydantic_ai_harness/pydantic_ai_harness/tool_call_judge/) | `ToolCallJudge`、`ToolCallVerdict` | 第二个模型在 `before_tool_execute` 对每次工具调用回答一个风险问题，可在执行前阻断；`yes` 阻断、`no` 放行、`unsure` 按 `on_uncertain` |
| [`trajectory_judge/`](../src/pydantic_ai_harness/pydantic_ai_harness/trajectory_judge/) | `TrajectoryJudge`、`AllGood`、`Steer`、`TrajectoryVerdict` | 第二个模型按节奏（`every`）审查实时运行的最近 `window` token，并在中途纠偏（`Steer` 经 `RunContext.enqueue` 以 `'asap'` 优先级投递） |

### 4.8 运行时、持久化与自扩展

| 模块 | 主要类 | 用途 |
|------|--------|------|
| [`step_persistence/`](../src/pydantic_ai_harness/pydantic_ai_harness/step_persistence/) | `StepPersistence`；store `StepStore`（协议）、`InMemoryStepStore`、`FileStepStore`、`SqliteStepStore`、`MongoStepStore`、`PostgresStepStore`；类型 `StepEvent`、`ContinuableSnapshot`、`RunRecord`、`ToolEffectRecord`、`EventKind`、`SnapshotState`、`ToolEffectStatus`；事件 `SnapshotSaved`；helper `continue_run`、`fork_run`、`annotate_tool_effect`、`is_provider_valid` | 追加式事件日志 + 可继续快照 + 工具效应台账，用于保存、恢复（`continue_run`）、分叉（`fork_run`）与崩溃后判定重放是否安全 |
| [`media/`](../src/pydantic_ai_harness/pydantic_ai_harness/media/) | `MediaStore`（协议）、`DiskMediaStore`、`SqliteMediaStore`、`S3MediaStore`、`PostgresMediaStore`、`MongoMediaStore`；`MediaContext`、`KeyStrategy`、`default_key_strategy`、`media_uri_for`、`parse_media_uri`、`make_static_public_url`、`PublicUrlResolver`；`externalize_media`、`restore_media` | 内容寻址媒体存储，把大二进制/文本 part 从快照中外部化（供 Step Persistence 复用；非 capability，直接使用） |
| [`logfire/`](../src/pydantic_ai_harness/pydantic_ai_harness/logfire/) | `ManagedPrompt` | 用 Logfire 托管 prompt 承载指令（版本化/灰度，无需重新部署）；须权衡 prompt-cache 失效 |
| [`absurd/`](../src/pydantic_ai_harness/pydantic_ai_harness/absurd/) | `AbsurdDurability`、`AbsurdParallelExecutionMode` | 把模型请求、MCP 调用、函数工具调用检查点到 PostgreSQL 上的 Absurd step；须在 async Absurd task handler 内运行 |
| [`aws_lambda/`](../src/pydantic_ai_harness/pydantic_ai_harness/aws_lambda/) | `AWSLambdaDurability`、`durable_agent_handler`、`run_durable`、`AgentLoopGone` | 在 AWS Lambda durable functions 上持久化执行（`DurableContext.step(...)`） |
| [`capability_creation/`](../src/pydantic_ai_harness/pydantic_ai_harness/capability_creation/) | `CapabilityCreation`、`CapabilityStore`、`AuthoredCapability`、`CapabilityCreationToolset`、`CapabilityValidationError`、`validate_capability_file`、`load_capability_instance` | Agent 在运行中编写、校验并持久化**新的 capability**；宿主在后续运行经 `store.load_active()` + `capabilities=` 加载 |
| [`experimental/acp/`](../src/pydantic_ai_harness/pydantic_ai_harness/experimental/acp/) | `PydanticAIACPAgent`、`run_acp_stdio`、`run_acp_stdio_sync`、`AcpSession`、`AcpSessionConfig`、`McpServer`、`SessionStore`、`InMemorySessionStore`、`StoredSession`、`ToolCallPermission`、`default_permission_scope`、`ToolCallPresentation`、`chain_presenters`、`default_coding_presenter`、`AcpFileSystemToolset`、`AcpTerminalToolset`、`acp_filesystem`、`acp_terminal` | 经 Agent Client Protocol（stdio JSON-RPC）把 Agent 暴露给 Zed/Toad 等编辑器。导入时 `warn_experimental('acp')` 发 `HarnessExperimentalWarning` |

### 4.9 实验性与废弃 shim

- `experimental/`：顶层 [`experimental/__init__.py`](../src/pydantic_ai_harness/pydantic_ai_harness/experimental/) 导出 `HarnessExperimentalWarning`；其下可随时变更/移除。**ACP 是唯一保留的 experimental capability**；`experimental/` 下另有指向主命名空间的 `authoring/`、`compaction/`、`context/`、`docs/`、`dynamic_workflow/`、`media/`、`overflow/`、`planning/`、`step_persistence/`、`subagents/` 转发包。
- 废弃 shim（仍可导入并告警）：

| 旧模块 | 重定向到 | 兼容别名 |
|--------|----------|----------|
| `cache_stability/` | `warn_on_cache_busts` | `CacheStabilityMonitor = WarnOnCacheBusts` |
| `context/` | `repo_context` | — |
| `docs/` | `pydantic_ai_docs` | `PyaiDocs`、`PyaiDocsToolset`、`PyaiDocsTopic` |
| `overflowing_tool_output/` | `tool_output_limits` | `OverflowingToolOutput = ToolOutputLimits` |
| `runtime_authoring/` | `capability_creation` | `RuntimeAuthoring = CapabilityCreation`、`AuthoringToolset = CapabilityCreationToolset` |

包内另有类/模块级改名告警：`compaction.LimitWarner`→`WarnNearLimits`、`compaction.SlidingWindow`→`SlidingWindowCompaction`、`guardrails.GuardResult`/`InputGuard`/`OutputGuard`→带 `rail` 的新名等。

### 4.10 内部 helper（下划线前缀，非 capability）

| 文件 | 内容 |
|------|------|
| [`_combine.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_combine.py) | combined capability 的合并辅助 |
| [`_durable.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_durable.py) | durable 相关辅助 |
| [`_events.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_events.py) | 事件基础设施 |
| [`_mcp.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_mcp.py) | MCP 集成辅助（含只读告警） |
| [`_monty_exec.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_monty_exec.py) | Monty 沙箱执行 |
| [`_output.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_output.py) | 输出处理辅助 |
| [`_usage.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_usage.py) | usage 辅助 |
| [`_warn.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_warn.py) | `HarnessDeprecationWarning`、`MCPReadOnlyNoToolsWarning`、`warn_module_renamed`、`warn_default_changed`、`warn_class_renamed`、`warn_argument_renamed`、`warn_argument_ignored` |
| [`_web_search.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_web_search.py) | Web 搜索辅助 |
| [`_workspace.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_workspace.py) | `RequireWorkspace`、`require_workspace`、`workspace_path`、`raise_tool_failure`、`metadata_dir` 等 |
| [`_workspace_provider.py`](../src/pydantic_ai_harness/pydantic_ai_harness/_workspace_provider.py) | workspace provider 辅助 |

---

## 5. 使用示例

### 5.1 完整编码 Agent

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import LocalWorkspace, WebSearch
from pydantic_ai_harness import Advisor, Coder

agent = Agent(
    'anthropic:claude-opus-5-5',
    capabilities=[
        LocalWorkspace('.'),           # 本地 checkout 即 workspace（非沙箱）
        Coder(),                       # 文件、shell、仓库上下文、子 Agent、上下文管理
        WebSearch(),                   # 联网查文档与报错
        Advisor('openai:gpt-5.2'),     # 卡住时咨询另一个模型
    ],
)
```

换成隔离云沙箱只需替换 workspace：

```python
from pydantic_ai import Agent
from pydantic_ai_harness import Coder
from pydantic_ai_harness.modal_sandbox import ModalSandbox

agent = Agent('anthropic:claude-opus-5-5', capabilities=[ModalSandbox(), Coder()])
```

### 5.2 拆开 `Coder`（等价积木）

`Coder` 是普通 `CombinedCapability`，「拆开的方式和组装时一样」：

```python
from pydantic_ai.capabilities import LocalWorkspace
from pydantic_ai_harness import (
    ClearToolResults, FileSystem, RepoContext,
    Shell, SubAgents, ToolOutputLimits, WarnNearLimits,
)
from pydantic_ai_harness.repair_tool_arguments import RepairToolArguments

capabilities = [
    LocalWorkspace('.'),
    FileSystem(tools=('read_file', 'write_file', 'edit_file', 'list_files', 'grep'), max_retries=5),
    Shell(tools=['shell']),
    RepoContext(),
    SubAgents(include_self=True),
    ClearToolResults(max_fraction=0.7),
    WarnNearLimits(max_context_fraction=0.9),
    ToolOutputLimits(),
    RepairToolArguments(),
]
```

### 5.3 组合一个研究 Agent

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import LocalWorkspace, WebFetch, WebSearch
from pydantic_ai_harness import SubAgent, SubAgents, ToolOutputLimits
from pydantic_ai_harness.tool_output_limits import LocalFileStore

sub_researcher = SubAgent(
    Agent(
        name='researcher',
        description='Research a focused sub-question on the web and report back with findings and source links',
        capabilities=[WebSearch(local=True), WebFetch(local=True), ToolOutputLimits(store=LocalFileStore())],
    )
)

agent = Agent(
    'anthropic:claude-opus-5-5',
    capabilities=[
        LocalWorkspace('.'),
        WebSearch(local=True),
        WebFetch(local=True),
        SubAgents(agents=[sub_researcher], agent_folders=None),
        ToolOutputLimits(store=LocalFileStore()),
    ],
)
```

### 5.4 CLI 直接运行内置 Agent

```bash
uvx --with "pydantic-ai-harness[coder]" clai -a pydantic_ai_harness.coder:coder_agent -m anthropic:claude-opus-5-5
```

`coder_agent` / `researcher_agent` 是无模型（model-less）的 Agent，供 CLI 以 `module:variable` 加载。

---

## 6. extra 与依赖

基础依赖（[`pyproject.toml`](../src/pydantic_ai_harness/pyproject.toml) 的 `uv-dynamic-versioning` hooks）：

```
genai-prices>=0.0.71
httpx>=0.28.1
json-repair>=0.58.0
pydantic-ai-slim==<与 harness 同版本>
```

可选 extra（用于各能力的可选依赖；`pydantic-ai-harness[anthropic]`、`[cli]` 等冒号后的 provider/CLI extra 直接透传给 `pydantic-ai-slim`）：

`anthropic`、`cli`、`code-mode`（别名 `codemode`）、`researcher`（duckduckgo + web-fetch）、`temporal`、`dbos`、`logfire`、`modal`、`e2b`、`sprites`、`dynamic-workflow`、`acp`、`exa`、`aws-lambda`、`absurd`、`youdotcom`、`mongodb`、`skills`、`browser-use`、`playwright`、`stackone`、`ordinal`、`grain`、`day-ai`、`posthog`、`pylon`、`prompt-injection-defender`（及其 `-ml`）、`github`、`slack`、`coder`、`logfire-mcp`、`linear`、`notion`、`google-workspace`。

安装示例：

```bash
uv add "pydantic-ai-harness[coder,anthropic]"
```

`pydantic-ai-harness` 会带上 `pydantic-ai-slim`，因此可独立安装；要求 Python 3.11+。

---

## 7. 相关资源

- [`AGENTS.md`](../src/pydantic_ai_harness/AGENTS.md)：包目标、词汇、AICA preflight、命名规范、遥测要求、编码标准（pyright strict、ruff 120/单引号/max-complexity 15、CI 100% 分支覆盖）。
- [`agent_docs/`](../src/pydantic_ai_harness/agent_docs/)：`capability-authoring.md`、`testing-capabilities.md`、`concurrency.md`、`core-boundary.md`、`docs-conventions.md`、`review-checklist.md`（[`index.md`](../src/pydantic_ai_harness/agent_docs/index.md) 提供任务路由）；以 `code_mode` 为范例。
- [`pydantic_ai_harness/.agents/skills/`](../src/pydantic_ai_harness/pydantic_ai_harness/.agents/skills/)：内置两个 Agent Skill——`pydantic-ai-harness` 与 `migrating-deep-agents-to-pydantic-ai-harness`。
- [`examples/`](../src/pydantic_ai_harness/examples/)：`coding_agent.py`、`research_agent.py`（及 `README.md`）。
- [`gh-aw/`](../src/pydantic_ai_harness/gh-aw/)：GitHub Agentic Workflows 引擎定义（`pydantic.md`）。
- [`integration_tests/`](../src/pydantic_ai_harness/integration_tests/)：localstack / mongodb / postgres / redis 的实时测试（`test_live_*.py`）。
- [`scripts/`](../src/pydantic_ai_harness/scripts/)：`gh_aw_engine_version.py`、`playwright_smoke.py`、`run-mutmut.sh`。

相关主题文档：[05 工具/Toolset/Capability](05-tools-toolsets-capabilities.md)（`AbstractCapability` 与 hooks）、[06 Durable Execution](06-durable-execution.md)（harness 的 durability 能力建立在其上）、[09 Pydantic Evals](09-pydantic-evals.md)（评估 harness Agent）。
