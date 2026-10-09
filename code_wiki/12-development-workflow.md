# 12 · 开发工作流

本篇覆盖构建、测试、类型检查、CI 与发布流程，是「如何搭建环境 / 如何运行 / 如何贡献」的完整参考。所有命令、target、配置项均可在仓库中核对。

涉及的关键文件：

| 文件 | 作用 |
|------|------|
| [Makefile](../Makefile) | 本地开发命令入口 |
| [pyproject.toml](../pyproject.toml) | workspace 定义、工具链配置（Ruff/Pyright/mypy/pytest/coverage/codespell） |
| [.pre-commit-config.yaml](../.pre-commit-config.yaml) | 提交前钩子 |
| [.github/workflows/ci.yml](../.github/workflows/ci.yml) | 唯一的主 CI 工作流（含 tag 触发的发布作业） |
| [.github/release.yml](../.github/release.yml) | 发布 changelog 分类/排除规则 |
| [docs/contributing.md](../docs/contributing.md) | 贡献流程 |
| [.github/workflows/AGENTS.md](../.github/workflows/AGENTS.md) | gh-aw 与 CI 工作流的维护约定 |

---

## 1. 环境与安装

项目使用 [`uv`](https://docs.astral.sh/uv/) 管理，根 [pyproject.toml](../pyproject.toml) 的 `[tool.uv]` 声明：

- `required-version = ">=0.9.25"`
- `default-groups = ["dev", "lint"]`
- `exclude-newer = "7 days"`（除 `[tool.uv.exclude-newer-package]` 白名单外，只接受 7 天内发布的依赖）
- `conflicts`：`mcp-tasks` extra 与 dev 组的 FastMCP 生态互斥——因此**所有** `--all-extras` 调用都带 `--no-extra mcp-tasks`
- `exclude-dependencies = ["browser-use"]`（其精确 pin 会锁死整个 workspace；该能力改由专门的 CI job 单独安装验证）

支持的 Python 版本为 **3.11–3.14**（`workspace members` 见下）。workspace 成员：

```
pydantic_ai_slim, pydantic_evals, pydantic_graph, clai,
examples, src/pydantic_ai_harness, src/pydantic_clai2
```

常用命令：

```bash
make install          # 安装依赖 + 所有包 + lint 组 + pre-commit 钩子
make help             # 列出全部 target（从 Makefile 的 ## 注释生成）
make                  # 完整本地流水线：format lint typecheck testcov（= make all）
make test             # 快速测试（无覆盖率）
make testcov          # 带覆盖率与 HTML 报告
```

`make install` 的完整行为：先跑 `.uv`（检查 uv 是否安装），再执行

```bash
uv sync --frozen --all-extras --no-extra mcp-tasks --all-packages --group lint
pre-commit install --install-hooks   # 若未安装 pre-commit 则先用 uv tool install
```

`make install-all-python` 会为 3.11/3.12/3.13/3.14 各建一个 venv（`UV_PROJECT_ENVIRONMENT=.venv311` … `.venv314`）。

---

## 2. Makefile target 全表

[DEFAULT_GOAL](../Makefile) 为 `all`。

| Target | 实际命令 | 说明 |
|--------|----------|------|
| `.uv` | `uv --version`（失败则提示安装） | target 前置检查 |
| `install` | `uv sync --frozen --all-extras --no-extra mcp-tasks --all-packages --group lint` + `pre-commit install --install-hooks` | 安装依赖、包与钩子 |
| `install-all-python` | 分别 `UV_PROJECT_ENVIRONMENT=.venv311…314 uv sync --python 3.11…3.14 --frozen …` | 多版本 venv |
| `sync` | `uv sync --all-extras --no-extra mcp-tasks --all-packages --group lint` | 更新包与 `uv.lock`（非 frozen） |
| `format` | `uv run ruff format` + `uv run ruff check --fix --fix-only` | 格式化并修复 |
| `lint` | `uv run ruff format --check` + `uv run ruff check` | 只检查 |
| `typecheck-pyright` | `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright`（可加 `--threads $(PYRIGHT_THREADS)`、`--pythonversion $(PYRIGHT_PYTHON)`），并对 `.github/scripts` 用 `-p .github/scripts` 再跑一次 | 全量 Pyright |
| `typecheck-changed` | `uv run python scripts/typecheck_changed.py` | CI 的增量 Pyright 入口（见 §3.2） |
| `typecheck-mypy` | `uv run mypy` | mypy（`tests/typed_agent.py`，`strict`） |
| `typecheck` | `typecheck-pyright` | 默认只跑 Pyright |
| `typecheck-both` | `typecheck-pyright` + `typecheck-mypy` | 两者都跑 |
| `test` | `COLUMNS=150 uv run pytest -n auto --dist=loadgroup --durations=20` | 快速测试（无覆盖率）；可设 `PYTEST_PYTHON` |
| `test-all-python` | 3.11–3.14 各跑 `coverage run -p -m pytest` 后 `coverage combine` + `report` | 全版本覆盖率 |
| `testcov` | `coverage run -m pytest -n auto --dist=loadgroup --durations=20` + `coverage combine/report/html` | 覆盖率 + HTML |
| `integration-localstack` | `uv run --all-packages --all-extras --no-extra mcp-tasks pytest src/pydantic_ai_harness/integration_tests/localstack` | 需 Docker + `LOCALSTACK_AUTH_TOKEN` |
| `integration-mongodb` | 同上，路径 `.../mongodb` | 需 `docker run -d -p 27017:27017 mongo:8` 或 `MONGODB_TEST_URL` |
| `integration-postgres` | 同上，路径 `.../postgres` | 需 `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:17` 或 `POSTGRES_TEST_URL` |
| `integration-redis` | 同上，路径 `.../redis` | 需 `docker run -d -p 6379:6379 redis:8` 或 `REDIS_TEST_URL` |
| `update-examples` | `uv run -m pytest --update-examples tests/test_examples.py` | 重新生成文档示例 |
| `update-vcr-tests` | `uv run -m pytest --record-mode=rewrite tests` | 重新录制 cassettes（需 API key） |
| `all` | `format lint typecheck testcov` | 完整本地流水线 |
| `help` | `awk` 解析 Makefile 的 `##` 注释 | 打印帮助 |

环境变量（Makefile 顶部注释）：`PYRIGHT_THREADS`（默认空=单进程；`auto`=每逻辑核最多一个 worker，正整数封顶；0/off/非法值均等于 `auto`）、`PYRIGHT_PYTHON`（指定类型检查所用 Python 版本）、`PYTEST_PYTHON`。

---

## 3. 工具链配置（根 [pyproject.toml](../pyproject.toml)）

### 3.1 Ruff

- `[tool.ruff]`：`line-length = 120`、`target-version = "py311"`；`include` 显式列出 Python 目录（各包、`tests/**`、`docs/**`、部分 `scripts/` 与 `.github/scripts/` 文件）。
- `[tool.ruff.lint]`：`preview = true`（启用 `PLW1514`）、`explicit-preview-rules = true`；`select = ["E4","E7","E9","F"]`；`extend-select` 含 `Q`、`RUF100`、`RUF018`、`RUF043`、`C90`、`UP`、`I`、`D`、`TID251`、`PLW1514`、`PGH003`、`BLE001`；`ignore` 关闭 `D100/D102/D104/D105/D107`。
- `flake8-quotes = { inline-quotes = "single", multiline-quotes = "double" }`；`mccabe = { max-complexity = 15 }`。
- `[tool.ruff.lint.isort]`：`combine-as-imports = true`；`known-first-party = ["pydantic_ai","pydantic_evals","pydantic_graph","pydantic_ai_harness","pydantic_clai2"]`。
- `[tool.ruff.lint.pydocstyle]`：`convention = "google"`。
- `[tool.ruff.lint.flake8-tidy-imports.banned-api]`：禁 `typing.TypedDict`（改用 `typing_extensions.TypedDict`）与 `asyncio.Lock`（改用 `anyio.Lock`）。
- `[tool.ruff.format]`：`docstring-code-format = false`（docstring 里的代码由 pytest-examples 处理后端）、`quote-style = "single"`。
- `[tool.ruff.lint.per-file-ignores]`：`examples/**` 放宽 `D101/D103`；`tests/**`、`docs/**`、若干 `.github/scripts/*` 关闭 `D`；`!src/pydantic_clai2/**` 关闭 `BLE001`（即 CLI 边界允许盲捕获）。

### 3.2 Pyright / mypy

```toml
[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"
reportMissingTypeStubs = false
reportUnnecessaryIsInstance = false
reportUnnecessaryTypeIgnoreComment = true
reportMissingModuleSource = false
venvPath = '.'
venv = ".venv"
```

- `include` 覆盖各包 + `tests` + `examples` + `clai` + harness/clai2 + 两个 `scripts/typecheck_changed` 相关文件。
- `executionEnvironments`：`tests` 追加 `extraPaths=["examples"]`、关闭 `reportUnusedFunction`/`reportPrivateImportUsage`；`scripts/typecheck_changed.py` 与其测试按 Python 3.11 分析。
- `exclude`：`src/pydantic_ai_harness/.../browser_use`、`tests/harness/browser_use`、`examples/.../weather_agent_gradio.py`、`pydantic_ai_slim/pydantic_ai/embeddings/voyageai.py`。
- **`.github/scripts` 是独立的嵌套 Pyright 项目**（`[.github/scripts/pyrightconfig.json](../.github/scripts/pyrightconfig.json)` 继承根配置），因为 Pyright 永远跳过点目录内部——故 `make typecheck-pyright` 会跑两次。
- `[tool.mypy]`：`files = "tests/typed_agent.py"`、`strict = true`。

**增量类型检查**（[scripts/typecheck_changed.py](../scripts/typecheck_changed.py)）：只检查"自上次通过以来内容变化的文件 + 传递 import 它们的文件"，checkpoint 记录在 `.git` 目录下（按 worktree、不提交）。以下情况回退到"Pyright 报告的所有 tracked 文件减去未变的 `tests/`"：首次运行、新的 Pyright/Python 版本、`pyproject.toml`/`uv.lock`/`Makefile` 变更、import 解析目标变化、或变更覆盖超过半个项目。只有三种情况把**整个项目**交给 `make typecheck-pyright`：`CI`、Python < 3.11（读不了 `pyproject.toml`）、无法复现的 Pyright 配置。`PYRIGHT_TIME_BUDGET` 会让一个耗时超预算的**通过**运行失败。

### 3.3 pytest

```toml
[tool.pytest.ini_options]
anyio_mode = "auto"                                   # 所有 async def 测试自动经 anyio
testpaths = ["tests", ".github/scripts/test_ci_duration.py"]
xfail_strict = true
markers = ["modal_live", "e2b_live", "sprites_live", "temporal"]
filterwarnings = ["error", ...多项定向 ignore...]
```

- `anyio_mode = "auto"`：**不要**再写 `@pytest.mark.anyio`；默认后端 asyncio，`--anyio-backend=trio` 可跑可移植测试。
- `filterwarnings = ["error"]` 把警告升级为错误，后面跟一长串带原因的定向忽略（如 anyio 未 await 协程、harness 实验性 capability、botocore/cohere/google.genai、logfire 传播上下文等）。

### 3.4 coverage

```toml
[tool.coverage.run]
patch = ["subprocess"]
concurrency = ["multiprocessing", "thread"]
data_file = ".coverage/.coverage"
include = ["pydantic_ai_slim/**", "pydantic_evals/**", "pydantic_graph/**",
           "src/pydantic_ai_harness/pydantic_ai_harness/**", "tests/**"]
branch = true

[tool.coverage.report]
fail_under = 100     # 硬门禁：新代码必须 100% 分支覆盖
skip_covered = true
show_missing = true
precision = 2
```

`exclude_lines` 排除 `pragma: no cover` / `pragma: lax no cover` / `raise NotImplementedError` / `if TYPE_CHECKING:` / `@overload` / `@abstractmethod` / `assert_never` / `if __name__ == .__main__.:` 等；`omit` 排除 `tests/example_modules/*`、Exa 集成、部分仅开发用文件以及 `tests/harness/**/*_live.py`。`[tool.coverage.paths]` 把 CI runner 路径映射回本地。

### 3.5 其它

- `[tool.inline-snapshot]`：`format-command = "ruff format --stdin-filename {filename}"`；`[tool.inline-snapshot.shortcuts]`：`snap-fix`/`snap`。
- `[tool.codespell]`：`skip = '.git*,*.svg,*.lock,*.css,*.yaml,...'`、`check-hidden = true`、`ignore-words-list = 'asend,fpr,aci,gage'`。
- `[tool.logfire]`：`ignore_no_config = true`。
- 构建约束 `build-constraint-dependencies = ["hatchling<1.32"]`（因发布 Action 拒绝 metadata 2.5）。

---

## 4. pre-commit 钩子

[.pre-commit-config.yaml](../.pre-commit-config.yaml)：

| 来源 | 钩子 | 作用 / 参数 |
|------|------|-------------|
| pre-commit-hooks v4.3.0 | `no-commit-to-branch` | 阻止直接提交到 `main` |
| | `check-yaml` | `args: ["--unsafe"]` |
| | `check-toml` | — |
| | `end-of-file-fixer` | 排除 `.github/workflows/agentics-maintenance.yml` 与 `*.lock.yml` |
| | `trailing-whitespace` | 同上排除 |
| | `check-added-large-files` | `--maxkb=1024 --enforce-all`；排除 cassettes 与 `uv.lock` |
| texthooks 0.6.8 | `fix-smartquotes` / `fix-spaces` / `fix-ligatures` | 排除 cassettes 与生成的工作流 |
| codespell v2.3.0 | `codespell` | `--skip tests/*/cassettes/*` |
| zizmor-pre-commit v1.23.1 | `zizmor` | GitHub Actions 安全审计 |
| 本地 `no-rst-syntax` | pygrep | 禁止 Python/Markdown 里的 RST 语法（` ``foo`` `、行尾 `::`、Sphinx role）；改用单反引号、```` ```python ```` 围栏、引用式链接 |
| 本地 `clai-help` | `uv run pytest clai/update_readme.py` | 校验 `clai --help` 与 README 同步；排除 `src/pydantic_clai2`、`tests/clai2` |
| 本地 `format` | `make format` | 仅对 `python` 文件 |
| 本地 `lint` | `make lint` | 仅对 `python` 文件 |
| 本地 `check-cassettes` | `uv run python scripts/check_cassettes.py` | 仅在 `tests/` 变更时触发 |

> **Pyright 不是 pre-commit 钩子**：由 CI 运行全量检查（`quality` job 里的 `make typecheck-changed`，在 `CI` 下退化为全量 `make typecheck-pyright`）。

---

## 5. 测试约定

详见 [tests/AGENTS.md](../tests/AGENTS.md) 与 `tests/conftest.py`。

### 5.1 异步与后端

- `anyio_mode = "auto"`；默认 asyncio。`tests/harness` 与 `tests/clai2` 在各自 `conftest.py` 里把 `anyio_backend` 固定为 asyncio，不要逐文件覆盖。
- 跑 Trio：`uv run pytest <path> --anyio-backend=trio --record-mode=none`（选择性、手动/周期性，不加入普通 CI 矩阵）。

### 5.2 VCR 与录制

- **默认风格**：VCR + 公共 API 测试——像用户那样用 `Agent(...)`、`agent.run(...)`，对真实 provider 响应录制的 cassettes 回放。
- **单元测试的定位**：仅用于"definitory"的内部行为（预先请求守卫、真实模型不会触发的防御分支），或 VCR 保护不到的场景（cassette matcher 对请求体不敏感，内部载荷改变仍可能匹配旧录制而变绿）；每个单元测试应说明为何不是 VCR 测试。
- **录制**：`cassetter` 插件提供 `@pytest.mark.vcr` 与 `vcr` fixture；`--record-mode=rewrite` 录制、默认回放。实时 WebSocket cassette 位于 `tests/realtime/cassettes/<module>/<test>.yaml`，用 `uv run --env-file .env pytest --record-mode=rewrite <test>` 录制（含原始帧、密钥已擦除、音频截断）。
- **缓存前缀回归网**：录制也充当全套件的 prompt-cache 前缀回归网；故意移动前缀的测试须标 `@pytest.mark.moves_cache_prefix(reason=...)`。
- 断言"实际发出的请求"有四种互不替代的机制：`request_capture`（httpx 边界 hook，首选）、快照 cassette 中的请求体（`single_request_body`，评审面）、逐测试 matcher（`@pytest.mark.vcr(additional_matchers=['body'])`）、捕获 adapter 渲染结果。详见 tests/AGENTS.md「Asserting what goes out on the wire」。

### 5.3 常用 fixture / helper

| 来源 | fixture / helper | 说明 |
|------|------------------|------|
| `conftest.py` | `allow_model_requests` | 绕过默认的 `ALLOW_MODEL_REQUESTS = False` |
| | `blockbuster`（autouse） | 事件循环内的阻塞调用抛 `BlockingError`；扫描 `pydantic_ai`/`pydantic_graph`/`pydantic_evals`/`clai` |
| | `model`（`indirect=True`） | 字符串参数（`'openai'`/`'anthropic'`/`'google'` 等）→ 配置好的 `Model` |
| | `env` | `TestEnv`，临时增删环境变量 |
| | `assets_path` / `image_content` / `audio_content` / `video_content` / `document_content` / `text_document_content` | session-scoped 二进制素材 |
| | `disable_ssrf_protection_for_vcr` | 需要下载 URL 内容的 VCR 测试 |
| | `request_capture` | `RequestCapture`，`.body(path)`/`.bodies(path)`/`.headers` |
| | `IsNow`/`IsStr`/`IsDatetime`/`IsBytes`/`IsInt`/`IsFloat`/`IsList`/`IsInstance` | 断言 matcher；另有 `IsSameStr()` |
| `models/conftest.py` | `anthropic_model`（`capture=True`）、`content_blocks`/`message_shape`/`cache_breakpoints` | Anthropic 专用工厂与请求体投影 |

`tests/conftest.py` 会把 `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`，并按包的安装情况 `collect_ignore` `harness` / `clai2`。

### 5.4 运行单个测试（仓库推荐）

```bash
uv run pytest path/to/test.py::test_name
```

Coding guidelines 明确：**不要**在本地跑仓库级 Pyright/pytest/coverage（`make typecheck`/`make test` 都慢，CI 会在合适时机跑）。迭代时只做定点检查：

```bash
PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright path/to/file.py
uv run pytest path/to/test.py::test_name
```

**进程隔离约定**：不要在进程内可跑的逻辑上用子进程测试（起新解释器并重新 import 会让整套件显著变慢）；只有依赖进程边界的行为（CLI 调用、解释器启动、import 隔离）才用子进程，并从子进程环境里清掉 `COVERAGE_*`。

### 5.5 测试目录布局

`tests/` 顶层：`conftest.py`（共享 fixture）、`cassette_hooks.py`（录制期擦除/头过滤）、`assets/`（二进制素材）、`cassettes/`、`models/`（含 `conftest.py` 与按文件分目录的 `cassettes/test_openai` 等）、`providers/`、`durable_exec/`、`evals/`、`graph/`、`harness/`、`clai2/`、`realtime/`、`ext/`、`benchmarks/` 等；根级 `test_*.py` 为功能测试（优先 VCR + parametrize）。CLAI2 专属辅助文件在 `tests/clai2/`（`menu_script.py`、`fake_gh_cli.py`、`surface_terminal.py` 等）。

---

## 6. CI：`.github/workflows/ci.yml`

### 6.1 触发与全局设置

```yaml
on:
  push:
    branches: [main]
    tags: ["**"]          # 排除 !clai2-bleeding（该 tag 由 clai2-bleeding.yml 移动）
  pull_request: {}
```

全局 `env`：`COLUMNS=150`、`UV_PYTHON=3.12`、`UV_FROZEN=1`、`UV_PYTHON_PREFERENCE=only-managed`，以及用于认证 uv 的 Git fetch 的 `GIT_CONFIG_*`。`permissions: contents: read`；`concurrency` 为 PR 每 ref 一组（新推送取消旧的）、push/tag 每 run_id 一组（互不取消）。

### 6.2 `classify`：变更分类

PR 会先分类，输出 `content_only`、`docs_changed`、`clai2_only`、`clai2_changed`、`pyright_changed` 五个布尔量，据此决定走全量还是精简路径。非 PR（push/tag）一律"全量"。规则要点：

- 仅改 `tests/*`、`.github/*`、`.macroscope/*` 之外的"非测试内容"才算 `content_only=false`；纯文档/说明改动可让 `content_only=true`。
- `clai2_only`：变更全部落在 `src/pydantic_clai2/*`、`tests/clai2/*`（共享输入如 `pyproject.toml`、`uv.lock`、`tests/conftest.py` 不在内）。
- `pyright_changed`：触及 `*.py`/`*.pyi`/`py.typed`/`pyproject.toml`/`uv.lock`/`uv.toml`/`pyrightconfig*.json`/`Makefile`/`ci.yml`。
- 读取 PR 文件数或列表失败、少于实际、或 ≥3000 时，保守地跑全量 CI。

### 6.3 作业表

| 作业 | 名称 | 触发条件（简） | 说明 |
|------|------|----------------|------|
| `classify` | classify changes | 总是 | 输出上述分类 |
| `quality` | quality checks | `content_only != true` | 见 §6.4 |
| `mypy` | — | `content_only != true && clai2_only != true` | `make typecheck-mypy` |
| `docs-assets` | — | `content_only != true` 或 `docs_changed` | 图片 tinify 检查、lychee 离线链接/锚点检查、doc snippet 测试 |
| `docs-only` | docs-only checks on Python 3.13 | `content_only && docs_changed` | 纯文档 PR 的精简路径 |
| `content-checks` | content checks | `content_only && !docs_changed` | 纯内容（非文档）改动 |
| `test` | test on `<py>` (`<install>`) | `content_only != true` | 矩阵：Python 3.11–3.14 × 安装形态（slim/evals/harness/clai2/standard） |
| `test-all-extras` | test on `<py>` (all-extras, shard …) | `content_only != true && clai2_only != true` | 全 extras（显式 `--no-extra mcp-tasks`），分片 |
| `test-durable-exec` | test durable-exec on `<py>` (`<resolution>`) | 同上 | 持久化执行（Temporal/DBOS/Prefect…） |
| `test-lowest-versions` | test on `<py>` (lowest-versions, shard …) | 同上 | 最低依赖版本 |
| `test-temporal-latest` | test Temporal latest on Python 3.11 | 同上 | 最新 Temporal |
| `test-examples` | test examples on `<py>` | 同上 | 示例（`tests/test_examples.py`） |
| `test-harness-browser-use` | test harness browser-use | 同上 | 单独安装 `browser-use` 并跑/类型检查 |
| `test-clai2-clipboard` | test clai2 clipboard on `<os>` | `clai2_changed`（见 §6.5） | macOS/Windows runner |
| `test-fastmcp-4` | test FastMCP 4 compatibility | 同上 | 用 `--with` 覆盖层到 FastMCP 4 |
| `harness-integration-changes` | classify harness integration changes | PR 或分支 | 判定是否需要跑 harness 集成测试 |
| `harness-localstack-integration` / `harness-mongodb-integration` / `harness-redis-integration` | 对应名称 | 依赖上面的判定 | 需要 Docker/外部服务 |
| `latest-versions-canary` | latest versions canary | tag | 发布前的 canary |
| `coverage` | — | 依赖 test 系列成功 | 合并覆盖率产物，`coverage report` + `strict-no-cover` 审计 |
| `check` | — | `always()` | `re-actors/alls-green` 分支保护总门禁 |
| `deploy-docs` | — | tag 且 `check` 成功 | 向 `pydantic/unified-docs` 派发 `docs-update` |
| `release-build` | build release artifacts | tag 且 `check` 成功 | 见 §8 |
| `release` | publish to PyPI | tag 且 release-build 成功 | 见 §8 |
| `gh-aw-engine-pin` | gh-aw engine / read pin | `release` 成功 | 发布后读取 gh-aw 引擎 pin |
| `gh-aw-engine-pin-reminder` | gh-aw engine / pin reminder | release-build + engine-pin 成功 | pin 落后则开 issue |

### 6.4 `quality` 作业细目

在 Python 3.13 + lint 缓存下安装（`uv sync --all-extras --no-extra mcp-tasks --all-packages --group lint`），随后：

1. 跑所有 GitHub Agentic Workflow 的策略测试（`pytest .github/scripts/test_*.py`，含 `test_agentic_workflow_guard.py`、`test_ci_content_classifier.py` 等）。
2. 跑 `scripts/test_typecheck_changed.py`（增量类型检查脚本自身的测试）。
3. `uv lock --script .github/scripts/pydantic-ai-runner --check`（gh-aw runner 的 script lock 是否新鲜）。
4. 对 runner 自身依赖 `uv run --script .github/scripts/pydantic-ai-runner` 做 smoke（断言输出含 `empty prompt`）。
5. 拉取 gh-aw 兼容策略 `gh-aw/main/.github/aw/compat.json`，跑 `agentic_workflow_guard.py check --compatibility-file ...`。
6. PR 时用分页 REST 拿变更文件列表，跑 `agentic_workflow_guard.py check --changed-file-list ...`（校验 `*.md` 源与 `.lock.yml` 同步重编译）。
7. `pre-commit/action --all-files --verbose`（`SKIP: no-commit-to-branch`、`UV_NO_SYNC=1`）。
8. `make typecheck-changed`（仅当 `pyright_changed == true`；`PYRIGHT_THREADS=auto`、`PYRIGHT_TIME_BUDGET=200`）。
9. 与最新稳定 release 做公共 API 兼容性检查（`check_api_compatibility.py --against <tag>`）。
10. `uv run --script .../gh_aw_engine_version.py --published`、`uv build --all-packages --no-sources`、`check_http_dependencies.py`、`uvx twine check --strict dist/*`。

### 6.5 精简路径的边界

- `clai2_only` 为真时，只跑 `pydantic-clai2` 的测试单元，跳过其余 workspace 作业（`mypy` 也被跳过）。
- `test-clai2-clipboard` 只在变更触及 `src/pydantic_clai2/*`、`tests/clai2/*` 或 `ci.yml` 本身时跑（因为它用 macOS/Windows runner）。

---

## 7. Agentic Workflows（gh-aw）

### 7.1 源文件与编译

`.github/workflows/` 下的 `pydantic-ai-*.md` 是[agentic workflows](https://github.com/githubnext/gh-aw) 的**人类可编辑源**（frontmatter + prompt），经 `gh aw compile` 编译成生成的 `pydantic-ai-*.lock.yml`。**Actions 只运行 `.lock.yml`，从不运行 `.md`**。

现存源/锁对（部分）：`pydantic-ai-attention-triage`、`pydantic-ai-bug-hunter`、`pydantic-ai-community-demand`、`pydantic-ai-docs-drift`、`pydantic-ai-feature-digest`、`pydantic-ai-pr-review`、`pydantic-ai-provider-mapping-sweep`、`pydantic-ai-provider-parity-explore`、`pydantic-ai-regression-detector`、`pydantic-ai-roundtrip-sweep`、`pydantic-ai-stale-issues-finder`、`pydantic-ai-streaming-resilience-sweep`、`pydantic-ai-ui-security-review`。此外 `.github/workflows/agentics-maintenance.yml` 是 gh-aw 自动生成的独立维护工作流。

**硬性约定**（[.github/workflows/AGENTS.md](../.github/workflows/AGENTS.md)）：

- **绝不手改 `*.lock.yml`**——它是生成物，手改会在下次重编译被静默覆盖。
- 编辑 `.md` 源后**必须在同一次变更里 `gh aw compile` 并提交重生成的锁**；改了源没改锁 = 不完整。
- frontmatter（`on:` 触发、`permissions`、`tools`、`safe-outputs`、jobs、路径/`detect` 过滤）与 `imports:` 的 `shared/*.md` 都会在编译期内联进锁，因此都需要重编译。
- **例外**：`shared/prompts/` 下的 agent prompt 在运行时通过 `fetch-dynamic-prompt` 动作 / Logfire 变量获取，不烘进锁，编辑后无需重编译。

### 7.2 策略守卫

[.github/scripts/agentic_workflow_guard.py](../.github/scripts/agentic_workflow_guard.py) 在 CI 中做静态检查；每个 check 都对应一个真实到过 `main`、浪费过模型预算的缺陷。检查项包括：`dangling-needs`（引用未声明为依赖的 `needs.<job>`）、`provider-health-*`、`provider-engine-config`、`assigned-alert-metadata-gate`、`safe-output-job-max`、`prompt-path-outside-workspace`、`timeout-declared`、`job-timeout-env-missing`/`job-timeout-env-mismatch`、`job-timeout-too-short`、`compiler-version-drift`、`awf-binary-version`、`ai-credits-accounting`、`lock-not-regenerated`。

本地运行：

```bash
uv run python .github/scripts/agentic_workflow_guard.py check --base-ref origin/main
```

新增 check 时必须配一个回归测试（构建自真正出问题的配置）。

### 7.3 两个审查机器人

- `CI Review`（`pydantic-ai-pr-review.md`）：在 `CI` 工作流**成功**后自动运行，仅限 same-repo PR，使用 Z.AI Coding Plan 引擎，提交正式 `APPROVE`/`REQUEST_CHANGES`。
- `douwebot`（[.github/workflows/bots.yml](../.github/workflows/bots.yml)）：仅在打上 `douwebot` 标签时运行（`pull_request_target` 路径，Claude Opus 5.5），完成后删除标签。

两者相互独立，不互相读取状态。`CI Review` 通过 `workflow_run` 触发，不携带 `github.event.pull_request`，须从 `needs.eligibility.outputs` 读取 PR 信息；当顶层 `if:` 引用自定义 job 的输出时，**该 job 也必须出现在 prompt 正文中**（否则 activation 会因空字符串而跳过，且被 `if:` 跳过的 job 报告为成功——详见该目录 AGENTS.md）。

### 7.4 `Protect .github` 与 provider health

- [protect-github-dir.yml](../.github/workflows/protect-github-dir.yml)：作者没有可信权限时拒绝任何改动 `.github/` 的 PR（信任信号是解析后的 `.permission`，不是 `.role_name`）；仅在仓库设置里标为 required 后才真正阻断合并。不要给它加 `paths:` 过滤。
- Z.AI 支撑的工作流必须 import `shared/provider-health.md`，并在顶层 gate 里带上 `needs.provider_health.outputs.ready == 'true'`；健康检查调用 `GET https://api.z.ai/api/monitor/usage/quota/limit`。

---

## 8. 发布流程

**没有独立的 `release.yml`**——发布完全由 [.github/workflows/ci.yml](../.github/workflows/ci.yml) 中 **tag 触发**的作业完成（本仓库的 `.github/release.yml` 只定义 changelog 分类规则，见 §8.2）：

```
push tag
  └─ (需 check 通过)
       ├─ latest-versions-canary
       ├─ deploy-docs          # 向 pydantic/unified-docs 派发 docs-update
       ├─ release-build        # uv build --all-packages --no-sources + 临时 venv 冒烟安装/导入
       │     └─ 冒烟：import clai, pydantic_ai, pydantic_evals, pydantic_graph,
       │               pydantic_ai_harness, pydantic_clai2；`pai/clai/clai2 --help`
       │     └─ 产出 release-dist artifact，输出 package-version / harness-version
       ├─ release              # pypa/gh-action-pypi-publish（OIDC，environment: release，skip-existing）
       └─ gh-aw-engine-pin / -reminder   # 发布后如 gh-aw 引擎 pin 落后则开 issue
```

`release-build` 与 `release` 分离：构建在一个**没有发布凭证**的独立 job 里完成，OIDC token 只在 `release` job 中针对已构建产物使用。

### 8.1 tag 与「不是发布」的 tag

CI tag 触发为 `tags: ["**"]`，但排除 `!clai2-bleeding`——该 tag 由 [clai2-bleeding.yml](../.github/workflows/clai2-bleeding.yml) 随 CLAI 变更移动，用于 `/update` 的 `main` 渠道（见 [src/pydantic_clai2/scripts/build_bleeding.sh](../src/pydantic_clai2/scripts/build_bleeding.sh)），并非正式发布。

### 8.2 `.github/release.yml`（changelog 规则）

[.github/release.yml](../.github/release.yml) 定义 GitHub Release 变更日志的自动分类：

| 分类 | label |
|------|-------|
| ⚠️ Compatibility Notes | `compatibility impact` |
| 🚀 Features | `feature` |
| 🐛 Bug Fixes | `bug` |
| 📦 Dependencies | `dependency` |

`docs` 与 `chore` label 的 PR 被排除在 changelog 之外。

文档由外部仓库 [pydantic/unified-docs](https://github.com/pydantic/unified-docs) 渲染与发布；本仓库负责校验（链接/锚点、示例可执行）并在发布时派发 `docs-update`。[docs-navigation.yml](../.github/workflows/docs-navigation.yml) 在 PR 打上 `trigger:docs` 标签时派发 `docs-preview`。

---

## 9. 辅助脚本

### 9.1 [scripts/](../scripts/)

| 脚本 | 用途 |
|------|------|
| `typecheck_changed.py` | CI 的增量 Pyright 入口：构建 import 图，仅检查改动文件与其传递导入者；checkpoint 存于 `.git`；`CI` 或 Python < 3.11 时回退全量（见 §3.2） |
| `test_typecheck_changed.py` | 上述脚本的测试（CI 在 `quality` job 里单跑，因其会真实调用 Pyright） |
| `check_cassettes.py` | 校验每个 VCR cassette 都有对应测试，报告孤儿 cassette（pre-commit `check-cassettes` 调用） |
| `upload_test_files.py` | 上传 `tests/assets` 二进制到各 provider 文件存储，打印 ID/URI 供测试使用（`source .env && uv run python scripts/upload_test_files.py`） |
| `verify_bedrock_access.py` | 手动校验 Bedrock + S3 凭证与测试文件访问 |
| `verify_vertex_gcs.py` / `verify_vertex_gcs_all_types.py` / `verify_vertex_gcs_tool_result.py` | 手动校验 Vertex AI 对 gs:// URI 的支持（各类文件类型 / tool result） |
| `gather-review-context.sh` / `gather-pydantic-ai-review-context.sh` | 为审查收集上下文（shell） |

### 9.2 [.github/scripts/](../.github/scripts/)

Python 脚本（多数都带 `test_<name>.py` 回归测试）：

| 脚本 | 用途 |
|------|------|
| `agent_provider_health.py` | 基于 provider 健康门控 agent 推理，并对运维 incident 做对账 |
| `agent_spend_report.py` | `gh-aw` 工作流的每周消耗报告，投递到 Slack |
| `agentic_workflow_guard.py` | gh-aw 静态策略守卫（见 §7.2） |
| `check_api_compatibility.py` | 与上一个稳定 release 比较公共 API 兼容性 |
| `check_http_dependencies.py` | 检查构建产物中 HTTP 依赖的约束 |
| `ci_duration.py` | 统计 CI 耗时（`testpaths` 中亦有 `test_ci_duration.py`） |
| `community_demand.py` | 判断老 issue 的真实社区需求，给真正有需求的加 `community-backed` label（`snapshot` + `apply` 两阶段） |
| `feature_digest.py` | 每周 feature digest：挑出至多五个未被考虑的 feature request |
| `issue_pr_attention_monitor.py` | 对陈旧 issue/PR 分类并执行有界提醒策略 |
| `semantic_owner_router.py` | 把 open item 确定性地路由给语义维护者（需先有 `p:1-highest`/`p:2-high` 优先级标签） |
| `triage_models.py` / `triage_telemetry.py` | triage 自动化的类型边界 / 失败即静默的 Logfire 事件发射 |

其它：`pydantic_ai_gh_aw_shim/`（gh-aw 的 pydantic-ai shim，含 `todo_write.py`、`grep.py` 等工具适配）、`pydantic-ai-runner`（+ `.lock`，gh-aw runner 的 `uv run --script` 入口）、`pyrightconfig.json`（独立嵌套 Pyright 项目）、shell 脚本 `install-sandbox-tools.sh`、`prefetch-github-context.sh`、`prefetch-open-issues.sh`、`prewarm-pydantic-ai-runner.sh`、`pydantic-ai-runner-launch.sh`。

### 9.3 CLAI2 专属

[src/pydantic_clai2/scripts/build_bleeding.sh](../src/pydantic_clai2/scripts/build_bleeding.sh)：从当前 checkout 构建 `/update` 在 `main` 渠道安装的 sdists（`pydantic-clai2`、`pydantic-ai-harness`、`pydantic-ai-slim`、`pydantic-graph`，命名 `<package>-<commit>.tar.gz`）与 `clai2-bleeding.json`。其 `PACKAGES` 列表须与 [cli/self_update.py](../src/pydantic_clai2/pydantic_clai2/cli/self_update.py) 保持同步（有测试比对）。

---

## 10. 文档

- 源目录：[docs/](../docs/)；路由/侧边栏/重定向由 [docs/navigation.yml](../docs/navigation.yml) 管理。**在 `docs/` 下新增页面必须在 navigation.yml 登记**，否则 `tests/test_docs_navigation.py` 失败；有意不发布的页面放入 `UNPUBLISHED_PAGES` 并注明原因。
- navigation.yml 的所有 route 相对 Pydantic AI 文档根；`slug` 给完整 canonical route，`aliases` 只用于重定向源；**不要**加 `/ai` 前缀或前导斜杠。改导航时请维护者给 PR 打 `trigger:docs` 标签来校验。
- CI 校验文档页之间的每个链接（含锚点），锚点由标题文本生成——重命名标题会静默打断所有指向它的链接；被链接的标题用 `{#custom-id}` 钉住锚点。
- 规范见 [docs/AGENTS.md](../docs/AGENTS.md)：项目名统一写作 `Pydantic AI`；API 链接用**引用式** `[ElementName][module.path.ElementName]`；提示用 `!!! note` / `!!! warning`（而非 blockquote/GitHub alert）；provider 特有配置放 `docs/models/{provider}.md`；示例必须可执行，用 fence 属性（如 `{test="skip" lint="skip"}`、`typecheck="skip - <reason>"`）排除；功能表用 `Notes`/`Provider Support Notes` 列与 `Full feature support`/`Limited parameter support`/`Unsupported` 标签。
- `docs/index.md` 与根 `README.md` 讲述同一故事，需保持同步：前者用相对链接、tabs（`=== "..."`）、编号注解（`(1)!`）等 MkDocs 专用标记；后者用绝对链接、`###` 段落与单行 `#` 注释。镜像的代码示例保持代码一致，只有注释/注解/链接形式/fence 属性可不同。
- 贡献流程见 [docs/contributing.md](../docs/contributing.md)：与维护者先对齐再写非平凡代码；清晰的 issue + 最小复现 + Logfire trace；PR 按优先级审查而非提交顺序；不要对未预先对齐的 PR 强行 force-push。

---

## 11. 提交与 PR 约定

来源：根 [AGENTS.md](../AGENTS.md)、[.github/pull_request_template.md](../.github/pull_request_template.md) 与各 `AGENTS.md`。

- **PR 标题直接进入发布 changelog**：写祈使句说明改动，**不加** `fix:` / `docs:` / `chore:` 前缀（前缀属于 commit subject），并用反引号包裹所有代码标识符（类名、关键字参数、模块路径、CLI flag、环境变量、文件路径）。可对照已合并 PR 核对：`gh pr list --state merged --limit 20`。
- 提交 PR 时填好 PR 模板并写明应被关闭的 issue 编号。
- **不要**把 Claude 加为 co-author；提交只以用户身份署名，不写 `Co-Authored-By`。
- 不要留下未提交的本地改动。
- **推送不是终点**：任务完成的判据是 **CI 绿 + 无未解决评论**（见 `pushing-commits-to-the-repo` skill）。
- 推送限制是「从真实失败中得出的结论」，不是读某个元数据字段就判定的（例如 `maintainerCanModify: false` 并不意味着你不能推送）。
- 变更范围要克制：bugfix 只做能解决已复现问题的最小改动（常是一行 + 一个回归测试），不要借机重构共享协议或扩大 sibling 覆盖。
