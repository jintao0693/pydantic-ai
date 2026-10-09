"""第 2.2 章验收测试：证明消息协议与会话历史的关键行为。

全部**离线同步**、无需 API Key、无网络（``FunctionModel`` / ``TestModel``）。

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_2/test_ch_2_2.py -q
"""

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
)

from ch_2_2 import (
    build_agent,
    build_plain_agent,
    make_manual_request,
    make_manual_response,
    message_summary,
    roundtrip_json,
    two_turn_conversation,
)


def test_first_turn_has_request_then_response() -> None:
    """第一轮：历史恰好两条——一条 request 加一条 response，形状可断言。"""
    first, _second = two_turn_conversation(build_agent())

    assert len(first.all_messages()) == 2
    assert len(first.new_messages()) == 2
    assert [m.kind for m in first.all_messages()] == ["request", "response"]
    assert message_summary(first.all_messages()) == [("request", ["user-prompt"]), ("response", ["text"])]
    assert first.output == "记住了，Alice。"


def test_second_turn_history_grows() -> None:
    """第二轮历史更长：把 new_messages() 作为 message_history 追加后，all_messages() 由 2 变 4。"""
    first, second = two_turn_conversation(build_agent())

    assert len(second.all_messages()) == 4
    assert len(second.all_messages()) > len(first.all_messages())
    # 第二轮的完整历史，前缀就是第一轮的全部消息（历史被原样接上）
    assert list(second.all_messages()[:2]) == list(first.all_messages())


def test_new_messages_is_increment_only() -> None:
    """new_messages() 只含本次增量：第二轮仅 2 条，等于 all_messages() 去掉历史前缀。"""
    first, second = two_turn_conversation(build_agent())

    assert len(second.new_messages()) == 2
    assert list(second.new_messages()) == list(second.all_messages())[2:]
    # 增量里只包含第二轮的输入与输出
    assert [m.kind for m in second.new_messages()] == ["request", "response"]
    assert isinstance(second.new_messages()[0], ModelRequest)
    assert second.new_messages()[0].parts[0].content == "我叫什么？"


def test_second_turn_reads_name_from_history() -> None:
    """历史被正确携带的证据：第二轮能答出第一轮才出现过的名字「Alice」。"""
    _first, second = two_turn_conversation(build_agent())
    assert second.output == "你叫 Alice。"


def test_all_messages_json_roundtrip_is_lossless() -> None:
    """序列化往返：ModelMessagesTypeAdapter 读回的对象与原始列表相等。"""
    _first, second = two_turn_conversation(build_agent())
    original = list(second.all_messages())

    raw = second.all_messages_json()
    restored = ModelMessagesTypeAdapter.validate_json(raw)

    assert restored == original
    assert [m.kind for m in restored] == ["request", "response", "request", "response"]
    # 再序列化一次，字节也与原始的 all_messages_json() 一致（确定性往返）
    assert ModelMessagesTypeAdapter.dump_json(restored) == raw
    # 辅助函数与 in-place 写出一致
    assert roundtrip_json(original) == original


def test_manual_request_is_a_valid_history_entry() -> None:
    """手工构造的 user_text_prompt 请求，可直接作为 message_history 使用。"""
    agent = build_agent()
    manual = make_manual_request("你好呀")

    assert manual.kind == "request"
    assert message_summary([manual]) == [("request", ["user-prompt"])]
    assert manual.parts[0].content == "你好呀"

    result = agent.run_sync(message_history=[manual])
    assert len(result.all_messages()) == 2
    assert isinstance(result.all_messages()[1], ModelResponse)


def test_manual_response_parts_and_text() -> None:
    """手工构造的 ModelResponse(parts=[TextPart(...)]) 判别为 response 并可读回 text。"""
    manual = make_manual_response("你好，很高兴见到你。")

    assert manual.kind == "response"
    assert message_summary([manual]) == [("response", ["text"])]
    assert manual.text == "你好，很高兴见到你。"
    assert isinstance(manual.parts[0], TextPart)


def test_plain_testmodel_message_shape() -> None:
    """TestModel 走一轮，消息形状与内容无关，始终是 request(user-prompt) + response(text)。"""
    result = build_plain_agent().run_sync("你好")

    assert len(result.all_messages()) == 2
    # ModelMessage 是 Annotated[...] 联合别名，不能直接用于 isinstance；
    # 请用具体的 ModelRequest / ModelResponse 做类型判断。
    assert all(isinstance(m, (ModelRequest, ModelResponse)) for m in result.all_messages())
    assert message_summary(result.all_messages()) == [("request", ["user-prompt"]), ("response", ["text"])]
