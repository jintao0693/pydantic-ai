# 1.3 Pydantic v2 精要：模型、校验、序列化、JSON Schema 与 DI 基础

> 配套 labs：[`labs/part_1/ch_1_3.py`](../labs/part_1/ch_1_3.py) · 验收测试：[`labs/part_1/test_ch_1_3.py`](../labs/part_1/test_ch_1_3.py)

---

## 1. 本课目标

学完本章，你应当能够：

1. 用 `BaseModel` + `Field(...)` 声明**带约束**的数据模型，并解释 `model_config` 中 `extra`、`str_strip_whitespace`、`frozen` 的含义。
2. 在四种校验形态之间**按需选择**：`field_validator`、`model_validator(mode="after")`、`BeforeValidator`/`AfterValidator` 注解式校验、以及容器/联合类型自带的校验。
3. 正确使用嵌套模型、`list[Model]`、`dict[str, Model]` 与 **判别式联合**（`Field(discriminator=...)`）。
4. 用 `model_dump()` / `model_dump_json()` / `model_validate()` / `model_validate_json()` 完成**可往返**的序列化，并写出自定义序列化器。
5. 用 `TypeAdapter` 校验**非模型类型**（如 `list[Ticket]`）。
6. 读懂 `Model.model_json_schema()` 的输出，并说清它如何成为 **LLM 结构化输出 / 工具参数 / 输出类型**的共同契约。
7. 解析 `ValidationError.errors()` 的结构，写出可回归的校验断言。
8. 说出「类型即契约」在 Pydantic AI 中落在哪三个位置（`output_type`、工具参数 schema、依赖注入类型）。

---

## 2. 前置知识

- 第 1.2 章：类型注解、`uv`、`pytest`（本章所有示例都可在 `uv run --no-project` 下离线运行）。
- Python 泛型容器写法：`list[str]`、`dict[str, int]`、`X | None`。
- `typing.Literal` 与 `typing.Annotated` 的作用（`Annotated[T, ...]` 表示「给类型 T 附加元数据」）。

---

## 3. 为什么需要它

LLM 应用里，**你几乎所有的「接口」都不是函数，而是数据形状**：

- 你要求模型输出一条结构化结果（一个 JSON 对象），前端 / 下游要按字段取值；
- 你给模型一组工具，工具的参数也是结构化数据；
- 你把用户输入、上下文、记忆塞进模型时，同样要保证它们**形状正确**。

裸 JSON 的问题是：**校验发生在很远的地方**。字段缺失、类型错、枚举写错、两个字段互相矛盾——这些错误往往到运行中、下游、甚至数据库写入时才炸。

Pydantic v2 的定位就是：**把「数据形状」变成可执行的类型契约**，并提供三件套——

| 能力 | 解决的问题 |
|---|---|
| **校验（Validation）** | 外部数据进来（模型输出、HTTP 请求、配置文件）时，第一时间判定是否合法，并给出结构化错误 |
| **序列化（Serialization）** | 内部 Python 对象出去（写库、发响应）时，稳定地变成 dict / JSON |
| **JSON Schema** | 把契约**提前告诉 LLM**：模型的输出必须满足这份 schema，工具的入参必须满足这份 schema |

> Pydantic AI 建立在这三件套之上。可以说：**不懂 Pydantic v2，就无法真正读懂 Pydantic AI 的 `output_type`、tools、`RunContext` 类型**。本章就是地基。

---

## 4. 核心概念

### 4.1 心智模型：模型 = 契约，实例 = 已验证的数据

```text
外部数据（JSON / dict / 环境变量）
        │
        ▼  model_validate(...)  ── 校验 + 类型转换 + 归一化
   ┌─────────────┐
   │  BaseModel  │   ← 类型契约（字段名、类型、约束、跨字段规则）
   └─────────────┘
        │
        ▼  model_dump() / model_dump_json()  ── 序列化
内部 Python 对象（下游要的 dict / JSON 文本）
```

**关键点**：Pydantic 不是「校验就完事了」，它默认还会做**类型转换（coercion）**——`"42"` 会被转成 `42`，`{"id": 1}` 会被提升成 `User(id=1)`。你拿到的一定是**已验证且已归一化**的对象。

### 4.2 校验生命周期：`before → 核心类型校验 → after`

理解这个顺序，你就能决定校验器写在哪一层：

```text
原始输入
  │
  ├─(1) field_validator(mode="before") / BeforeValidator    ← 拿到原始值，可做归一化（去空白、小写）
  │
  ├─(2) 核心类型校验                                          ← int 必须是 int、"low" 必须在 Literal 里、min_length 等
  │
  ├─(3) field_validator(mode="after") / AfterValidator       ← 拿到已转成目标类型的值，可再做变换
  │
  └─(4) model_validator(mode="after")                        ← 所有字段就绪，做跨字段交叉校验
```

> 注意：`mode="after"` 的**模型级**校验器，只有在**所有字段都通过核心校验、模型能被构造**时才会运行。若某字段本身非法（例如 `priority="urgent"`），模型校验器根本不会执行——这也是排错时容易误解的一点。

### 4.3 Pydantic AI 中「类型即契约」的三个落点

| 落点 | 你用 Pydantic 写什么 | 框架拿它做什么 |
|---|---|---|
| **输出类型** `Agent(output_type=...)` | 一个 `BaseModel`（或联合 / 列表） | `model_json_schema()` 转成模型的输出约束，返回后 `model_validate*` 校验并给你**类型安全的对象** |
| **工具参数** `@agent.tool` | 函数的类型注解（常包含 `BaseModel` 参数） | 生成工具参数的 JSON Schema 发给模型，模型按 schema 生成参数，框架校验后再调用你的函数 |
| **依赖注入** `Agent(deps_type=...)` | 一个任意类型（常是 `BaseModel` 或 `dataclass`） | `RunContext[DepsT]` 全程类型安全，工具/指令里能静态检查出字段名拼写错误 |

记住这张表：**你在 Pydantic 里定义一次形状，框架在「告诉模型」「校验模型」「给你的代码」三处复用同一份契约。**

---

## 5. 最小可运行示例（完整代码 + 逐行讲解）

下面的代码与 [`labs/part_1/ch_1_3.py`](../labs/part_1/ch_1_3.py) 完全一致。它用「工单（Ticket）」这一个贴近真实业务的模型集合，把本章所有特性串了一遍。

```python
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
```

### 逐行讲解（按功能分组）

**A. 类型别名与注解式校验器**

- `Priority = Literal["low", "medium", "high"]`：枚举不必用 `Enum`，`Literal` 更轻，且会**直接映射为 JSON Schema 的 `enum`**（见 §6.6），模型一眼就能看懂取值范围。
- `normalize_label` / `slugify` 是**普通函数**。它们不继承任何东西，可以被到处复用——这正是注解式校验器的价值：把「归一化规则」和「字段声明」放在一起，而不是散落成一堆方法。

**B. `User`：嵌套模型**

- `model_config = ConfigDict(frozen=True)`：实例**不可变**，且可哈希，所以能安全地当 `watchers` 的字典值、放进 `set`。
- `Field(ge=1)`、`Field(min_length=1, max_length=50)`、`Field(description=...)`：约束 + 文档。`description` 不只给人看——它会进入 JSON Schema，**成为交给模型的字段说明**。

**C. `Comment` / `StatusChange` + 判别式联合**

- 两个模型都带 `kind: Literal[...]`。
- `TicketEvent = Annotated[Comment | StatusChange, Field(discriminator="kind")]`：告诉 pydantic「先看 `kind` 字段，再决定用哪个分支校验」。相比普通 `A | B` 联合，判别式联合**更快、报错更准**（会报 `union_tag_invalid` 并指出非法 tag），并且生成带 `discriminator` 的 JSON Schema。

**D. `Ticket`：字段约束与 `model_config`**

- `extra="forbid"`：多余字段直接报 `extra_forbidden`，避免上游拼写错误被静默忽略。
- `str_strip_whitespace=True`：所有字符串字段自动去首尾空白（在核心校验阶段生效）。
- `assignee: User | None = None`：**可选字段**的标准写法，`None` 是默认值。
- `Field(default_factory=list)`：**可变默认值必须用 `default_factory`**，否则会踩「所有实例共享同一个 list」的坑。
- `Annotated[int, Field(ge=0, le=100)]`：把约束写进类型本身，字段声明处只留默认值，读起来更清爽。
- `external_label: Annotated[str, BeforeValidator(normalize_label)]`：在类型校验**之前**归一化。
- `slug: Annotated[str, AfterValidator(slugify)]`：在类型校验**之后**变换。

**E. 两种「方法式」校验器**

- `@field_validator("tags")`：只拿到 `tags` 一个字段，负责**清洗**（小写、去空白、去重、保序）。
- `@model_validator(mode="after")`：拿到**整个已构造的 `Ticket`**，做**跨字段**业务规则（high 必须有 assignee）。校验不通过时 `raise ValueError`，pydantic 会把它包成 `ValidationError`，`loc` 为空元组 `()`。

**F. `TypeAdapter(list[Ticket])`**

- `list[Ticket]` 不是 `BaseModel`，没有 `model_validate` / `model_json_schema`。`TypeAdapter` 把**任意类型**包装成「有校验/序列化/schema 能力」的对象。

**G. `main()`**

按顺序演示 6 件事：成功校验 → 序列化 → 往返一致 → JSON Schema → 失败校验（字段级 + 模型级）→ `TypeAdapter`。

### 运行与关键输出

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_3.py
```

关键输出节选：

```text
priority='high'  tags=['bug', 'urgent', '支付']                 # field_validator 已规范化
external_label='jira-1024'  slug='payment-callback-timeout'     # before / after 校验器
event types=['Comment', 'StatusChange']                         # 判别式联合
往返一致：True                                                   # model_validate_json(model_dump_json())

共 4 个错误：
  loc=('id',)         type=greater_than_equal
  loc=('title',)      type=string_too_short
  loc=('priority',)   type=literal_error
  loc=('unexpected',) type=extra_forbidden
5b) loc=()            type=value_error                          # high 却无 assignee（模型级交叉校验）
```

请注意两个细节：

1. `errors()` 里**每个错误都带 `loc`（出错位置）和 `type`（错误类型）**——这是你能写出精确断言的关键。
2. `5)` 中 `priority="urgent"` 已经非法，所以**模型级校验器没有机会执行**；`5b)` 单独构造了一个「字段都合法、但跨字段矛盾」的用例，才触发 `value_error`。

---

## 6. 深入剖析

### 6.1 `BaseModel`：字段声明、默认值、可选字段、`model_config`

字段声明就是「**名字: 类型 = 默认值**」。没有默认值的字段即 **required**。

| 场景 | 写法 | 说明 |
|---|---|---|
| 必填字段 | `name: str` | 缺少即报 `missing` |
| 有默认值 | `priority: str = "medium"` | 缺省时用默认值 |
| 可变默认值 | `tags: list[str] = Field(default_factory=list)` | **禁止**直接写 `= []` |
| 可选字段（可为 `None`） | `assignee: User \| None = None` | 类型含 `None` 且默认 `None` |
| 有默认值的可选字段 | `limit: int = Field(default=10)` | 类型不含 `None`，但可省略 |

`model_config`（用 `ConfigDict` 声明）是**模型级开关**，工程里最常用的几个：

| 配置 | 默认 | 作用 |
|---|---|---|
| `extra` | `"ignore"` | `"forbid"`：拒绝未知字段；`"allow"`：保留未知字段 |
| `str_strip_whitespace` | `False` | `True`：所有 `str` 自动去首尾空白 |
| `frozen` | `False` | `True`：实例不可变、可哈希 |
| `validate_assignment` | `False` | `True`：`obj.field = x` 时也校验 |
| `populate_by_name` | `False` | 允许用字段名（而非仅 alias）填充 |
| `from_attributes` | `False` | `True`：可从任意对象属性（如 ORM 对象）构造 |
| `use_enum_values` | `False` | `True`：把 `Enum` 存为它的 value |
| `strict` | `False` | `True`：关闭宽松类型转换（`"1"` 不再变 `1`） |

> 工程建议：**对外部输入（模型输出、HTTP body、配置）用 `extra="forbid"`**，让拼写错误立刻暴露；对内构造的宽松场景保持默认即可。

`Field(...)` 的常用参数（约束项会进入 JSON Schema）：

| 类别 | 参数 | 示例 |
|---|---|---|
| 数值 | `gt` / `ge` / `lt` / `le` / `multiple_of` | `Field(ge=1, le=100)` |
| 字符串 | `min_length` / `max_length` / `pattern` | `Field(min_length=3, pattern=r"^[a-z0-9-]+$")` |
| 容器 | `min_length` / `max_length` | `Field(min_length=1)` |
| 元数据 | `description` / `title` / `examples` | `Field(description="优先级")` |
| 别名 | `alias` / `validation_alias` / `serialization_alias` | `Field(alias="ticket_id")` |
| 默认 | `default` / `default_factory` | `Field(default_factory=list)` |

### 6.2 四种校验形态：怎么选

| 形态 | 作用范围 | 拿到什么值 | 典型用途 |
|---|---|---|---|
| `field_validator(mode="before")` | 单字段 | 原始输入 | 归一化（去空白、小写、解析字符串） |
| `field_validator(mode="after")`（默认） | 单字段 | 已转成目标类型的值 | 值级约束（去重、格式检查） |
| `model_validator(mode="after")` | 整个模型 | 已构造的模型实例 | **跨字段**规则（A 与 B 必须一致） |
| `model_validator(mode="before")` | 整个模型 | 原始输入 dict | 结构改写（补字段、兼容旧格式） |

**注解式校验器**（`Annotated[T, BeforeValidator(fn)]` / `AfterValidator(fn)`）与上面等价，但：

- 把逻辑写成**可复用函数**，而不是绑定到某个类的方法；
- 更适合放进**共享模块 / 类型库**里复用。

`field_validator` 的签名要点：

```python
@field_validator("a", "b")            # 可同时作用于多个字段
@classmethod                          # v2 要求显式 @classmethod（可写在 @field_validator 之上或之下）
def check(cls, v): ...                # 只接收字段值；mode="before" 时加 info 参数可拿 context
```

> 若校验器需要访问其他字段或上下文，请用 `model_validator(mode="after")` + `self`，或 `field_validator` 的 `ValidationInfo` 参数（`info.data` / `info.context`）。

### 6.3 嵌套模型、容器与联合

| 类型写法 | 行为 | JSON Schema 表现 |
|---|---|---|
| `User`（嵌套） | dict 自动提升为 `User` | `$ref: "#/$defs/User"` |
| `User \| None` | 可空；`null` 合法 | `anyOf: [{$ref}, {type: null}]` |
| `list[User]` | 逐元素校验 | `type: array, items: {$ref}` |
| `dict[str, User]` | 逐值校验（key 恒为 str） | `type: object, additionalProperties: {$ref}` |
| `User \| Admin`（普通联合） | **依次尝试**各分支 | `anyOf` |
| `Annotated[User \| Admin, Field(discriminator="kind")]` | 按 tag **直接定位**分支 | `oneOf` + `discriminator` |

**判别式联合的三条规则**（踩坑高发区）：

1. 每个分支必须声明**同一个判别字段**（本章是 `kind`），且其类型为 `Literal[...]`（各分支取值互斥）。
2. 判别的字段名用 `Field(discriminator="kind")` 指定（不是 `Literal` 字段的名字）。
3. 从 dict 构造时报错为 `union_tag_invalid`（tag 非法）或 `union_tag_not_found`（缺 tag）。**给判别字段设默认值不会让缺 tag 的输入自动通过**——想做默认分支请用 `Tag`/`Discriminator` 的高级写法，或显式补全。

### 6.4 序列化与反序列化 API

| 方法 | 方向 | 产出 | 常用参数 |
|---|---|---|---|
| `model_dump()` | 对象 → Python 结构 | `dict`（值可为 datetime 等对象） | `mode="json"`、`exclude`、`include`、`exclude_none`、`by_alias` |
| `model_dump_json()` | 对象 → JSON 文本 | `str` | `indent`、`exclude_none`、`by_alias` |
| `model_validate(obj)` | Python 结构 → 对象 | 模型实例 | `strict`、`context` |
| `model_validate_json(s)` | JSON 文本 → 对象 | 模型实例 | `strict`、`context` |
| `model_validate_strings(obj)` | 字符串值 → 对象 | 模型实例 | 处理「数字也是字符串」的输入 |

**`mode="python"` vs `mode="json"`**：前者保留 Python 原生类型（`datetime` 仍是 `datetime`），后者转成 JSON 可编码的类型（`datetime` → ISO 字符串）。`model_dump_json()` 等价于 `mode="json"` 再序列化。

**往返一致性（roundtrip）**是序列化的黄金准则：

```python
assert Ticket.model_validate(ticket.model_dump()) == ticket
assert Ticket.model_validate_json(ticket.model_dump_json()) == ticket
```

**自定义序列化器**用 `@field_serializer`：

```python
from datetime import datetime
from pydantic import field_serializer

class Event(BaseModel):
    at: datetime

    @field_serializer("at")
    def _ser_at(self, value: datetime, _info) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")
```

配套地，`@model_serializer` 可自定义**整个模型**的序列化输出，`@computed_field` 可添加「只出现在输出里、不参与输入校验」的计算字段（例如 `@computed_field def is_overdue(self) -> bool:`）。

### 6.5 `TypeAdapter`：给非模型类型一份能力

模型类自带 `model_validate` / `model_dump` / `model_json_schema`，但 `list[Ticket]`、`dict[str, User]`、`tuple[int, str]` **没有**。`TypeAdapter` 补上：

```python
from pydantic import TypeAdapter

adapter = TypeAdapter(list[Ticket])
adapter.validate_python([{"id": 1, "title": "abc"}])   # → list[Ticket]
adapter.validate_json('[{"id": 1, "title": "abc"}]')    # 从 JSON 文本
adapter.dump_python([ticket])                           # → list[dict]
adapter.dump_json([ticket])                             # → bytes
adapter.json_schema()                                   # → {"type": "array", "items": {...}}
```

**工程用途**：批量接口的入参/出参、给 Pydantic AI 传「元素是模型的列表」作为 `output_type`、为工具函数返回 `list[Model]` 提供 schema。

> 复用同一份 `TypeAdapter` 实例即可缓存 schema 与校验器，性能更好——所以 labs 把 `TICKET_LIST_ADAPTER` 提为模块级常量。

### 6.6 JSON Schema：LLM 结构化输出的契约

`Ticket.model_json_schema()` 会产出标准 JSON Schema。本章示例的关键片段（节选）：

```json
{
  "type": "object",
  "required": ["id", "title"],
  "additionalProperties": false,
  "properties": {
    "priority": { "type": "string", "enum": ["low", "medium", "high"], "default": "medium" },
    "assignee": { "anyOf": [{ "$ref": "#/$defs/User" }, { "type": "null" }], "default": null },
    "events": {
      "type": "array",
      "items": {
        "oneOf": [{ "$ref": "#/$defs/Comment" }, { "$ref": "#/$defs/StatusChange" }],
        "discriminator": { "propertyName": "kind", "mapping": { "comment": "#/$defs/Comment", "status_change": "#/$defs/StatusChange" } }
      }
    }
  },
  "$defs": { "User": { "...": "..." }, "Comment": { "...": "..." }, "StatusChange": { "...": "..." } }
}
```

**为什么它对 LLM 应用至关重要**：

- **给模型看**：把 schema 作为「输出约束」发给模型（native / tool / prompted 模式），模型就能生成符合形状的 JSON；
- **校验模型**：模型返回后用 `model_validate_json` 校验，失败则**重试或报错**；
- **给你的代码**：校验通过后你拿到的就是类型安全的对象，无需 `dict.get(...)` 到处防御。

在 Pydantic AI 中，同一份 schema 出现在：

1. `output_type=SomeModel` → 输出约束的 schema；
2. 工具函数的参数 → `ToolDefinition.parameters_json_schema`；
3. `TypeAdapter(...)` → 用于非模型输出类型。

> 一个关键认知：**你定义的模型既是「运行时校验器」，也是「给模型的说明书」**。字段名、`description`、约束都会影响模型能不能正确填值——所以给字段写 `description` 不是可选项，而是提示工程的一部分。

### 6.7 `ValidationError` 与 `errors()`

校验失败抛 `pydantic.ValidationError`。它的 `errors()` 返回一个 list，每项包含：

| 键 | 含义 | 示例 |
|---|---|---|
| `type` | 机器可读的错误类型 | `"literal_error"`、`"string_too_short"`、`"extra_forbidden"` |
| `loc` | 出错位置（字段路径元组） | `("priority",)`、`("events", 0)`、`()`（模型级） |
| `msg` | 人类可读信息 | `"Input should be 'low', 'medium' or 'high'"` |
| `input` | 触发错误的原始输入 | `"urgent"` |
| `ctx` | 附加上下文（如约束值） | `{"ge": 1}` |
| `url` | 指向文档的说明链接 | pydantic 错误文档 |

其他常用成员：`str(exc)`（多行可读报告）、`exc.error_count()`、`exc.json()`（结构化 JSON）。

**工程用法**：把 `errors()` 里的 `loc` 映射成给模型的「重试提示」。例如 Pydantic AI 会把校验错误回灌给模型，让它修正——这正是「类型即契约 + 自动重试」的闭环。

### 6.8 `pydantic-settings`：从环境变量读配置

应用配置（API Key、DB URL、超时）不该硬编码，而应来自环境变量或 `.env`。`pydantic-settings` 提供 `BaseSettings`，把「校验」这套能力直接用于配置：

```python
# pip install pydantic-settings   （或 uv add pydantic-settings）
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    openai_api_key: str          # 读环境变量 APP_OPENAI_API_KEY
    request_timeout: float = 30
    debug: bool = False

settings = Settings()            # 自动从环境变量 / .env 读取并做类型校验
```

要点：

- **字段名 → 环境变量名**：默认大小写不敏感，`env_prefix` 加前缀；`env_nested_delimiter` 支持嵌套（`APP_DB__HOST`）。
- **类型安全**：`request_timeout` 是 `float`，环境里的 `"30"` 会被转成 `30.0`，非法值会在启动时**立刻**报错（fail-fast）。
- **`.env` 文件**：开发期用 `env_file=".env"` 读取；生产用真实环境变量。

> 本章 labs 不需要 `pydantic-settings`（无外部配置）。理解其思想即可：**配置也是一种「要校验的外部输入」**。

---

## 7. 常见变体与工程实践

1. **`Annotated` 复用约束类型**：把常用约束抽成别名，全局一致。
   ```python
   from typing import Annotated
   from pydantic import Field
   PositiveInt = Annotated[int, Field(ge=1)]
   Tag = Annotated[str, Field(min_length=1, max_length=32)]
   ```
2. **`Enum` 替代 `Literal`**：需要「带描述/别名的取值集合」时用 `Enum`；需要与模型交互、希望 enum 描述进入 schema 时也有对应支持。简单取值集合优先 `Literal`。
3. **`@computed_field`**：派生字段只出现在输出中。
   ```python
   from pydantic import computed_field
   @computed_field
   @property
   def is_high(self) -> bool:
       return self.priority == "high"
   ```
4. **递归模型**：自引用类型（树、评论线程）用 `model_rebuild()` 或 `from __future__ import annotations` 解决前向引用。
5. **宽松输入、严格内部**：入口用默认（宽松，允许 `"1"→1`），核心逻辑可开 `strict`；对外部不可信输入建议 `strict` + `extra="forbid"`。
6. **性能**：模型定义在**模块级**（一次编译）；`TypeAdapter` 也应为常量复用；避免在循环里反复 `TypeAdapter(...)` 或 `create_model`。
7. **不要用 Pydantic 模型做「可变业务实体」**：它是**数据契约**，不是 ORM。可变状态用 dataclass 或普通类更合适。

---

## 8. 练习（含参考答案要点）

**练习 1（基础）** 给 `Ticket` 增加字段 `due_date: datetime | None = None`，要求序列化后的 JSON 是 `"YYYY-MM-DD"` 格式。
> 参考要点：用 `Annotated[datetime | None, ...]` + `@field_serializer("due_date")` 返回 `value.strftime("%Y-%m-%d")`；注意 `None` 时返回 `None`。可用 `DateTime` 或自定义 `BeforeValidator` 解析输入。

**练习 2（校验器）** 让 `tags` 必须**至少 1 个**、且每个标签匹配 `^[a-z0-9-]+$`。
> 参考要点：`Field(min_length=1)` 管数量；用 `AfterValidator` 写正则函数（或 `field_validator` 在归一化后再校验），并对每个元素用 `re.fullmatch`。

**练习 3（交叉校验）** 新增规则：`story_points == 0` 的工单不允许是 `high` 优先级（用 `model_validator`）。
> 参考要点：在 `mode="after"` 校验器里 `if self.story_points == 0 and self.priority == "high": raise ValueError(...)`。断言时检查 `loc == ()` 与 `type == "value_error"`。

**练习 4（判别式联合）** 再增加一个事件类型 `Attachment(kind="attachment", url, size_bytes)`，并让 `events` 支持它。
> 参考要点：新模型带 `kind: Literal["attachment"] = "attachment"`；把联合扩成 `Annotated[Comment | StatusChange | Attachment, Field(discriminator="kind")]`；验证 `model_json_schema()["properties"]["events"]["items"]["discriminator"]["mapping"]` 含 `"attachment"`。

**练习 5（TypeAdapter）** 用 `TypeAdapter(dict[str, Ticket])` 校验一个「按状态分组的工单字典」，并打印其 JSON Schema。
> 参考要点：`adapter = TypeAdapter(dict[str, Ticket])`；`validate_python({...})` 的每个值应提升为 `Ticket`；`json_schema()["additionalProperties"]` 应指向 `#/$defs/Ticket`。

---

## 9. 验收标准

运行以下命令，**全绿即视为本章已掌握**：

```bash
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_3.py -q
```

自测清单（应能不看答案解释）：

- [ ] 能说出 `field_validator` 与 `model_validator` 的适用场景差异；
- [ ] 能解释 `mode="before"` 与 `mode="after"` 的执行时机；
- [ ] 能写出一个通过 `discriminator` 区分的联合类型；
- [ ] 能解释 `model_dump()` 与 `model_dump_json()` 的输出差异；
- [ ] 能用 `errors()` 的 `loc` / `type` 写出精确断言；
- [ ] 能说明 `model_json_schema()` 在 Pydantic AI 中的三处落点。

---

## 10. 常见坑与排错

| 症状 | 原因 | 修复 |
|---|---|---|
| 所有实例共享同一个列表 | 用了 `tags: list[str] = []` | 改用 `default_factory=list` |
| 模型级校验器「没触发」 | 有其他字段未通过核心校验，模型根本没构造出来 | 先修字段级错误；或用 `mode="before"` 在更早阶段处理 |
| 字段名 `python_` 之类冲突 | 字段名与 `BaseModel` 方法重名 | 用 `Field(alias=...)` 或加前导下划线（pydantic 会剥离变量私有名） |
| 未知字段被静默丢弃 | 默认 `extra="ignore"` | 设 `extra="forbid"`，让多余字段报 `extra_forbidden` |
| `User \| None` 却报「不是 User」 | 给了非 dict 的输入，或缺少 `None` 分支 | 确认类型含 `| None`；输入用 dict 而非字符串 |
| 判别式联合报 `union_tag_not_found` | 输入缺 `kind` 字段 | 显式补 `kind`，或改用带默认分支的 `Discriminator` 写法 |
| `model_dump()` 里有 `datetime` 对象，JSON 序列化失败 | 用了 `mode="python"` | 用 `model_dump(mode="json")` 或 `model_dump_json()` |
| 循环导入 / 前向引用报错 | 模型互相引用、类尚未定义 | 用字符串注解、`from __future__ import annotations`，必要时 `model_rebuild()` |
| `TypeAdapter` 每次 temp 构造，性能差 | 在热路径里重复创建 | 提为模块级常量复用 |
| 约束没进 JSON Schema | 用了自定义函数校验（pydantic 无法推断） | 能用 `Field(ge=...)` 等声明式约束的，就别用命令式校验器 |

---

## 11. 面试延伸

**Q1. Pydantic v2 的校验生命周期是怎样的？`before` 和 `after` 各自适合做什么？**
> 答：`before` 在核心类型校验**之前**运行，拿到原始输入，适合归一化/解析（去空白、小写、把字符串解析成结构）；`after` 在核心校验**之后**，拿到已是目标类型的值，适合值级约束与变换。模型级 `mode="after"` 在所有字段就绪后运行，适合跨字段规则。要点：`after` 的模型校验器只在模型能被构造时执行。

**Q2. 为什么 Pydantic AI 用 Pydantic 模型做「契约」？`model_json_schema()` 在其中扮演什么角色？**
> 答：同一份模型定义同时提供三件事——给模型看的 JSON Schema（约束输出/工具参数）、运行时的校验、给业务代码的类型安全对象。`model_json_schema()` 是「把契约转达给模型」的桥梁；校验失败时 `errors()` 又能作为重试提示回灌给模型，形成闭环。

**Q3. 普通联合 `A | B` 和判别式联合 `Annotated[A | B, Field(discriminator="kind")]` 有何区别？各适合什么场景？**
> 答：普通联合会**依次尝试**每个分支（可能报一堆迷惑错误、且慢），适合分支少、形状差异大的情况；判别式联合先读 tag 字段，直接定位分支，报错精确（`union_tag_invalid`），并要求生成 `oneOf + discriminator` 的 schema，适合**标签清晰、面向模型输出**的多分支场景。代价是每个分支必须有同名的 `Literal` 判别字段。

**Q4. 什么时候用 `TypeAdapter`？为什么不应该在热路径里反复创建它？**
> 答：当你的类型**不是** `BaseModel`（如 `list[Model]`、`dict[str, Model]`、元组、基础类型）时，用 `TypeAdapter` 获得校验/序列化/JSON Schema 能力。它会编译并缓存 schema 与校验器，重复创建会重复编译，所以应提为模块级常量。

**Q5. 在 Agent 系统里，你会如何用 Pydantic 处理「模型输出不合法」？**
> 答：把 `output_type` 设为模型（或联合/列表），约束会作为 schema 发给模型；返回后 `model_validate_json` 校验；失败则捕获 `ValidationError`，把 `errors()` 中的 `loc`/`msg` 作为反馈回灌给模型重试（Pydantic AI 的 output retries 即此机制）。同时用 `extra="forbid"`、判别式联合、`description` 提高一次命中率，并设置重试上限避免死循环。

---

## 12. 延伸阅读

- 源码篇：[`../../code_wiki/03-models-providers-profiles.md`](../../code_wiki/03-models-providers-profiles.md) —— 重点看 §9「函数签名 → JSON Schema」与 §1.4 `ModelRequestParameters`：理解 `function_schema()` 如何把函数注解编译成 `ToolDefinition.parameters_json_schema`，以及输出的 schema 如何进入请求参数。
- 源码篇：[`../../code_wiki/04-messages-and-output.md`](../../code_wiki/04-messages-and-output.md) —— 理解 `output_type` 如何被解析为输出 schema、校验失败如何触发重试。
- 官方文档：Pydantic v2 [Models](https://docs.pydantic.dev/latest/concepts/models/)、[Validators](https://docs.pydantic.dev/latest/concepts/validators/)、[Serialization](https://docs.pydantic.dev/latest/concepts/serialization/)、[JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)、[Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)。
- 下一章：[`1.4`](./1-4-anyio-async-and-structure.md)（并发与异步结构，为篇二的 Agent 运行打基础）。
