"""第 1.1 章验收测试：证明「确定性玩具 Agent」确实实现了 ReAct 循环的机理。

全部离线、纯标准库 + pytest，无 API Key、无网络。

    cd textbook/labs
    uv run --no-project --with pytest python -m pytest part_1/test_ch_1_1.py -q
"""

import pytest

from ch_1_1 import (
    FIXED_NOW,
    AgentNotConvergedError,
    execute_tool,
    run_agent,
)


def test_multistep_arithmetic_returns_correct_answer() -> None:
    """一个需要两步工具调用的问题，能得到正确最终答案。"""
    result = run_agent("(2 + 3) * 4")
    assert result.answer == "20"
    assert result.converged is True
    assert result.steps == 3  # add 一轮、mul 一轮、final 一轮


def test_trace_follows_react_order() -> None:
    """轨迹顺序符合 ReAct：先工具（action/observation），最后才是 final。"""
    result = run_agent("(2 + 3) * 4")
    phases = [entry["phase"] for entry in result.trace]

    assert phases[0] == "thought"  # 每轮都从「思考」开始
    assert phases[-1] == "final"  # 最终一定以答案收尾
    assert "action" in phases and "observation" in phases  # 确实发生了工具调用
    # 第一次 action 一定早于第一次 observation；所有 observation 都在 final 之前
    assert phases.index("action") < phases.index("observation")
    assert max(i for i, p in enumerate(phases) if p == "observation") < phases.index("final")


def test_tool_results_backfilled_into_next_turn_context() -> None:
    """工具结果被正确回填进对话，且下一轮模型确实看到了更大的上下文。"""
    result = run_agent("(2 + 3) * 4")

    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert [m["content"] for m in tool_messages] == [5, 20]  # add→5，mul→20，顺序正确

    actions = [entry for entry in result.trace if entry["phase"] == "action"]
    # 第二轮模型的可见上下文，比第一轮更大（因为插入了工具结果）
    assert actions[1]["context_len"] > actions[0]["context_len"]
    # final 那轮的上下文包含全部历史
    final_entry = result.trace[-1]
    assert final_entry["context_len"] > actions[-1]["context_len"]


def test_max_steps_raises_not_converged() -> None:
    """max_steps 耗尽时，抛出明确的「未收敛」异常，且不产生 final。"""
    with pytest.raises(AgentNotConvergedError) as excinfo:
        run_agent("(2 + 3) * 4", max_steps=1)

    trace = excinfo.value.trace
    assert len(trace) > 0  # 保留了部分轨迹，便于排错
    assert all(entry["phase"] != "final" for entry in trace)
    assert "max_steps=1" in str(excinfo.value)


def test_unknown_tool_is_rejected() -> None:
    """模型调用不存在的工具时被拒绝，结果回填后模型收敛给出结论。"""
    result = run_agent("2 ^ 3")

    observations = [entry for entry in result.trace if entry["phase"] == "observation"]
    assert observations and observations[0]["ok"] is False
    assert observations[0]["tool"] == "pow"
    assert "pow" in (observations[0]["error"] or "")

    # 宿主层的拒绝理由也一致
    ok, value, error = execute_tool("pow", (2, 3))
    assert ok is False and value is None and "未知工具" in (error or "")

    assert result.converged is True
    assert "pow" in result.answer  # 最终答案向用户说明失败原因


def test_single_tool_time_question_converges() -> None:
    """单工具问题：一次 now() 工具调用后即收敛。"""
    result = run_agent("现在几点？")

    actions = [entry for entry in result.trace if entry["phase"] == "action"]
    assert len(actions) == 1
    assert actions[0]["tool"] == "now"
    assert FIXED_NOW in result.answer


def test_tools_are_called_in_dependency_order() -> None:
    """子表达式先算：先 add，再 mul——顺序即规划，不可颠倒。"""
    result = run_agent("(2 + 3) * 4")
    called_tools = [entry["tool"] for entry in result.trace if entry["phase"] == "action"]
    assert called_tools == ["add", "mul"]
