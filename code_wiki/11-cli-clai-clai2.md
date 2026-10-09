# 11 · CLI 与终端客户端

本篇覆盖与"终端交互"相关的四个部分，均可在仓库中核对源码：

- **核心内置 CLI**：`pydantic_ai._cli`（`python -m pydantic_ai`，控制台脚本 `pai`）
- **`clai` 包**：官方 CLI 的薄封装（PyPI 上的 `clai`）
- **`pydantic-clai2`**：能力感知（capability-aware）的完整终端客户端
- **`examples`**：`pydantic-ai-examples` 示例集合

三者关系见文末第 5 节。文中所有路径均相对 `code_wiki/`。

---

## 1. 核心内置 CLI：`pydantic_ai._cli`

目录：[pydantic_ai_slim/pydantic_ai/_cli/](../pydantic_ai_slim/pydantic_ai/_cli/)

| 文件 | 作用 |
|------|------|
| [__init__.py](../pydantic_ai_slim/pydantic_ai/_cli/__init__.py) | CLI 的全部实现：参数解析、Agent 加载、交互循环、slash 命令 |
| [web.py](../pydantic_ai_slim/pydantic_ai/_cli/web.py) | `clai web` 子命令的服务端实现（`run_web_command`） |

两者对外只暴露 `__all__ = 'cli', 'cli_exit'`。除标准库外，CLI 依赖 `rich`、`prompt-toolkit`、`pyperclip`、`argcomplete`——它们属于可选依赖组 `cli`（缺依赖时抛出的错误提示为 `pip install "pydantic-ai-slim[cli]"`）：

```python
try:
    import argcomplete
    import pyperclip
    from prompt_toolkit import PromptSession
    ...
except ImportError as _import_error:
    raise ImportError(
        'Please install `rich`, `prompt-toolkit`, `pyperclip` and `argcomplete` to use the Pydantic AI CLI, '
        'you can use the `cli` optional group — `pip install "pydantic-ai-slim[cli]"`'
    ) from _import_error
```

### 1.1 关键模块级对象

| 名称 | 说明 |
|------|------|
| `PYDANTIC_AI_HOME` | `Path.home() / '.pydantic-ai'`，存放 prompt 历史与配置 |
| `PROMPT_HISTORY_FILENAME` | `'prompt-history.txt'` |
| `SUPPORTED_CLI_TOOL_IDS` | `sorted(bint.kind for bint in SUPPORTED_NATIVE_TOOLS if bint not in NATIVE_TOOLS_REQUIRING_CONFIG)`——可在 `web` 子命令里通过 `-t` 启用的原生工具 ID 列表 |
| `cli_agent` | 默认 Agent：`Agent()`（无模型、无工具），仅带 `cli_system_prompt` |
| `SimpleCodeBlock` / `LeftHeading` | 覆写 Rich Markdown 渲染：代码块去掉背景色、标题左对齐——通过 `Markdown.elements.update(fence=..., heading_open=...)` 注入 |

`cli_system_prompt()` 是一个 `@cli_agent.system_prompt` 函数，注入当前日期/时间/时区与 `sys.platform`，并要求回答"简洁且始终用 markdown"。

### 1.2 核心公开函数与签名

```python
# 加载一个 Agent：'module:variable'（uvicorn 风格）或 YAML/JSON Agent Spec 文件路径
def load_agent(agent_path: str) -> Agent[Any, Any] | None: ...

# 运行 CLI，返回进程退出码；web 子命令转到 _cli_web，否则 _cli_chat
def cli(args_list: Sequence[str] | None = None, *, prog_name: str = 'clai',
        default_model: str = 'openai:gpt-5') -> int: ...

# 运行 CLI 并 sys.exit
def cli_exit(prog_name: str = 'clai'): ...
```

- `load_agent`：若后缀是 `.yaml`/`.yml`/`.json`，走 `Agent.from_file(path)`（[agent/spec.py](../pydantic_ai_slim/pydantic_ai/agent/spec.py) 的 `AgentSpec`）；否则把当前工作目录插入 `sys.path`，用 `TypeAdapter(ImportString)` 解析 `module:variable`，并校验结果确实是 `Agent` 实例，否则返回 `None`。
- `cli()` 的第一步是计算 `qualified_model_names`（只保留含 `:` 的 `known_model_names()`，这样补全能显示 `openai:gpt-5.2` 而不是裸 `gpt-5.2`），随后判断 `args_list[0] == 'web'`。

`_run_chat_command` 决定模型与 Agent：

```python
agent: Agent[object, str] = cli_agent
if args.agent:
    loaded = load_agent(args.agent)          # 失败 -> 打印错误，返回 1
    ...
model_arg_set = args.model is not None
if agent.model is None or model_arg_set:
    agent.model = infer_model(args.model or default_model)   # UserError -> 打印，返回 1
```

随后按终端能力决定是否打印启动 banner（`_print_intro`），把 `--code-theme dark` 映射为 `monokai`、`light` 映射为 `default`，最后进入一次性运行 `ask_agent(...)` 或交互循环 `run_chat(...)`（均通过 `anyio.run`）。

### 1.3 `chat`（默认命令）参数表

`_cli_chat` 使用 `argparse.ArgumentParser(prog=prog_name)`，描述文本内嵌 `subcommands:` 说明（Pydantic AI CLI v<版本>）。参数：

| 参数 | 说明 |
|------|------|
| `prompt`（位置参数，`nargs='?'`） | 单次模式的一次性 prompt；省略则进入交互模式 |
| `-l`, `--list-models` | 列出所有可用模型并退出 |
| `--version` | 打印版本并退出 |
| `-m`, `--model MODEL` | `"<provider>:<model>"`，如 `openai:gpt-5`；默认 `openai:gpt-5`（`default_model`） |
| `-a`, `--agent AGENT` | `module:variable` 或 YAML/JSON spec 文件 |
| `-t`, `--code-theme THEME` | `dark`（默认，映射 `monokai`）/ `light`（映射 `default`）/ 任意 pygments 主题 |
| `--no-stream` | 关闭流式输出 |
| `--mcp-config PATH` | MCP servers 配置文件路径（JSON，`mcpServers` 形态，同 Claude Desktop / Claude Code / Cursor） |

`-m` 参数挂有 `argcomplete.ChoicesCompleter(qualified_model_names)`，故支持 Tab 补全。

### 1.4 交互循环与 slash 命令

`run_chat(...)` 的签名（供 `Agent.to_cli` 复用）：

```python
async def run_chat(
    stream: bool,
    agent: AbstractAgent[AgentDepsT, OutputDataT],
    console: Console,
    code_theme: str,
    prog_name: str,
    config_dir: Path | None = None,
    deps: AgentDepsT = None,
    message_history: Sequence[ModelMessage] | None = None,
    model: models.Model | models.KnownModelName | str | None = None,
    model_settings: ModelSettings | None = None,
    usage_limits: _usage.UsageLimits | None = None,
    toolsets: Sequence[AbstractToolset[AgentDepsT]] | None = None,
) -> int: ...
```

要点：

- **历史文件**：`(config_dir or PYDANTIC_AI_HOME) / 'prompt-history.txt'`，父目录自动创建、文件 `touch`，交给 `PromptSession(history=FileHistory(...))`。
- **工具集生命周期**：用 `AsyncExitStack` 在整个会话期间持有 `toolsets`（而非每轮进入）。`MCPToolset` 会引用计数，因此每轮的进入变成 no-op，避免每条消息都起子进程、重做 `tools/list` 握手；不可达的 MCP server 在启动时就失败。
- **输入提示**：`session.prompt_async(f'{prog_name} ➤ ', auto_suggest=CustomAutoSuggest([...]), multiline=multiline)`。`CustomAutoSuggest` 在历史建议之外提供 `/markdown`、`/multiline`、`/usage`、`/exit`、`/cp` 的前缀补全。
- **每轮**调用 `ask_agent(...)` 并把返回的 messages 写回 `messages`，累加 `session_turns`、`session_usage`；`anyio` 取消异常打印 `Interrupted`，其它异常打印异常名与 `__cause__`。

`ask_agent(...)` 负责单轮运行：非流式走 `agent.run(...)` 后 `console.print(Markdown(...))`；流式走 `agent.iter(...)` + Rich `Live`（`refresh_per_second=15`，`vertical_overflow='ellipsis'`），用 `pending_calls: dict[str, str]` 按 `tool_call_id` 记录进行中的工具调用（工具并发返回可能乱序）。它还会在 `finally` 中把本轮 `turn_usage` 合并进会话总用量。

`handle_slash_command(...)` 处理以下 slash 命令：

| 命令 | 行为 |
|------|------|
| `/markdown` | 以 `Syntax(lexer='markdown')` 打印最后一条消息中的文本 part |
| `/multiline` | 切换多行输入；提示用 `[Meta+Enter]` 或 `[Esc]`+`[Enter]` 提交 |
| `/exit` | 打印 `Exiting…` 并返回退出码 `0` |
| `/cp` | 若最后一条是 `ModelResponse` 且有文本，用 `pyperclip.copy` 复制到剪贴板 |
| `/usage` 或 `/usage-...` | 打印会话累计用量；`/usage --json` 会被上游把空格替换成 `-`，因此实际到达的是 `/usage---json`，用 `option = ident_prompt[len('/usage'):].strip('-')` 解析 |

`format_usage(usage, turns, *, as_json=False)` 渲染 `Turns / Tokens(Input/Output) / Requests / Tool calls`，或单行 JSON（`soft_wrap=True` 便于管道）。

### 1.5 `web` 子命令与 `run_web_command`

`_cli_web` 因为要让主解析器保留位置参数 `prompt`，把 `web` 单独路由：`cli()` 检测到 `args_list[0] == 'web'` 就调用 `_cli_web(args_list[1:], ...)`。

| 参数 | 默认 | 说明 |
|------|------|------|
| `--agent`, `-a` | — | `module:variable` 或 YAML/JSON spec；省略则创建通用 Agent |
| `-m`, `--model`（可重复） | — | `"provider:model_name"`；第一个在 UI 中预选，其余作为选项 |
| `-t`, `--tool`（可重复） | — | 内建原生工具 ID，取值来自 `SUPPORTED_CLI_TOOL_IDS` |
| `-i`, `--instructions` | — | 系统指令；指定 `--agent` 时作为对其指令的追加 |
| `--html-source` | — | 聊天 UI 的 HTML 来源（URL 或文件路径）；否则从 CDN 下载 |
| `--host` | `127.0.0.1` | 绑定主机 |
| `--port` | `7932` | 绑定端口 |
| `--allowed-host`（可重复） | — | 除 IP/localhost 外允许应答的主机名；支持 `*.example.com` 与 `*` |

[web.py](../pydantic_ai_slim/pydantic_ai/_cli/web.py) 的 `run_web_command(...)` 组装 `create_web_app(...)`（来自 `pydantic_ai.ui._web`）并 `uvicorn.run(app, host, port)`：

- 若给了 `--agent`，用 `load_agent` 加载，失败返回 `1`；否则 `Agent()`。
- 若 Agent 无模型且未指定 `--model`，用 `default_model`。
- 逐一校验 `--tool`：未知 ID → 警告跳过；`SUPPORTED_CLI_TOOL_IDS` 之外（需要配置）→ 警告跳过。
- `--host` 会被自动加入 `allowed_hosts`，避免 CLI 广告一个随后又拒绝的 URL。
- 缺 `uvicorn` 时提示 `pip install uvicorn`，返回 `1`。

### 1.6 `Agent.to_cli` / `to_cli_sync`

定义在 [agent/abstract.py](../pydantic_ai_slim/pydantic_ai/agent/abstract.py) 的 `AbstractAgent` 上：

```python
async def to_cli(
    self: Self,
    deps: AgentDepsT = None,
    prog_name: str = 'pydantic-ai',
    message_history: Sequence[ModelMessage] | None = None,
    model_settings: ModelSettings | None = None,
    usage_limits: _usage.UsageLimits | None = None,
    model: models.Model | models.KnownModelName | str | None = None,
) -> None: ...

def to_cli_sync(...同参数...) -> None:   # 内部 _utils.run_until_complete(self.to_cli(...))
```

`to_cli` 直接构造 `Console()` 并调用 `run_chat(stream=True, agent=self, console=..., code_theme='monokai', prog_name=prog_name, ...)`。注意它**不传 `config_dir`**，因此历史仍写入 `~/.pydantic-ai/prompt-history.txt`。

### 1.7 控制台脚本声明

| 声明位置 | 脚本 | 目标 |
|----------|------|------|
| 根 [pyproject.toml](../pyproject.toml) | `pai` | `pydantic_ai._cli:cli_exit`（注释标注 `TODO(v3)`：`clai` 自 2025-05 起已在 PyPI） |
| [pydantic_ai_slim/pyproject.toml](../pydantic_ai_slim/pyproject.toml) | `pai` | 同上 |
| [pydantic_ai_slim/pydantic_ai/__main__.py](../pydantic_ai_slim/pydantic_ai/__main__.py) | `python -m pydantic_ai` | 调用 `cli_exit()` |

---

## 2. `clai` 包

目录：[clai/](../clai/)。它依赖**仅** `pydantic-ai`（`pydantic-ai=={{ version }}`，workspace 覆盖），**不依赖 harness**。

- [clai/pyproject.toml](../clai/pyproject.toml)：`name = "clai"`，`requires-python = ">=3.11"`，控制台脚本 `clai = "clai:cli"`，wheel 仅打包 `clai` 子包。
- [clai/clai/__init__.py](../clai/clai/__init__.py)（约 10 行）：定义 `__version__ = _metadata_version('clai')`，`cli()` 调用 `_cli.cli_exit('clai')`。
- [clai/clai/__main__.py](../clai/clai/__main__.py)：`python -m clai` 同样委托 `_cli.cli_exit('clai')`。

也就是说，`clai` 是对核心 CLI 的**极薄封装**：不含自己的参数解析，所有行为都来自 `pydantic_ai._cli`。

[clai/README.md](../clai/README.md) 给出的运行方式：

```bash
export OPENAI_API_KEY='your-api-key-here'   # 按所选 provider 设置
uvx clai                                    # 免安装
uv tool install clai && clai                # 全局安装
pip install clai && clai                    # pip
```

交互模式下的特殊命令为 `/exit`、`/markdown`、`/multiline`（提示：Ctrl+D 提交）、`/cp`（README 未列 `/usage`，因其与核心实现略有出入）。README 还包含 `clai web -m openai:gpt-5.2` 与 `clai web --agent my_agent:my_agent` 的 Web UI 用法。

> README 的 `## Help` 段落由测试 [clai/update_readme.py](../clai/update_readme.py) 自动校验：它用 `cli(['--help'], prog_name='clai')` 捕获输出（`COLUMNS=150`），把版本号正则替换成 `...`，再与 README 中的代码块比对；不一致时**写入 README 并 fail**，迫使提交者同步。该文件在 Python ≥ 3.13 上跳过（输出略有差异）。

---

## 3. `pydantic-clai2`

目录：[src/pydantic_clai2/](../src/pydantic_clai2/)。依赖 `pydantic-ai-harness[coder]`（精确 pin）+ `pydantic-ai-slim[anthropic,mcp,openai]` + 一批终端/服务库（`termflow-md`、`prompt-toolkit`、`rich`、`keyring`、`cryptography`、`pyfiglet`、`python-dotenv`、`genai-prices`、`pillow`、`logfire[httpx]`、`opentelemetry-instrumentation-httpx`、`pydantic-monty`、`websockets`）。

**定位**：面向 Pydantic AI 的、可单独安装的能力感知流式终端客户端。编码工具来自内置 `coder` 插件（默认开启，`/plugins disable coder` 可切换为纯聊天）。版本方案与 harness 相同（`0.<minor>.<patch>` + `uv-dynamic-versioning`）。

> 平台说明：源码注释指出 Windows 上 CLAI 暂不提供 agent workspace / 仓库上下文。

### 3.1 分层原则（见 [src/pydantic_clai2/AGENTS.md](../src/pydantic_clai2/AGENTS.md)）

| 层 | 拥有 | 绝不做 |
|----|------|--------|
| Pydantic AI core | Agent 循环、hooks、events、toolsets | 知道 CLAI 存在 |
| `pydantic_ai_harness` | 可复用 capability（`Coder`、`Shell`…） | 向终端打印 |
| `pydantic_clai2` | 提示循环、渲染、`/commands`、插件加载 | 重新实现 core hook |

若变更需要改动 Agent 循环 → 提到 core；若是无终端的可复用行为 → 放 harness；只有"外壳"本身留在这里。

### 3.2 入口与控制台脚本

```toml
[project.scripts]
clai2 = "pydantic_clai2.__main__:main"
# 与发行包同名，使 `uvx pydantic-clai2` 无需 --from
pydantic-clai2 = "pydantic_clai2.__main__:main"
```

[src/pydantic_clai2/pydantic_clai2/__main__.py](../src/pydantic_clai2/pydantic_clai2/__main__.py) 是"import-light 入口：先动画再加载依赖"：

1. 用 `python-dotenv` 的 `find_dotenv(usecwd=True)` 加载 `.env`（OSError/UnicodeDecodeError 时打印到 stderr）。
2. 导入 `Splash`，设 `os.environ['PYDANTIC_AI_NO_BANNER'] = '1'`。
3. 仅当无参数且未设 `CLAI_NO_SPLASH` 时启用 splash；再读配置数据库 `$XDG_CONFIG_HOME/pydantic-clai2/config.db` 的 `display.splash` 决定是否动画。
4. `splash.start()` → 导入 `pydantic_clai2.cli._cli.run` → 在 `warnings.catch_warnings()` 与 `quiet_telemetry_logs()` 下运行 `run(splash=splash)`，最后 `splash.stop()`。

`quiet_telemetry_logs()` 给 `logfire`、`opentelemetry` 两个 logger 挂 `NullHandler`，避免遥测 SDK 的导出重试/超时记录撕裂实时显示；未设 `sys.warnoptions` 时全局 `simplefilter('ignore', Warning)`（可用 `-W` / `PYTHONWARNINGS` 恢复）。

[src/pydantic_clai2/pydantic_clai2/__init__.py](../src/pydantic_clai2/pydantic_clai2/__init__.py) 用惰性 `__getattr__` 暴露进程内 API，`__all__` 为：`DEFAULT_PLUGINS`、`Session`、`StreamRenderer`、`chat`、`create_agent`、`open_stock_agent`。

### 3.3 `cli/_cli.py` 的 `run` 参数全表

[src/pydantic_clai2/pydantic_clai2/cli/_cli.py](../src/pydantic_clai2/pydantic_clai2/cli/_cli.py) 的 `run(*, splash: Splash | None = None) -> None`：

| 参数 | metavar | 说明 |
|------|---------|------|
| `--resume [SESSION-ID]` | SESSION-ID | 恢复已保存会话；不带 ID 打开浏览器 |
| `--resume-claude [SESSION-ID]` | SESSION-ID | 导入并恢复 Claude Code 会话；不带 ID 浏览 |
| `--resume-codex [SESSION-ID]` | SESSION-ID | 导入并恢复 Codex 会话 |
| `--worktree`, `-w [NAME]` | NAME | 在 Git worktree 中启动；省略 NAME 则生成一个 |
| `-a`, `--agent MODULE:ATTR` | MODULE:ATTR | 与已有 `Agent` 实例对话；该会话不加载插件，除非给 `-m` 否则保留 Agent 自身模型 |
| `-m`, `--model` | — | provider 限定的模型名 |
| `-p`, `--prompt TEXT` | TEXT | 单次运行、只打印答案（headless） |
| `--request-limit` | int | 单次 prompt 的最大模型请求数 |
| `--database` | Path | 设置数据库位置（在 `--worktree` 改目录前 `.resolve()`） |
| `command` | `config` \| `plugins` | 子命令 |
| `arguments` | `argparse.REMAINDER` | 传给子命令的参数 |

三个 resume 标志在 `add_mutually_exclusive_group` 中互斥；`_resume_source` 把 `--resume-claude`/`--resume-codex` 折叠成 `resume` + `resume_from`。`_validate_args` 的约束：

- `config`/`plugins` 不能与 `--resume`、`--worktree`、`--agent` 组合；
- `--worktree` 不能与 `--resume` 组合；
- `--prompt` 不能与子命令组合、必须非空，且不能是"隐式 resume"（`--resume == ''`）——必须给出显式 `--resume SESSION-ID`。

流程要点：`config`/`plugins` 子命令直接把 `handler(store, args.arguments)` 打到 stdout 后返回；`--worktree` 通过 `open_worktree` 打开并 `os.chdir`；模型/请求上限以 `overrides` 层叠进 `resolve_settings`（`overrides = store.overrides() | project.overrides`，再叠加 `-m`/`CLAI_MODEL`/`--request-limit`）。`--prompt` 走 `run_headless` 并 `raise SystemExit(...)`；否则 `asyncio.run(chat(...))`。启动错误（`ValueError/TypeError/ImportError/AttributeError/LookupError/OSError`）通过 `parser.error(str(exc))` 报告；`/update` 触发 `Relaunch` 时用 `os.execv` 原地重启（`relaunch_argv` 只保留 `--agent/--model/--request-limit/--database/--resume`）。

### 3.4 进程内 API

| 名称 | 说明 |
|------|------|
| `chat(agent, *, deps, plugins=(), usage_limits=None, console=None, settings=None, store=None, builtin_plugins=(), project=None, resume=None, resume_from=None, load_plugins=True, worktree=None)` | 启动一次 asyncio 终端会话；Esc 取消当前轮，Ctrl-C 清空空闲输入，Ctrl-D / `/exit` 退出 |
| `create_agent(model=None)` | 构建基础 CLAI Agent（`Agent(model, deps_type=type(None), capabilities=[customization_guide()])`）；编码工具来自 `coder` 插件 |
| `create_stock_agent(model=None)` | 构建 CLI 拥有的 `StockAgent` 模板（`output_type=str`） |
| `open_stock_agent(*, workspace, model=None, capabilities=(), plugin_settings=None)` | 异步上下文管理器：为**代码内**（无终端）运行打开 CLAI 的 stock 编码 Agent，仅含 `coder`/`repo_context`/`compaction` |
| `Session` | 会话状态（见 `runtime/_session.py`） |
| `StreamRenderer` | 流式渲染器 |
| `DEFAULT_PLUGINS` | 内置插件声明元组 |

`open_stock_agent` 用临时目录里的**私有** `SettingsStore` 与 `Console(file=devnull)` 隔离用户保存/投放的插件；`plugin_settings` 只能命名 `coder`/`repo_context`/`compaction`，否则抛 `UserError`。

### 3.5 内建 `/命令` 一览（`_app.py` 注册）

`create_shell` 在 [src/pydantic_clai2/pydantic_clai2/_app.py](../src/pydantic_clai2/pydantic_clai2/_app.py) 中注册命令（`Command` 定义于 [commands.py](../src/pydantic_clai2/pydantic_clai2/commands.py)）：

| 命令 | 说明 |
|------|------|
| `/effort` | 查看/设置 reasoning effort |
| `/fast` | 切换 Codex priority processing（仅 `openai-codex` provider 可见） |
| `/resume` | 浏览/恢复已保存会话；`claude`/`codex` 导入其会话 |
| `/keys` | 管理已保存 API key |
| `/login` | 登录（`openai-codex`、`github-copilot` 或插件新增的；`NAME@PROFILE` 加账号） |
| `/accounts` | 增删改/排序/登出账号；`MODEL@*` 按顺序尝试 |
| `/set`（别名 `/settings`） | 修改设置；无参数打开菜单 |
| `/theme` | 选择 Termflow 调色板；无参数打开选择器 |
| `/system_prompt` | 查看系统提示词、编辑你自己的 instructions |
| `/model` | 选择模型/回退链或打开选择器；子命令 `add`、`settings`、`chains` |
| `/add_model`、`/model_settings` | 已弃用，等价 `/model add`、`/model settings` |
| `/chain` | `/model chains` 的别名 |
| `/help` | 显示命令 |
| `/clear`（别名 `/new`） | 清屏新开会话；旧会话仍保存 |
| `/usage`、`/cost` | 用量/保留历史成本 |
| `/exit` | 退出 |
| `/update` | 按 `updates.channel`（stable/main）安装最新 CLAI |
| `/config` | `show|get|set|reset` 设置 |
| `/spinner` | 选择工作动画；无参数打开选择器 |
| `/plugins` | 管理插件；无参数打开菜单（`args_during_turn=True`） |
| `/reload` | 不重启重载 CLAI2 代码 |
| `/fork` | 后台运行会话副本：`/fork [@model] PROMPT` |
| `/tasks` | 查看/控制委派任务 |
| `/forks` | 显示后台 forks |

`Command` 的元数据字段：`name`、`description`、`handler`、`complete`、`available`、`raw`（原样传参）、`during_turn`、`args_during_turn`、`during_turn_subcommands`、`live`。`Commands` 是实例级注册表（基于 Code Puppy 的 registry/completer 模式），支持 `register`/`register_many`/`unregister`/`execute`/`get_completions`/`help`，并区分"轮中可运行"与"保持编辑器 live"。

### 3.6 源码布局

按 [src/pydantic_clai2/AGENTS.md](../src/pydantic_clai2/AGENTS.md) 的 Source layout 与"File map"，包结构如下（仅列关键目录与文件）：

| 子包 / 位置 | 内容 |
|-------------|------|
| 根 | `_app.py`（提示循环、内建命令、`open_stock_agent`、`create_agent`/`create_stock_agent`）、`__main__.py`、`__init__.py`、`auth.py`、`commands.py`、`customization.py`、`errors.py`、`gh_cli.py`、`slack_app.py`、`pkce.py`、`logfire_oauth.py`、`openrouter_auth.py`、`warm_imports.py` |
| `cli/` | `_cli.py`（参数/启动）、`agent_import.py`、`headless.py`、`self_update.py`、`shell_passthrough.py`、`effort.py`、`command_context.py` |
| `config/` | `__init__.py`（`Settings`/`PluginSettings`）、`settings_store.py`（SQLite）、`credential_store.py`、`project_settings.py`（`.clai/settings.json`）、`api_keys.py`、`theme_names.py`、`features.py`（`SUPPORTED_FEATURES`/`CAPABILITY_REQUIREMENTS`）、`plugin_requirements.py` |
| `runtime/` | `_session.py`（`Session`）、`sessions.py`、`forks.py`、`worktrees.py`、`imported_sessions.py`、`claude_code_sessions.py`、`codex_sessions.py`、`imported_history.py`、`session_naming.py`、`reloading.py`、`speculation.py`、`speculative_mode.py`、`sandbox_calls.py`、`eager_timing.py`、`capability_guard.py`、`_processes.py`、`tasks.py`、`project_identity.py`、`session_settings.py` |
| `models/` | `model_catalog.py`、`model_settings.py`、`model_options.py`、`accounts.py`、`chains.py`、`profiles.py`、`key_profiles.py`、`custom_params.py`、`usage.py`、`vllm.py`、`openrouter.py`、`github_copilot.py` |
| `plugins/` | `__init__.py`（`Plugin`/`PluginHost`/`LoadedPlugin`/`collect`/事件 dataclass）、`loader.py`（`PluginLoader`）、`_factories.py`、`_git.py`、`compatibility.py`、`describe.py`、`keys.py` |
| `builtin_plugins/` | 20+ 内置插件（详见 3.8） |
| `mcp/` | `__init__.py`（`mcp` 插件）、`_command.py`、`_form.py`、`_runtime.py`、`_settings.py`、`_store.py`、`_tokens.py` |
| `ui/` | `prompt/`（编辑/按键/面布/剪贴板/选择，如 `prompt_surface.py`、`live_prompt.py`、`text_clipboard.py`）、`menus/`（`plugin_menu.py`、`model_picker.py`、`field_menu.py`、`session_browser.py`、`rewind.py`…）、`rendering/`（`_rendering.py`、`theme.py`、`status.py`、`spinners.py`、`usage_report.py`、`history.py`…）、`telemetry.py` |

### 3.7 插件 API 与稳定导入路径

插件作者依赖的**稳定**导入路径是 `pydantic_clai2.plugins` 与 `pydantic_clai2.commands`（[plugins/__init__.py](../src/pydantic_clai2/pydantic_clai2/plugins/__init__.py)、[commands.py](../src/pydantic_clai2/pydantic_clai2/commands.py)）。

- **插件模型**：`Plugin` 的声明式子类，语义同 core 的 `AbstractCapability`——每个贡献都是一个有"空默认"的方法，插件只覆写需要的。
  - `get_capabilities`（工具/指令/agent-run hooks）、`get_commands`（`/commands`）、`render`（自定义输出）、`get_status_segments`、`get_spinners`、`get_model_providers`、`get_logins`、`configure`（设置菜单）、`on_session_start`/`on_session_end`/`on_turn_start`/`on_turn_end`/`on_plugin_load_failed`。
  - 设置模型是类参数：`Plugin[Settings]` 校验进 `self.settings`；`self.host` 是 `PluginHost`（console、conversation、`session_id`、status、full screen、saved settings）。
- **规则摘要**（AGENTS.md）：声明而非注册（`get_*` 只被 loader 调用一次）；一个方法一个时刻、一个 typed event；无字符串二次分发；单一 async 拼写；observer 返回 `None`、decider 修改事件；失败即取消（fail closed）；载荷用 kw-only dataclass（非 `BaseModel`）；不对插件用 `getattr`/`hasattr`；仅 shell 自己的时刻。
- **加载/卸载**：每个插件一个 `LoadedPlugin`；运行中的 run 不更换插件（先绑定能力快照）；`load` 跑 `on_session_start`、`unload` 跑 `on_session_end`；`reload` 是"卸载、重新 import、加载"；指令顺序 = capability 顺序（core `CapabilityOrdering`）；注册按 name 幂等。
- 兼容矩阵在 [plugins/compatibility.py](../src/pydantic_clai2/pydantic_clai2/plugins/compatibility.py)。用户契约见 [src/pydantic_clai2/PLUGINS.md](../src/pydantic_clai2/PLUGINS.md)，设计见 `docs/declarative-plugins.md`。

### 3.8 内置插件清单

`DEFAULT_PLUGINS` 与 `STOCK_PLUGINS` 定义在 [src/pydantic_clai2/pydantic_clai2/_app.py](../src/pydantic_clai2/pydantic_clai2/_app.py)。**默认启用**（`enabled=False` 之外）：

| id | factory | 备注 |
|----|---------|------|
| `coder` | `pydantic_clai2.builtin_plugins.coder` | settings：`unrestricted_filesystem=True`、`repo_context=False`、`sub_agents=False`、`agent_folders=['agents']` |
| `ask_user` | `pydantic_clai2.builtin_plugins.ask_user_menu` | 模型运行中发起选择题 |
| `repo_context` | `pydantic_clai2.builtin_plugins.repo_context` | 读取 `AGENTS.md`/`CLAUDE.md` 进指令 |
| `compaction` | `pydantic_clai2.builtin_plugins.compaction` | 上下文压缩 + `/compact` |
| `persistence` | `pydantic_clai2.runtime.sessions` | 会话持久化 |
| `observability` | `pydantic_clai2.builtin_plugins.logfire` | 默认 Logfire 追踪 |
| `notifications` | `pydantic_clai2.builtin_plugins.notifications` | 桌面通知 |
| `mcp` | `pydantic_clai2.mcp` | `/mcp` 服务器管理 |

**默认关闭**（`enabled=False`）：`github`、`pylon`、`google_workspace`、`day_ai`、`ordinal`、`notion`、`slack`、`logfire_mcp`、`posthog`、`grain`、`linear`、`herdr`。

`STOCK_PLUGINS` 与 `DEFAULT_PLUGINS` 相同，只是把 `coder` 的 `sub_agents` 置为 `True`（CLI 拥有的 agent 在构造时绑定插件，使 Coder 可安全委派）。`open_stock_agent` 只加载 `coder`/`repo_context`/`compaction`。

其它内置插件实现（`notifications`、`github`、`pylon`、`google_workspace`、`day_ai`、`ordinal`、`notion`、`logfire_mcp`、`posthog`、`grain`、`linear`）同样位于 `builtin_plugins/`；loader 会把保存声明中的旧 factory 路径重定向到该包。

### 3.9 `Settings` 默认值（节选）

[config/__init__.py](../src/pydantic_clai2/pydantic_clai2/config/__init__.py) 的 `Settings`（不可变、`extra='forbid'`）：`model='openai-codex:gpt-6-astra'`、`request_limit=10000`、`tool_retries=3`、`pool_accounts=True`、`speculative_code_mode=False`、`instructions=''`、`session_namer=True`、`theme='default'`、`spinner='working'`、`thinking=True`、`splash=True`、`tool_calls='detailed'`、`tool_output=False`、`shell_lines=20`。`update_channel` 取值 `stable`/`main`（其存储名为旧的 `bleeding`）。

### 3.10 测试与本地验证

- `pytest-anyio`；真实模型调用全局阻断；用 `TestModel` + `Console(file=StringIO())` 驱动外壳。
- 加载插件用 `load_plugin(PluginClass, host)`；断言 handler 的效果（被取消的 turn、被改写的文本），而非 mock 调用次数；renderer 用合成事件，且不得要求安装 `Coder`。
- 取消测试用真实 anyio cancel scope，用 `Event` 排序，禁止 sleep。
- 修改设置/数据库 schema 前须考虑升级/降级/分支切换，回归用例加到 `tests/test_settings_compatibility.py`。

仓库根目录下的本地验证命令（CLAI 与 Harness 共享 `uv.lock`、`.venv`、Pyright 配置）：

```bash
uv sync --locked --all-packages --group lint
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run --no-sync pyright pydantic-clai2/src pydantic-clai2/tests
uv run --no-sync pytest -p no:cacheprovider -c pydantic-clai2/pyproject.toml pydantic-clai2/tests
```

`/update` 的两个渠道见 [cli/self_update.py](../src/pydantic_clai2/pydantic_clai2/cli/self_update.py)：`stable` 跟随 PyPI，`main` 跟随 GitHub release `clai2-bleeding`（由 [.github/workflows/clai2-bleeding.yml](../.github/workflows/clai2-bleeding.yml) 结合 [src/pydantic_clai2/scripts/build_bleeding.sh](../src/pydantic_clai2/scripts/build_bleeding.sh) 生成；`CLAI_BLEEDING_URL` 可改指向）。

---

## 4. `examples` 示例集合

目录：[examples/](../examples/)（发行包 `pydantic-ai-examples`），依赖 `pydantic-ai-slim[openai,google,groq,anthropic,ag-ui,realtime]` + `pydantic-evals` + 一批演示库（`fastapi`、`uvicorn`、`gradio`、`logfire`、`duckdb`、`datasets`、`modal`、`twelvelabs`、`mcp[cli]` 等；行宽 88）。

运行方式（见 [examples/pydantic_ai_examples/__main__.py](../examples/pydantic_ai_examples/__main__.py)）：

```bash
uv run -m pydantic_ai_examples.<module>          # 原地运行某个示例
uv run -m pydantic_ai_examples --copy-to <dest>  # 复制全部示例到新目录
uv run -m pydantic_ai_examples -v                # 打印 pydantic_ai 版本
```

`--copy-to` 会拒绝已存在的目标目录，只复制 `*. *` 顶层文件（子包目录不复制）。

### 4.1 顶层示例

| 文件 | 说明 |
|------|------|
| `bank_support.py` | 小型银行支持 Agent（SQLite） |
| `weather_agent.py` / `weather_agent_gradio.py` | 多工具天气问答 / Gradio UI 变体 |
| `rag.py` | pgvector 向量检索增强的 RAG |
| `flight_booking.py` | 多 Agent 委派查找航班 |
| `sql_gen.py` / `data_analyst.py` | 生成 SQL / 数据分析 |
| `pydantic_model.py` | 从文本构造 Pydantic 模型 |
| `stream_markdown.py` / `stream_whales.py` | 流式渲染 |
| `roulette_wheel.py` | 轮盘游戏 Agent |
| `chat_app.py`（+ `chat_app.html` / `chat_app.ts`） | FastAPI 聊天应用 |
| `cancel_and_resume.py` | 交互式取消（Esc / Ctrl-C）与恢复流式运行 |
| `medical_agent_delegation.py` | 医疗分诊 + Agent 委派 |
| `twelvelabs_video_agent.py` | TwelveLabs 视频 Agent |
| `realtime_voice.py` / `realtime_text_to_audio.py` / `realtime_handoff.py` | 实时语音 |

### 4.2 子包

- `ag_ui/`：AG-UI 前端适配（`agentic_chat.py`、`human_in_the_loop.py`、`shared_state.py`、`tool_approval.py` 等）。
- `evals/`：pydantic-evals 示例（`example_01_generate_dataset.py` … `example_04_compare_models.py`）。
- `realtime_camera/`：浏览器摄像头 + 语音助手（WebSocket）。
- `realtime_webrtc/`：浏览器语音 Agent（WebRTC + Pydantic AI sideband）。
- `slack_lead_qualifier/`：Slack 线索筛选 Agent。

---

## 5. 三者关系小结

```
pydantic-graph ──> pydantic-ai-slim ──> clai         （薄封装，仅核心 _cli，依赖 pydantic-ai）
                          │
                          ├──────────> examples       （示例应用，依赖 slim + evals）
                          │
                          └──────────> pydantic-ai-harness ──> pydantic-clai2
                                                              （完整终端客户端，依赖 harness[coder] + slim[anthropic,mcp,openai]）
```

`clai` 与核心共用同一份 `_cli` 实现（参数解析、`load_agent`、`run_chat`/`ask_agent`、slash 命令、`web` 子命令均在 `pydantic_ai_slim/pydantic_ai/_cli/`）；`pydantic-clai2` 则在这些 core 原语（`Agent` 循环、`AbstractCapability`、hooks）与 harness capability 之上另建了完整的外壳（`_app.py` 的提示循环、`commands.py` 的注册表、`plugins/` 的加载器与 `ui/` 的渲染/菜单）。
