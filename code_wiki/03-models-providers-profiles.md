# 03 · 模型 / Provider / Profile

本篇覆盖模型访问层：如何把 `'openai:gpt-4o'` 这样的字符串解析成具体模型实例，请求参数（工具 / 输出 / 指令 / 思考 / 缓存）如何在基类被归一化，以及规范化请求如何映射到 provider 的线上格式。

相关目录：[models/](../pydantic_ai_slim/pydantic_ai/models/)、[providers/](../pydantic_ai_slim/pydantic_ai/providers/)、[profiles/](../pydantic_ai_slim/pydantic_ai/profiles/)、[embeddings/](../pydantic_ai_slim/pydantic_ai/embeddings/)，以及 [settings.py](../pydantic_ai_slim/pydantic_ai/settings.py)、[usage.py](../pydantic_ai_slim/pydantic_ai/usage.py)。

> 阅读顺序建议：先看 §1（`Model` 基类的调用契约），再看 §2（字符串 → 实例）、§3/§4（Provider / Profile 两层能力事实），最后看 §5–§8（设置、用量、嵌入、包装模型）与 §9（签名编译）。

---

## 1. `models/` — 模型适配层

### 1.1 目录构成与分类

`models/` 下的模块可分三类：

| 类别 | 模块 | 关键类 / 入口 |
|------|------|----------------|
| **provider 适配器** | `openai.py` | `OpenAIChatModel`、`OpenAIResponsesModel`、`OpenAIChatModelSettings`、`OpenAIResponsesModelSettings` |
| | `anthropic.py` / `google.py` / `bedrock.py` / `bedrock_mantle.py` / `groq.py` / `mistral.py` / `cohere.py` / `xai.py` | `AnthropicModel`、`GoogleModel`、`BedrockConverseModel`、`BedrockMantleChatModel` / `BedrockMantleResponsesModel`、`GroqModel`、`MistralModel`、`CohereModel`、`XaiModel` |
| | `cerebras.py` / `crusoe.py` / `huggingface.py` / `ollama.py` / `openrouter.py` / `snowflake.py` / `zai.py` / `github_copilot.py` / `openai_codex.py` | `CerebrasModel`、`CrusoeModel`、`HuggingFaceModel`、`OllamaModel`、`OpenRouterModel`、`SnowflakeModel`、`ZaiModel`、`GitHubCopilotModel`、`OpenAICodexModel` |
| | `system_one.py` / `typesafe.py` / `mcp_sampling.py` / `decision.py` | `SystemOneModel`、`TypeSafeModel`、MCP 采样、决策模型 |
| **非 provider 模型** | `function.py` | `FunctionModel`（本地函数驱动的测试替身） |
| | `test.py` | `TestModel`、`TestStreamedResponse` |
| | `wrapper.py` | `WrapperModel`（所有包装模型的基类） |
| | `fallback.py` | `FallbackModel`、`ResponseRejected` |
| | `concurrency.py` | `ConcurrencyLimitedModel`、`limit_model_concurrency` |
| | `instrumented.py` | `InstrumentedModel`、`InstrumentationSettings`、`instrument_model` |
| **共享内部** | `__init__.py` | `Model`、`ModelRequestParameters`、`StreamedResponse`、`CompletedStreamedResponse`、`infer_model`、`parse_model_id`、`resolve_request_tools`、`prepare_return_schemas`、`download_item`、`ALLOW_MODEL_REQUESTS` |
| | `_abstract.py` | `AbstractModel` |
| | `_known_model_names.py` | `KnownModelName`（巨型 `Literal`）、`KnownEmbeddingModelName` 等 |
| | `_continuation.py` | `merge_responses`、`merge_mode`、`_ContinuationStreamedResponse`、`cancel_suspended_job` |
| | `_prompt_cache.py` | `snap_cache_retention`、`snap_cache_setting`、`split_cache_setting`、`excess_cache_points` |
| | `_tool_choice.py` | `resolve_tool_choice`、`support_tool_forcing`、`tool_forcing_unavailable_reason` |
| | `_reasoning_details.py` / `_decode_errors.py` | 推理细节与错误解码辅助 |
| | `_anthropic_containers.py` / `_anthropic_bedrock_count_tokens.py` | Anthropic 专用、后台较长的辅助（见 `models/AGENTS.md`） |

### 1.2 `AbstractModel`

[models/_abstract.py#L18-L99](../pydantic_ai_slim/pydantic_ai/models/_abstract.py#L18-L99)。`Model` 与 `RealtimeModel` 共享的最小身份（后者在 `pydantic_ai.realtime`，不是 `Model` 子类）。

| 成员 | 类型 | 说明 |
|------|------|------|
| `model_name` | 抽象 `property -> str` | 模型名 |
| `system` | 抽象 `property -> str` | provider 名，流入 OTel 的 `gen_ai.system` 语义属性 |
| `base_url` | `property -> str \| None` | 默认 `None` |
| `model_id` | `property -> str` | `f'{self.system}:{self.model_name}'` |
| `context_window` | `property -> int \| None` | 模型一次可处理的 input+output token 上限；wrapper 报告被包装者的，`FallbackModel` 取候选中的最小值 |
| `label` | `property -> str` | 人类可读展示名，处理 `gpt-5 -> GPT 5`、`claude-sonnet-4-5 -> Claude Sonnet 4.5`、OpenRouter 风格 `meta-llama/llama-3-70b -> Llama 3 70b` |
| `__aenter__` / `__aexit__` | 异步上下文 | 默认无操作，子类可管理客户端生命周期 |

### 1.3 `Model`（`models/__init__.py#L470`）

`class Model(AbstractModel, Generic[InterfaceClient])`。

#### 类级能力标志（供 `__init_subclass__` 之后由适配器声明）

| ClassVar | 默认 | 语义 |
|---|---|---|
| `supported_tool_deferral_modes: frozenset[ToolDeferralMode]` | `frozenset()` | 适配器渲染器实现的 `tool_deferral_mode` 取值 |
| `supported_tool_addition_modes: frozenset[ToolAdditionMode]` | `frozenset()` | 适配器渲染器实现的 `tool_addition_mode` 取值 |
| `compaction_requires_encrypted_content: bool` | `False` | 该 API 是否只认携带加密内容的 `CompactionPart` |
| `compaction_retains_standing_prompt: bool` | `False` | 压缩项是否继续承担窗口的前导 system 项 |

`tool_deferral_mode` / `tool_addition_mode` 两个 property 做的是**交集**：`profile` 声明某个 mode 时，只有适配器在 `supported_*` 里声明了它才会生效（`models/__init__.py#L670-L680`）。因此声明为空的 `Model` 子类永远不会把工具解析成自己无法渲染的线上形状。

#### 实例字段与方法签名

| 成员 | 签名 / 类型 | 说明 |
|------|-------------|------|
| `_provider` | `Provider[InterfaceClient]` | 实例属性；`provider` property 用 `getattr` 读取，无则 `None` |
| `_profile` | `ModelProfileSpec \| None` | 用户 `profile=` 参数 |
| `_settings` | `ModelSettings \| None` | 默认设置 |
| `__init__` | `(*, settings=None, profile=None)` | 调用 `preload_pricing_data()` |
| `settings` | `property -> ModelSettings \| None` | |
| `request`（唯一 `@abstractmethod`） | `async (messages, model_settings, model_request_parameters) -> ModelResponse` | 由 `_agent_graph.ModelRequestNode._make_request(...)` 调用 |
| `count_tokens` | `async (messages, model_settings, model_request_parameters) -> RequestUsage` | 默认抛 `NotImplementedError`；实现后支持 `UsageLimits.count_tokens_before_request` |
| `compact_messages` | `async (request_context: ModelRequestContext, *, instructions=None) -> ModelResponse` | 可选，仅部分 provider（如 OpenAI Responses）支持 |
| `request_stream` | `@asynccontextmanager async (messages, model_settings, model_request_parameters, run_context=None) -> AsyncGenerator[StreamedResponse]` | 默认抛错 |
| `cancel_suspended_response` | `async (response: ModelResponse) -> None` | 默认 no-op |
| `continuation_delay` | `(response: ModelResponse) -> float \| None` | 默认 `None`（立即续跑）；后台轮询型模型返回轮询间隔 |
| `customize_request_parameters` | `(params: ModelRequestParameters) -> ModelRequestParameters` | 基类应用 profile 的 `json_schema_transformer` 到 function/output 工具与 output_object |
| `prepare_request` | `(model_settings, model_request_parameters) -> tuple[ModelSettings \| None, ModelRequestParameters]` | 见 §1.3.1 |
| `prepare_messages` | `(messages, model_request_parameters=None) -> list[ModelMessage]` | 见 §1.3.2 |
| `profile` | `@cached_property -> ModelProfile` | 见 §1.3.3 |
| `context_window` | `property -> int \| None` | `self.profile.get('context_window')` |
| `resolve_cache_retention` | `(model_settings) -> timedelta \| None` | 合并默认与请求设置后取最长保留档；多档触发时 1h > 30m > 5m |
| `supported_native_tools` | `@classmethod -> frozenset[type[AbstractNativeTool]]` | 默认空集；子类必须显式声明 |

`__init_subclass__` 会把仍覆写已废弃 `resolve_prompt_cache_retention` 的子类路由到新名（`models/__init__.py#L550`）。

#### 1.3.1 `prepare_request` 的调用链（`models/__init__.py#L847-L948`）

这是所有适配器 `request()` 开头都会调用的归一化入口：

1. `model_settings = merge_model_settings(self.settings, model_settings)`（后者优先）。
2. `params = self.customize_request_parameters(model_request_parameters)`。
3. `params = prepare_return_schemas(params, supports_tool_return_schema=profile['supports_tool_return_schema'])`：不支持的模型把 `return_schema` 注入工具 `description`。
4. 解析统一 `thinking`，写入 `params.thinking` 并从 `model_settings` 剥离；`False` 只在 `thinking_always_enabled` 为假时生效。
5. `_resolve_cache(...)` 把统一 `cache` 解析进 `params.cache` 并剥离。
6. 去重 native tools（按 `unique_id`）。
7. `params.with_default_output_mode(self._default_structured_output_mode(...))`：`output_mode == 'auto'` 时按 profile 的 `default_structured_output_mode` 落定；若 Tool Output 会阻止 thinking 则改用 `native`。
8. 清理与 output_mode 不匹配的字段（`output_tools` / `output_object` / `prompted_output_template`）。
9. 需要时补默认 `prompted_output_template`（`DEFAULT_PROMPTED_OUTPUT_TEMPLATE`）。
10. 把 `prompted_output_instructions` 追加为最后一个 `InstructionPart`（`InstructionPart.sorted`）。
11. 校验能力支持：`native`/`tool`/text/image 与 profile 不符时抛 `UserError`。
12. 解析延迟工具可见性：有 native / deferred 工具时调用 `_resolve_request_tools`，否则把 `tool_visibility` 全标记为 `'visible'`。

#### 1.3.2 `prepare_messages`：provider 无关的历史预处理

`models/__init__.py#L950-L1047`。在适配器自己的 message-prep 之前调用，产出同构形状：

- `_convert_speech_parts(...)`：realtime `SpeechPart` → `UserPromptPart` / `TextPart`（`supports_audio_input` 决定是否带音频）。
- `_translate_legacy_tool_reveals(...)`：把旧的伪造 `search_tools` 交换升级到本模型的 reveal 通道（仅当有 native reveal 通道）。
- `ToolAvailabilityDeltaPart` 渲染：能"扣留 schema"的模型走 `_synthesize_tool_availability_delta_messages`，否则走 `_announce_tool_availability_delta_messages`（`_hides_deferred_schemas` 判定）。
- `synthesize_local_tool_search_messages(...)`：把 native `NativeToolSearch*Part` 投影为本地 `ToolSearch*Part` 形状（跨 provider）。
- `_wrap_non_leading_system_prompts(...)`：当 `supports_inline_system_prompts=False` 时，把非前导 `SystemPromptPart` 包成 `<system>...</system>` 的 `UserPromptPart`。

> 注意：`prepare_messages` 返回**新列表**，但保留持久的 message history 不受影响——上层的 `RunContext.messages` 才是权威。参见 `models/AGENTS.md` 里关于"注入位置必须按 message identity 而非长度"的规则。

#### 1.3.3 `profile` 属性的解析顺序（`models/__init__.py#L1132-L1186`）

后层覆盖前层：

1. `DEFAULT_PROFILE`（`ModelProfile` 每个键的基线值）。
2. `provider.model_profile(model_name)`（provider 对该模型的默认）。
3. genai-prices 的 `context_window`（**仅当** provider 层或 partial 用户层没有显式设置该键，含显式 `None`）。
4. 用户 `profile=`：partial dict 合并，或 `Callable[[ModelProfile], ModelProfile]` 完全接管。
5. 与 `self.__class__.supported_native_tools()` 求交集，保证 `profile['supported_native_tools']` 是"实际可用"的唯一真相来源。

```python
from pydantic_ai import Agent
from pydantic_ai.models import infer_model

model = infer_model('openai:gpt-5.6')
print(model.profile['default_structured_output_mode'])  # 'tool'
print(model.context_window)                             # 来自 genai-prices 或 profile
```

### 1.4 `ModelRequestParameters`（`models/__init__.py#L186-L317`）

`@dataclass(repr=False, kw_only=True)`，每次请求的工具 / 输出 / 指令配置。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `function_tools` | `list[ToolDefinition]` | `[]` | 函数工具 |
| `native_tools` | `list[AbstractNativeTool]` | `[]` | provider 原生工具 |
| `tool_visibility` | `dict[str, ToolVisibility] \| None` | `None` | 每个函数工具的线上可见性；`None` 表示未解析（作者态） |
| `revealed_tool_names` | `set[str]`（`repr=False`） | `set()` | 历史已揭示的工具名 |
| `deferred_capability_ids` | `set[str]`（`repr=False`） | `set()` | 运行中延迟加载的 capability id |
| `output_mode` | `OutputMode` | `'text'` | |
| `output_object` | `OutputObjectDefinition \| None` | `None` | |
| `output_tools` | `list[ToolDefinition]` | `[]` | |
| `prompted_output_template` | `str \| Literal[False] \| None` | `None` | |
| `allow_text_output` | `bool` | `True` | |
| `allow_image_output` | `bool` | `False` | |
| `instruction_parts` | `list[InstructionPart] \| None` | `None` | 结构化指令，带 static/dynamic 元数据 |
| `thinking` | `ThinkingLevel \| None` | `None` | 由基类从 `ModelSettings.thinking` 解析 |
| `cache` | `CacheSetting \| None` | `None` | 由基类从 `ModelSettings.cache` 解析 |

`ToolVisibility = Literal['visible', 'deferred', 'withheld', 'via_history']`（`models/__init__.py#L173-L183`）。

关键 helper：

- `visibility_of(tool_name)`：无条目时，`defer_loading` 的函数工具回退 `'withheld'`，其余 `'visible'`；输出工具永远 `'visible'`。
- `tool_defs`（`@cached_property`）：`{name: def}`，合并 function + output 工具。
- `declared_tool_defs` / `declared_function_tools`：过滤掉 `withheld` / `via_history` 后的线上声明集合。
- `prompted_output_instructions`：由 `StructuredTextOutputSchema.build_instructions` 渲染。
- `with_default_output_mode(mode)`：`output_mode == 'auto'` 时落定，同时同步 `allow_text_output`。

`_utils.dataclasses_no_defaults_repr` 作为 `__repr__`，因此未解析时的 `tool_visibility=None` 不会出现在打印里。

### 1.5 `ModelRequestContext` 与解析上下文

| 类 | 位置 | 关键字段 |
|----|------|----------|
| `ModelRequestContext` | `#L325-L415` | `model`、`messages`（独立浅列表）、`model_settings`、`model_request_parameters`、`model_id`（选择令牌）、`streaming`、私有 `_usage_response_ledger` |
| `ModelResolutionContext[DepsT]` | `#L418-L431` | `agent`、`deps`（比 `RunContext` 更窄，因模型解析发生在 run context 拥有模型之前） |
| `ModelSelectionContext[DepsT]` | `#L433-L467` | 继承前者，加 `model`、`run_step`、`prompt`、`messages`、`usage` |

`ModelRequestContext.model_id` 是**选择令牌**（如 `'openai:gpt-5.6-sol'` 或别名），可能与解析后模型的 `model_id` 不同；durable 执行优先携带它，以便 worker 侧重新解析别名。

### 1.6 `StreamedResponse` 与 `CompletedStreamedResponse`

`StreamedResponse(ABC)`（`models/__init__.py#L1231-L1495`）：

| 成员 | 类型 | 说明 |
|------|------|------|
| `model_request_parameters` | `ModelRequestParameters` | 必需，供 `_parts_manager` 类型化提升 |
| `final_result_event` / `provider_response_id` / `provider_details` / `finish_reason` / `state` / `metadata` | `init=False` 字段 | |
| `_event_iterator` / `_usage` / `_cancelled` / `_finished` / `_first_chunk_monotonic` | 私有 | |
| `_parts_manager` | `@cached_property -> ModelResponsePartsManager` | 懒构建 |
| `__aiter__` | `AsyncIterator[ModelResponseStreamEvent]` | 三层包裹：`iterator_with_final_event` → `iterator_with_part_end` → `iterator_with_cancel_guard` |
| `_get_event_iterator` | `@abstractmethod async -> AsyncIterator[...]` | 子类把 vendor 流翻译成规范化事件 |
| `cancel` / `close_stream` / `get_stream_cancel_errors` | async / return type | `cancel()` 先置 `_cancelled`；`close_stream()` 默认抛 `NotImplementedError` |
| `get()` | `-> ModelResponse` | 依据 `state`/`_finished`/`_cancelled` 派生 `'complete' \| 'incomplete' \| 'interrupted' \| 'suspended'` |
| `usage` | `property -> RequestUsage` | 流未耗尽时不是最终用量 |
| `time_to_first_chunk(request_start)` | `(float) -> float \| None` | 首块到达耗时 |

`CompletedStreamedResponse`（`#L1498-L1664`）包装已完成的 `ModelResponse`，用于 `SkipModelRequest` 短路、capability 短路、durable 在执行体内排空真实流等场景。其 `replay_events` 控制回放：

- `False`（默认）：不产生事件。
- `True`：由各 part 合成 `PartStartEvent`（**不**再补 `PartDeltaEvent`，避免重复）。
- `list[ModelResponseStreamEvent]`：直接回放捕获到的真实事件（保留粒度）。

续跑（continuation）相关的 provider 无关逻辑集中在 [_continuation.py](../pydantic_ai_slim/pydantic_ai/models/_continuation.py)：`merge_mode` 返回 `'replace-same-id' | 'replace-new' | 'accumulate'`，`merge_responses` 据此折叠；上限为 `MAX_GENERATION_CONTINUATIONS = 10`（新生成段）与 `MAX_BACKGROUND_POLLS = 1000`（同 id 轮询）。流式版本是 `_ContinuationStreamedResponse`。

---

## 2. 模型字符串 → 具体 `Model`

### 2.1 解析函数

| 函数 | 位置 | 行为 |
|------|------|------|
| `parse_model_id(model)` | `#L1723-L1738` | 在首个 `:` 处 `split(':', maxsplit=1)`，返回 `(provider_name, model_name)`；无前缀返回 `(None, model)` |
| `_suggest_known_model_name(...)` | `#L1741-L1756` | 用 `difflib.get_close_matches` 给出拼写建议 |
| `infer_model_profile(model)` | `#L1777-L1821` | 只解析 provider 不构造模型，返回 `ModelProfile`；未知名返回 `DEFAULT_PROFILE`；补 `context_window`。**不与 `supported_native_tools` 求交**（与 `Model.profile` 不同） |
| `infer_model(model, provider_factory=infer_provider)` | `#L1824-L1965` | 构造具体 `Model` |
| `known_model_names()` | `#L121-L129` | `@cache` 返回 `KnownModelName` 的字面量元组，是枚举已知模型的公开稳定方式 |

### 2.2 `infer_model` 的分派顺序（顺序敏感）

1. 已是 `Model` → 直接返回；`'test'` → `TestModel()`。
2. `parse_model_id`；无 provider 前缀 → 抛 `UserError`（含拼写建议）。
3. `provider_factory(provider_name)`（默认 `infer_provider`）；若用默认且类名未知，先给出拼写建议再抛错。
4. `bedrock-mantle` 特例：按 `bedrock_mantle_model_profile(model_name)['bedrock_mantle_interface']` 选 Chat 或 Responses 模型（并要求 `BedrockMantleProvider`）。
5. **OpenAI 兼容/自有类必须在 `openai` 之前判断**：`openrouter`、`cerebras`、`crusoe`、`snowflake`、`ollama`、`zai`、`github-copilot`、`openai-codex` 各有独立模型类。
6. 其余按 `model_kind` 分派：
   - `('openai', 'openai-responses', 'azure-responses')` → `OpenAIResponsesModel`
   - `('openai-chat', *OpenAIChatCompatibleProvider)` → `OpenAIChatModel`
   - `('google', 'google-cloud')` → `GoogleModel`；`groq`/`cohere`/`mistral`/`anthropic`/`bedrock`/`huggingface`/`xai`/`typesafe` 同理
   - `system-one` → `SystemOneModel`（要求 `SystemOneProvider`）
   - `gateway/<upstream>`：先用 `normalize_gateway_provider` 归一化，再按上游分派

兼容性类型别名（`#L132-L171`）：

```python
OpenAIChatCompatibleProvider = Literal[
    'alibaba', 'azure', 'cerebras', 'crusoe', 'deepseek', 'fireworks', 'github',
    'github-copilot', 'heroku', 'litellm', 'moonshotai', 'nebius', 'ollama',
    'openrouter', 'ovhcloud', 'sambanova', 'snowflake', 'together', 'vercel', 'vllm', 'zai',
]
OpenAIResponsesCompatibleProvider = Literal[
    'azure', 'deepseek', 'fireworks', 'nebius', 'openai-codex',
    'openrouter', 'ovhcloud', 'sambanova', 'together',
]
```

### 2.3 `KnownModelName`

[models/_known_model_names.py](../pydantic_ai_slim/pydantic_ai/models/_known_model_names.py) 用一个 `TypeAliasType('KnownModelName', Literal[...])` 承载数百个 `'provider:model'` 字面量（`anthropic:claude-*`、`bedrock:*`、`bedrock-mantle:openai.*` 等）。用 `get_literal_values(KnownModelName.__value__, unpack_type_aliases='eager')` 展开；公开枚举请用 `known_model_names()`。

---

## 3. `providers/` — 鉴权与客户端

### 3.1 `Provider` 抽象（`providers/__init__.py#L42-L139`）

```python
class Provider(ABC, Generic[InterfaceClient]):
    _client: InterfaceClient
    _own_http_client: AsyncHTTPClient | None = None
    _http_client_factory: Callable[[], AsyncHTTPClient] | None = None
    _entered_count: int = 0
    _model_id_namespace: str | None = None
```

| 成员 | 类型 | 说明 |
|------|------|------|
| `name` | 抽象 `property -> str` | 流入每条 part 的 `provider_name`；thinking / native-tool 检测回放历史时会读它，**改名会破坏旧历史回放** |
| `model_id_namespace` | `property -> str` | `self._model_id_namespace or self.name` |
| `base_url` | 抽象 `property -> str` | |
| `client` | 抽象 `property -> InterfaceClient` | |
| `model_profile` | `@staticmethod (model_name) -> ModelProfile \| None` | 默认 `None` |
| `realtime_model_profile` | `@staticmethod (model_name) -> RealtimeModelProfile \| None` | 默认 `None` |
| `__aenter__` / `__aexit__` | 引用计数式管理自有 HTTP 客户端 | 退出到 0 时 `aclose()`；`_enter_lock` 是懒建的 `anyio.Lock` |

`missing_api_key_error(message)` 在缺失凭证时补一条指向 `Agent('test')` 的提示。

### 3.2 注册表

- `infer_provider_class(provider) -> type[Provider]`（`#L142-L301`）：`gateway/` 前缀先经 `normalize_gateway_provider` 归一化，然后 `if/elif` 分派，未知抛 `ValueError`。已知名字包括：`openai` / `openai-chat` / `openai-responses` / `openai-codex` / `deepseek` / `openrouter` / `vercel` / `azure` / `azure-responses` / `google` / `google-cloud` / `bedrock` / `bedrock-mantle` / `groq` / `anthropic` / `mistral` / `cerebras` / `cohere` / `system-one` / `crusoe` / `xai` / `moonshotai` / `fireworks` / `together` / `heroku` / `huggingface` / `ollama` / `github`（deprecated）/ `github-copilot` / `litellm` / `vllm` / `nebius` / `ovhcloud` / `alibaba` / `sambanova` / `sentence-transformers` / `snowflake` / `typesafe` / `voyageai` / `zai`。
- `infer_provider(provider) -> Provider`（`#L304-L313`）：`gateway/<upstream>` → `gateway_provider(upstream)`；否则 `infer_provider_class(provider)()`。

### 3.3 OpenAI 兼容 provider 的共享基类

[providers/_openai_compatible.py](../pydantic_ai_slim/pydantic_ai/providers/_openai_compatible.py) 的 `OpenAICompatibleProvider(Provider[AsyncOpenAI])` 统一了 HTTP 客户端生命周期：`_get_http_client(...)`（自建则记 `_own_http_client` 与 `_http_client_factory`）、`_create_openai_client(...)`、`_set_http_client(...)`。各 OpenAI 兼容 provider 在此之上只填 `name` / `base_url` / `client` / `model_profile`。

### 3.4 Gateway（`providers/gateway.py`）

`gateway_provider(upstream_provider, /, *, route=None, api_key=None, base_url=None, http_client=None)`：

- 凭证来自 `PYDANTIC_AI_GATEWAY_API_KEY`（或 `PAIG_API_KEY`）；base URL 来自 `PYDANTIC_AI_GATEWAY_BASE_URL`（或 `PAIG_BASE_URL`），否则从 key 的区域推断（`pylf_v…_<region>_…`）。
- `_GATEWAY_PROVIDER_ALIASES`：`chat→openai-chat`、`responses→openai-responses`、`converse→bedrock`、`google→google-cloud`；`normalize_gateway_provider` 去前缀并归一化。
- `_GATEWAY_ROUTE_REMAP`：`openai-chat`/`openai-responses`→`openai`，`google-cloud`→`google-vertex`。
- 通过 `_GatewayRequestHook` 注入 `Authorization: Bearer` 与 `traceparent`；`is_gateway_provider(provider)` 用弱引用集合判定。

> `models/AGENTS.md` 明确：**能力判定按客户端类而非 `base_url`**。Gateway 像普通代理一样通过 provider 的正常 SDK 客户端访问 API，不能因 host 而拆开降级。

---

## 4. `profiles/` — 模型家族事实

### 4.1 `ModelProfile` 全字段表（`profiles/__init__.py#L55-L296`）

`class ModelProfile(TypedDict, total=False)`。所有字段可选，缺省即文档化的默认值。默认值来自 `DEFAULT_PROFILE`（`#L359-L387`）。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `supports_tools` | `bool` | `True` | 是否支持工具 |
| `supports_text_output` | `bool` | `True` | |
| `supports_tool_return_schema` | `bool` | `False` | 是否原生支持工具返回 schema；否则注入描述 |
| `supports_json_schema_output` | `bool` | `False` | 即 native 结构化输出（`NativeOutput`） |
| `supports_json_object_output` | `bool` | `False` | 强制 JSON 模式（`PromptedOutput`） |
| `supports_image_output` | `bool` | `False` | |
| `supports_audio_input` | `bool` | `False` | 用户消息中的音频；目前无 profile 置 `True` |
| `supports_inline_system_prompts` | `bool` | `False` | API 是否接受任意位置的 inline system prompt；`False` 时非前导项被包成 `<system>` |
| `default_structured_output_mode` | `StructuredOutputMode` | `'tool'` | |
| `prompted_output_template` | `str` | `DEFAULT_PROMPTED_OUTPUT_TEMPLATE` | `{schema}` 占位 |
| `native_output_requires_schema_in_instructions` | `bool` | `False` | |
| `json_schema_transformer` | `type[JsonSchemaTransformer] \| None` | `None` | |
| `supports_cache` | `bool` | `False` | 请求侧缓存配置是否由 Pydantic AI 打开 |
| `supports_auto_cache` | `bool` | `False` | provider 服务端自动缓存模式 |
| `supported_cache_retentions` | `tuple[CacheRetention, ...]` | `()` | 支持的保留档，最短优先 |
| `default_cache_retention` | `timedelta \| None` | `None` | 无显式请求时的保留时长（provider 基础设施，家族函数不得设） |
| `supports_thinking` | `bool` | `False` | |
| `thinking_always_enabled` | `bool` | `False` | 隐含 `supports_thinking=True` |
| `thinking_enabled_by_default` | `bool` | `False` | 未配置时是否思考 |
| `supports_forced_tool_choice` | `bool` | `True` | |
| `supports_forced_tool_choice_with_thinking` | `bool` | `True` | |
| `forced_tool_choice_disables_thinking` | `bool` | `False` | |
| `thinking_tags` | `tuple[str, str]` | `('<think>', '</think>')` | 文本响应中解析 thinking 的标签对 |
| `ignore_streamed_leading_whitespace` | `bool` | `False` | 流式前导空白修复（Ollama+Qwen3 等） |
| `supported_native_tools` | `frozenset[type[AbstractNativeTool]]` | `SUPPORTED_NATIVE_TOOLS` | |
| `context_window` | `int \| None` | `None` | 缺省时由 genai-prices 填充 |
| `tool_deferral_mode` | `ToolDeferralMode \| None` | `None` | |
| `tool_addition_mode` | `ToolAdditionMode \| None` | `None` | |

已废弃字段：`tool_additions`（→ `tool_addition_mode`）、`deferred_tools_require_tool_search`（`True` → `tool_deferral_mode='with_tool_search'`）。`_LEGACY_PROVIDER_PROFILE_KEYS` 把 provider 前缀旧键（如 `openai_supports_tool_choice_required`）翻译为当前拼写。

类型别名：

```python
ToolDeferralMode = Literal['standalone', 'with_tool_search']
ToolAdditionMode = Literal['by_reference', 'with_definitions']
ModelProfileSpec = ModelProfile | Callable[[ModelProfile], ModelProfile]
```

`merge_profile(base, *overrides)`（`#L401-L414`）用 dict 展开合并，`None` 视为空，并在展开前逐输入翻译旧键，因此 override 里的旧键仍能覆盖 base。

### 4.2 `prompt_cache_outlook`

`prompt_cache_outlook(messages, *, profile=None, retention=None, now=None) -> PromptCacheOutlook`（`#L429-L486`），`PromptCacheOutlook = Literal['warm', 'cold', 'unknown']`。纯函数，用最近一条 `ModelResponse.timestamp` 与保留窗口比较判断缓存冷热；`'cold'` 时是执行历史改写维护（压缩 / 修剪 / 修复）的低成本窗口。`_expected_cache_retention` 会让 `CachePoint.ttl` 在 provider 认可的档位内延长窗口。

### 4.3 按家族的模块

`profiles/` 下：`openai.py`、`anthropic.py`、`google.py`、`amazon.py`、`meta.py`、`grok.py`、`groq.py`、`mistral.py`、`cohere.py`、`deepseek.py`、`qwen.py`、`moonshotai.py`、`zai.py`、`openai_codex.py`、`harmony.py`、`decision.py`、`typesafe.py`。

示例：`profiles/openai.py` 的 `openai_model_profile(model_name)` 用前缀表 `_REASONING_SUPPORT_BY_PREFIX` 推导 reasoning 三事实（`enabled_by_default` / `can_be_disabled` / `supports_mode` / `supports_context`），返回 `OpenAIModelProfile(...)`；`OpenAIJsonSchemaTransformer` 把 schema 改写为 OpenAI strict 模式（`additionalProperties: false`、全字段 required、展开 `$ref` 等）。`profiles/anthropic.py` 的 `anthropic_model_profile` 推导 `anthropic_supports_adaptive_thinking` 等前缀事实，并在支持 tool search 时设 `tool_deferral_mode='standalone'`。

规则（见 `profiles/AGENTS.md` 与 `pydantic_ai/AGENTS.md`）：把**固有**能力标志放进 profile；provider 的客户端/鉴权行为放 providers 或适配器；优先用显式能力字段而非散落的 `isinstance` / provider 名判断。

---

## 5. `settings.py` — 模型设置

### 5.1 类型别名

```python
ThinkingEffort = Literal['minimal', 'low', 'medium', 'high', 'xhigh']
ThinkingLevel  = bool | ThinkingEffort
CacheRetention = Literal['5m', '30m', '1h']
CacheSetting   = bool | CacheRetention | CacheConfig
ToolChoiceScalar = Literal['none', 'required', 'auto']
ToolChoice     = ToolChoiceScalar | list[str] | ToolOrOutput | None
ServiceTier    = Literal['auto', 'default', 'flex', 'priority']
```

`CacheConfig`（`total=False`）字段：`retention: CacheRetention`、`messages: bool`（默认 `True`）。`ToolOrOutput` 是 `@dataclass`，字段 `function_tools: list[str]`，用于"限定函数工具但保留输出工具 / 直接文本图像输出"。

### 5.2 `ModelSettings(TypedDict, total=False)`

跨 provider 设置。字段（含 `Supported by:` 文档串）：`max_tokens`、`temperature`、`top_p`、`top_k`、`timeout`、`parallel_tool_calls`、`tool_choice`、`seed`、`presence_penalty`、`frequency_penalty`、`logit_bias`、`stop_sequences`、`extra_headers`、`thinking`、`cache`、`service_tier`、`extra_body`。

- `timeout: int | float | Timeout`：数值秒处处可用；legacy `httpx.Timeout` 也会被接受。在自建 HTTP 客户端上，数值超时只能**缩短**不能**延长**客户端的 connect 超时（默认 5s）与池超时。
- 每条 `Supported by:` 列表由 `tests/models/test_model_settings_support.py` 校验（探测各模型类的出站请求），所以新增转发字段时必须同步更新。
- `ServiceTier` 的跨 provider 映射表见 `settings.py#L109-L127`（OpenAI / Anthropic / Bedrock / Google / Google Cloud）。

`merge_model_settings(base, overrides)`（`#L621-L630`）：`base | overrides`（overrides 优先），二者皆空返回 `None`。

### 5.3 provider 特有设置类

定义在各自 model 模块，字段以 provider 前缀命名，遵循"强类型字段替代 `extra_body` dict"的规则：

- `models/openai.py`：`OpenAIChatModelSettings`（`openai_reasoning_effort`、`openai_logprobs`、`openai_store`、`openai_service_tier`、`openai_prompt_cache_retention`、`openai_prompt_cache_options`、`openai_cache_instructions`、`openai_continuous_usage_stats` 等）；`OpenAIResponsesModelSettings` 继承之，追加 `openai_reasoning_mode`、`openai_reasoning_context`、`openai_native_tools` 等。
- 其他：`AnthropicModelSettings`、`GoogleModelSettings`、`GoogleModelSettings.google_cloud_service_tier`、`BedrockModelSettings` 等。

> 规则：**不要**为未验证的能力限制加客户端 guard——把用户选择的设置转发出去，让 provider API 反馈真正的不兼容（`models/AGENTS.md` rule:26）。

---

## 6. `usage.py` / `prices.py` — 用量与成本

### 6.1 `UsageBase`（`usage.py#L83-L279`）

`@dataclass(repr=False, init=False, eq=False)`，是 `genai_prices.types.AbstractUsage` 的实现。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `input_tokens` | `int`（别名 `request_tokens`） | `0` | 所有模态的输入总量，**含**缓存与音频 token |
| `cache_write_tokens` | `int` | `0` | 含于 `input_tokens` |
| `cache_read_tokens` | `int` | `0` | 含于 `input_tokens`，含 `cache_audio_read_tokens` |
| `output_tokens` | `int`（别名 `response_tokens`） | `0` | |
| `input_audio_tokens` | `int` | `0` | 含于 `input_tokens` |
| `cache_audio_read_tokens` | `int` | `0` | 含于 `cache_read_tokens` 与 `input_audio_tokens` |
| `output_audio_tokens` | `int` | `0` | 含于 `output_tokens` |
| `audio_seconds` | `float` | `0` | 按时长计费的模型（如 xAI Grok Voice） |
| `details` | `dict[str, int]` | `{}` | 额外细节（**不参与计价**） |
| `cost` | `Decimal \| None` | `None` | 尽力而为的 USD 成本；未知时为 `None` 而非 0 |

属性 / 方法：`total_tokens`、`cache_hit_ratio`（`cache_read_tokens / input_tokens`，无输入返回 `0.0`）、`opentelemetry_attributes()`（产出 `gen_ai.usage.*`，并把 `cache_write_tokens`/`cache_read_tokens`/音频 token 放进 `gen_ai.usage.details.*`，同时避免与一等 token 属性重名导致重复计数）、`has_values()`。它通过 `__get_pydantic_core_schema__` 保留任意额外字段（历史兼容键 `requests` / `request_tokens` / `response_tokens` / `total_tokens`）。

### 6.2 子类

| 类 | 位置 | 额外成员 |
|----|------|----------|
| `RequestUsage` | `#L282-L362` | `requests` 属性恒为 `1`；`incr` / `__add__`；类方法 `extract(data, *, provider, provider_url, provider_fallback, api_flavor='default', details=None)`（委托 genai-prices 的 `extract_usage`） |
| `RunUsage` | `#L365-L443` | 额外 `requests: int`、`tool_calls: int`；支持 `incr` / `__add__` / `__sub__`（字段级差值，用于报告嵌套操作增量） |
| `UsageLimits` | `#L470-L628` | `cost_limit`、`request_limit`（默认 `50`）、`tool_calls_limit`、`input_tokens_limit`、`output_tokens_limit`、`total_tokens_limit`、`per_request_input_tokens_limit`、`count_tokens_before_request`（默认 `False`）；方法 `has_token_limits` / `check_before_request` / `check_cost` / `check_tokens` / `check_before_tool_call` / `check_per_request_input_tokens` |

`count_tokens_before_request=True`（默认关闭）会在请求前调用 `model.count_tokens`，支持者：Anthropic、Google、Bedrock Converse、OpenAI Responses。

### 6.3 价格与上下文窗口的加载

[_genai_prices.py](../pydantic_ai_slim/pydantic_ai/_genai_prices.py) 提供 `preload_pricing_data()`、`iter_provider_references(...)`、`lookup_context_window(...)`、`calculate_price_for_usage(...)`、`best_effort_price(...)`、`fill_response_cost(response)`。`Model.__init__` 会调用 `preload_pricing_data()`。

[prices.py](../pydantic_ai_slim/pydantic_ai/prices.py) 的 `update_in_background() -> UpdatePrices` 立即下载最新价目表，并每小时刷新一次；下载不阻塞代码，失败保留上一份并用 genai-prices logger 记录。返回的 updater 应用退出时调 `stop()`。

`ModelResponse.cost()` 与 `EmbeddingResult.cost()` 都基于 genai-prices 计算。

---

## 7. `embeddings/` — 向量嵌入

### 7.1 `EmbeddingModel`（`embeddings/base.py#L8-L116`）

```python
class EmbeddingModel(ABC):
    @property @abstractmethod
    def model_name(self) -> str: ...
    @property @abstractmethod
    def system(self) -> str: ...
    @abstractmethod
    async def embed(self, inputs, *, input_type, settings=None) -> EmbeddingResult: ...
```

具体成员：`__init__(*, settings=None)`、`settings` property、`base_url` property（默认 `None`）、`prepare_embed(inputs, settings=None) -> tuple[list[str], EmbeddingSettings]`、`max_input_tokens()`（默认 `None`）、`count_tokens(text)`（默认 `NotImplementedError`）。

### 7.2 数据与设置类型

- `EmbeddingResult`（`embeddings/result.py#L21-L119`）：`embeddings`、`inputs`、`input_type`、`model_name`、`provider_name`、`timestamp`、`usage: RequestUsage`、`provider_details`、`provider_response_id`；`__getitem__` 可按索引或原文取值；`cost()`。
- `EmbedInputType = Literal['query', 'document']`。
- `EmbeddingSettings(TypedDict, total=False)`（`embeddings/settings.py`）：`dimensions`、`truncate`、`extra_headers`、`extra_body`；`merge_embedding_settings(base, overrides)`。

### 7.3 `Embedder` 门面（`embeddings/__init__.py#L145-L394`）

`@dataclass(init=False, eq=False)`，字段 `instrument: InstrumentationSettings | bool | None`。

| 方法 | 说明 |
|------|------|
| `__init__(model, *, settings=None, defer_model_check=True, instrument=None)` | 惰性校验模型 |
| `instrument_all(instrument=True)` | 类级默认（`_instrument_default`） |
| `model` | property |
| `override(*, model=UNSET)` | `@contextmanager` 临时换模型 |
| `embed_query` / `embed_documents` / `embed` | 异步门面 |
| `max_input_tokens` / `count_tokens` | |
| `embed_query_sync` / `embed_documents_sync` / `embed_sync` / `max_input_tokens_sync` / `count_tokens_sync` | 同步变体（`_utils.run_until_complete`） |

`infer_embedding_model(model, *, provider_factory=infer_provider)`（`#L83-L139`）：必须带 `provider:` 前缀；分派到 `OpenAIEmbeddingModel`（含 OpenAI 兼容 provider）、`CohereEmbeddingModel`、`BedrockEmbeddingModel`、`GoogleEmbeddingModel`、`SentenceTransformerEmbeddingModel`、`VoyageAIEmbeddingModel`。

provider 模块：`openai.py`、`google.py`、`cohere.py`、`bedrock.py`、`voyageai.py`、`sentence_transformers.py`；另有 `wrapper.py`、`instrumented.py`、`test.py`。

---

## 8. 非 provider 模型（测试 / 包装 / 组合）

| 类 | 位置 | 关键点 |
|----|------|--------|
| `FunctionModel` | `models/function.py#L49` | 由本地 `function` / `stream_function` 驱动；声明全部 deferral/addition modes；`model_name` 默认由函数名生成，`system='function'` |
| `TestModel` | `models/test.py#L62` | 默认调用所有工具再产出响应；字段 `call_tools`、`custom_output_text`、`custom_output_args`、`seed`、`last_model_request_parameters`；`provider` 返回 `None`；`__test__ = False` 避免 pytest 收集 |
| `WrapperModel` | `models/wrapper.py#L31` | 包装另一个 `Model`，逐方法转发（`request` / `request_stream` / `prepare_request` / `prepare_messages` / `profile` / `context_window` 等），`__init__(wrapped)` 走 `infer_model` |
| `FallbackModel` | `models/fallback.py#L90` | `__init__(default_model, *fallback_models, fallback_on=(ModelAPIError,))`；`fallback_on` 支持异常类型元组 / 处理器 / 响应处理器 / 混合序列；提供 `ResponseRejected` |
| `ConcurrencyLimitedModel` | `models/concurrency.py#L42` | 限制并发；`limit_model_concurrency(...)` 上下文 |
| `InstrumentedModel` | `models/instrumented.py#L333` | 继承 `WrapperModel`，用 `InstrumentationSettings` 打 OTel；`instrument_model(model, instrument)` 是入口 |

`InstrumentationSettings`（`models/instrumented.py#L64-L182`）字段：`tracer`、`include_binary_content`、`include_content`、`include_model_request_parameters`、`version: Literal[2,3,4,5,6]`（默认 5）、`use_aggregated_usage_attribute_names`。

---

## 9. 函数签名 → JSON Schema

### 9.1 `function_schema`（[_function_schema.py](../pydantic_ai_slim/pydantic_ai/_function_schema.py#L110-L295`）

```python
def function_schema(
    function: Callable[..., Any],
    schema_generator: type[GenerateJsonSchema],
    *,
    tool_name: str | None = None,
    takes_ctx: bool | None = None,
    docstring_format: DocstringFormat = 'auto',
    require_parameter_descriptions: bool = False,
) -> FunctionSchema:
```

流程：用 `_griffe.doc_descriptions` 抽取 docstring 与字段描述 → 遍历 `signature(function).parameters`（首个 `RunContext[...]` 参数被识别为 ctx）→ 逐字段构建 TypedDict 字段 schema（`is_model_like` 元数据标记）→ `_build_schema` 处理**单模型型参数**（把模型 schema 直接铺平到顶层，validator 输出包成 `{name: value}`）→ `GenerateSchema.clean_schema` → `create_schema_validator` → `schema_generator().generate(...)` → 计算 `return_schema`。

`FunctionSchema`（`#L44-L107`）字段：`function`、`name`、`description`、`validator: SchemaValidator`、`json_schema`、`takes_ctx`、`is_async`、`single_arg_name`、`positional_fields`、`var_positional_field`、`return_schema`；`single_field_name` 属性；`call(args_dict, ctx)` 与 `_call_args(...)`。

`takes_ctx` / `is_call_ctx` / `extract_return_schema_type` / `find_typed_parameter` / `validate_schema_signature` 等辅助也在此模块。

### 9.2 `GenerateToolJsonSchema`（`tools.py#L275-L316`）

继承 `pydantic.json_schema.GenerateJsonSchema`：

- `enum_schema(...)`：当 Enum 混入 `UseEnumMemberDocstrings` 时，把成员 docstring 转成 `anyOf`-of-`const` 并带上描述（含别名解析、`None` 成员的降级）。
- `_named_required_fields_schema(...)`：去掉无用的属性 `title`。

### 9.3 `ToolDefinition`（`tools.py#L586-L788`）

`@dataclass(repr=False, kw_only=True)`，是 `ModelRequestParameters.function_tools` 承载并被适配器序列化到线上的形状。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | `str` | — | |
| `parameters_json_schema` | `ObjectJsonSchema` | `{'type': 'object', 'properties': {}}` | |
| `description` | `str \| None` | `None` | |
| `outer_typed_dict_key` | `str \| None` | `None` | 非 `object` schema 的输出工具的外层键 |
| `strict` | `bool \| None` | `None` | `None` 时按 provider 推断 |
| `sequential` | `bool` | `False` | 是否作为独占屏障执行 |
| `kind` | `ToolKind` | `'function'` | `'function' \| 'output' \| 'external' \| 'unapproved'` |
| `metadata` | `dict[str, Any] \| None` | `None` | 不发送给模型 |
| `timeout` | `float \| None` | `None` | |
| `defer_loading` | `bool` | `False` | 揭示后仍保持作者意图 |
| `unless_native` | `str \| None`（别名 `prefer_native`/`prefer_builtin`） | `None` | native 工具受支持时从线上移除（本地回退） |
| `with_native` | `str \| None` | `None` | 属于某 native 工具的语料 |
| `tool_kind` | `ToolPartKind \| None` | `None` | 跨 provider 类型化 call/return 形状判别 |
| `return_schema` | `ObjectJsonSchema \| None` | `None` | |
| `include_return_schema` | `bool \| None` | `None` | |
| `toolset_id` | `str \| None` | `None` | |
| `capability_id` | `str \| None` | `None` | 贡献该工具的 capability |

方法：`function_signature`（`@cached_property -> FunctionSignature`）、`render_signature(body, **kwargs)`、`defer` 属性（`kind in ('external', 'unapproved')`）。

> **`kind`（调用语义）与 `tool_kind`（类型化 part 形状）是两个不同概念**，不要混淆。新增 typed native tool 需同步扩展 `ToolPartKind`、定义子类与 narrower、注册到 `_TOOL_CALL_NARROWERS` / `_NATIVE_CALL_NARROWERS` / `_TOOL_RETURN_NARROWERS` / `_NATIVE_RETURN_NARROWERS`，并加入判别联合（见 `tools.py#L701-L708` 的实现说明）。

### 9.4 `resolve_request_tools` 与 `prepare_return_schemas`

`resolve_request_tools(params, supported_types, *, can_withhold_tool_schemas=None, tool_addition_mode=None)`（`models/__init__.py#L2134-L2258`）的三条规则：

1. `unless_native` 命中受支持 native 工具 → 从线上丢弃。
2. `with_native` 命中**不受支持** native 工具 → 清空 `with_native`。
3. `defer_loading` 是作者意图，本函数一次性把 provider 表示解析进 `tool_visibility`。

此外有两类独立剔除：`optional=True` 只管"本模型不支持"路径；语料为空时，仅当 `ToolSearchTool` 是 `optional` 且无 `with_native` 成员才剔除。

`prepare_return_schemas(params, *, supports_tool_return_schema)`（`#L2261-L2301`）：未 opt-in 的工具清空 `return_schema`；支持的模型原样保留；其它模型把 schema 注入 `description`。

其他内部 helper：`_get_final_result_event`（判定 `FinalResultEvent`）、`_convert_speech_parts`、`_trim_messages_before_compaction`、`_wrap_non_leading_system_prompts`、`download_item`（默认最大 50 MiB，见 `_MAX_FILE_URL_DOWNLOAD_BYTES`）、`create_async_http_client`、`get_user_agent`。

---

## 10. 相关文件清单

| 关注点 | 文件 |
|--------|------|
| 模型基类与请求归一化 | [models/__init__.py](../pydantic_ai_slim/pydantic_ai/models/__init__.py)、[models/_abstract.py](../pydantic_ai_slim/pydantic_ai/models/_abstract.py) |
| 模型名枚举 | [models/_known_model_names.py](../pydantic_ai_slim/pydantic_ai/models/_known_model_names.py) |
| 续跑折叠 | [models/_continuation.py](../pydantic_ai_slim/pydantic_ai/models/_continuation.py) |
| 缓存 / 工具选择辅助 | [models/_prompt_cache.py](../pydantic_ai_slim/pydantic_ai/models/_prompt_cache.py)、[models/_tool_choice.py](../pydantic_ai_slim/pydantic_ai/models/_tool_choice.py) |
| 适配器范例 | [models/openai.py](../pydantic_ai_slim/pydantic_ai/models/openai.py)、[models/anthropic.py](../pydantic_ai_slim/pydantic_ai/models/anthropic.py) |
| Provider 抽象与注册表 | [providers/__init__.py](../pydantic_ai_slim/pydantic_ai/providers/__init__.py)、[providers/_openai_compatible.py](../pydantic_ai_slim/pydantic_ai/providers/_openai_compatible.py)、[providers/gateway.py](../pydantic_ai_slim/pydantic_ai/providers/gateway.py) |
| Profile | [profiles/__init__.py](../pydantic_ai_slim/pydantic_ai/profiles/__init__.py)、[profiles/openai.py](../pydantic_ai_slim/pydantic_ai/profiles/openai.py)、[profiles/anthropic.py](../pydantic_ai_slim/pydantic_ai/profiles/anthropic.py) |
| 设置 | [settings.py](../pydantic_ai_slim/pydantic_ai/settings.py) |
| 用量与价格 | [usage.py](../pydantic_ai_slim/pydantic_ai/usage.py)、[prices.py](../pydantic_ai_slim/pydantic_ai/prices.py)、[_genai_prices.py](../pydantic_ai_slim/pydantic_ai/_genai_prices.py) |
| 嵌入 | [embeddings/__init__.py](../pydantic_ai_slim/pydantic_ai/embeddings/__init__.py)、[embeddings/base.py](../pydantic_ai_slim/pydantic_ai/embeddings/base.py)、[embeddings/result.py](../pydantic_ai_slim/pydantic_ai/embeddings/result.py)、[embeddings/settings.py](../pydantic_ai_slim/pydantic_ai/embeddings/settings.py) |
| 签名编译与工具定义 | [_function_schema.py](../pydantic_ai_slim/pydantic_ai/_function_schema.py)、[tools.py](../pydantic_ai_slim/pydantic_ai/tools.py) |
| 规范与约束 | [models/AGENTS.md](../pydantic_ai_slim/pydantic_ai/models/AGENTS.md)、[providers/AGENTS.md](../pydantic_ai_slim/pydantic_ai/providers/AGENTS.md)、[profiles/AGENTS.md](../pydantic_ai_slim/pydantic_ai/profiles/AGENTS.md) |
