"""第 1.3 章验收测试：Pydantic v2 精要。

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_3.py -q
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ch_1_3 import TICKET_LIST_ADAPTER, Ticket, User, build_example_ticket


def _error_locs(exc: ValidationError) -> set[tuple[Any, ...]]:
    """把 errors() 的 loc 收成集合，便于断言"哪个字段报错"。"""
    return {err["loc"] for err in exc.errors()}


# ---------------------------------------------------------------------------
# 合法数据
# ---------------------------------------------------------------------------
def test_valid_ticket_passes_and_parses_nested_models() -> None:
    ticket = build_example_ticket()
    assert isinstance(ticket, Ticket)
    # 嵌套模型被解析为 User 实例，可选字段 assignee 非空
    assert isinstance(ticket.assignee, User)
    # 判别式联合被解析成对应子类型
    assert [type(e).__name__ for e in ticket.events] == ["Comment", "StatusChange"]
    assert ticket.priority == "high"


# ---------------------------------------------------------------------------
# field_validator：标签规范化
# ---------------------------------------------------------------------------
def test_field_validator_normalizes_tags() -> None:
    ticket = Ticket(id=1, title="三段式标题", tags=["Bug", "bug", "  urgent  ", "", "支付"])
    # 小写、去空白、去重、保持首次出现顺序、丢弃空串
    assert ticket.tags == ["bug", "urgent", "支付"]


# ---------------------------------------------------------------------------
# 注解式校验器：BeforeValidator / AfterValidator
# ---------------------------------------------------------------------------
def test_annotated_validators_apply() -> None:
    ticket = Ticket(id=1, title="abc", external_label="  JIRA-1024  ", slug="Hello World")
    assert ticket.external_label == "jira-1024"  # BeforeValidator 归一化
    assert ticket.slug == "hello-world"  # AfterValidator 变换


# ---------------------------------------------------------------------------
# model_validator(mode="after")：跨字段交叉校验
# ---------------------------------------------------------------------------
def test_model_validator_cross_field_rule() -> None:
    # high 优先级但缺 assignee → 拒绝，loc 为 ()（模型级）
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=1, title="紧急故障处理", priority="high")
    errors = exc_info.value.errors()
    assert errors[0]["type"] == "value_error"
    assert errors[0]["loc"] == ()

    # 补上 assignee 即通过
    ok = Ticket(id=1, title="紧急故障处理", priority="high", assignee=User(id=1, name="A", email="a@x.com"))
    assert ok.assignee is not None


# ---------------------------------------------------------------------------
# 非法枚举 / 越界 / 长度约束
# ---------------------------------------------------------------------------
def test_invalid_literal_priority_reports_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=1, title="abc", priority="urgent")
    assert ("priority",) in _error_locs(exc_info.value)
    assert "literal_error" in {e["type"] for e in exc_info.value.errors()}


def test_numeric_range_constraint_ge() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=0, title="abc")
    assert ("id",) in _error_locs(exc_info.value)
    assert "greater_than_equal" in {e["type"] for e in exc_info.value.errors()}


def test_string_length_constraint() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=1, title="ab")  # min_length=3
    assert ("title",) in _error_locs(exc_info.value)
    assert "string_too_short" in {e["type"] for e in exc_info.value.errors()}


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=1, title="abc", unknown_field="x")
    assert ("unknown_field",) in _error_locs(exc_info.value)
    assert "extra_forbidden" in {e["type"] for e in exc_info.value.errors()}


# ---------------------------------------------------------------------------
# 序列化 / 反序列化往返
# ---------------------------------------------------------------------------
def test_dump_and_validate_roundtrip() -> None:
    ticket = build_example_ticket()

    from_dict = Ticket.model_validate(ticket.model_dump())
    from_json = Ticket.model_validate_json(ticket.model_dump_json())

    assert from_dict == ticket
    assert from_json == ticket
    # 序列化后是纯 dict / JSON 文本
    dumped = ticket.model_dump()
    assert isinstance(dumped, dict) and isinstance(dumped["assignee"], dict)
    assert '"priority":"high"' in ticket.model_dump_json()


# ---------------------------------------------------------------------------
# JSON Schema：LLM 结构化输出的契约
# ---------------------------------------------------------------------------
def test_model_json_schema_shape() -> None:
    schema = Ticket.model_json_schema()

    assert schema["type"] == "object"
    props = schema["properties"]
    assert {"id", "title", "priority", "tags", "assignee", "events"} <= set(props)
    # Literal 映射为 enum；可选字段映射为 anyOf + null
    assert props["priority"]["enum"] == ["low", "medium", "high"]
    assert {"$ref": "#/$defs/User"} in props["assignee"]["anyOf"]
    assert {"type": "null"} in props["assignee"]["anyOf"]
    # 嵌套模型进入 $defs；判别式联合带上 discriminator
    assert {"User", "Comment", "StatusChange"} <= set(schema["$defs"])
    assert schema["properties"]["events"]["items"]["discriminator"]["propertyName"] == "kind"
    # ID 约束进入 schema
    assert props["id"]["minimum"] == 1
    assert schema["required"] == ["id", "title"]


# ---------------------------------------------------------------------------
# TypeAdapter：校验非模型类型 list[Ticket]
# ---------------------------------------------------------------------------
def test_type_adapter_validates_list_of_tickets() -> None:
    assert TICKET_LIST_ADAPTER is not None
    parsed = TICKET_LIST_ADAPTER.validate_python(
        [build_example_ticket().model_dump(), {"id": 43, "title": "新增导出功能"}]
    )
    assert len(parsed) == 2
    assert all(isinstance(t, Ticket) for t in parsed)
    assert parsed[1].priority == "medium"  # 默认值生效
    # JSON Schema of list[Ticket] is an array of $ref
    assert TypeAdapter(list[Ticket]).json_schema()["type"] == "array"


# ---------------------------------------------------------------------------
# 非法判别式：报错定位到出错的联合分支
# ---------------------------------------------------------------------------
def test_discriminated_union_rejects_unknown_tag() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(id=1, title="abc", events=[{"kind": "push", "body": "x"}])
    assert ("events", 0) in _error_locs(exc_info.value)
    assert "union_tag_invalid" in {e["type"] for e in exc_info.value.errors()}
