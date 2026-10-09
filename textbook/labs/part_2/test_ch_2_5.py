"""第 2.5 章验收测试：依赖注入与 RunContext。

全部离线（FunctionModel 驱动），无 API Key、无网络。同步 pytest 即可。

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_5.py -q
"""

from __future__ import annotations

from ch_2_5 import EXAMPLE_DEPS, Deps, ModelProbe, build_agent, run_once


def test_tool_reads_injected_deps() -> None:
    """工具通过 RunContext[Deps] 拿到了注入的 ctx.deps.db。"""
    probe, result = run_once(Deps(user_name="Alice", db={"alice": "1200"}))
    assert probe.tool_returns == ["1200"]
    assert result.output == "1200"


def test_deps_type_is_the_static_contract() -> None:
    """deps_type 只做静态类型参数化，暴露在 Agent 上。"""
    agent = build_agent()
    assert agent.deps_type is Deps


def test_instructions_personalized_from_deps() -> None:
    """指令函数读取 ctx.deps.user_name，生成的指令里含用户名。"""
    probe, _ = run_once(EXAMPLE_DEPS)
    assert "Alice" in (probe.instructions[0] or "")


def test_instructions_recomputed_for_new_deps() -> None:
    """换一份 deps 再跑，指令会按新用户名重算（非全局缓存）。"""
    probe, _ = run_once(Deps(user_name="Carol", db={"alice": "5"}))
    assert "Carol" in (probe.instructions[0] or "")


def test_usage_records_requests_and_tokens() -> None:
    """result.usage（属性）记录了本次运行的请求数与 token 用量。"""
    _, result = run_once(EXAMPLE_DEPS)
    usage = result.usage
    assert usage.requests == 2  # 一轮工具调用 + 一轮收尾
    assert usage.input_tokens > 0
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens


def test_model_settings_driven_by_deps() -> None:
    """动态 model_settings 依据 deps 决定温度（有账户数据→0.0，空库→0.7）。"""
    probe_full, _ = run_once(Deps(user_name="Alice", db={"alice": "1"}))
    probe_empty, _ = run_once(Deps(user_name="Alice", db={}))
    assert (probe_full.model_settings[0] or {}).get("temperature") == 0.0
    assert (probe_empty.model_settings[0] or {}).get("temperature") == 0.7


def test_override_swaps_deps() -> None:
    """agent.override(deps=...) 临时替换依赖，无需改动工具签名。"""
    probe = ModelProbe()
    agent = build_agent(probe)
    with agent.override(deps=Deps(user_name="Bob", db={"alice": "9999"})):
        result = agent.run_sync("再查一次 alice 的余额")
    assert (result.output, probe.tool_returns[-1]) == ("9999", "9999")


def test_missing_account_uses_fallback() -> None:
    """注入的 db 里没有该账户时，工具返回兜底值。"""
    _, result = run_once(Deps(user_name="Alice", db={}))
    assert result.output == "<未知账户>"
