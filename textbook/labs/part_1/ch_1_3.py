"""第 1.3 章 · Pydantic v2 精要 —— 工单（Ticket）领域模型。

本模块用一个贴近真实业务的「工单」模型集合，串起 Pydantic v2 的核心能力：

    字段约束 / 默认值 / 可选字段 / model_config
    → field_validator（标签规范化）
    → model_validator(mode="after")（跨字段交叉校验）
    → BeforeValidator / AfterValidator（注解式校验）
    → 嵌套模型、list[Model]、dict[str, Model]、判别式联合
    → 序列化 / 反序列化往返（model_dump / model_dump_json / model_validate*）
    → model_json_schema()（LLM 结构化输出的契约）
    → ValidationError 与 errors() 的结构
    → TypeAdapter 校验非模型类型（如 list[Ticket]）

运行：
    cd textbook/labs
    uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_3.py
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# 0. 类型别名：枚举用 Literal 表达，直接映射到 JSON Schema 的 enum
# ---------------------------------------------------------------------------
Priority = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# 1. 注解式校验器（函数）：不写方法、直接挂在字段类型上，可复用
# ---------------------------------------------------------------------------
def normalize_label(value: str) -> str:
    """前置校验：去首尾空白并转小写（BeforeValidator 在类型校验之前运行）。"""
    return value.strip().lower()


def slugify(value: str) -> str:
    """后置校验：把标题式字符串压成 URL slug（AfterValidator 在类型校验之后运行）。"""
    return value.strip().lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# 2. 嵌套模型：User 被复用在 assignee / watchers 中
# ---------------------------------------------------------------------------
class User(BaseModel):
    """负责人 / 关注人。``frozen=True`` 让其不可变、可哈希，适合当字典值。"""

    model_config = ConfigDict(frozen=True)

    id: int = Field(ge=1, description="用户 ID，正整数")
    name: str = Field(min_length=1, max_length=50, description="姓名")
    email: str = Field(description="邮箱")


# ---------------------------------------------------------------------------
# 3. 判别式联合：用 kind 字段区分不同事件，避免"猜类型"式校验
# ---------------------------------------------------------------------------
class Comment(BaseModel):
    """评论事件。"""

    kind: Literal["comment"] = "comment"
    author: str = Field(min_length=1, description="评论者")
    body: str = Field(min_length=1, description="评论内容")


class StatusChange(BaseModel):
    """状态流转事件。"""

    kind: Literal["status_change"] = "status_change"
    old_status: str = Field(min_length=1, description="原状态")
    new_status: str = Field(min_length=1, description="新状态")


# 判别式联合：pydantic 先读 kind，再只在对应分支上做校验（更快、报错更准）
TicketEvent = Annotated[Comment | StatusChange, Field(discriminator="kind")]


# ---------------------------------------------------------------------------
# 4. 核心模型：Ticket
# ---------------------------------------------------------------------------
class Ticket(BaseModel):
    """工单：聚合了字段约束、校验器、嵌套 / 容器类型与序列化。"""

    # extra="forbid"：拒绝未知字段，防止上游拼写错误被静默吞掉
    # str_strip_whitespace=True：所有 str 字段先自动去首尾空白
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(ge=1, description="工单 ID，正整数")
    title: str = Field(min_length=3, max_length=120, description="标题")
    priority: Priority = Field(default="medium", description="优先级")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    assignee: User | None = Field(default=None, description="负责人，可为空（可选字段）")
    story_points: Annotated[int, Field(ge=0, le=100)] = Field(default=0, description="故事点，0–100")
    # BeforeValidator：进入 int/str 核心校验之前先做归一化
    external_label: Annotated[str, BeforeValidator(normalize_label)] = Field(
        default="", description="外部系统标签，自动小写"
    )
    # AfterValidator：核心校验通过之后再做变换
    slug: Annotated[str, AfterValidator(slugify)] = Field(default="", description="URL slug")
    # dict[str, Model]：角色 → 关注人
    watchers: dict[str, User] = Field(default_factory=dict, description="关注人，按角色索引")
    # list[Model]：事件流，元素是判别式联合
    events: list[TicketEvent] = Field(default_factory=list, description="事件流")

    # ---- 字段级校验：只拿到当前字段，适合做规范化 / 清洗 ----------------
    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        """标签统一小写、去空白、去重，并保持首次出现顺序。"""
        seen: set[str] = set()
        result: list[str] = []
        for raw in value:
            tag = raw.strip().lower()
            if tag and tag not in seen:
                seen.add(tag)
                result.append(tag)
        return result

    # ---- 模型级校验：拿到整个已校验对象，适合做跨字段交叉校验 ----------
    @model_validator(mode="after")
    def check_high_priority_has_assignee(self) -> Ticket:
        """业务规则：high 优先级必须指定负责人，否则拒绝。"""
        if self.priority == "high" and self.assignee is None:
            raise ValueError("high 优先级工单必须指定 assignee")
        return self


# ---------------------------------------------------------------------------
# 5. TypeAdapter：给非 BaseModel 类型（这里是 list[Ticket]）一份校验 / 序列化能力
# ---------------------------------------------------------------------------
TICKET_LIST_ADAPTER: TypeAdapter[list[Ticket]] = TypeAdapter(list[Ticket])


def build_example_ticket() -> Ticket:
    """构造一条合法的示例工单，供 main 与测试共用。"""
    alice = User(id=1, name="Alice", email="alice@example.com")
    bob = User(id=2, name="Bob", email="bob@example.com")
    return Ticket(
        id=42,
        title="支付回调偶发超时",
        priority="high",
        # 故意混入大小写 / 空白 / 重复，验证 field_validator
        tags=["Bug", "bug", "  urgent  ", "支付"],
        assignee=alice,
        story_points=8,
        external_label="  JIRA-1024  ",
        slug="Payment Callback Timeout",
        watchers={"reporter": bob},
        events=[
            {"kind": "comment", "author": "Bob", "body": "复现步骤已附在描述里"},
            {"kind": "status_change", "old_status": "open", "new_status": "in_progress"},
        ],
    )


def main() -> None:
    ticket = build_example_ticket()

    print("=" * 68)
    print("1) 校验成功：标签被规范化，嵌套 / 联合类型均被解析为模型对象")
    print("=" * 68)
    print(f"priority={ticket.priority!r}  tags={ticket.tags}")
    print(f"external_label={ticket.external_label!r}  slug={ticket.slug!r}")
    print(f"assignee={ticket.assignee}")
    print(f"event types={[type(e).__name__ for e in ticket.events]}")

    print()
    print("=" * 68)
    print("2) 序列化：model_dump() / model_dump_json()")
    print("=" * 68)
    print("model_dump():")
    print(json.dumps(ticket.model_dump(), ensure_ascii=False, indent=2))
    print("model_dump_json(indent=2):")
    print(ticket.model_dump_json(indent=2))

    print()
    print("=" * 68)
    print("3) 反序列化往返：model_validate_json(model_dump_json()) == 原对象")
    print("=" * 68)
    restored = Ticket.model_validate_json(ticket.model_dump_json())
    print(f"往返一致：{restored == ticket}")

    print()
    print("=" * 68)
    print("4) JSON Schema：LLM 结构化输出 / 工具参数所用的契约")
    print("=" * 68)
    schema = Ticket.model_json_schema()
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    print(f"$defs 中的嵌套模型：{list(schema.get('$defs', {}))}")

    print()
    print("=" * 68)
    print("5) 校验失败：ValidationError.errors() 的结构")
    print("=" * 68)
    try:
        Ticket(
            id=0,  # ge=1 越界
            title="x",  # min_length=3 太短
            priority="urgent",  # Literal 枚举非法
            assignee=None,  # 触发 model_validator
            unexpected="字段",  # extra="forbid"
        )
    except ValidationError as exc:
        print(f"共 {exc.error_count()} 个错误：")
        for err in exc.errors():
            print(f"  loc={err['loc']!r:<22} type={err['type']:<20} msg={err['msg']}")

    print()
    print("5b) 交叉校验失败：high 优先级却没有 assignee（loc 为 ()，即模型级）")
    try:
        Ticket(id=7, title="紧急：线上故障", priority="high")
    except ValidationError as exc:
        for err in exc.errors():
            print(f"  loc={err['loc']!r:<22} type={err['type']:<20} msg={err['msg']}")

    print()
    print("=" * 68)
    print("6) TypeAdapter 校验 list[Ticket]")
    print("=" * 68)
    payload = [ticket.model_dump(), {"id": 43, "title": "新增导出功能"}]
    parsed = TICKET_LIST_ADAPTER.validate_python(payload)
    print(f"元素个数={len(parsed)}  第二个={parsed[1]!r}")


if __name__ == "__main__":
    main()
