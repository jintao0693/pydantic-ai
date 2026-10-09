"""第 2.2 章配套示例：消息协议与会话历史（离线运行，无需 API Key）。

本模块把「一次对话」拆成消息层的可观测事实，演示四件事：

  1. 两轮对话：第一轮 ``run_sync("我叫 Alice")``，把 ``result.new_messages()``
     作为 ``message_history`` 传给第二轮 ``run_sync("我叫什么？")``；
  2. ``all_messages()`` / ``new_messages()`` 的区别：前者是**全部历史**，
     后者只是**本次运行的增量**——多轮对话必须追加 ``new_messages()``；
  3. 序列化往返：``all_messages_json()`` 写出，``ModelMessagesTypeAdapter``
     读回，断言 ``读回 == 原对象``（持久化历史的标准姿势）；
  4. 手工构造消息：``ModelRequest.user_text_prompt(...)`` 与
     ``ModelResponse(parts=[TextPart(...)])``。

之所以用 ``FunctionModel`` 而不是 ``TestModel`` 来做两轮对话，是因为要让第二轮
的回答**真的依赖**第一轮的历史：只有当历史被正确携带时，模型才能读出「Alice」。
``TestModel`` 则用来演示消息「形状」（kind / part 类型），与历史内容无关。

运行：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_2.py
"""

from __future__ import annotations

import re
from typing import Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# --------------------------------------------------------------------------- #
# 1. 一个「真的有记忆」的假模型：从历史里读用户的名字
# --------------------------------------------------------------------------- #
# 第一轮输入形如「我叫 Alice」；用正则把名字抠出来，存进上下文（这里是历史文本里）。
_NAME_RE = re.compile(r"我叫\s*([A-Za-z\u4e00-\u9fff]+)")


def user_texts(messages: Sequence[ModelMessage]) -> list[str]:
    """把所有 ``ModelRequest`` 里的 ``UserPromptPart`` 文本按顺序取出来。

    这就是「模型从历史里读用户输入」的读取侧——它遍历的是**全部**消息，
    包括本轮新加的与 ``message_history`` 带进来的。
    """
    texts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    texts.append(part.content if isinstance(part.content, str) else "[多模态内容]")
    return texts


def memory_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """确定性模型：用户问「我叫什么」时，从历史里翻出此前记录的名字。

    若历史没被携带，这里就找不到名字，只能回答「不知道」——第二轮的回答
    因此成了「历史是否真的传过去了」的可观测证据。
    """
    joined = "\n".join(user_texts(messages))
    name_match = _NAME_RE.search(joined)

    if "我叫什么" in joined:
        name = name_match.group(1) if name_match else "不知道"
        return ModelResponse(parts=[TextPart(f"你叫 {name}。")])
    if name_match:
        return ModelResponse(parts=[TextPart(f"记住了，{name_match.group(1)}。")])
    return ModelResponse(parts=[TextPart("好的。")])


def build_agent() -> Agent[None, str]:
    """构造一个带记忆的离线 agent（``FunctionModel`` 无网络请求）。"""
    return Agent(FunctionModel(memory_model))


def build_plain_agent() -> Agent[None, str]:
    """构造一个纯 ``TestModel`` agent，用来观察消息的「形状」而非内容。"""
    return Agent(TestModel())


# --------------------------------------------------------------------------- #
# 2. 两轮对话：new_messages() 作为下一轮的 message_history
# --------------------------------------------------------------------------- #
def two_turn_conversation(agent: Agent[None, str]) -> tuple[object, object]:
    """跑两轮对话，返回 ``(第一轮结果, 第二轮结果)``。

    - 第一轮：``run_sync("我叫 Alice")``；
    - 第二轮：把第一轮的 ``new_messages()`` 作为 ``message_history`` 传入。
    """
    first = agent.run_sync("我叫 Alice")
    second = agent.run_sync("我叫什么？", message_history=first.new_messages())
    return first, second


# --------------------------------------------------------------------------- #
# 3. 序列化往返：all_messages_json() ↔ ModelMessagesTypeAdapter
# --------------------------------------------------------------------------- #
def roundtrip_json(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """把消息列表序列化成 JSON 再读回，返回反序列化结果。

    ``ModelMessagesTypeAdapter`` 是解码 ``ModelMessage`` 判别联合的**公开**入口；
    它知道 ``kind='request' | 'response'`` 与每个 part 的 ``part_kind``，能在
    读回时重建正确的类型（包括多模态内容）。
    """
    raw = ModelMessagesTypeAdapter.dump_json(list(messages))
    return ModelMessagesTypeAdapter.validate_json(raw)


# --------------------------------------------------------------------------- #
# 4. 手工构造消息
# --------------------------------------------------------------------------- #
def make_manual_request(text: str) -> ModelRequest:
    """手工构造一条「用户说了一句话」的请求消息。

    ``ModelRequest.user_text_prompt`` 是官方便捷构造器：等价于
    ``ModelRequest(parts=[UserPromptPart(text)])``，但不必自己拼 part。
    """
    return ModelRequest.user_text_prompt(text)


def make_manual_response(text: str) -> ModelResponse:
    """手工构造一条「模型回了文本」的响应消息。"""
    return ModelResponse(parts=[TextPart(text)])


# --------------------------------------------------------------------------- #
# 5. 打印辅助：把消息的形状摊平给人看
# --------------------------------------------------------------------------- #
def part_label(part: object) -> str:
    """返回 ``part_kind``，若带字符串 ``content`` 则附上内容预览。"""
    kind = getattr(part, "part_kind")
    content = getattr(part, "content", None)
    if isinstance(content, str):
        return f"{kind}({content!r})"
    return f"{kind}"


def message_summary(messages: Sequence[ModelMessage]) -> list[tuple[str, list[str]]]:
    """返回 ``[(kind, [part_kind, ...]), ...]``，供打印与断言共用。"""
    return [(message.kind, [part.part_kind for part in message.parts]) for message in messages]


def print_messages(title: str, messages: Sequence[ModelMessage]) -> None:
    print(f"  {title}（共 {len(messages)} 条）：")
    for index, message in enumerate(messages, start=1):
        parts = " + ".join(part_label(part) for part in message.parts)
        print(f"    [{index}] {message.kind:<8} {parts}")


# --------------------------------------------------------------------------- #
# 6. 演示
# --------------------------------------------------------------------------- #
def main() -> None:
    agent = build_agent()

    print("=" * 74)
    print("① 两轮对话：第一轮 new_messages() 作为第二轮 message_history")
    print("=" * 74)
    first, second = two_turn_conversation(agent)
    print(f"第一轮输出：{first.output!r}")
    print_messages("第一轮 all_messages()", first.all_messages())
    print_messages("第一轮 new_messages()", first.new_messages())
    print(f"第二轮输出：{second.output!r}")
    print_messages("第二轮 all_messages()", second.all_messages())
    print_messages("第二轮 new_messages()", second.new_messages())
    print(
        f"\n  结论：第一轮 all/new 都是 {len(first.all_messages())} 条；"
        f"第二轮 all={len(second.all_messages())} 条（历史在增长），"
        f"new={len(second.new_messages())} 条（只是增量）。"
    )
    assert second.output == "你叫 Alice。", "历史没被带到第二轮——模型读不到名字"
    print("  第二轮能回答出「Alice」，证明历史确实被携带。")

    print()
    print("=" * 74)
    print("② 消息形状：TestModel 走一轮，看 kind 与 part 类型")
    print("=" * 74)
    plain = build_plain_agent().run_sync("你好")
    print_messages("all_messages()", plain.all_messages())
    print(f"  形状摘要：{message_summary(plain.all_messages())}")

    print()
    print("=" * 74)
    print("③ 序列化往返：all_messages_json() ↔ ModelMessagesTypeAdapter")
    print("=" * 74)
    raw = second.all_messages_json()
    restored = ModelMessagesTypeAdapter.validate_json(raw)
    print(f"  all_messages_json() 字节数：{len(raw)}")
    print(f"  读回后消息条数：{len(restored)}")
    print(f"  往返一致（对象相等）：{restored == list(second.all_messages())}")
    assert restored == list(second.all_messages())

    print()
    print("=" * 74)
    print("④ 手工构造消息：user_text_prompt / ModelResponse(parts=[...])")
    print("=" * 74)
    manual_request = make_manual_request("你好呀")
    manual_response = make_manual_response("你好，很高兴见到你。")
    print(f"  手工 ModelRequest.parts：{message_summary([manual_request])}")
    print(f"  手工 ModelResponse.text：{manual_response.text!r}")
    # 手工构造的 request 可以直接当作历史喂给 agent（此处不再传 user_prompt，
    # 即「无新输入、直接续接历史」）。
    resumed = agent.run_sync(message_history=[manual_request])
    print(f"  以手工 request 为历史的运行输出：{resumed.output!r}")
    print(f"  该次运行消息条数：{len(resumed.all_messages())}")
    print("\n全部演示通过。")


if __name__ == "__main__":
    main()
