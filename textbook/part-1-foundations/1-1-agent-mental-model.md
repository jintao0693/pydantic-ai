# 1.1 智能体心智模型：LLM、上下文、工具调用、ReAct、成本与延迟

> 篇一 · 入门与工程基座 · 配套 labs：`labs/part_1/ch_1_1.py`、`labs/part_1/test_ch_1_1.py`

## 1. 本课目标

1. 用一句话说清 **LLM 的本质**（next-token 预测）并解释「同样的输入为什么会有不同输出」。
2. 说清 **上下文窗口** 里 system / user / assistant / tool 四类消息的角色、顺序与 token 预算。
3. 复述 **工具调用（function calling）** 的四步闭环：模型输出结构化调用 → 宿主执行 → 结果回填 → 再推理。
4. 画出 **Agent 循环**（`UserPromptNode → ModelRequestNode → CallToolsNode`），说清「何时结束、如何避免死循环」。
5. 区分 **ReAct** 与「纯 workflow / 链式调用」，并能就具体任务做取舍。
6. 从成本与延迟两个维度评估一次 Agent 运行（计价、prompt cache、并发、流式）。
7. 列举典型失败模式，并指出框架分别用哪个机制兜底。

## 2. 前置知识

- 会读写 Python 函数、类、字典与 `typing` 基础（`list[str]`、`dict[str, Any]`）。
- 能读懂形如 `{"name": ..., "arguments": ...}` 的 JSON 结构。
- 会用命令行跑 `python xxx.py`。**不需要** API Key，也不需要任何 LLM SDK。

## 3. 为什么需要它

### 3.1 一个致命的误解

初学者常见的心智模型是「我调一次模型，模型就替我把活干完」，于是写出：

```python
answer = model.chat("帮我查北京天气，如果下雨就提醒带伞")
print(answer)  # 期望：它真的去查了天气
```

结果模型要么一本正经地胡说（幻觉），要么回你「好的，建议带伞」。因为它**根本没法上网**。根源在于：**LLM 只会产生文本，不能执行任何动作**。所谓「智能体」，是你在模型外面**自己搭出来的一套循环**——模型负责「说要做什么」，你的程序负责「真的去做」，再把结果塞回去让模型「接着说」。

### 3.2 为什么必须先建立心智模型

框架能替你写这个循环，但**不能替你理解循环**。线上出问题时，心智模型就是排查地图：

- 反复调同一工具、烧光 token → 看循环的**终止条件**。
- 同一 prompt 昨天好好的今天跑偏 → 看 **temperature 与非确定性**。
- 报「上下文超限」→ 看 **token 预算**。
- 工具执行成功了模型却假装不知道 → 看 **结果回填**。

本章就是这张地图。labs 用**纯标准库**手写一遍循环，把框架封装起来的每一步摊开；等你读第 2 章时会一眼认出 `run()` 背后走的正是这套流程。

## 4. 核心概念

### 4.1 LLM 的本质：next-token 预测

LLM 数学上就是一个函数：`f(tokens[0..n-1]) -> 词表上的概率分布`。它只做一件事——看前文，预测下一个 token，然后采样、拼回、再预测：

```text
"今天天气真"     → 分布{好:0.6, 不错:0.2, 差:0.1, ...} → 采样得"好"
"今天天气真好"   → 分布{，:0.4, 啊:0.3, }:0.1, ...}   → 采样得"，"
```

由此得到三个必须记住的结论：

| 结论 | 含义 | 工程影响 |
|------|------|----------|
| **只会补全文本** | 没有任何「执行」能力 | 想让它做事，必须靠工具 + 循环 |
| **本质是概率采样** | 输出由 logits + 采样策略决定 | 同样的输入可能不同输出 |
| **无状态、无记忆** | 每次调用都是全新的一次 | 历史必须由你显式塞回上下文 |

### 4.2 非确定性：为什么同样的输入会不同输出

采样里有个参数 **temperature**：`0` 近乎取最高概率（最稳，但**仍不保证**逐字节相同——浮点、批处理、服务端实现都会引入抖动）；`0.7` 按分布采样、有多样性；`1.5` 分布被拉平、更易跑偏。此外还有 `top_p`、`seed`、服务端版本更新等因素。

> **工程含义**：任何依赖 LLM 的逻辑都不能假设输出可复现。这正是 Pydantic AI 提供 `TestModel` / `FunctionModel` 的原因，也是本章 labs 一开始就做成**确定性**的原因。

### 4.3 上下文窗口：消息的角色与顺序

模型每次看到的是一串**消息列表**，按角色组织：

```text
system  : 你是客服助手，只回答与订单相关的问题……      ← 行为约束（角色/规则）
user    : 我的订单 #123 到哪了？                       ← 用户输入
assistant(tool_call): get_order(id="123")             ← 模型要求调工具
tool    : {"status": "shipped", "eta": "10-12"}       ← 宿主回填的结果
assistant: 您的订单已发货，预计 10 月 12 日送达。        ← 模型的最终回答
```

| 角色 | 谁写的 | 作用 | 对应 Pydantic AI |
|------|--------|------|------------------|
| **system** | 开发者 | 全局行为约束、人设、规则 | `system_prompt=` / `instructions=` → `SystemPromptPart` |
| **user** | 用户 | 本轮诉求 | `UserPromptPart` |
| **assistant** | 模型 | 文本，或工具调用 | `TextPart` / `ToolCallPart` |
| **tool** | 宿主 | 工具执行结果（成败都要回填） | `ToolReturnPart` / `RetryPromptPart` |

**顺序即语义**：system 放最前（约束先入为主）；工具调用与工具结果必须**成对相邻**，否则很多 provider 会直接报「消息结构非法」。

### 4.4 token 预算：上下文窗口是有边界的

上下文窗口是模型一次能看的总 token 上限（8k / 128k / 200k …），被两部分吃掉：**输入 token**（system + 全部历史 + 本轮 user，按输入单价计费）与**输出 token**（模型新生成，按输出单价计费，通常更贵）。

- 每一轮循环都会**重新**把「system + 全部历史」发给模型，历史越长，每轮输入越贵。
- 当「输入 + 最大输出」超过窗口，轻则截断，重则报错。工程上需裁剪历史、摘要压缩、限制工具输出长度。

### 4.5 工具调用（function calling）机制

这是 Agent 与「普通聊天」的分水岭，完整闭环四步：

```text
① 先把工具「说明书」给模型（name + description + 参数 JSON Schema）
② 模型输出结构化的调用意图：{"name": "add", "arguments": {"a": 2, "b": 3}}
   —— 模型只是「说要调」，并没有执行！
③ 宿主查表找到真正的 add 函数、执行，得到 5
④ 把结果作为 tool 消息回填，再次请求模型
   —— 模型看到 5，才决定「继续调工具」还是「给最终答案」
```

**关键认知**：模型输出的是一个**结构化调用请求**，执行权始终在你的程序手里。所以「未知工具」可以被你**拒绝**，「危险操作」可以被你**拦截审批**。

### 4.6 Agent 循环：把上面的一切串起来

把 §4.5 的第 ②③④ 步放进循环，就是 Agent 循环：

```text
                 ┌─────────────────────────────┐
                 │  UserPromptNode             │  组装 system + history + user
                 └──────────────┬──────────────┘
                                ▼ ModelRequest
                 ┌─────────────────────────────┐
                 │  ModelRequestNode           │  发给模型 → 得到 ModelResponse
                 └──────────────┬──────────────┘
                    响应里有需要宿主执行的工具调用？
             ┌──────────────────┴───────────────────┐
             ▼ 有                                    ▼ 没有
   ┌───────────────────────┐              ┌──────────────────────┐
   │  CallToolsNode        │              │  产出 FinalResult     │
   │  执行工具 → 回填结果    │              │  → End，循环结束       │
   └──────────┬────────────┘              └──────────────────────┘
              └──── 新一轮 ModelRequest ───► 回到 ModelRequestNode
```

| 概念 | Pydantic AI 节点 |
|------|------------------|
| 组装 prompt | `UserPromptNode` |
| 请求模型 | `ModelRequestNode` |
| 执行工具 / 判定终结 | `CallToolsNode` |
| 终结标记 | `End(FinalResult)` |

**何时结束？** 当模型这一轮**没有**产生「需要宿主执行、且尚未结算」的工具调用时，响应本身就是最终结果（`CallToolsNode` 返回 `End(FinalResult)`），否则构造新的 `ModelRequestNode` 继续。

**如何避免死循环？** 三道闸：① 步数上限（labs 的 `max_steps`；框架的 `UsageLimits.request_limit`，默认 50）；② 资源上限（token / 成本）；③ 收紧终结判定（模型必须能触发最终输出）。

### 4.7 ReAct 范式：Reasoning + Acting

ReAct = **Rea**soning + **Act**ing，要求模型每轮交替「思考」与「行动」：

```text
Thought: 用户问 (2+3)*4，先算括号里的 2+3
Action:  add(2, 3)         Observation: 5
Thought: 再算 5*4
Action:  mul(5, 4)         Observation: 20
Thought: 子计算完成，汇总
Final:   20
```

**与「纯 workflow / 链式调用」的区别**：

| 维度 | 纯 workflow / 链式 | ReAct Agent |
|------|--------------------|-------------|
| 谁决定下一步 | 开发者**硬编码** | 模型**运行时**决定 |
| 步数 | 固定 | 动态（1～N 步） |
| 可预测性 | 高 | 低 |
| 适合 | 步骤明确的任务（表单校验、ETL） | 步骤未知、需探索的任务（研究、排障、编码） |

**取舍原则**：能用 workflow 解决的，别用 Agent。Agent 的价值恰在「不知道几步、走哪几步」时。生产系统多是**混合**：外层 workflow 固定骨架，个别探索性节点内嵌 ReAct。

### 4.8 成本与延迟

**成本** = 输入 token × 输入单价 + 输出 token × 输出单价。注意：

- **多轮循环是乘法放大器**：N 轮 = N 次付费请求，且每轮重发历史，输入成本随轮数超线性增长。
- **prompt cache（前缀缓存）**：provider 常对「相同前缀」的输入 token 打折。把稳定内容（system prompt、工具定义、长文档）放前面，易变内容放后面，即可命中缓存降本。

**延迟** = 模型推理 × 轮数 + 工具执行。两个抓手：**并发**（同轮独立工具并行，`max_concurrency`；`sequential=True` 作屏障）与**流式**（提前首 token 时间，总时长不变但体感大幅改善——不省钱）。

### 4.9 典型失败模式

| 失败模式 | 现象 | 兜底机制 |
|----------|------|----------|
| **幻觉** | 工具不存在却照样回答 | 结构化输出 + 校验器；工具白名单 |
| **工具误用** | 调不存在的工具 / 参数错 | 宿主按 JSON Schema 校验、拒绝未知工具、错误回填 |
| **上下文溢出** | 报错或历史被截断 | token 预算、历史裁剪/压缩、工具输出限额 |
| **无限循环** | 反复调同一工具 | `max_steps` / `request_limit` / 终结判定 |
| **非幂等副作用** | 重复下单、重复发送 | 幂等键、审批（`requires_approval`）、持久化去重 |

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 `labs/part_1/ch_1_1.py` **逐字一致**，用标准库实现一个**确定性玩具 Agent**。先通读，再看逐段讲解。

```python
"""第 1.1 章配套示例：确定性「玩具 Agent」（仅标准库）。

本模块用一个**规则函数**模拟 LLM，只用标准库（不依赖任何模型 SDK，也不依赖
pydantic-ai），把 Agent 循环完整地摊开给你看：

    模型输出（tool_call / final） → 宿主执行工具 → 结果回填进对话 → 再推理

之所以要「确定性」，是因为真实 LLM 的输出不可复现；只有把不确定性拿掉，
才能用 pytest 精确断言「轨迹顺序」「结果回填」「防死循环」这些**机理**。

运行：

    python ch_1_1.py

你会看到：每一步的 ReAct 轨迹（thought / action / observation / final）、
完整的对话消息列表，以及最终答案。
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Any, Callable

# 一个「确定性」的 now 工具返回值：真实系统中来自系统时钟，这里固定以便测试。
FIXED_NOW = "2026-10-09T09:00:00+08:00"


# --------------------------------------------------------------------------- #
# 1. 工具注册表（Tool Registry）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tool:
    """一个可被模型调用的工具：名字 +（供模型看的）描述 + 真正执行的函数。"""

    name: str
    description: str
    fn: Callable[..., Any]


def _div(a: int, b: int) -> float:
    if b == 0:
        raise ValueError("division by zero")
    return a / b


# 注册表是「名字 → 工具」的映射；模型只会输出**名字 + 参数**，
# 具体执行谁，由宿主（我们）在这张表里查。查不到就是「未知工具」。
TOOLS: dict[str, Tool] = {
    "add": Tool("add", "两数相加：add(a, b) -> a + b", operator.add),
    "sub": Tool("sub", "两数相减：sub(a, b) -> a - b", operator.sub),
    "mul": Tool("mul", "两数相乘：mul(a, b) -> a * b", operator.mul),
    "div": Tool("div", "两数相除：div(a, b) -> a / b", _div),
    "now": Tool("now", "返回当前时间戳（此处为确定性模拟值）", lambda: FIXED_NOW),
}

# 表达式里的运算符 → 工具名。注意 '^' 映射到并不存在的 'pow'，
# 用来演示「模型调用了不存在的工具」这一失败模式。
OP_TO_TOOL: dict[str, str] = {"+": "add", "-": "sub", "*": "mul", "/": "div", "^": "pow"}


# --------------------------------------------------------------------------- #
# 2. 把「问题」解析成一棵表达式树，再摊平成求值顺序（模拟模型的“规划”）
# --------------------------------------------------------------------------- #
AST = "int | tuple[str, AST, AST]"  # 叶子是数字；内部节点是 (运算符, 左, 右)

_TOKEN_RE = re.compile(r"\s*(?:(\d+)|([+\-*/^()]))")


def tokenize(text: str) -> list[str]:
    """把 "(2 + 3) * 4" 切成 ['(', '2', '+', '3', ')', '*', '4']。"""
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"无法解析的字符：{text[pos:]!r}")
        number, op = match.group(1), match.group(2)
        tokens.append(number if number is not None else op)
        pos = match.end()
    return tokens


def parse(text: str) -> Any:
    """递归下降解析：优先级 `^` > `*` `/` > `+` `-`，支持括号。"""
    tokens = tokenize(text)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def eat() -> str:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        return token

    def parse_atom() -> Any:
        token = peek()
        if token == "(":
            eat()
            node = parse_expr()
            if peek() != ")":
                raise ValueError("括号不匹配")
            eat()
            return node
        if token is None or not token.isdigit():
            raise ValueError(f"期望数字或 '('，实际 {token!r}")
        return int(eat())

    def parse_power() -> Any:
        node = parse_atom()
        if peek() == "^":
            op = eat()
            node = (op, node, parse_power())  # 右结合
        return node

    def parse_term() -> Any:
        node = parse_power()
        while peek() in ("*", "/"):
            op = eat()
            node = (op, node, parse_power())
        return node

    def parse_expr() -> Any:
        node = parse_term()
        while peek() in ("+", "-"):
            op = eat()
            node = (op, node, parse_term())
        return node

    node = parse_expr()
    if pos != len(tokens):
        raise ValueError("表达式存在多余内容")
    return node


def _postorder(node: Any, out: list[Any]) -> None:
    if isinstance(node, int):
        return
    _, left, right = node
    _postorder(left, out)
    _postorder(right, out)
    out.append(node)


def op_nodes(ast: Any) -> list[Any]:
    """后序遍历：返回所有运算节点，且**父节点一定排在其子节点之后**。

    这个顺序就是「模型必须先算哪些子表达式」的规划结果。
    """
    out: list[Any] = []
    _postorder(ast, out)
    return out


# --------------------------------------------------------------------------- #
# 3. FakeLLM：用规则函数模拟「模型的下一次输出」
# --------------------------------------------------------------------------- #
@dataclass
class Action:
    """模型的一次输出：要么调用工具，要么给出最终答案。"""

    kind: str  # "tool_call" | "final"
    thought: str
    tool: str | None = None
    args: tuple[Any, ...] = ()
    answer: str | None = None


def _last_tool_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if messages and messages[-1].get("role") == "tool":
        return messages[-1]
    return None


def _tool_results(messages: list[dict[str, Any]]) -> list[Any]:
    """把对话里**成功**的工具结果按顺序取出来——这就是「回填」的读取侧。"""
    return [m["content"] for m in messages if m.get("role") == "tool" and m.get("ok")]


def _resolve(node: Any, index: dict[int, int], results: list[Any]) -> Any:
    """把一个表达式节点求值：叶子直接返回；运算节点用第 i 个工具结果替换。"""
    if isinstance(node, int):
        return node
    return results[index[id(node)]]


def _is_time_question(question: str) -> bool:
    return any(keyword in question for keyword in ("几点", "时间", "now"))


class FakeLLM:
    """一个确定性的「模型」：根据当前对话（尤其是工具结果）决定下一步。"""

    def decide(self, question: str, messages: list[dict[str, Any]]) -> Action:
        # 上一轮工具失败 → 收敛：不再重试，直接给出结论（避免死循环）。
        last_tool = _last_tool_message(messages)
        if last_tool is not None and not last_tool["ok"]:
            return Action(
                kind="final",
                thought=f"工具 {last_tool['name']} 失败了，继续算也没意义。",
                answer=f"无法完成：工具 {last_tool['name']} 执行失败（{last_tool['error']}）。",
            )

        # 时间类问题：一次工具调用即可收敛。
        if _is_time_question(question):
            results = _tool_results(messages)
            if not results:
                return Action(kind="tool_call", thought="需要查询当前时间。", tool="now")
            return Action(kind="final", thought="已拿到时间，可以回答了。", answer=f"现在是 {results[0]}。")

        # 算术问题：按「后序规划」逐个发出工具调用，直到全部子表达式算完。
        ast = parse(question)
        ops = op_nodes(ast)
        index = {id(node): i for i, node in enumerate(ops)}
        results = _tool_results(messages)

        if len(results) < len(ops):
            op, left, right = ops[len(results)]
            tool = OP_TO_TOOL.get(op, op)
            args = (_resolve(left, index, results), _resolve(right, index, results))
            return Action(
                kind="tool_call",
                thought=f"下一步需要计算 {tool}{args}。",
                tool=tool,
                args=args,
            )

        value = ast if isinstance(ast, int) else _resolve(ast, index, results)
        return Action(kind="final", thought="所有子计算都完成了，汇总结果。", answer=str(value))


# --------------------------------------------------------------------------- #
# 4. 宿主侧：执行工具
# --------------------------------------------------------------------------- #
def execute_tool(name: str, args: tuple[Any, ...]) -> tuple[bool, Any, str | None]:
    """执行工具，返回 (是否成功, 结果, 错误信息)。

    未知工具会被**拒绝**——这正是宿主对模型输出行使「否决权」的地方。
    """
    tool = TOOLS.get(name)
    if tool is None:
        available = ", ".join(sorted(TOOLS))
        return False, None, f"未知工具 {name!r}（可用：{available}）"
    try:
        return True, tool.fn(*args), None
    except Exception as exc:  # noqa: BLE001 - 演示：任何异常都回填给模型，让它决定怎么办
        return False, None, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# 5. Agent 循环：模型 → 工具 → 回填 → 再模型 …… 直到 final 或耗尽步数
# --------------------------------------------------------------------------- #
@dataclass
class AgentResult:
    answer: str
    trace: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    steps: int
    converged: bool


class AgentNotConvergedError(RuntimeError):
    """达到 max_steps 仍未产出最终答案——用于演示「防死循环」的硬边界。"""

    def __init__(self, message: str, trace: list[dict[str, Any]], messages: list[dict[str, Any]]):
        super().__init__(message)
        self.trace = trace
        self.messages = messages


def run_agent(question: str, max_steps: int = 8, llm: FakeLLM | None = None) -> AgentResult:
    """驱动 Agent 循环。

    - `question`：用户输入。
    - `max_steps`：最多允许的「模型调用轮数」，防止模型陷入无限循环。
    - 返回 `AgentResult`；若耗尽 `max_steps` 仍未收敛，抛 `AgentNotConvergedError`。
    """
    llm = llm or FakeLLM()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    trace: list[dict[str, Any]] = []

    for step in range(1, max_steps + 1):
        action = llm.decide(question, messages)
        trace.append({"step": step, "phase": "thought", "content": action.thought})

        if action.kind == "final":
            trace.append({"step": step, "phase": "final", "content": action.answer, "context_len": len(messages)})
            messages.append({"role": "assistant", "content": action.answer})
            return AgentResult(action.answer or "", trace, messages, step, True)

        # 1) 记录模型发出的工具调用（action）
        trace.append(
            {"step": step, "phase": "action", "tool": action.tool, "args": list(action.args), "context_len": len(messages)}
        )
        messages.append({"role": "assistant", "tool_call": {"name": action.tool, "args": list(action.args)}})

        # 2) 宿主执行
        ok, value, error = execute_tool(action.tool or "", action.args)

        # 3) 记录结果并把结果**回填**进对话（observation），供下一轮模型读取
        trace.append(
            {"step": step, "phase": "observation", "tool": action.tool, "result": value, "ok": ok, "error": error}
        )
        messages.append(
            {"role": "tool", "name": action.tool, "content": value if ok else None, "ok": ok, "error": error}
        )

    raise AgentNotConvergedError(f"达到 max_steps={max_steps} 仍未收敛", trace, messages)


# --------------------------------------------------------------------------- #
# 6. 演示
# --------------------------------------------------------------------------- #
def _print_run(question: str, **kwargs: Any) -> None:
    print("=" * 68)
    print(f"问题：{question}")
    try:
        result = run_agent(question, **kwargs)
    except AgentNotConvergedError as exc:
        print(f"未收敛：{exc}")
        for entry in exc.trace:
            print(f"  {entry}")
        return
    for entry in result.trace:
        print(f"  {entry}")
    print(f"步数：{result.steps}，收敛：{result.converged}")
    print(f"最终答案：{result.answer}")
    print("消息列表：")
    for message in result.messages:
        print(f"  {message}")


if __name__ == "__main__":
    # 多步工具调用：先 add 再 mul，体现 ReAct 的「边推理边行动」。
    _print_run("(2 + 3) * 4")

    # 单步工具调用：时间类问题。
    _print_run("现在几点？")

    # 工具误用：'^' 映射到不存在的工具 pow，宿主拒绝后模型收敛给出结论。
    _print_run("2 ^ 3")

    # 防死循环：把 max_steps 卡到 1，多步问题无法算完 → 明确的「未收敛」。
    _print_run("(2 + 3) * 4", max_steps=1)
```

### 5.1 逐段讲解

**Part 1 · 工具注册表**：对应 §4.5 第 ① 步。`Tool` 用 `@dataclass(frozen=True)` 保证定义不可变；`TOOLS` 是 `名字 → 工具` 映射。真实框架会把这份「说明书」转成 JSON Schema 随请求发给模型。

**Part 2 · 解析与规划**：`parse` 是**递归下降解析器**，把 `(2 + 3) * 4` 变成树 `mul(add(2,3), 4)`；`op_nodes` 做**后序遍历**得到 `[add(2,3), mul(_,4)]`——后序保证「子表达式永远排在父表达式之前」，即**必须先算的先算**。真 Agent 里「下一步做什么」由模型决定，这里固化为确定性规则。

**Part 3 · FakeLLM**：`decide(question, messages)` 就是「模型的下一步输出」，只看问题与当前对话。三条规则：① 上一轮工具失败 → `final` 承认失败；② 时间类问题 → 先 `tool_call("now")`，拿到结果后 `final`；③ 算术问题 → 用 `len(results)` 对上后序规划的第几个运算，没算完就发 `tool_call`，算完了就 `final`。注意 `results = _tool_results(messages)`：模型是从**对话里读回**工具结果的，这就是「回填」的读取侧。

**Part 4 · 宿主执行**：三点关键——`TOOLS.get(name)` 查不到就**拒绝**未知工具（不崩溃）；执行抛异常就**把错误作为结果**返回（让模型自决）；统一返回 `(ok, value, error)`，无论成败都能回填。

**Part 5 · Agent 循环**：§4.6 的可执行版，核心是一个 `for step in range(1, max_steps + 1)`。`final` → 记账返回（**出口 1：正常收敛**）；`tool_call` → 记 action、写入 assistant 工具调用、执行、把结果写成 tool 消息；预算用尽 → 抛 `AgentNotConvergedError`（**出口 2：硬边界防死循环**）。`trace` 记录每轮的 thought/action/observation/final，`context_len` 记录该轮模型看到几条消息。

运行结果（节选）：

```text
问题：(2 + 3) * 4
  {'step': 1, 'phase': 'action', 'tool': 'add', 'args': [2, 3], 'context_len': 1}
  {'step': 1, 'phase': 'observation', 'tool': 'add', 'result': 5, 'ok': True, 'error': None}
  {'step': 2, 'phase': 'action', 'tool': 'mul', 'args': [5, 4], 'context_len': 3}
  {'step': 2, 'phase': 'observation', 'tool': 'mul', 'result': 20, 'ok': True, 'error': None}
  {'step': 3, 'phase': 'final', 'content': '20', 'context_len': 5}
步数：3，收敛：True
最终答案：20
```

留意 `context_len` 的 `1 → 3 → 5`：它直观说明**每一轮循环历史都在变长**——正是 §4.4「输入 token 随轮数增长」的微观证据。

## 6. 深入剖析

**6.1 循环的「状态」是什么？** 只有三样：`messages`（**唯一真相来源**，决定模型下轮看到什么）、`trace`（旁路记录，不影响循环）、`step`（防死循环计数）。框架里对应 `GraphAgentState`（`message_history` / `usage` / `run_step`）与 `RunContext`。理解「状态就是消息列表」是理解一切 Agent 框架的钥匙：所谓「续接对话」就是把上一轮的 `messages` 再传进去。

**6.2 为什么工具结果要「原样回填」？** `execute_tool` 无论成败都返回三元组、失败信息也写进对话，是刻意的：**模型需要看到失败才能修正**。框架里对应 `ToolRetryError`（回填 `RetryPromptPart`，模型下轮重试）与 `ToolFailedError`（回填失败信息，模型自决）。反模式是工具失败时在宿主 `raise` 出去，让「一次工具抖动」直接崩掉整个 Agent。

**6.3 「何时结束」在框架里的精确语义**：本章用 `kind == "final"` 判断，框架用 `end_strategy` 处理「同一轮既有工具调用又有输出」：

| `end_strategy` | 行为 |
|----------------|------|
| `early` | 输出优先，首个有效输出即结束，**函数工具不执行** |
| `graceful`（默认） | 先按序跑工具；工具抛 `ModelRetry` 会**抑制**输出、把重试交给模型 |
| `exhaustive` | 所有工具都跑，取首个有效输出为结果 |

默认 `graceful` 而非 `early`：工具副作用通常比「早结束」更重要（同时要求「写文件」和「输出总结」时，`early` 可能跳过写文件导致数据丢失）。

**6.4 防死循环三层防线**：① 步数（`max_steps` ↔ `request_limit`）；② 资源（`UsageLimits` 的 token/成本/工具调用上限）；③ 终结（输出工具 / 结构化输出 / `end_strategy`）。三层缺一不可：只靠步数可能烧光预算，只靠预算可能一步超支，只靠终结判定模型可能永不产出终结信号。

**6.5 从玩具到框架的映射**：

| 本节课件 | Pydantic AI 概念 |
|----------|------------------|
| `messages` 列表 | `ModelMessage` / `message_history` |
| `TOOLS` 注册表 | `Tool` / `Toolset` |
| `Tool.description` | 从函数签名/文档串生成 JSON Schema |
| `FakeLLM.decide` | `Model.request()` |
| `execute_tool` | `ToolManager`（校验 + 执行 + 重试） |
| `run_agent` 的 `for` | `UserPromptNode → ModelRequestNode → CallToolsNode` |
| `AgentNotConvergedError` | `UsageLimitExceeded` |
| `trace` | OTel span（Logfire） |

**6.6 为什么需要 Pydantic AI 这样的框架**：手写「能跑」的循环只要 30 行；「能用」的循环还要处理 provider 格式差异、工具参数校验、输出校验与重试、并发与超时、取消、持久化、事件流、可观测性。框架的价值就在此——**类型安全**（`Agent[AgentDepsT, OutputDataT]`，工具参数类型 → JSON Schema → 运行时校验一条链）、**结构化输出**（直接拿 Pydantic 模型）、**可测试**（`TestModel` / `FunctionModel`）、**可观测**（内置 OTel span 树）、**可组合**（Capability / Toolset 封装横切行为）。

## 7. 常见变体与工程实践

- **单步工具调用**（`现在几点？`）：一次调用即收敛，适合「查一个值 → 总结」。
- **多步链式调用**（`(2 + 3) * 4`）：后一步依赖前一步输出，ReAct 最典型形态（查订单 → 查物流 → 总结）。
- **并行工具调用**：同轮多个互不依赖的调用可并发执行（Pydantic AI 默认并行独立工具，`sequential=True` 作屏障）。
- **工具失败的自我修正**：可重试错误（`ModelRetry`）vs 终态失败（`ToolFailed`，不消耗重试预算）。
- **混合 workflow + ReAct**：外层固定流程，个别节点内嵌 ReAct，生产主流形态。

**工程实践清单**：① 永远设步数/预算上限；② 工具 `description` 写清楚，模型靠它判断何时用哪个工具；③ 工具输出别太大，否则瞬间吃光上下文；④ 副作用工具要幂等 + 审批；⑤ 稳定前缀放前面以命中 prompt cache；⑥ 面向用户一律优先流式。

## 8. 练习

**练习 1（基础）** 把默认 `max_steps` 改成 2 后运行 `(2 + 3) * 4` 会怎样？为什么？

<details><summary>参考答案要点</summary>抛 `AgentNotConvergedError`。该问题需要 2 次工具调用 + 1 次 `final` 共 3 轮，`max_steps=2` 只够发出两次工具调用便耗尽预算——这是防死循环的预期行为。</details>

**练习 2（基础）** `context_len` 在三轮里分别是多少？用它解释「多轮循环是输入成本放大器」。

<details><summary>参考答案要点</summary>分别是 1、3、5。每轮都要把「全部历史」重发一次，所以输入 token 随轮数累积增长，而非被摊薄——即每轮重付。</details>

**练习 3（进阶）** 给注册表加 `sqrt(x)` 并让 `parse` 支持 `sqrt(9)` 这样的前缀式，需要动哪些函数？如何避免「没算完就 final」？

<details><summary>参考答案要点</summary>动 `TOOLS`、解析器（新增一元节点表示）、`op_nodes`/`_resolve`（一元节点只有一个操作数）。避免提前 final 的关键：只要「已成功结果数」`<` 「运算节点数」就必须继续发工具调用。</details>

**练习 4（进阶）** 把 `FakeLLM` 改成「工具失败后重试一次，仍失败才放弃」，并说明对应框架里的什么机制。

<details><summary>参考答案要点</summary>在 `messages` 里数同名工具的失败次数，未达上限就再发一次 `tool_call`，达上限才 `final`。对应 Pydantic AI 的**重试预算**：失败回填 `RetryPromptPart`，模型下轮重试，超过 `max_retries` 后抛 `UnexpectedModelBehavior`。要点是重试必须有限。</details>

**练习 5（综合）** 任务「读长文 → 提取公司名 → 逐个查股价 → 汇总表格」，该用 workflow、ReAct 还是混合？哪一步最可能出 §4.9 的哪类失败？

<details><summary>参考答案要点</summary>推荐**混合**：骨架（读文→提取→汇总）用 workflow；「逐个查股价」数量不定、可能失败，适合 ReAct/并行循环。最易出问题的是查股价——可能**无限循环**（查不到就反复重试）、可能**上下文溢出**（结果太多）；若随后还要下单则涉及**非幂等副作用**。对策：重试上限、限制返回条数、副作用走审批。</details>

## 9. 验收标准

完成本章的最低标准（`test_ch_1_1.py` 全绿），对应的 7 条自动断言：

1. 多步问题得到正确最终答案（`answer == "20"`，`steps == 3`）。
2. 轨迹顺序符合 ReAct（先 action/observation，最后 final）。
3. 工具结果被回填，且下一轮上下文更大。
4. `max_steps` 耗尽抛 `AgentNotConvergedError`，且无 final。
5. 未知工具被拒绝，模型收敛给出带原因的结论。
6. 单工具问题（时间）一次调用即收敛。
7. 工具按依赖顺序调用（先 `add` 后 `mul`）。

手动自测：能运行 `python part_1/ch_1_1.py` 看到四种轨迹；能白板画出三个节点与两个出口；能口述工具调用四步闭环与 `context_len` 递增的成本含义。

```bash
cd textbook/labs
uv run --no-project --with pytest python -m pytest part_1/test_ch_1_1.py -q
```

## 10. 常见坑与排错

| 现象 | 原因 | 修复 |
|------|------|------|
| 反复调同一工具 | 缺终止条件 / 结果没回填，模型以为没算 | 设 `max_steps`；确认工具结果真的进了 `messages` |
| 报「消息结构非法」 | tool 结果没紧跟对应的 assistant 工具调用 | 保持调用与结果**成对相邻** |
| 突然「上下文超限」 | 历史无限增长 / 工具输出过大 | 裁剪历史、限制工具输出、设 token 上限 |
| 同样输入结果不同 | LLM 是概率采样 | 测试用 `TestModel`/`FunctionModel`；生产接受抖动 |
| 工具从未被调用 | `description` 太模糊 / 参数 schema 有问题 | 写清描述、检查参数类型 |
| 未知工具报错 | 模型「幻觉」出不存在的工具 | 宿主拒绝 + 回填错误（本章示例） |
| 编造工具结果 | 宿主没执行，模型凭空说了结果 | 用结构化输出 + 校验，只在工具真返回后才允许引用 |

**labs 专属排错**：`test_unknown_tool_is_rejected` 失败 → 检查 `OP_TO_TOOL` 中 `'^'` 是否仍映射到未注册的 `'pow'`；`test_tool_results_backfilled_into_next_turn_context` 失败 → 检查 `run_agent` 是否把 tool 消息 `append` 回了 `messages`；`_resolve` 依赖表达式的 `id()`，解析后**不要**重造等值但不同的元组，否则索引错位。

## 11. 面试延伸

**Q1. LLM 为什么会有幻觉？它和「模型不会执行工具」有什么关系？**
要点：LLM 的优化目标是「生成像样的文本」而非「说真话」，缺事实或工具能力时仍会继续像样地补全——这就是幻觉。正因如此，Agent 必须把「获取事实」外包给工具。区分「模型只会生成文本」与「模型会执行工具」是设计一切 Agent 的前提。

**Q2. 什么是 function calling？一次工具调用的完整链路？**
要点：四步——① 把工具 spec（name + description + JSON Schema）随请求发给模型；② 模型输出结构化调用请求（**不执行**）；③ 宿主校验、执行；④ 结果回填为 tool 消息，再次请求模型，由模型决定继续调工具还是给最终答案。强调执行权在宿主。

**Q3. ReAct 与 workflow 的边界？什么时候不该用 Agent？**
要点：判据是「下一步是否可预知」。步骤确定 → workflow（可预测、便宜、稳定）；步骤未知、需探索 → ReAct。能硬编码的别交给模型。生产多是混合：固定骨架 + 局部 ReAct。

**Q4. 如何防止 Agent 无限循环？**
要点：三层防线——步数上限（`request_limit`/`max_steps`）、资源上限（token/成本/工具调用数）、严格终结判定（输出类型 + `end_strategy`）；并强调重试必须有上限、失败回填要携带可修正信息。

**Q5. 一次 Agent 运行的成本和延迟由什么决定？怎么优化？**
要点：成本 = 输入 token × 输入价 + 输出 token × 输出价，轮数会让输入成本超线性增长；延迟 = 模型推理 × 轮数 + 工具耗时。优化：prompt cache（稳定前缀前置）、裁剪/压缩历史、限制工具输出、并行独立工具、并发上限、流式改善首字体验。注意流式不省钱、只改善体感。

## 12. 延伸阅读

- [01 · 架构总览](../../code_wiki/01-architecture-overview.md)：单仓库布局与「一次运行的端到端数据流」。
- [02 · 核心 Agent 循环](../../code_wiki/02-core-agent-loop.md)：`UserPromptNode → ModelRequestNode → CallToolsNode` 的函数级细节、`end_strategy`、`UsageLimits`。
- [05 · 工具 / Toolset / Capability](../../code_wiki/05-tools-toolsets-capabilities.md)：工具注册、校验、执行、组合的完整机制。
- 下一章：1.2 工程基座：uv、类型系统、anyio、pytest（`labs/part_1/ch_1_2.py`）。
