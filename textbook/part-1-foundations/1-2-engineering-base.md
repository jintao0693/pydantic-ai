# 1.2 工程基座：uv、类型系统、anyio、pytest

> 本课配套 labs：[`labs/part_1/ch_1_2.py`](../../labs/part_1/ch_1_2.py)、验收测试 [`labs/part_1/test_ch_1_2.py`](../../labs/part_1/test_ch_1_2.py)。

---

## 1. 本课目标

学完本章，你应当能够：

1. 说清 `pyproject.toml` 的四个关键部分（`[project]` / `dependencies` / `[dependency-groups]` / `[tool.*]`）各自管什么，以及 `uv.lock` 为什么必须进版本控制。
2. 独立完成 `uv python install` / `uv sync` / `uv run` / `uv add` / `uv lock` 这一套日常动作，并解释 workspace 是什么、什么时候用。
3. 解释「严格类型模式」的价值，说出 pyright 与 mypy 的取舍，并知道为什么仓库里禁用 `typing.TypedDict`、`Any` 要少用。
4. 用 pytest 组织测试：`test_*.py` 命名、fixture、`@pytest.mark.parametrize`、`pytest.raises`、跳过与标记。
5. **在没有 API Key、没有网络的情况下**，用 `TestModel` 与 `FunctionModel` 把 Agent 跑通并做出稳定断言——这是本章的核心产出。
6. 说清 logging / Logfire 埋点的定位，会用 `make format/lint/typecheck/test` 的「CI 本地复现」思路。

---

## 2. 前置知识

- 已完成 **1.1 智能体心智模型**：知道「Agent = 模型 + 指令 + 工具 + 循环」。
- Python ≥ 3.11：会写函数、`with`、类型注解（`x: int`、`def f() -> str`）。
- 用过命令行，知道 `cd`、环境变量、退出码。
- 若你想补齐异步基础，见篇零的 0.2 章（异步、类型系统与虚拟环境）。

---

## 3. 为什么需要它

写一个「能跑的 LLM demo」很容易，写一个「团队能长期维护、能进 CI、能被面试官追问」的项目很难。差距几乎全在**工程基座**上：

| 没有基座时的症状 | 基座提供的答案 |
|------------------|----------------|
| 同事 clone 下来跑不起来：Python 版本不对、依赖版本漂移 | `requires-python` + `uv.lock` 锁定可复现环境 |
| 改一个函数，线上另一处悄悄崩了 | 严格类型检查在 CI 里拦住它 |
| 测试要真实调用模型：慢、贵、还要 API Key，于是干脆不测 | `TestModel` / `FunctionModel` 离线跑通 |
| 「在我机器上是好的」 | `uv sync --frozen` + `uv run` 保证一致 |
| 提交前忘了格式化，review 全是格式噪音 | ruff + pre-commit 自动兜底 |

对就业而言，这一章是最容易被低估、也最能拉开差距的部分：面试官问「你怎么测一个 Agent？」时，能答出 `TestModel`/`FunctionModel`/VCR 三层策略的人，和只会说「我手动试了试」的人，完全是两个档次。

---

## 4. 核心概念

### 4.1 依赖与解释器：uv 同时管两件事

传统分工里，`pyenv` 管 Python 版本、`pip`/`venv` 管依赖、`pip-tools`/`poetry` 管锁文件。`uv` 把这些合成一条命令：

| 需求 | uv 命令 |
|------|---------|
| 装一个新 Python 解释器 | `uv python install 3.12` |
| 把项目钉在某版本 | `uv python pin 3.12`（写入 `.python-version`） |
| 按 `uv.lock` 精确安装依赖 | `uv sync`（CI 里用 `uv sync --frozen`） |
| 在项目环境里跑命令 | `uv run python script.py` / `uv run pytest` |
| 加依赖并自动写回 `pyproject.toml` + 刷新 `uv.lock` | `uv add pydantic-ai-slim` |
| 只重算锁文件、不安装 | `uv lock` |

关键心智模型：**`pyproject.toml` 写「我想要什么」，`uv.lock` 写「实际解析到了什么」**。前者是人读的声明，后者是机器用的、跨平台的精确快照。因此 `uv.lock` 必须提交进 Git——它才是「同事 clone 下来能得到同一个环境」的保证。

### 4.2 `pyproject.toml` 的四块拼图

```toml
[project]                        # 1) 元数据 + 运行时依赖
name = "my-agent-app"
version = "0.1.0"
requires-python = ">=3.11"       # 解释器下限，uv 据此报错而非静默降级
dependencies = [
    "pydantic-ai-slim",
]

[dependency-groups]              # 2) 开发期依赖（PEP 735），不随包发布
dev = [
    "pytest",
    "ruff",
]

[tool.ruff]                      # 3) 各工具配置，统一住在一个文件里
line-length = 120

[tool.pytest.ini_options]        # 4) 测试配置
testpaths = ["tests"]
```

- `[project].dependencies` 是**运行**这个包所必需的；`[dependency-groups]` 下的 `dev` 只在开发时用（测试、lint）。`uv add --dev pytest` 会写进这里。
- `requires-python` 不是文档，是**运行时约束**：`uv` 会挑满足它的解释器，不满足就直接失败。
- `[tool.*]` 是工具专属命名空间：`[tool.ruff]`、`[tool.pyright]`、`[tool.pytest.ini_options]`、`[tool.coverage.*]` 各管一摊。

### 4.3 workspace：一个仓、多个包

当仓库里不止一个可安装包（比如本仓库有 `pydantic-ai-slim`、`pydantic-evals`、`pydantic-graph`…），用 workspace 让它们共享一个锁文件、一套虚拟环境：

```toml
[tool.uv.workspace]
members = ["pydantic_ai_slim", "pydantic_evals", "pydantic_graph", "clai"]

[tool.uv.sources]                # 本地包直接指向源码目录，而不是去 PyPI 拉
pydantic-ai-slim = { workspace = true }
```

好处：**改一个包，另一个包立刻用上改后的代码**，不需要 `pip install -e` 循环。本教材的 labs 刻意用 `uv run --no-project --with ...` 脱离 workspace，避免与仓库的依赖解析打架（见 4.5）。

### 4.4 类型系统：为什么值得严格

Python 是动态语言，但 LLM 应用大量在「结构化输出」「工具参数校验」上出错，而这类错误恰恰是类型系统最擅长的：

- **早失败**：`result.output` 本该是 `str`，类型检查器在你敲代码时就告诉你它可能是别的。
- **可重构**：把 `deps_type` 从 `int` 改成 `MyDeps`，类型检查器会指出所有受影响的调用点。
- **可读的契约**：函数签名即文档。

**pyright vs mypy**：

| 维度 | pyright | mypy |
|------|---------|------|
| 实现 | TypeScript（Node） | Python |
| 速度 | 快（可多线程） | 慢 |
| 严格模式 | `typeCheckingMode = "strict"` | `strict = true` |
| 生态 | VS Code / Pylance 内建 | 老牌、插件多 |
| 本仓库 | **默认门禁**（`make typecheck` → pyright） | 仅对 `tests/typed_agent.py` 跑一遍 |

两者都能开严格模式，实践中常见做法是**以 pyright 为主、mypy 兜底**。

**`Any` 的代价**：`Any` 会「传染」——一旦某个值是 `Any`，经过它的一切都失去检查。`cast()` 同理，是关掉检查而不是修复检查。能用 `TypedDict` 精确描述 kwargs 时就不要用 `dict[str, Any]`：

```python
from typing import TypedDict   # 注意：本仓库禁用 typing.TypedDict，改用 typing_extensions.TypedDict（见 §10）

class AgentRetries(TypedDict, total=False):
    tools: int
    output: int
```

这样 `retries={"tools": 3}` 能被检查器验证键名与值类型。

### 4.5 测试：pytest 组织与「离线跑通」

pytest 的基本约定：

- 文件叫 `test_*.py`，函数叫 `test_*`，pytest 自动发现。
- **fixture** 提供可复用的准备逻辑（构造对象、清理资源），通过参数名注入。
- **`@pytest.mark.parametrize`** 用一张表把同一个测试跑多组输入。
- **`pytest.raises(Exc, match=...)`** 断言「确实抛出了预期的异常」。
- **标记**（`@pytest.mark.skip` / `skipif` / `xfail`）处理「暂时不适用」与「已知会失败」。

**LLM 代码的测试难点**：真实模型慢、贵、不确定，还需要 Key。pydantic-ai 提供两把「假模型」：

| 假模型 | 一句话 | 用在哪 |
|--------|--------|--------|
| `TestModel` | 内置的「永远成功」模型，不联网 | 跑通流程、测工具循环、测结构化输出 |
| `FunctionModel` | **你自己写函数**当模型，完全掌控每一步输出 | 精确构造场景、断言特定分支 |

`TestModel` 是「黑盒假模型」，`FunctionModel` 是「白盒假模型」。本章的最小示例把两者各跑一遍。

**为什么用 `uv run --no-project` 跑 labs**：本教材的 labs 位于 pydantic-ai 仓库内部，若参与仓库 workspace 的依赖解析，就会和仓库自身的 extras/冲突规则纠缠。`--no-project` 让它自成一个临时环境、只装指定的包，反而更干净、更可复现：

```bash
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest -q
```

---

## 5. 最小可运行示例

### 5.1 运行方式

labs 不需要任何 API Key，也不需要联网：

```bash
cd textbook/labs

# 直接运行示例
uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_2.py

# 跑本章验收测试
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_2.py -q
```

### 5.2 完整代码：`labs/part_1/ch_1_2.py`

```python
"""第 1.2 章 · 工程基座：用 TestModel / FunctionModel 离线运行 Agent。

本模块演示「不连真实模型、不消耗 API Key」也能把 Agent 跑通并从测试中
稳定断言它，这正是工程基座（uv + 类型系统 + pytest）落到 pydantic-ai 上
的第一个抓手。

运行方式（无需任何 API Key，也不需要联网）：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_2.py
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# ---------------------------------------------------------------------------
# 1) TestModel：一行代码，让 Agent 在没有真实模型时也能跑完整个循环
# ---------------------------------------------------------------------------


def run_with_test_model() -> str:
    """用 TestModel 跑通一次 Agent 运行，返回 result.output。"""
    agent = Agent(
        TestModel(),  # 一个「永远成功」的假模型，不发起任何网络请求
        instructions="你是一个用于离线测试的助手。",
    )
    result = agent.run_sync("用一句话介绍你自己")
    return result.output


# ---------------------------------------------------------------------------
# 2) FunctionModel：用一个自定义函数充当模型，完全控制模型行为
# ---------------------------------------------------------------------------


def _last_user_text(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条用户消息的文本。"""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return part.content
    return ''


def build_rule_based_model() -> FunctionModel:
    """构造一个「规则模型」：函数被调用一次，就返回一条我们指定的响应。"""

    def rule_based(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # messages 是到目前为止的完整历史；info 描述本次请求暴露的工具/输出
        user_text = _last_user_text(messages)
        if '你好' in user_text:
            reply = f'你好！你刚刚说的是：{user_text}'
        else:
            reply = f'收到 {len(user_text)} 个字符，可用工具 {len(info.function_tools)} 个。'
        return ModelResponse(parts=[TextPart(content=reply)])

    return FunctionModel(rule_based)


def run_with_function_model(prompt: str) -> str:
    """用 FunctionModel 驱动 Agent，返回可预测的输出。"""
    agent = Agent(build_rule_based_model())
    return agent.run_sync(prompt).output


# ---------------------------------------------------------------------------
# 3) 观察消息协议：一次运行留下了哪些消息
# ---------------------------------------------------------------------------


def message_kinds(prompt: str = 'ping') -> list[str]:
    """返回一次运行的 all_messages() 中每条消息的 kind 序列。"""
    agent = Agent(TestModel())
    result = agent.run_sync(prompt)
    return [message.kind for message in result.all_messages()]


def main() -> None:
    print('=== 1) TestModel：无需真实模型也能跑通 ===')
    print(run_with_test_model())
    print()

    print('=== 2) FunctionModel：用函数精确控制模型行为 ===')
    print(run_with_function_model('你好，世界'))
    print(run_with_function_model('随便说点什么'))
    print()

    print('=== 3) 一次运行留下的消息 kind 序列 ===')
    print(' -> '.join(message_kinds()))


if __name__ == '__main__':
    main()
```

### 5.3 逐行讲解

- **`from __future__ import annotations`**：让所有注解延迟求值，配合 `list[str]` 这类新式语法更省心。
- **`TestModel()`**：不传参数就是一个「永远成功、无工具调用」的假模型。它在被请求时返回固定文本 `success (no tool calls)`。
- **`Agent(TestModel(), instructions=...)`**：`instructions` 是给模型的指令（会进入请求），`TestModel` 会照单全收但不真正「理解」。
- **`agent.run_sync("...")`**：同步入口，内部驱动「用户输入 → 模型请求 → 工具/输出 → 结束」整条图，返回 `AgentRunResult`；`.output` 是最终输出（此处是 `str`）。
- **`_last_user_text`**：遍历 `messages`（`list[ModelRequest | ModelResponse]`），取最后一条 `ModelRequest` 里最后一个 `UserPromptPart` 的文本。`isinstance` 在这里是必要的——消息是**判别联合**（discriminated union），靠 `kind`/`part_kind` 区分。
- **`build_rule_based_model`**：`FunctionModel` 接受一个函数 `(messages, info) -> ModelResponse`。函数每被调用一次，就相当于「模型思考一次」。我们据此写死回复逻辑。
- **`info: AgentInfo`**：本次请求的元信息，含 `function_tools` / `output_tools` / `allow_text_output` / `instructions` 等。示例只用了 `len(info.function_tools)`，说明「模型能看到哪些工具」这件事是可控的。
- **`ModelResponse(parts=[TextPart(content=...)])`**：这是模型的「一轮回答」。`TextPart` 是纯文本片段；未来你会用到 `ToolCallPart` 等其它 part 类型。
- **`result.all_messages()`**：把这次运行产生的完整历史取出来。示例打印每条消息的 `kind`，你会看到最小的 `request -> response` 序列。
- **`main()` + `if __name__ == '__main__'`**：让文件既能被 `pytest` import，也能被 `python` 直接运行。

### 5.4 实际运行输出

```
=== 1) TestModel：无需真实模型也能跑通 ===
success (no tool calls)

=== 2) FunctionModel：用函数精确控制模型行为 ===
你好！你刚刚说的是：你好，世界
收到 6 个字符，可用工具 0 个。

=== 3) 一次运行留下的消息 kind 序列 ===
request -> response
```

注意第 2 段第二行的 `6`：`'随便说点什么'` 正好 6 个字符——因为输出由**我们的函数**决定，所以它可以被精确断言。这就是 `FunctionModel` 的价值。

---

## 6. 深入剖析

### 6.1 `TestModel` 的构造参数

```text
TestModel(
    *,
    call_tools: list[str] | Literal['all'] = 'all',
    custom_output_text: str | None = None,
    custom_output_args: Any | None = None,
    seed: int = 0,
    model_name: str = 'test',
    profile: ModelProfileSpec | None = None,
    settings: ModelSettings | None = None,
)
```

| 参数 | 默认 | 作用 |
|------|------|------|
| `call_tools` | `'all'` | 模型会调用哪些工具；`[]` 表示一个都不调 |
| `custom_output_text` | `None` | 自定义返回的文本（不设则返回 `success (no tool calls)`） |
| `custom_output_args` | `None` | 结构化输出模式下返回的对象 |
| `seed` | `0` | 固定随机种子，保证行为可复现 |

### 6.2 `FunctionModel` 的契约

```text
FunctionModel(
    function: FunctionDef | None = None,
    *,
    stream_function: StreamFunctionDef | None = None,
    model_name: str | None = None,
    profile: ModelProfileSpec | None = None,
    settings: ModelSettings | None = None,
)
```

其中（pydantic-ai-slim 2.54.0 实测）：

```text
FunctionDef  = Callable[[list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]]
AgentInfo(
    *,
    function_tools: list[ToolDefinition],
    allow_text_output: bool,
    output_tools: list[ToolDefinition],
    model_settings: ModelSettings | None,
    model_request_parameters: ModelRequestParameters,
    instructions: str | None,
)
```

要点：

- 函数**可以返回一个 `ModelResponse`，也可以返回 awaitable（即 `async def`）**。
- 返回值必须是 `ModelResponse`——pydantic-ai 在 2.x 已把「返回裸字符串」的写法收紧了，请统一构造 `ModelResponse(parts=[...])`。
- `stream_function` 用于 `run_stream*` 路径；本章不涉及，见篇三/篇二。

### 6.3 消息与结果对象

- `ModelResponse(parts=[TextPart(content=...)])`：模型的一轮回答；`parts` 是片段列表。
- `result.output`：`AgentRunResult` 的最终输出（本章是 `str`）。
- `result.all_messages()`：完整历史 `list[ModelMessage]`；每条消息有 `kind`（`'request'` / `'response'`），每个 part 有 `part_kind`（`'user-prompt'` / `'text'` …）。消息协议详见 2.2 章与源码篇 `code_wiki/04-messages-and-output.md`。

### 6.4 `pyproject.toml` 中你会用到的 `[tool.*]`

```toml
[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]     # 本仓库基础规则集
extend-select = ["I", "UP", "D", "C90"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"

[tool.pytest.ini_options]
anyio_mode = "auto"                  # 所有 async 测试自动经 anyio 运行
```

### 6.5 anyio：异步运行时

pydantic-ai 内部统一用 **anyio** 处理异步（可跑在 asyncio 或 trio 之上）。对使用者的直接含义有两个：

1. **写测试不必再手动 `async` 转换**：pytest 装 `anyio` 插件后，`anyio_mode = "auto"` 会让所有 `async def test_*` 自动被执行；否则默认 `strict` 模式要求你写 `@pytest.mark.anyio`。
2. **`anyio.Lock` 而非 `asyncio.Lock`**：跨后端可移植。本仓库直接禁用了 `asyncio.Lock`（见 §10）。

本章的 labs **刻意只用同步 `run_sync`**，让「工程基座」这件事不被异步细节遮蔽；异步测试放到了 2.1 章。

### 6.6 pytest 进阶：fixture、parametrize、raises

```python
import pytest

@pytest.fixture
def echo_agent():                 # 提供可复用对象，按需注入
    ...

@pytest.mark.parametrize(         # 一张表跑多组
    ('prompt', 'expected'),
    [('a', 'echo: a'), ('hello', 'echo: hello')],
)
def test_echo(echo_agent, prompt, expected):
    assert echo_agent.run_sync(prompt).output == expected

def test_missing_model():
    with pytest.raises(UserError, match='`model` must either be set'):
        Agent().run_sync('hi')
```

- `@pytest.mark.parametrize` 的首参是「参数名元组」，次参是「值列表的列表」。
- fixture 用**函数名**作为参数注入；作用域（`scope="function"` / `"module"` / `"session"`）决定复用粒度。
- `match=` 是正则匹配异常消息，比只断言异常类型更严格。
- 跳过与标记：`@pytest.mark.skip(reason=...)`、`@pytest.mark.skipif(sys.version_info < (3, 12), reason=...)`、`@pytest.mark.xfail`；自定义标记（如 `@pytest.mark.slow`）要在 `[tool.pytest.ini_options].markers` 里登记，否则会告警。

### 6.7 logging 与 Logfire

- **最小可用的日志**是标准库 `logging`：

  ```python
  import logging
  logging.basicConfig(level=logging.INFO)
  logger = logging.getLogger('my_agent')
  logger.info('run started')
  ```

- **生产级可观测性**用 Logfire（基于 OpenTelemetry）。概念上的两步是：先 `logfire.configure()` 初始化，再为 pydantic-ai 打开埋点（`logfire.instrument_pydantic_ai()`，或 agent 侧的 `Agent.instrument_all()` / `agent.instrument()`），之后每次运行、每个模型请求、每次工具调用都会成为带 `run_id` 的 span。

  本章不依赖 Logfire 也能讲清：**埋点不是「打印日志」，而是把运行结构化地记录成 trace**，以便回放与排障。离线时你可以先用 `logging` 与 `result.all_messages()` 观察运行，等接入真实模型再上 Logfire（详见 3.4 章）。

### 6.8 ruff 与 pre-commit

- **ruff** 同时做「格式化」和「lint」：`uv run ruff format` 改格式，`uv run ruff check --fix` 修可自动修的问题，`uv run ruff check` 只报告。
- **pre-commit** 把「提交前必须做的事」固化成钩子（`.pre-commit-config.yaml`）：提交时自动跑 format/lint、检查 YAML/TOML、阻止提交大文件等。装一次（`pre-commit install`）之后不用再从记忆里提醒自己。

### 6.9 CI 本地复现

本仓库把 CI 的每一步都做成 Makefile target，让你在本地就能复现：

| 命令 | 作用 |
|------|------|
| `make format` | 格式化 + 自动修复 |
| `make lint` | 只检查、不改文件 |
| `make typecheck` | 类型检查（默认 pyright） |
| `make test` | 快速测试（无覆盖率） |
| `make all` | `format lint typecheck testcov` 全流程 |

思路：**CI 跑什么，本地就能跑什么**，把失败提前到提交之前。本教材 labs 的对应「验收」就是 pytest 全绿。

---

## 7. 常见变体与工程实践

1. **`TestModel` 自定义文本**：`TestModel(custom_output_text='固定回复')`，适合断言「期望返回什么」。
2. **`TestModel` 控制工具调用**：`TestModel(call_tools=[])` 让模型不调任何工具，用于只测「纯文本路径」。
3. **`TestModel` 结构化输出**：`TestModel(custom_output_args=MyModel(...))` 配合 `output_type=MyModel`，测结构化输出而无需真实模型。
4. **`FunctionModel` 写异步**：把函数写成 `async def`，返回值仍是 `ModelResponse`，用于测并发/取消路径。
5. **`FunctionModel` 模拟多轮**：根据 `messages` 的长度或内容返回不同响应，从而构造「先调工具、再给答案」的多步场景。
6. **`TypedDict` 描述 kwargs**：用 `typing_extensions.TypedDict`（而非 `typing.TypedDict`）替代 `dict[str, Any]`。
7. **严格模式渐进开启**：新项目直接 `typeCheckingMode = "strict"`；老项目可先开核心规则、逐步收紧。
8. **workspace 拆分**：一旦出现第二个可安装包（CLI、SDK、插件），就上 workspace 共享锁文件。
9. **成本/延迟护栏**：测试里用 `TestModel` 天然零成本；涉及真实模型时用 `UsageLimits`（见 3.5 章）。
10. **CI 分片**：测试多了之后用 `pytest -n auto --dist=loadgroup` 并行。

---

## 8. 练习

**练习 1（基础）**：为本章 labs 增加一个 `TestModel(custom_output_text='pong')` 的 Agent，写测试断言 `run_sync('ping').output == 'pong'`。
> 参考要点：直接构造 `Agent(TestModel(custom_output_text='pong'))`；断言 `.output` 精确相等。

**练习 2（类型）**：把 `AgentRetries` 用 `typing_extensions.TypedDict(total=False)` 定义出来，并写一个 `Agent(retries=...)` 的用法；解释为什么不用 `dict[str, Any]`。
> 参考要点：`TypedDict` 能为固定键提供键名与值类型检查；`dict[str, Any]` 会让检查器束手无策。

**练习 3（pytest）**：用 `@pytest.mark.parametrize` 覆盖 5 组输入，验证 `FunctionModel` 的「回显」行为；再补一个 `pytest.raises` 用例。
> 参考要点：参数化避免复制粘贴 5 个测试；`pytest.raises(UserError, match=...)` 验证无模型时的报错。

**练习 4（FunctionModel）**：写一个 `FunctionModel`，让它在第一次被调用时返回一个 `ToolCallPart`（触发工具），并在工具返回后返回最终文本，断言工具真的被调用了。
> 参考要点：读取 `messages` 判断是否已有工具返回消息；用 `info.function_tools` 确认工具可见；断言工具副作用（如列表被 append）。

**练习 5（工程）**：为一个新项目手写最小 `pyproject.toml`（含 `[project]`、`dependencies`、`[dependency-groups].dev`、`[tool.ruff]`、`[tool.pytest.ini_options]`），用 `uv sync` 装好并跑通一个空测试。
> 参考要点：`requires-python = ">=3.11"`；`uv add --dev pytest` 自动写 `[dependency-groups]`；`uv run pytest` 应收集到 0 个测试并正常退出。

---

## 9. 验收标准

完成本章后，下列命令必须全绿：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_2.py
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_2.py -q
```

自测清单：

- [ ] 能解释 `pyproject.toml` 与 `uv.lock` 的分工，并说出为什么后者要进 Git。
- [ ] 能默写 `uv python install` / `uv sync` / `uv run` / `uv add` / `uv lock` 的用途。
- [ ] 能说出 pyright 与 mypy 的差异，以及 `Any` 为什么会「传染」。
- [ ] 能写出 fixture + parametrize + `pytest.raises` 的 pytest 组合。
- [ ] 能用 `TestModel` 与 `FunctionModel` 各跑通一个离线 Agent，并断言输出。
- [ ] 能说出本章验收测试的用例数与失败时如何定位。

---

## 10. 常见坑与排错

| 现象 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: pydantic_ai` | 忘了用 `uv run`，跑在系统 Python 上 | 统一用 `uv run --no-project --with ... python ...` |
| `requires-python` 报错 | 解释器版本低于 `requires-python` | `uv python install 3.12` 后用 `uv python pin 3.12` |
| 锁文件与 `pyproject.toml` 不一致 | 手改了依赖却没重算锁 | `uv lock` 或直接 `uv sync`（会顺带更新） |
| `uv` 解析 `pyproject.toml` 报 `failed to parse year in date "7 days"` | uv 版本过旧，无法解析 `exclude-newer` 的相对日期格式 | 升级 uv（本仓库要求 `uv >= 0.9.25`） |
| `FunctionModel` 返回字符串却报类型/行为不符 | 2.x 起务必返回 `ModelResponse` | 用 `ModelResponse(parts=[TextPart(content=...)])` |
| 从 `messages` 取用户文本时 `AttributeError` | 消息是判别联合，`ModelResponse` 没有 `UserPromptPart` | 先 `isinstance(message, ModelRequest)` 再取 `UserPromptPart` |
| 测试挂起 | 误在同步上下文里 `await`，或漏了 `anyio_mode` 配置 | 本章用同步 `run_sync`；异步测试配 `anyio_mode = "auto"` |
| `pytest` 找不到 `ch_1_2` | 在错误的工作目录运行 | `cd textbook/labs` 后再 `python -m pytest part_1/test_ch_1_2.py` |
| `typing.TypedDict` 被 ruff 拦下 | 本仓库禁用 `typing.TypedDict` | 改用 `typing_extensions.TypedDict`（本仓库还禁 `asyncio.Lock`，改用 `anyio.Lock`） |
| 警告被当作错误 | 本仓库 `filterwarnings = ["error"]` | 修掉警告，或按既有约定定向 `ignore` |

---

## 11. 面试延伸

**Q1：`pyproject.toml` 和 `uv.lock` 有什么区别？为什么锁文件要提交？**
> 要点：前者是人写的声明式依赖（宽松约束），后者是解析后的精确、跨平台快照。提交锁文件才能让所有人/CI 得到同一套依赖，避免「在我机器上能跑」。

**Q2：你怎么在没有 API Key 的情况下测试一个 Agent？**
> 要点：三层策略——`TestModel` 跑通流程、`FunctionModel` 精确构造场景、VCR/录制回放测真实 provider 的请求形状。强调「离线、确定、快」。

**Q3：`TestModel` 与 `FunctionModel` 分别适合什么场景？**
> 要点：`TestModel` 是内置黑盒假模型，适合流程与工具循环的冒烟测试；`FunctionModel` 让你写函数当模型，适合构造特定分支、多轮工具调用、异步/取消路径，断言可精确到字。

**Q4：为什么 pydantic-ai 推荐 `anyio` 而不是直接用 `asyncio`？**
> 要点：anyio 是跨后端抽象（asyncio / trio），同一份代码可移植；pytest 的 anyio 插件让 `async def` 测试自动运行；跨后端的同步原语要用 `anyio.Lock`。

**Q5：严格类型检查在 LLM 应用里到底防住了什么？**
> 要点：结构化输出、工具参数、消息 part 的判别联合——这些最容易在运行时才炸的地方，正是类型系统能提前拦住的地方；`Any` 与 `cast` 会关掉这些保护。

---

## 12. 延伸阅读

- 源码篇·开发工作流：[`../../code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md)（Makefile、Ruff、Pyright、pytest、CI、发布全流程）。
- 源码篇·Pydantic Evals：[`../../code_wiki/09-pydantic-evals.md`](../../code_wiki/09-pydantic-evals.md)（把「断言输出」升级为「批量评估」）。
- 源码篇·核心 Agent 循环：[`../../code_wiki/02-core-agent-loop.md`](../../code_wiki/02-core-agent-loop.md)（`Agent`、运行方法矩阵、`RunContext`）。
- 源码篇·模型/Provider/Profile：[`../../code_wiki/03-models-providers-profiles.md`](../../code_wiki/03-models-providers-profiles.md)。
- 下一章：**1.3 Pydantic v2 精要**——把类型系统真正落地为「可校验的数据模型」。
