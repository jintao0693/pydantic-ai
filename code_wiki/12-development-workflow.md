# 12 · 开发工作流

本篇覆盖构建、测试、类型检查、CI 与发布流程，是「如何运行 / 如何贡献」的参考。

---

## 1. 环境与安装

项目使用 [`uv`](https://docs.astral.sh/uv/)（需 `>= 0.9.25`，见根 `pyproject.toml` 的 `required-version`），支持 Python 3.11–3.14。

```bash
make install        # uv sync --frozen --all-extras --no-extra mcp-tasks --all-packages --group lint + 安装 pre-commit
make help           # 列出全部 target
make                # 完整本地流水线：format lint typecheck testcov
```

`make install-all-python` 会为 3.11/3.12/3.13/3.14 各建一个 venv（`.venv311` … `.venv314`）。

---

## 2. Makefile 目标

| Target | 命令 | 说明 |
|--------|------|------|
| `install` | `uv sync --frozen --all-extras --no-extra mcp-tasks --all-packages --group lint` + `pre-commit install` | 安装依赖、包与 pre-commit 钩子 |
| `install-all-python` | 各 Python 版本分别 `uv sync` | 多版本 venv |
| `sync` | `uv sync --all-extras --no-extra mcp-tasks --all-packages --group lint` | 更新包与 `uv.lock`（非 frozen） |
| `format` | `uv run ruff format` + `uv run ruff check --fix --fix-only` | 格式化 |
| `lint` | `uv run ruff format --check` + `uv run ruff check` | 只检查不修改 |
| `typecheck-pyright` | `PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright …`（并对 `.github/scripts` 单独跑） | Pyright 检查 |
| `typecheck-changed` | `uv run python scripts/typecheck_changed.py` | CI 的 Pyright 入口（增量，仅改动的文件与其传递导入者） |
| `typecheck-mypy` | `uv run mypy` | mypy（`tests/typed_agent.py`，`strict`） |
| `typecheck` / `typecheck-both` | 上述组合 | — |
| `test` | `pytest -n auto --dist=loadgroup --durations=20` | 快速本地测试（无覆盖率） |
| `testcov` | `coverage run -m pytest …` + `coverage combine/report/html` | 带覆盖率与 HTML 报告 |
| `test-all-python` | 各版本分别 `coverage run -p -m pytest` 后合并 | 全版本覆盖率 |
| `integration-{localstack,mongodb,postgres,redis}` | 对应的 `src/pydantic_ai_harness/integration_tests/<svc>` | 需 Docker/外部服务 |
| `update-examples` | `uv run -m pytest --update-examples tests/test_examples.py` | 重新生成文档示例 |
| `update-vcr-tests` | `uv run -m pytest --record-mode=rewrite tests` | 重新录制 cassettes（需 API key） |

---

## 3. 工具链配置（根 `pyproject.toml`）

- **Ruff**：`line-length = 120`、`target-version = "py311"`；选中规则含 `E4,E7,E9,F` 及 `Q`、`RUF100`、`C90`、`UP`、`I`、`D`、`TID251`、`PLW1514`、`PGH003`、`BLE001`；Google docstring 风格、单引号、`mccabe max-complexity = 15`；禁用 API：`typing.TypedDict`（用 `typing_extensions.TypedDict`）与 `asyncio.Lock`（用 `anyio.Lock`）。
- **Pyright**：`typeCheckingMode = "strict"`、`pythonVersion = "3.11"`；`.github/scripts` 为独立嵌套项目（因为 Pyright 跳过点目录）。
- **mypy**：`files = "tests/typed_agent.py"`、`strict = true`。
- **pytest**：`anyio_mode = "auto"`（所有 `async def` 测试自动经 anyio，无需标记）、`testpaths = ["tests", ".github/scripts/test_ci_duration.py"]`、`xfail_strict = true`；标记 `modal_live` / `e2b_live` / `sprites_live` / `temporal`；`filterwarnings = ["error", ...]` 加多项定向忽略。
- **coverage**：`branch = true`、**`fail_under = 100`**、`skip_covered = true`。这是硬门禁：新代码必须 100% 分支覆盖（除排除项外）。

---

## 4. pre-commit 钩子

[.pre-commit-config.yaml](../.pre-commit-config.yaml)：

| 钩子 | 作用 |
|------|------|
| `no-commit-to-branch` | 阻止直接提交到 `main` |
| `check-yaml` / `check-toml` / `end-of-file-fixer` / `trailing-whitespace` | 基础文件卫生 |
| `check-added-large-files --maxkb=1024` | 阻止大文件（排除 cassettes 与 `uv.lock`） |
| `fix-smartquotes` / `fix-spaces` / `fix-ligatures`（texthooks） | 文本修正 |
| `codespell` | 拼写检查 |
| `zizmor` | GitHub Actions 安全审计 |
| `no-rst-syntax` | 禁止 Python/Markdown 中的 RST 语法 |
| `clai-help` | `uv run pytest clai/update_readme.py` |
| `format` / `lint` | 调用 `make format` / `make lint` |
| `check-cassettes` | `uv run python scripts/check_cassettes.py` |

> Pyright **不是** pre-commit 钩子；由 CI 运行全量检查。

---

## 5. 测试约定

见 `tests/AGENTS.md` 与 `tests/conftest.py`：

- **异步**：`anyio_mode = "auto"`；默认 asyncio 后端，`--anyio-backend=trio` 可跑可移植测试。`tests/harness` 与 `tests/clai2` 在各自 `conftest.py` 中固定 asyncio。
- **默认风格**：VCR + 公共 API 测试（记录真实 provider 响应为 cassette）；单元测试仅用于内部/确定性行为，且需说明为何不使用 VCR。Cassette 同时充当 prompt-cache 前缀回归网（`@pytest.mark.moves_cache_prefix(reason=...)`）。
- **录制**：`cassetter` pytest 插件提供 `@pytest.mark.vcr` 与 `vcr` fixture；`--record-mode=rewrite` 录制，默认回放。实时 WebSocket cassette 位于 `tests/realtime/cassettes/`。
- **常用 fixture/helper**：`model`（`indirect=True`）、`allow_model_requests`、`request_capture`（wire 级请求捕获）、`env`、二进制内容 fixture、断言 helper（`IsNow`、`IsStr`、`IsInstance` 等）、`blockbuster`（自动阻塞调用检测）。
- `conftest.py` 将 `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`，并按包安装情况 `collect_ignore` `harness` / `clai2`。

**运行单个测试**（仓库推荐）：

```bash
uv run pytest path/to/test.py::test_name
```

**测试子目录布局**（`tests/`）：根级 feature 测试、`assets/`、`benchmarks/`、`cassettes/`、`clai2/`、`durable_exec/`、`evals/`、`graph/`、`harness/`、`models/`、`profiles/`、`providers/`、`realtime/`、`ext/` 等。

---

## 6. CI（`.github/workflows/ci.yml`）

触发：push 到 `main`、push tag、所有 PR。

主要作业：

| 作业 | 说明 |
|------|------|
| `classify` | PR 变更分类，输出 `content_only` / `docs_changed` / `clai2_only` / `pyright_changed` 等，决定走全量还是精简路径 |
| `quality` | 运行 `.github/scripts/test_*.py`、增量类型检查脚本测试、gh-aw 策略与 lock 新鲜度检查、`pre-commit --all-files`、`make typecheck-changed`、API 兼容性检查、`uv build`、`check_http_dependencies.py`、`twine check --strict` |
| `mypy` | `make typecheck-mypy` |
| `docs-assets` | 图片 tinify 检查、lychee 离线链接/锚点检查、doc snippet 测试 |
| `test` | 矩阵 Python 3.11–3.14 × 安装形态（slim / evals / harness / clai2 / standard）= 20 个 cell |
| `test-all-extras` / `test-durable-exec` / `test-lowest-versions` / `test-temporal-latest` | 各种依赖与版本组合 |
| `test-examples` / `test-harness-browser-use` / `test-clai2-clipboard` / `test-fastmcp-4` | 专项 |
| `coverage` | 合并覆盖率产物，`coverage report` + `strict-no-cover` 审计 |
| `check` | `re-actors/alls-green` 分支保护门禁 |

**Agentic Workflows（gh-aw）**：`.github/workflows/` 下大量 `*.md` 源文件（如 `pydantic-ai-pr-review.md`、`pydantic-ai-bug-hunter.md`）经 `gh aw compile` 生成对应的 `*.lock.yml`。**不要手改 `.lock.yml`**；编辑源后重新编译并提交 lock。`AGENTS.md`（该目录）详述了该流程与策略守卫。

---

## 7. 发布流程

**没有独立的 `release.yml`** —— 发布完全由 `ci.yml` 中 tag 触发的作业完成：

```
push tag
  └─ (需 check 通过，且 latest-versions-canary 通过)
       ├─ release-build   # uv build --all-packages --no-sources + 在临时 venv 冒烟安装/导入
       ├─ release         # pypa/gh-action-pypi-publish（OIDC，environment: release）
       ├─ deploy-docs     # 向 pydantic/unified-docs 派发 docs-update
       └─ gh-aw-engine-pin / -reminder  # 发布后如 gh-aw 引擎 pin 落后则开 issue
```

文档由外部仓库 [pydantic/unified-docs](https://github.com/pydantic/unified-docs) 渲染与发布；本仓库负责校验（链接/锚点、示例可执行）并在发布时派发 `docs-update`。`docs-navigation.yml` 在 PR 打上 `trigger:docs` 标签时派发 `docs-preview`。

---

## 8. 辅助脚本

`scripts/`：

| 脚本 | 用途 |
|------|------|
| `typecheck_changed.py` | CI 的增量 Pyright 入口：构建导入图，仅检查改动文件与传递导入者；记录 checkpoint；`CI` 环境或 Python < 3.11 时回退全量 |
| `check_cassettes.py` | 校验每个 VCR cassette 都有对应测试，报告孤儿 cassette |
| `upload_test_files.py` | 上传 `tests/assets` 二进制到各 provider 文件存储，打印 ID/URI 供测试使用 |
| `test_typecheck_changed.py` | 增量类型检查脚本的测试 |
| `verify_bedrock_access.py` / `verify_vertex_gcs*.py` | 手动校验 Bedrock / Vertex 凭证与测试文件访问 |

`.github/scripts/`（CI 与 Agent 自动化；每个都有对应 `test_*.py`）：`agent_provider_health.py`、`agent_spend_report.py`、`agentic_workflow_guard.py`、`check_api_compatibility.py`、`check_http_dependencies.py`、`ci_duration.py`、`community_demand.py`、`feature_digest.py`、`issue_pr_attention_monitor.py`、`semantic_owner_router.py`、`triage_telemetry.py`、`triage_models.py`，以及 gh-aw 的 `pydantic-ai-runner` / `pydantic_ai_gh_aw_shim/`。

---

## 9. 文档

- 源目录：[docs/](../docs/)；路由/侧边栏由 [docs/navigation.yml](../docs/navigation.yml) 管理（新增 `docs/` 下页面必须在此登记，否则 `tests/test_docs_navigation.py` 失败；未发布页面放入 `UNPUBLISHED_PAGES` 并注明原因）。
- 规范见 `docs/AGENTS.md`：项目名统一为 "Pydantic AI"；API 链接用引用式 `[Element][module.path.Element]`；admonition 用 `!!! note` / `!!! warning`；provider 特有配置放在 `docs/models/{provider}.md`；示例必须可执行；按 fence 用 `{test="skip" lint="skip"}` 排除。
- `docs/index.md` 与根 `README.md` 需保持同步。
- 贡献流程见 [docs/contributing.md](../docs/contributing.md)：与维护者先对齐再写非平凡代码；清晰的 issue + 最小复现 + Logfire trace；PR 按优先级审查。

---

## 10. 提交与 PR 约定

- PR 标题直接进入发布 changelog：写祈使句、说明改动，不加 `fix:` / `docs:` 前缀（前缀属于 commit subject），并用反引号包裹代码标识符。
- 提交不要添加 Claude 作为 co-author。
- 推送后任务未结束：需 CI 绿、无未解决评论（见根 `AGENTS.md` 与 `pushing-commits-to-the-repo` skill）。
- 不要留下未提交的本地改动。
