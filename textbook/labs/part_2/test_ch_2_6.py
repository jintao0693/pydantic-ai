"""第 2.6 章验收测试：模型 / Provider / Profile（离线，同步）。

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_6.py -q
"""

from __future__ import annotations

from ch_2_6 import (
    defer_model_check_demo,
    fallback_all_fail,
    fallback_run,
    infer_test_model_identity,
    override_switches,
    parse_examples,
    probe_settings,
    profile_facts,
)


# ---------------------------------------------------------------------------
# 模型身份字段：model_name / system / model_id / base_url
# ---------------------------------------------------------------------------
def test_infer_model_test_identity_fields() -> None:
    identity = infer_test_model_identity()
    assert identity["type"] == "TestModel"
    assert identity["model_name"] == "test"
    assert identity["system"] == "test"
    assert identity["model_id"] == "test:test"  # f'{system}:{model_name}'
    assert identity["base_url"] is None


# ---------------------------------------------------------------------------
# 字符串解析：'provider:model' 在第一个冒号处切分
# ---------------------------------------------------------------------------
def test_parse_model_id_splits_on_first_colon() -> None:
    parsed = parse_examples()
    assert parsed["openai:gpt-4o"] == ("openai", "gpt-4o")
    assert parsed["anthropic:claude-sonnet-4-5"] == ("anthropic", "claude-sonnet-4-5")
    assert parsed["test"] == (None, "test")  # 无前缀 → provider 为 None


# ---------------------------------------------------------------------------
# ModelSettings 合并：run 级设置覆盖 Agent 级设置，且未覆盖的键保留
# ---------------------------------------------------------------------------
def test_model_settings_merge_precedence() -> None:
    output, merged = probe_settings(
        agent_settings={"max_tokens": 100, "timeout": 20},
        run_settings={"temperature": 0.5},
    )
    assert output == "ok"
    # Agent 级 + run 级按 key 合并
    assert merged["max_tokens"] == 100
    assert merged["timeout"] == 20
    assert merged["temperature"] == 0.5


def test_run_settings_override_agent_settings() -> None:
    _, merged = probe_settings(
        agent_settings={"max_tokens": 100, "temperature": 0.0},
        run_settings={"temperature": 0.9},  # 覆盖 Agent 级同名键
    )
    assert merged["temperature"] == 0.9
    assert merged["max_tokens"] == 100  # 未被覆盖的键继续保留


# ---------------------------------------------------------------------------
# FallbackModel：主模型失败 → 回退备选
# ---------------------------------------------------------------------------
def test_fallback_model_falls_back_on_failure() -> None:
    output, calls = fallback_run()
    assert calls == ["primary", "backup"]  # 先试主模型，失败后才试备选
    assert output == "来自备选模型的回答"


def test_fallback_all_fail_raises_group() -> None:
    # 所有候选都失败 → 聚合为 FallbackExceptionGroup，内含各模型的原始异常
    assert fallback_all_fail() == ["ModelAPIError", "ModelAPIError"]


# ---------------------------------------------------------------------------
# Agent.override(model=...)：仅在 with 块内生效
# ---------------------------------------------------------------------------
def test_override_switches_model_within_context_only() -> None:
    before, during, after = override_switches()
    assert before == "baseline"
    assert during == "overridden"  # TestModel 顶替生效
    assert after == "baseline"  # 退出 with 后恢复原模型


# ---------------------------------------------------------------------------
# ModelProfile：能力事实（选型依据）
# ---------------------------------------------------------------------------
def test_profile_exposes_capability_facts() -> None:
    facts = profile_facts()
    function_profile = facts["function_model"]
    test_profile = facts["test_model"]
    assert isinstance(function_profile, dict) and isinstance(test_profile, dict)
    assert function_profile["supports_tools"] is True
    assert function_profile["supports_json_schema_output"] is True  # FunctionModel 支持 native 结构化输出
    assert test_profile["supports_json_schema_output"] is False  # TestModel 不支持
    assert facts["test_default_output_mode"] == "tool"


# ---------------------------------------------------------------------------
# defer_model_check：构造期不解析模型
# ---------------------------------------------------------------------------
def test_defer_model_check_keeps_string_model() -> None:
    # 未安装 openai extra 时，不加 defer_model_check 会在构造期直接报错；
    # 加上它，模型字符串被惰性保留。
    assert defer_model_check_demo() == "'openai:gpt-4o'"
