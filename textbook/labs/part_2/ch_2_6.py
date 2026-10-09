"""第 2.6 章 · 模型 / Provider / Profile —— 多模型切换与能力探测（离线版）。

本模块回答一个工程问题：**同一个 Agent，怎么在不同模型 / 供应商之间安全、可测地切换？**

主线（全部离线，不需要任何 API Key，也不发起网络请求）：

    字符串 'provider:model' → 模型实例
        parse_model_id / infer_model
    → 模型身份字段
        model_name / system / model_id / base_url
    → ModelSettings 的两级合并
        Agent(model_settings=...) 与 run_sync(..., model_settings=...)
    → 能力事实 ModelProfile
        supports_tools / supports_json_schema_output / default_structured_output_mode /
        supports_thinking ...
    → 多模型切换
        FallbackModel（主模型失败回退）+ FallbackExceptionGroup
        Agent.override(model=...)（测试/临时切换）
    → defer_model_check（构造期不解析模型，解耦 SDK / 环境变量）

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_6.py
"""

from __future__ import annotations

from pydantic_ai import Agent, FallbackExceptionGroup, ModelSettings
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model, infer_model, infer_model_profile, parse_model_id
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# 我们只挑 4 个「工程上最常用来做选型决策」的能力事实来展示。
PROFILE_KEYS: tuple[str, ...] = (
    "supports_tools",
    "supports_json_schema_output",
    "default_structured_output_mode",
    "supports_thinking",
)


# ---------------------------------------------------------------------------
# 1. 模型身份：Model 抽象的四个字段
# ---------------------------------------------------------------------------
def model_identity(model: Model) -> dict[str, str | None]:
    """抽取 `Model` 抽象层的身份字段，便于打印与断言。"""
    return {
        "type": type(model).__name__,
        "model_name": model.model_name,  # 供应商侧的模型名，如 'gpt-4o'
        "system": model.system,  # 供应商名，流入 OTel gen_ai.system
        "model_id": model.model_id,  # f'{system}:{model_name}'
        "base_url": model.base_url,  # 自定义端点，默认 None
    }


def infer_test_model_identity() -> dict[str, str | None]:
    """`'test'` 是最短的模型字符串；它会被解析成离线的 `TestModel`。"""
    return model_identity(infer_model("test"))


# ---------------------------------------------------------------------------
# 2. 字符串解析：'provider:model' 只在第一个冒号处切分
# ---------------------------------------------------------------------------
def parse_examples() -> dict[str, tuple[str | None, str]]:
    """`parse_model_id` 的直觉：无前缀 → (None, 原名)；有前缀 → (provider, 模型名)。"""
    return {
        "openai:gpt-4o": parse_model_id("openai:gpt-4o"),
        "anthropic:claude-sonnet-4-5": parse_model_id("anthropic:claude-sonnet-4-5"),
        "test": parse_model_id("test"),
    }


# ---------------------------------------------------------------------------
# 3. ModelSettings 的两级合并
# ---------------------------------------------------------------------------
def probe_settings(
    agent_settings: ModelSettings,
    run_settings: ModelSettings,
) -> tuple[str, dict[str, object]]:
    """用 FunctionModel 截获「框架实际发给模型」的设置，验证合并结果。

    `Agent(model_settings=...)` 是默认值，`run_sync(..., model_settings=...)` 优先级更高，
    两者按 key 合并（后者覆盖前者），最终结果可从 `AgentInfo.model_settings` 读到。
    """
    captured: dict[str, dict[str, object]] = {}

    def probe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["settings"] = dict(info.model_settings or {})
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(probe, model_name="settings-probe"), model_settings=agent_settings, output_type=str)
    result = agent.run_sync("你好", model_settings=run_settings)
    return result.output, captured["settings"]


# ---------------------------------------------------------------------------
# 4. FallbackModel：主模型失败时回退到备选
# ---------------------------------------------------------------------------
def _failing_model(model_name: str) -> FunctionModel:
    """构造一个「一调用就抛 ModelAPIError」的离线模型，模拟主模型不可用。"""

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelAPIError(model_name, f"{model_name} 模拟不可用（限流 / 宕机 / 网络）")

    return FunctionModel(boom, model_name=model_name)


def fallback_run() -> tuple[str, list[str]]:
    """主模型抛错 → FallbackModel 切换到备选模型 → 返回结果与调用轨迹。"""
    calls: list[str] = []

    def primary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append("primary")
        raise ModelAPIError("primary-model", "主模型不可用")

    def backup(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append("backup")
        return ModelResponse(parts=[TextPart("来自备选模型的回答")])

    model = FallbackModel(
        FunctionModel(primary, model_name="primary"),
        FunctionModel(backup, model_name="backup"),
    )
    result = Agent(model, output_type=str).run_sync("你好")
    return result.output, calls


def fallback_all_fail() -> list[str]:
    """所有候选模型都失败时，框架会聚合抛出 `FallbackExceptionGroup`。"""
    model = FallbackModel(_failing_model("m1"), _failing_model("m2"))
    try:
        Agent(model, output_type=str).run_sync("你好")
    except FallbackExceptionGroup as exc:
        return [type(e).__name__ for e in exc.exceptions]
    return []


# ---------------------------------------------------------------------------
# 5. Agent.override(model=...)：不改业务代码切换模型
# ---------------------------------------------------------------------------
def override_switches() -> tuple[str, str, str]:
    """返回 (override 前, override 中, override 后) 三次运行的结果。

    `override` 是上下文管理器，仅在 `with` 块内生效——这是测试里「用 TestModel 顶替真实
    模型」的标准手法，业务代码一行都不用改。
    """
    baseline = FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart("baseline")]),
        model_name="baseline",
    )
    agent = Agent(baseline, output_type=str)

    before = agent.run_sync("hi").output
    with agent.override(model=TestModel(custom_output_text="overridden")):
        during = agent.run_sync("hi").output
    after = agent.run_sync("hi").output
    return before, during, after


# ---------------------------------------------------------------------------
# 6. ModelProfile：模型家族固有事实
# ---------------------------------------------------------------------------
def profile_facts() -> dict[str, object]:
    """读取 profile，回答「这个模型支不支持结构化输出 / 思考」等选型问题。"""
    function_model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("")]))
    test_model = TestModel()
    return {
        "function_model": {key: function_model.profile[key] for key in PROFILE_KEYS},
        "test_model": {key: test_model.profile[key] for key in PROFILE_KEYS},
        # infer_model_profile 只解析 provider、不构造模型实例
        "test_default_output_mode": infer_model_profile("test")["default_structured_output_mode"],
    }


# ---------------------------------------------------------------------------
# 7. defer_model_check：构造期不解析模型
# ---------------------------------------------------------------------------
def defer_model_check_demo() -> str:
    """`defer_model_check=True` 让 `Agent('openai:gpt-4o')` 在构造时不加载 openai SDK。

    本仓库未安装 `openai` extra，若不加该参数，`Agent(...)` 会立刻抛 ImportError。
    """
    agent = Agent("openai:gpt-4o", defer_model_check=True, output_type=str)
    return repr(agent.model)


def main() -> None:
    print("=" * 68)
    print("1) infer_model('test') → 模型实例的四个身份字段")
    print("=" * 68)
    for key, value in infer_test_model_identity().items():
        print(f"  {key:<12}= {value!r}")

    print()
    print("=" * 68)
    print("2) parse_model_id：'provider:model' 在第一个冒号处切分")
    print("=" * 68)
    for raw, parsed in parse_examples().items():
        print(f"  parse_model_id({raw!r:<32}) = {parsed}")

    print()
    print("=" * 68)
    print("3) ModelSettings 合并：Agent 默认 + run 覆盖")
    print("=" * 68)
    output, merged = probe_settings(
        agent_settings={"max_tokens": 100, "timeout": 20},
        run_settings={"temperature": 0.5},
    )
    print(f"  Agent(model_settings={{'max_tokens': 100, 'timeout': 20}})")
    print(f"  run_sync(model_settings={{'temperature': 0.5}})")
    print(f"  → 实际发给模型：{merged}")
    print(f"  → 输出：{output!r}")

    print()
    print("=" * 68)
    print("4) FallbackModel：主模型失败 → 回退备选")
    print("=" * 68)
    fallback_output, fallback_calls = fallback_run()
    print(f"  调用轨迹：{fallback_calls}")
    print(f"  最终输出：{fallback_output!r}")
    print(f"  全部失败时的子异常：{fallback_all_fail()}")

    print()
    print("=" * 68)
    print("5) Agent.override(model=TestModel())：不改业务代码切换模型")
    print("=" * 68)
    before, during, after = override_switches()
    print(f"  override 前：{before!r}")
    print(f"  override 中：{during!r}")
    print(f"  override 后：{after!r}")

    print()
    print("=" * 68)
    print("6) ModelProfile：能力事实（选型依据）")
    print("=" * 68)
    facts = profile_facts()
    for model_key in ("function_model", "test_model"):
        print(f"  {model_key}: {facts[model_key]}")
    print(f"  infer_model_profile('test')['default_structured_output_mode'] = {facts['test_default_output_mode']!r}")

    print()
    print("=" * 68)
    print("7) defer_model_check：构造期不解析模型（解耦 SDK / 环境变量）")
    print("=" * 68)
    print(f"  Agent('openai:gpt-4o', defer_model_check=True).model = {defer_model_check_demo()!r}")


if __name__ == "__main__":
    main()
