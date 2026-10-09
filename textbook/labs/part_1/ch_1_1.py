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
