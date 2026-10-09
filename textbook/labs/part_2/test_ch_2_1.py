"""第 2.1 章验收测试：全部离线、全部同步（run_sync），无需任何 API Key。

运行：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_1.py -q
"""

from __future__ import annotations

import ch_2_1
from ch_2_1 import CityInfo

# 共 12 个断言，覆盖：TestModel 跑通、消息历史、用量、FunctionModel 文本、
# 结构化输出、instructions 注入、system_prompt 落点、同步流式。


def test_test_model_runs_offline() -> None:
    """TestModel 能离线跑通一次运行，返回非空文本。"""
    result = ch_2_1.run_with_test_model()
    assert isinstance(result.output, str)
    assert result.output.strip() != ''


def test_all_messages_contains_request_and_response() -> None:
    """一次运行的 all_messages() 里同时有用户请求与模型响应两类消息。"""
    result = ch_2_1.run_with_test_model()
    kinds = [message.kind for message in result.all_messages()]
    assert 'request' in kinds
    assert 'response' in kinds


def test_function_model_returns_expected_text() -> None:
    """FunctionModel 严格按自定义函数返回预期文本，可精确断言到字。"""
    assert ch_2_1.run_with_function_model('你好，世界') == '【FunctionModel】你说了：你好，世界'


def test_structured_output_is_pydantic_instance() -> None:
    """结构化输出的 result.output 是 Pydantic 实例，且字段值正确。"""
    city = ch_2_1.run_structured_output()
    assert isinstance(city, CityInfo)
    assert (city.city, city.country) == ('Paris', 'France')


def test_instructions_are_passed_to_model() -> None:
    """instructions 会被注入到 AgentInfo.instructions，随请求一起发给模型。"""
    captured = ch_2_1.compare_instructions_and_system_prompt()
    assert captured['instructions_from_info'] == '你是城市信息助手，只回答地理问题。'


def test_system_prompt_is_separate_from_instructions() -> None:
    """system_prompt 以 SystemPromptPart 进入请求 parts，与 instructions 落点不同。"""
    captured = ch_2_1.compare_instructions_and_system_prompt()
    assert captured['system_prompts'] == ['始终用简体中文回答。']


def test_usage_records_at_least_one_request() -> None:
    """result.usage 记录了至少 1 次模型请求。"""
    result = ch_2_1.run_with_test_model()
    assert result.usage.requests >= 1


def test_new_messages_equals_all_messages_without_history() -> None:
    """不续接历史时，new_messages() 与 all_messages() 长度一致。"""
    result = ch_2_1.run_with_test_model()
    assert len(result.new_messages()) == len(result.all_messages())


def test_stream_sync_returns_full_text() -> None:
    """run_stream_sync 的增量拼接结果等于完整文本。"""
    text = ch_2_1.stream_text_sync()
    assert text == '为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。'
