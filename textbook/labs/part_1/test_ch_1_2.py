"""第 1.2 章验收测试：全部离线，无需任何 API Key。

运行：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_2.py -q
"""

from __future__ import annotations

import pytest

import ch_1_2
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


def test_test_model_runs_without_api_key() -> None:
    """TestModel 能跑通一次完整运行，且返回非空文本输出。"""
    output = ch_1_2.run_with_test_model()
    assert isinstance(output, str)
    assert output.strip() != ''


def test_function_model_returns_expected_text() -> None:
    """FunctionModel 严格按自定义函数返回预期文本。"""
    assert ch_1_2.run_with_function_model('你好，世界') == '你好！你刚刚说的是：你好，世界'


def test_all_messages_contains_request_and_response() -> None:
    """一次运行的历史里至少有用户消息与模型消息。"""
    result = Agent(TestModel()).run_sync('ping')
    messages = result.all_messages()
    kinds = [message.kind for message in messages]
    assert len(messages) >= 2
    assert 'request' in kinds
    assert 'response' in kinds


def test_missing_model_raises_user_error() -> None:
    """pytest.raises：未指定模型时抛出 UserError。"""
    agent: Agent[None, str] = Agent()
    with pytest.raises(UserError, match='`model` must either be set'):
        agent.run_sync('hi')


@pytest.fixture
def echo_agent() -> Agent[None, str]:
    """fixture：构造一个把用户输入原样回显的 FunctionModel Agent。"""

    def echo(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=f'echo: {ch_1_2._last_user_text(messages)}')])

    return Agent(FunctionModel(echo))


@pytest.mark.parametrize(
    ('prompt', 'expected'),
    [
        ('a', 'echo: a'),
        ('hello', 'echo: hello'),
        ('你好', 'echo: 你好'),
    ],
)
def test_function_model_parametrized(echo_agent: Agent[None, str], prompt: str, expected: str) -> None:
    """parametrize + fixture：同一行为在多组输入下都能稳定复现。"""
    assert echo_agent.run_sync(prompt).output == expected
