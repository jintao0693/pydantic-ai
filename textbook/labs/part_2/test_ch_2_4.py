"""第 2.4 章验收测试：输出模式与校验/重试。

全部为同步测试，依赖 ``TestModel`` 离线运行。

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_4.py -q
"""

from __future__ import annotations

import pytest

from pydantic_ai import UnexpectedModelBehavior

from ch_2_4 import (
    Review,
    make_always_fail_agent,
    make_tool_mode_agent,
    run_list_mode,
    run_native_mode_offline,
    run_per_tool_budget,
    run_prompted_mode,
    run_retry_exhaustion,
    run_text_mode,
    run_tool_mode,
    run_union_mode,
    run_validator_retry,
)


# ---------------------------------------------------------------------------
# 1. 默认 tool 模式：结构化输出字段正确
# ---------------------------------------------------------------------------
def test_tool_mode_structured_output() -> None:
    result = run_tool_mode()
    assert isinstance(result.output, Review)
    assert result.output.score == 8
    assert result.output.summary


# ---------------------------------------------------------------------------
# 2. 默认模式：输出走的是名为 final_result 的输出工具，而非纯文本
# ---------------------------------------------------------------------------
def test_default_mode_uses_output_tool() -> None:
    result = run_tool_mode()
    assert [call.tool_name for call in result.response.tool_calls] == ["final_result"]
    assert result.response.text is None


def test_output_json_schema_contract() -> None:
    schema = make_tool_mode_agent().output_json_schema()
    assert schema["type"] == "object"
    assert {"score", "summary"} <= set(schema["properties"])
    assert schema["required"] == ["score", "summary"]


# ---------------------------------------------------------------------------
# 3. 输出校验器：先失败一次，重试后成功
# ---------------------------------------------------------------------------
def test_validator_fails_then_succeeds() -> None:
    outcome = run_validator_retry(fail_times=1)
    # validator 被调用两次：第一次抛 ModelRetry，第二次通过
    assert outcome.attempts == 2
    assert outcome.retry_indexes == [0, 1]
    # 第二次调用时，输出工具 final_result 已用掉 1 次重试
    assert outcome.retries_snapshots[1] == {"final_result": 1}
    assert isinstance(outcome.output, Review)


def test_validator_passes_without_retry() -> None:
    outcome = run_validator_retry(fail_times=0)
    assert outcome.attempts == 1
    assert outcome.retry_indexes == [0]


# ---------------------------------------------------------------------------
# 4. 重试预算耗尽：抛 UnexpectedModelBehavior
# ---------------------------------------------------------------------------
def test_retry_exhaustion_raises() -> None:
    with pytest.raises(UnexpectedModelBehavior) as exc_info:
        make_always_fail_agent(max_output_retries=1).run_sync("请评审这段代码")
    assert "Exceeded maximum output retries (1)" in str(exc_info.value)


def test_retry_exhaustion_helper_reports_message() -> None:
    exc = run_retry_exhaustion(max_output_retries=1)
    assert isinstance(exc, UnexpectedModelBehavior)
    assert "Exceeded maximum output retries (1)" in str(exc)


# ---------------------------------------------------------------------------
# 5. 全局预算 vs 每输出工具预算
# ---------------------------------------------------------------------------
def test_per_tool_budget_overrides_global() -> None:
    # 全局 AgentRetries(output=1) 很小，但 ToolOutput(max_retries=3) 调大了该工具预算
    output, attempts = run_per_tool_budget(fail_times=2)
    assert attempts == 3
    assert isinstance(output, Review)


# ---------------------------------------------------------------------------
# 6. 第二种输出模式：PromptedOutput 同样得到结构化结果
# ---------------------------------------------------------------------------
def test_prompted_mode_structured_output() -> None:
    result = run_prompted_mode()
    assert isinstance(result.output, Review)
    assert result.output.score == 6
    # prompted 模式没有输出工具调用，结构化结果来自对响应文本的解析
    assert result.response.tool_calls == []
    assert result.response.text


# ---------------------------------------------------------------------------
# 7. NativeOutput 需要 provider 原生支持
# ---------------------------------------------------------------------------
def test_native_mode_requires_provider_support() -> None:
    message = run_native_mode_offline()
    assert "not supported" in message


# ---------------------------------------------------------------------------
# 8. output_type 的其它形态
# ---------------------------------------------------------------------------
def test_text_mode_returns_str() -> None:
    assert run_text_mode() == "这是一段纯文本输出"


def test_list_mode_returns_reviews() -> None:
    reviews = run_list_mode()
    assert isinstance(reviews, list)
    assert len(reviews) == 2
    assert all(isinstance(item, Review) for item in reviews)


def test_union_mode_returns_review() -> None:
    output = run_union_mode()
    assert isinstance(output, Review)
