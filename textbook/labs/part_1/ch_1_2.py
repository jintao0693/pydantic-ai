"""第 1.2 章 · 工程基座：用 TestModel / FunctionModel 离线运行 Agent。

本模块演示「不连真实模型、不消耗 API Key」也能把 Agent 跑通并从测试中
稳定断言它，这正是工程基座（uv + 类型系统 + pytest）落到 pydantic-ai 上
的第一个抓手。

运行方式（无需任何 API Key，也不需要联网）：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_2.py
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# ---------------------------------------------------------------------------
# 1) TestModel：一行代码，让 Agent 在没有真实模型时也能跑完整个循环
# ---------------------------------------------------------------------------


def run_with_test_model() -> str:
    """用 TestModel 跑通一次 Agent 运行，返回 result.output。"""
    agent = Agent(
        TestModel(),  # 一个「永远成功」的假模型，不发起任何网络请求
        instructions="你是一个用于离线测试的助手。",
    )
    result = agent.run_sync("用一句话介绍你自己")
    return result.output


# ---------------------------------------------------------------------------
# 2) FunctionModel：用一个自定义函数充当模型，完全控制模型行为
# ---------------------------------------------------------------------------


def _last_user_text(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条用户消息的文本。"""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return part.content
    return ''


def build_rule_based_model() -> FunctionModel:
    """构造一个「规则模型」：函数被调用一次，就返回一条我们指定的响应。"""

    def rule_based(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # messages 是到目前为止的完整历史；info 描述本次请求暴露的工具/输出
        user_text = _last_user_text(messages)
        if '你好' in user_text:
            reply = f'你好！你刚刚说的是：{user_text}'
        else:
            reply = f'收到 {len(user_text)} 个字符，可用工具 {len(info.function_tools)} 个。'
        return ModelResponse(parts=[TextPart(content=reply)])

    return FunctionModel(rule_based)


def run_with_function_model(prompt: str) -> str:
    """用 FunctionModel 驱动 Agent，返回可预测的输出。"""
    agent = Agent(build_rule_based_model())
    return agent.run_sync(prompt).output


# ---------------------------------------------------------------------------
# 3) 观察消息协议：一次运行留下了哪些消息
# ---------------------------------------------------------------------------


def message_kinds(prompt: str = 'ping') -> list[str]:
    """返回一次运行的 all_messages() 中每条消息的 kind 序列。"""
    agent = Agent(TestModel())
    result = agent.run_sync(prompt)
    return [message.kind for message in result.all_messages()]


def main() -> None:
    print('=== 1) TestModel：无需真实模型也能跑通 ===')
    print(run_with_test_model())
    print()

    print('=== 2) FunctionModel：用函数精确控制模型行为 ===')
    print(run_with_function_model('你好，世界'))
    print(run_with_function_model('随便说点什么'))
    print()

    print('=== 3) 一次运行留下的消息 kind 序列 ===')
    print(' -> '.join(message_kinds()))


if __name__ == '__main__':
    main()
