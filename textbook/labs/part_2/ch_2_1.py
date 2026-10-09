"""第 2.1 章 · 第一个 Agent：run / run_sync / run_stream 与结构化输出。

本模块用 `TestModel` 与 `FunctionModel` 在**离线、无 API Key** 的前提下演示：

1. `Agent(...)` 的构造（`model` / `instructions` / `system_prompt` / `output_type`）；
2. `run_sync` / `run` / `run_stream_sync` / `run_stream` 四个运行入口；
3. `result.output` / `all_messages()` / `new_messages()` / `usage` 四个结果面；
4. 结构化输出：`output_type` 指定 Pydantic 模型，`result.output` 是校验后的实例；
5. `instructions` 与 `system_prompt` 在模型侧落到不同位置。

运行方式（无需任何 API Key，也不需要联网）：

    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_2/ch_2_1.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


class CityInfo(BaseModel):
    """结构化输出用的数据模型：模型必须同时给出城市与国家两个字段。"""

    city: str = Field(description='城市名称')
    country: str = Field(description='所属国家')


# ---------------------------------------------------------------------------
# 1) TestModel + run_sync：一行构造一个「永远成功」的离线 Agent
# ---------------------------------------------------------------------------


def run_with_test_model() -> AgentRunResult[str]:
    """用 TestModel 跑通一次同步运行，并把整个结果对象交出去。"""
    agent: Agent[None, str] = Agent(
        TestModel(),  # 「黑盒假模型」：不联网，固定返回 success (no tool calls)
        instructions='你是一个用于离线演示的助手。',
    )
    return agent.run_sync('用一句话介绍你自己')


# ---------------------------------------------------------------------------
# 2) FunctionModel：用函数精确控制模型返回的文本
# ---------------------------------------------------------------------------


def _last_user_text(messages: list[ModelMessage]) -> str:
    """从消息历史里取出最后一条用户消息的文本。"""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return part.content
    return ''


def build_function_model() -> FunctionModel:
    """构造一个「白盒假模型」：返回文本由我们的函数写死，可精确断言。"""

    def rule_based(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        user_text = _last_user_text(messages)
        return ModelResponse(parts=[TextPart(content=f'【FunctionModel】你说了：{user_text}')])

    return FunctionModel(rule_based)


def run_with_function_model(prompt: str) -> str:
    """用 FunctionModel 驱动 Agent，返回完全可预测的文本输出。"""
    agent = Agent(build_function_model())
    return agent.run_sync(prompt).output


# ---------------------------------------------------------------------------
# 3) 结构化输出：output_type 为一个 Pydantic 模型
# ---------------------------------------------------------------------------


def run_structured_output() -> CityInfo:
    """让 Agent 产出 CityInfo 实例：result.output 是经过校验的 Pydantic 实例。"""
    agent: Agent[None, CityInfo] = Agent(
        TestModel(custom_output_args=CityInfo(city='Paris', country='France')),
        output_type=CityInfo,  # 声明输出契约，框架据此约束并校验模型输出
    )
    return agent.run_sync('法国的首都是哪座城市？').output


# ---------------------------------------------------------------------------
# 4) instructions vs system_prompt：读 AgentInfo / ModelRequest 观察落点
# ---------------------------------------------------------------------------


def compare_instructions_and_system_prompt() -> dict[str, Any]:
    """用 FunctionModel 观察 instructions 与 system_prompt 分别被放到了哪里。"""
    captured: dict[str, Any] = {}

    def observe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # instructions 会被渲染进 AgentInfo.instructions，也写在 ModelRequest.instructions 上
        captured['instructions_from_info'] = info.instructions
        captured['instructions_on_request'] = next(
            (message.instructions for message in messages if isinstance(message, ModelRequest)),
            None,
        )
        # system_prompt 则以 SystemPromptPart 的形式进入第一条 ModelRequest 的 parts
        captured['system_prompts'] = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, SystemPromptPart)
        ]
        return ModelResponse(parts=[TextPart(content='已收到指令与系统提示。')])

    agent = Agent(
        FunctionModel(observe),
        instructions='你是城市信息助手，只回答地理问题。',
        system_prompt='始终用简体中文回答。',
    )
    agent.run_sync('上海在哪里？')
    return captured


# ---------------------------------------------------------------------------
# 5) 流式：run_stream_sync（同步）与 run_stream（异步）
# ---------------------------------------------------------------------------

_STREAM_TEXT = '为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。'


def stream_text_sync(prompt: str = '讲个冷笑话') -> str:
    """同步流式：`with agent.run_stream_sync(...) as streamed` + `stream_text(delta=True)`。"""
    agent = Agent(TestModel(custom_output_text=_STREAM_TEXT))
    with agent.run_stream_sync(prompt) as streamed:
        return ''.join(chunk for chunk in streamed.stream_text(delta=True))


async def stream_text_async(prompt: str = '讲个冷笑话') -> str:
    """异步流式：`async with agent.run_stream(...) as streamed` 形态与同步版本一致。"""
    agent = Agent(TestModel(custom_output_text=_STREAM_TEXT))
    async with agent.run_stream(prompt) as streamed:
        return ''.join([chunk async for chunk in streamed.stream_text(delta=True)])


async def run_async(prompt: str) -> str:
    """`run` 是原生异步入口：直接 `await`，与 `run_sync` 返回同类结果对象。"""
    agent = Agent(TestModel(custom_output_text='异步运行的输出'))
    result = await agent.run(prompt)
    return result.output


# ---------------------------------------------------------------------------
# 演示：直接 `python part_2/ch_2_1.py` 打印各段结果
# ---------------------------------------------------------------------------


def main() -> None:
    print('=== 1) TestModel + run_sync：离线跑通 ===')
    result = run_with_test_model()
    print('output         :', result.output)
    print('all_messages   :', len(result.all_messages()), '条')
    print('new_messages   :', len(result.new_messages()), '条')
    print('usage          :', result.usage)
    print()

    print('=== 2) FunctionModel：精确控制文本 ===')
    print(run_with_function_model('你好，世界'))
    print()

    print('=== 3) 结构化输出：output_type 为 Pydantic 模型 ===')
    city = run_structured_output()
    print('output         :', repr(city))
    print('isinstance     :', isinstance(city, CityInfo))
    print()

    print('=== 4) instructions vs system_prompt ===')
    captured = compare_instructions_and_system_prompt()
    print('info.instructions    :', captured['instructions_from_info'])
    print('request.instructions :', captured['instructions_on_request'])
    print('system_prompt parts  :', captured['system_prompts'])
    print()

    print('=== 5) 同步 / 异步流式 ===')
    print('run_stream_sync      :', stream_text_sync())
    print('run_stream (async)   :', asyncio.run(stream_text_async()))
    print('run (async)          :', asyncio.run(run_async('你好')))


if __name__ == '__main__':
    main()
