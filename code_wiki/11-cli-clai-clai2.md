# 11 · CLI 与终端客户端

本篇覆盖三个与终端交互相关的部分：

- 核心内置 CLI：`pydantic_ai._cli`（对外暴露为 `clai` / `pai`）
- `clai` 包（官方 CLI 的薄封装）
- `pydantic-clai2`（能力感知的终端客户端）
- `examples` 示例集合

---

## 1. 核心内置 CLI：`pydantic_ai._cli`

目录：[pydantic_ai_slim/pydantic_ai/_cli/](../pydantic_ai_slim/pydantic_ai/_cli/)

- `__init__.py`：CLI 实现。`__all__ = 'cli', 'cli_exit'`。
  - `cli_agent = Agent()` 与 `cli_system_prompt()`：默认 Agent 与感知日期/时间/平台的 system prompt。
  - `load_agent(agent_path)`：支持 `'module:variable'`（uvicorn 风格）与 YAML/JSON Agent Spec（经 `Agent.from_file`）。
  - `cli(args_list, prog_name='clai', default_model='openai:gpt-5') -> int`：返回退出码；`web` 子命令转到 `_cli_web`，否则 `_cli_chat`。
  - `cli_exit(prog_name='clai')`：运行并 `sys.exit`。
  - `run_chat(...)`：交互循环；`ask_agent(...)`：单次运行（经 `agent.iter` + Rich `Live` 流式，或非流式）。
  - Slash 命令由 `handle_slash_command` 处理：`/markdown`、`/multiline`、`/exit`、`/cp`、`/usage`。
  - `--mcp-config` 经 `pydantic_ai.mcp.load_mcp_toolsets` 加载工具集。
  - 历史写入 `~/.pydantic-ai/prompt-history.txt`。
- `web.py`：`clai web` 服务器命令（`run_web_command`）。

### `Agent.to_cli` / `to_cli_sync`

定义在 [agent/abstract.py](../pydantic_ai_slim/pydantic_ai/agent/abstract.py)：

- `async def to_cli(...)` —— 「在 CLI 聊天界面中运行 Agent」。
- `def to_cli_sync(...)` —— 同步版本。

两者接受 `deps`、`prog_name='pydantic-ai'`、`message_history`、`model_settings`、`usage_limits`、`model`，并接入 `_cli` 聊天循环。

控制台脚本（`pydantic-ai-slim` 与根包均声明）：

```toml
[project.scripts]
pai = "pydantic_ai._cli:cli_exit"
```

---

## 2. `clai` 包

目录：[clai/](../clai/)（依赖仅 `pydantic-ai`，**不依赖 harness**）。

- `pyproject.toml`：`name = "clai"`，控制台脚本 `clai = "clai:cli"`。
- `clai/__init__.py`（约 10 行）：`cli()` 调用 `pydantic_ai._cli.cli_exit('clai')`。
- `clai/__main__.py`：`python -m clai` 同样委托。

也就是说，`clai` 是对核心 CLI 的**极薄封装**。`README.md` 列出用法、`-l/--list-models`、`-m/--model`、`-a/--agent module:variable`（或 YAML/JSON spec）、`-t/--code-theme`、`--no-stream`、`--mcp-config` 与 `web` 子命令。

> `clai` 与 `pydantic-clai2` 相互独立：`clai` 只依赖核心；`clai2` 依赖 harness + 核心。

---

## 3. `pydantic-clai2`

目录：[src/pydantic_clai2/](../src/pydantic_clai2/)（依赖 `pydantic-ai-harness[coder]`）。

**定位**：面向 Pydantic AI 的、可单独安装的能力感知流式终端客户端。编码工具是内置的 `coder` 插件（默认开启，`/plugins disable coder` 可切换为纯聊天）。

### 3.1 分层（见 `AGENTS.md`）

- **core**：拥有 Agent 循环（从不知道 CLAI 存在）。
- **harness**：拥有可复用 capability（从不向终端打印）。
- **clai2**：拥有提示循环、渲染、`/commands` 与插件加载。

### 3.2 入口

- 控制台脚本 `clai2` 与 `pydantic-clai2` → `pydantic_clai2.__main__:main`。
- `__main__.py` 的 `main()`：加载 `.env`、设置 `PYDANTIC_AI_NO_BANNER`、启动 `Splash`，再委托 `pydantic_clai2.cli._cli.run`。
- `cli/_cli.py` 的 `run(*, splash)` 解析参数（`--worktree/-w`、`-a/--agent MODULE:ATTR`、`-m/--model`、`-p/--prompt`、`--request-limit`、`--database`、resume 标志、`config` / `plugins` 子命令）。
- 进程内 API：`pydantic_clai2.chat`、`create_agent`、`open_stock_agent`、`Session`。

### 3.3 源码布局

| 子包 | 内容 |
|------|------|
| 根 | `_app.py`（交互 shell）、`__main__.py`、`auth.py`、`commands.py`、`customization.py`、`errors.py`、`gh_cli.py`、`slack_app.py`、`pkce.py` 等 |
| `cli/` | 参数处理、`agent_import.py`、headless 输出、`self_update.py`、`shell_passthrough.py`、`effort.py` |
| `config/` | `Settings` / `PluginSettings`、`credential_store.py`、`settings_store.py`（SQLite）、`project_settings.py`（`.clai/settings.json`）、`api_keys.py`、`theme_names.py` |
| `runtime/` | `_session.py`（`Session`）、`sessions.py`、`forks.py`、`worktrees.py`、导入的会话（Claude Code / Codex）、`reloading.py`、`speculation.py`、`sandbox_calls.py`、`capability_guard.py` |
| `models/` | `model_catalog.py`、`accounts.py`、`chains.py`、`profiles.py`、`usage.py`、`vllm.py`、`openrouter.py`、`github_copilot.py` 等 |
| `plugins/` | `Plugin` API、`loader.py`（`PluginLoader`）、`_git.py`、`compatibility.py`、`keys.py` |
| `builtin_plugins/` | 20+ 内置插件：`coder.py`、`repo_context.py`、`compaction.py`、`ask_user_menu.py`、`logfire.py`、`slack.py`、`github.py`、`linear.py`、`notion.py`、`google_workspace.py`、`herdr.py`、`notifications.py` 等 |
| `mcp/` | MCP 服务器管理与表单 |
| `ui/` | `prompt/`（编辑器/按键/剪贴板）、`menus/`（插件/设置/模型/主题/浏览器菜单）、`rendering/`（主题、流式渲染、状态、spinner、用量报告）、`telemetry.py` |

插件 API：`pydantic_clai2.plugins` 与 `pydantic_clai2.commands` 是稳定的插件作者导入路径（子类化 `Plugin` 并覆写其贡献，类似 `AbstractCapability`）。

---

## 4. `examples` 示例集合

目录：[examples/](../examples/)（包 `pydantic-ai-examples`）。

运行方式：`uv run -m pydantic_ai_examples.<module>`；或用 `--copy-to <dest>` 复制全部示例。

### 4.1 顶层示例

| 文件 | 说明 |
|------|------|
| `bank_support.py` | 完整的小型银行支持 Agent（SQLite） |
| `weather_agent.py` / `weather_agent_gradio.py` | 多工具天气问答 / Gradio UI 变体 |
| `rag.py` | 向量检索（pgvector）增强的 RAG |
| `flight_booking.py` | 多 Agent 委派查找航班 |
| `sql_gen.py` / `data_analyst.py` | 生成 SQL / 数据分析 Agent |
| `pydantic_model.py` | 从文本构造 Pydantic 模型 |
| `stream_markdown.py` / `stream_whales.py` | 流式渲染示例 |
| `roulette_wheel.py` | 简单轮盘游戏 Agent |
| `chat_app.py`（+ `chat_app.html` / `chat_app.ts`） | FastAPI 聊天应用 |
| `cancel_and_resume.py` | 交互式取消（Esc / Ctrl-C）与恢复流式运行 |
| `medical_agent_delegation.py` | 医疗分诊 + Agent 委派 |
| `twelvelabs_video_agent.py` | TwelveLabs 视频 Agent |
| `realtime_voice.py` / `realtime_text_to_audio.py` / `realtime_handoff.py` | 实时语音示例 |

### 4.2 子包

- `ag_ui/`：AG-UI 前端适配演示（`agentic_chat.py`、`human_in_the_loop.py`、`shared_state.py`、`tool_approval.py` 等）。
- `evals/`：pydantic-evals 示例（`example_01_generate_dataset.py` … `example_04_compare_models.py`）。
- `realtime_camera/`：浏览器摄像头 + 语音助手（WebSocket）。
- `realtime_webrtc/`：浏览器语音 Agent（WebRTC + Pydantic AI sideband）。
- `slack_lead_qualifier/`：Slack 线索筛选 Agent。

---

## 5. 三者关系小结

```
pydantic-graph ──> pydantic-ai-slim ──> clai（薄封装，仅核心 CLI）
                          │
                          └──────────> pydantic-ai-harness ──> pydantic-clai2（完整终端客户端）
```
