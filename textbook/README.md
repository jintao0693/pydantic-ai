# 《Pydantic AI 智能体工程实战：从零到可就业》

本教材是一套**体系化课程**，目标是把读者从「会一点 Python」带到「能独立交付生产级 AI Agent、并通过 Agent 方向岗位面试」。

它**不是 API 手册**。每章都是「为什么 → 怎么做 → 亲手跑 → 自测验收 → 面试延伸」的闭环，配套**可运行 labs 与验收测试**。

> 与 `code_wiki/`（源码篇）的关系：源码篇回答「框架内部怎么实现」，本教材回答「工程上怎么用、怎么交付、怎么被录用」。每章末尾都给出对应的源码篇章节，便于双向查阅。

---

## 一、读者与学习路径

本教材同时服务三类读者，请按你的起点选择路径：

| 路径 | 起点 | 建议读法 |
|------|------|----------|
| **A · 零基础** | 没写过 Python 或只写过一点点 | 篇零 → 篇一 → 篇二 → 篇三 → 篇四 → 篇五 → 篇六 → 篇七（全量，约 34 章） |
| **B · 有 Python 基础，零 LLM 应用经验（主线）** | 会语法、写过后端/脚本 | 跳过篇零，从篇一开始（约 31 章） |
| **C · 有 LLM/Agent 使用经验，想进阶就业** | 用过 LangChain/裸 API | 篇一速览 → 重点篇三/篇四/篇五/篇六/篇七 |

**就业标准（结业定义）**：完成篇七的三个 Capstone，并通过 `textbook/labs` 全部验收测试 + 篇七面试题库自测 ≥ 80% 正确率。

---

## 二、课程结构（8 篇 · 34 章）

### 篇零 · 零基础补课（可选，3 章）
| 章 | 标题 | 配套 labs |
|----|------|-----------|
| 0.1 | Python 关键语法速成 | `labs/part_0/ch_0_1.py` |
| 0.2 | 异步、类型系统与虚拟环境 | `labs/part_0/ch_0_2.py` |
| 0.3 | 命令行与 Git 基础 | `labs/part_0/ch_0_3.py` |

### 篇一 · 入门与工程基座（3 章）
| 章 | 标题 | 配套 labs |
|----|------|-----------|
| 1.1 | 智能体心智模型：LLM、上下文、工具调用、ReAct、成本与延迟 | `labs/part_1/ch_1_1.py` |
| 1.2 | 工程基座：uv、类型系统、anyio、pytest | `labs/part_1/ch_1_2.py` |
| 1.3 | Pydantic v2 精要：模型、校验、序列化、JSON Schema、DI 基础 | `labs/part_1/ch_1_3.py` |

### 篇二 · Pydantic AI 核心（6 章）
| 章 | 标题 |
|----|------|
| 2.1 | 第一个 Agent：run / run_sync / stream 与结构化输出 |
| 2.2 | 消息协议与历史：ModelMessage、parts、历史维护 |
| 2.3 | 工具与 Toolset：定义、校验、执行、重试、组合 |
| 2.4 | 输出模式：text / tool / native / prompted / image 与校验器、重试 |
| 2.5 | 依赖注入与 RunContext：状态、上下文、取消、事件 |
| 2.6 | 模型 / Provider / Profile：多模型切换与能力探测 |

### 篇三 · 进阶能力（5 章）
| 章 | 标题 |
|----|------|
| 3.1 | Capabilities 与 Hooks：横切行为的组合与顺序 |
| 3.2 | 延迟工具 / 人工审批 / Tool Search / defer_loading |
| 3.3 | 流式与事件流：增量、事件、SSE 与 AG-UI |
| 3.4 | 可观测性：Logfire / OTel、用量与成本、span 语义 |
| 3.5 | 并发与超时：max_concurrency、tool_timeout、取消 |

### 篇四 · 质量与评估（3 章）
| 章 | 标题 |
|----|------|
| 4.1 | 测试策略：TestModel / FunctionModel / VCR / blockbuster |
| 4.2 | Pydantic Evals：数据集、评估器、报告 |
| 4.3 | 评估驱动开发与在线评估 |

### 篇五 · 生产化（4 章）
| 章 | 标题 |
|----|------|
| 5.1 | Durable Execution：Temporal / DBOS / Prefect |
| 5.2 | 持久化、恢复与分叉（step persistence） |
| 5.3 | UI 适配与 Realtime：AG-UI / Vercel AI / Web / 语音 |
| 5.4 | 安全与治理：SSRF、提示注入、Guardrails、预算 |

### 篇六 · Harness 与编码 Agent（6 章）
| 章 | 标题 |
|----|------|
| 6.1 | Harness 架构：Capability 组合与惰性导入 |
| 6.2 | Coder / Researcher 完整栈 |
| 6.3 | Workspace 与沙箱：local / modal / e2b / sprites / bubblewrap / ssh |
| 6.4 | 文件系统 / Shell / RepoContext / SubAgents / Planning |
| 6.5 | 上下文管理：compaction / tool output limits / memory / skills |
| 6.6 | 从零构建一个 Claude Code 类编码 Agent |

### 篇七 · 项目与就业（4 章）
| 章 | 标题 |
|----|------|
| 7.1 | Capstone A：可评估的客服 / 研究 Agent |
| 7.2 | Capstone B：编码 Agent（CLI + harness 插件 + 工具治理） |
| 7.3 | Capstone C：可持久化 / 可观测 / 可回归的生产 Agent |
| 7.4 | 面试题库与技能清单 |

---

## 三、每章统一体例

每章固定包含以下小节，便于教学与自测：

1. **本课目标** — 3–6 条可检验的学习目标
2. **前置知识** — 需要先掌握的内容
3. **为什么需要它** — 问题场景与动机
4. **核心概念** — 图解 / 表格 / 心智模型
5. **最小可运行示例** — 完整代码 + 逐行讲解
6. **深入剖析** — 关键 API 签名、参数表、默认值、行为
7. **常见变体与工程实践**
8. **练习** — 3–5 题，难度递增，附参考答案要点
9. **验收标准** — 可执行的自测清单 / 断言
10. **常见坑与排错**
11. **面试延伸** — 3–5 问，含答题要点
12. **延伸阅读** — 对应 `code_wiki/` 源码篇章节

---

## 四、环境准备

需要 **Python ≥ 3.11** 与 **uv**（`uv` 同时管理解释器与依赖）。

```bash
# 安装 uv（任选其一）
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或： pipx install uv

# 校验
uv --version
python3 --version
```

本教材的 labs **无需任何 API Key**：所有示例默认使用 `TestModel` / `FunctionModel` 离线运行。需要真实模型时，再设置对应 env（如 `OPENAI_API_KEY`）。

---

## 五、如何运行 labs 与验收测试

labs 位于 [`textbook/labs/`](labs/)，每章一个示例模块 `ch_x_y.py` 与一个验收测试 `test_ch_x_y.py`。

> labs 与仓库主 workspace 隔离，使用 `uv run --no-project` 以避免与 pydantic-ai 仓库的依赖发生冲突。

```bash
# 运行某一章的验收测试
cd textbook/labs
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_1.py -q

# 运行全部验收测试
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest -q

# 直接运行某一章的示例
uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_1.py
```

**验收标准**：所有 `test_*.py` 全绿，即可认为对应章节「已掌握」。

---

## 六、交付进度

| 批次 | 范围 | 状态 |
|------|------|------|
| Batch 1 | 篇零 + 篇一（6 章） | ✅ 已交付（labs 47 项验收测试全绿，已发布到飞书「教材篇」） |
| Batch 2 | 篇二（6 章） | ✅ 已交付（labs 全绿，已发布到飞书「教材篇 › 篇二」） |
| Batch 3 | 篇三（5 章） | 🔜 下一批 |
| Batch 4 | 篇四 + 篇五（7 章） | 待交付 |
| Batch 5 | 篇六（6 章） | 待交付 |
| Batch 6 | 篇七（4 章） | 待交付 |

---

## 七、目录约定

```
textbook/
├── README.md                     # 本文件：大纲、学习路径、如何跑 labs
├── part-0-preliminaries/         # 篇零 · 零基础补课
├── part-1-foundations/           # 篇一 · 入门与工程基座
├── part-2-core/                  # 篇二 · Pydantic AI 核心
├── part-3-advanced/              # 篇三 · 进阶能力
├── part-4-quality/               # 篇四 · 质量与评估
├── part-5-production/            # 篇五 · 生产化
├── part-6-harness/               # 篇六 · Harness 与编码 Agent
├── part-7-capstone/              # 篇七 · 项目与就业
└── labs/                         # 可运行示例与验收测试
    ├── README.md
    ├── pyproject.toml
    └── part_N/
        ├── ch_N_M.py
        └── test_ch_N_M.py
```
