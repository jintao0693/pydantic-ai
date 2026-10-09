"""第 2.5 章 · 依赖注入与 RunContext —— 离线可运行示例。

本模块把「依赖注入（DI）」这条主线摊开来讲：工具、指令、动态模型设置都需要访问
外部资源（数据库、配置、当前用户身份），但**不该**用全局变量把状态藏起来。Pydantic AI
的答案是：

    构造期声明 deps_type=Deps  ──►  给 Agent / RunContext / 工具做静态类型参数化
    运行期传入 deps=<实例>       ──►  把依赖真正注入到每次运行

随后所有个性化逻辑都通过 `RunContext[Deps]` 的 `ctx.deps` 读取依赖：

    - 工具（@agent.tool，首参约定 RunContext）
    - 指令（@agent.instructions，按用户身份生成个性化指令）
    - 动态模型设置（model_settings=Callable[[RunContext], ModelSettings]）

运行结束后用 `result.usage` 查看本次运行的 token / 请求计数。

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_5.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import (
    Agent,
    ModelMessage,
    ModelResponse,
    RunContext,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

# ---------------------------------------------------------------------------
# 0. 依赖对象：一次运行的「外部世界」快照
# ---------------------------------------------------------------------------
@dataclass
class Deps:
    """运行期依赖。

    它是**每次 run 一份**的普通对象，由调用方注入。工具、指令、动态设置都只**读取**它，
    绝不在工具里原地修改——否则就退化成「藏在参数里的全局状态」。

    - user_name：当前请求的用户身份（用于个性化指令）。
    - db：一个玩具用户库（真实系统里是数据库连接池 / Repository / 缓存客户端）。
    """

    user_name: str
    db: dict[str, str]


# ---------------------------------------------------------------------------
# 1. 观测探针：记录 FunctionModel 每次被调用时看到的 AgentInfo
# ---------------------------------------------------------------------------
@dataclass
class ModelProbe:
    """离线观测工具。

    `FunctionModel` 的模型函数会收到 `AgentInfo`，其中就包含本次请求真正生效的
    `instructions`（说明指令函数已求值）与 `model_settings`（说明动态设置已生效）。
    把这两者记下来，就能在 main 与 pytest 里**断言注入确实到达了模型**。
    """

    instructions: list[str | None] = field(default_factory=list)
    model_settings: list[ModelSettings | None] = field(default_factory=list)
    tool_returns: list[str] = field(default_factory=list)
    requests: int = 0


def _last_tool_return(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条工具返回，用来验证工具确实读到了注入的 deps。"""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart):
                return str(part.content)
    raise AssertionError("消息历史里没有 ToolReturnPart —— 工具没有被调用")


# ---------------------------------------------------------------------------
# 2. 动态模型设置：只读地使用 deps，决定采样温度
# ---------------------------------------------------------------------------
def settings_from_deps(ctx: RunContext[Deps]) -> ModelSettings:
    """`model_settings` 可以是一个以 RunContext 为入参的可调用对象。

    这里演示「依赖驱动配置」：有账户数据时用 0 温度保证可复现，否则放宽到 0.7。
    注意：这只是**读** deps，没有副作用。
    """
    return ModelSettings(temperature=0.0 if ctx.deps.db else 0.7)


# ---------------------------------------------------------------------------
# 3. 组装 Agent：deps_type 做静态类型参数化
# ---------------------------------------------------------------------------
def build_agent(probe: ModelProbe | None = None) -> Agent[Deps, str]:
    """构造一个注入了依赖契约的 Agent。

    - `deps_type=Deps`：只做**静态类型参数化**，让 `RunContext[Deps]`、工具的
      `ctx.deps`、动态设置都获得类型安全（IDE 能补全 `ctx.deps.user_name`）。
      它**不会**创建 Deps 实例——真正的值在 `run_sync(..., deps=...)` 时注入。
    - `output_type=str`：本轮输出是纯文本。
    - `model_settings=settings_from_deps`：每步按 RunContext 动态求值。
    """
    probe = probe if probe is not None else ModelProbe()

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """一个确定性的假模型：第一轮调工具，第二轮回显工具结果。"""
        probe.requests += 1
        probe.instructions.append(info.instructions)
        probe.model_settings.append(info.model_settings)

        if len(messages) == 1:
            # 第一轮：确认工具已注册，然后发起一次工具调用。
            assert any(t.name == "lookup_balance" for t in info.function_tools), "lookup_balance 未注册"
            return ModelResponse(parts=[ToolCallPart("lookup_balance", {"account": "alice"})])

        # 第二轮：把工具返回原样作为最终输出，便于断言「工具拿到了注入的 deps」。
        balance = _last_tool_return(messages)
        probe.tool_returns.append(balance)
        return ModelResponse(parts=[TextPart(balance)])

    agent: Agent[Deps, str] = Agent(
        FunctionModel(model_fn),
        deps_type=Deps,
        output_type=str,
        model_settings=settings_from_deps,
    )

    # 工具：首参约定为 RunContext[Deps]，通过 ctx.deps 读取注入的依赖。
    @agent.tool
    def lookup_balance(ctx: RunContext[Deps], account: str) -> str:
        """查询某个账户的余额。数据来自注入的 ctx.deps.db。"""
        return ctx.deps.db.get(account, "<未知账户>")

    # 指令：同样通过 RunContext 拿 deps，生成个性化指令（每次运行前重算）。
    @agent.instructions
    def personalize(ctx: RunContext[Deps]) -> str:
        return f"你在为 {ctx.deps.user_name} 服务，请用第二人称称呼对方。"

    return agent


# 默认依赖：一个 Alice 用户与她的玩具数据库。
EXAMPLE_DEPS = Deps(user_name="Alice", db={"alice": "1200", "bob": "80"})


def run_once(deps: Deps, probe: ModelProbe | None = None):
    """便捷入口：构造 Agent、注入 deps、跑一次，返回 (probe, result)。"""
    probe = probe if probe is not None else ModelProbe()
    agent = build_agent(probe)
    result = agent.run_sync("帮我查一下 alice 的余额", deps=deps)
    return probe, result


# ---------------------------------------------------------------------------
# 4. 演示
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 68)
    print("1) 注入 deps：工具通过 RunContext[Deps] 读到 ctx.deps.db")
    print("=" * 68)
    probe = ModelProbe()
    agent = build_agent(probe)
    result = agent.run_sync("帮我查一下 alice 的余额", deps=EXAMPLE_DEPS)

    print(f"最终输出（= 工具返回的余额）：{result.output!r}")
    print(f"探针记录的工具返回值：{probe.tool_returns}")

    print()
    print("=" * 68)
    print("2) 个性化指令：指令函数读取 ctx.deps.user_name")
    print("=" * 68)
    print(f"模型看到的 instructions（第 1 次请求）：{probe.instructions[0]!r}")

    print()
    print("=" * 68)
    print("3) 动态模型设置：model_settings=Callable[[RunContext], ModelSettings]")
    print("=" * 68)
    # 注意：ModelSettings 是 TypedDict，实例就是普通 dict，用下标取值。
    for i, settings in enumerate(probe.model_settings, start=1):
        print(f"第 {i} 次请求 temperature = {settings.get('temperature') if settings else None}")

    print()
    print("=" * 68)
    print("4) 运行用量：result.usage（属性，不是方法）")
    print("=" * 68)
    usage = result.usage
    print(f"requests      = {usage.requests}")
    print(f"input_tokens  = {usage.input_tokens}")
    print(f"output_tokens = {usage.output_tokens}")
    print(f"total_tokens  = {usage.total_tokens}")

    print()
    print("=" * 68)
    print("5) agent.override(deps=...)：临时替换依赖，无需改动工具签名")
    print("=" * 68)
    bob = Deps(user_name="Bob", db={"alice": "9999"})
    with agent.override(deps=bob):
        overridden = agent.run_sync("再查一次 alice 的余额")  # 不传 deps，用覆盖值
    print(f"override 后的输出：{overridden.output!r}")
    print(f"工具这次读到的是 Bob 的 db：{probe.tool_returns[-1]!r}")

    print()
    print("=" * 68)
    print("6) 反模式提醒（详见章节正文）")
    print("=" * 68)
    print("- deps 是「每次运行一份」的对象，不要在工具里原地改它 → 会变成隐藏耦合。")
    print("- 不要在工具里调用 agent.run_sync(...) → 会嵌套运行 / 死锁。")
    print("- deps_type 只做静态类型；运行期真正的值是 deps=... 注入的。")


if __name__ == "__main__":
    main()
