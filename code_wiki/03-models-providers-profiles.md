# 03 · 模型 / Provider / Profile

本篇覆盖模型访问层：如何把 `'openai:gpt-4o'` 这样的字符串解析成具体模型实例，以及规范化请求如何映射到 provider 的线上格式。

相关目录：`pydantic_ai_slim/pydantic_ai/models/`、`providers/`、`profiles/`、`embeddings/`，以及 `settings.py`。

---

## 1. `models/` — 模型适配层

### 1.1 目录构成

| 类别 | 模块（示例） |
|------|--------------|
| provider 适配器 | `openai.py`（`OpenAIChatModel` + `OpenAIResponsesModel`）、`anthropic.py`（`AnthropicModel`）、`google.py`（`GoogleModel`）、`bedrock.py`（`BedrockConverseModel`）、`bedrock_mantle.py`、`groq.py`、`mistral.py`、`cohere.py`、`xai.py`、`cerebras.py`、`crusoe.py`、`huggingface.py`、`ollama.py`、`openrouter.py`、`snowflake.py`、`zai.py`、`github_copilot.py`、`openai_codex.py`、`system_one.py`、`typesafe.py` |
| 非 provider 模型 | `function.py`（`FunctionModel`）、`test.py`（`TestModel`）、`wrapper.py`（`WrapperModel`）、`fallback.py`（`FallbackModel`）、`concurrency.py`、`instrumented.py`（`InstrumentedModel` + `InstrumentationSettings`）、`mcp_sampling.py`、`decision.py` |
| 共享内部 | `_abstract.py`、`__init__.py`、`_known_model_names.py`、`_prompt_cache.py`、`_tool_choice.py`、`_continuation.py`、`_reasoning_details.py`、`_decode_errors.py` |

### 1.2 `AbstractModel`

`models/_abstract.py`：`Model` 与 `RealtimeModel` 共享的最小身份。

- 抽象属性：`model_name -> str`、`system -> str`（provider 名，供 `gen_ai.system`）。
- 具体：`base_url`、`model_id`（`f'{self.system}:{self.model_name}'`）、`context_window`、`label`，以及异步上下文管理钩子。

### 1.3 `Model`

`models/__init__.py` 的 `class Model(AbstractModel, Generic[InterfaceClient])`。

- 类级能力标志：`supported_tool_deferral_modes`、`supported_tool_addition_modes`、`compaction_requires_encrypted_content`、`compaction_retains_standing_prompt`。
- 唯一 `@abstractmethod`：`async request(messages, model_settings, model_request_parameters) -> ModelResponse`。
- 可选覆写：`count_tokens`、`compact_messages`、`request_stream`（`@asynccontextmanager`，默认抛错）、`cancel_suspended_response`、`continuation_delay`。
- 关键方法：
  - `prepare_request(...)`：合并 settings、解析 `thinking`/`cache`、解析输出模式默认值、校验 profile 支持、解析延迟工具可见性。
  - `prepare_messages(...)`：provider 无关的历史预处理（语音 part、工具可用性增量、本地 tool-search 合成、非法前导 system prompt 包装）。
  - `profile` 属性：解析顺序为 `DEFAULT_PROFILE` → `provider.model_profile(model_name)` → genai-prices 的 `context_window` → 用户 `profile=` → 与类实现的 native 工具求交。

### 1.4 `ModelRequestParameters`

每次请求的工具/输出/指令配置：`function_tools`、`native_tools`、`tool_visibility`、`revealed_tool_names`、`deferred_capability_ids`、`output_mode`、`output_object`、`output_tools`、`prompted_output_template`、`allow_text_output`、`allow_image_output`、`instruction_parts`、`thinking`、`cache`。

### 1.5 `StreamedResponse`

`models/__init__.py` 的流式响应基类：字段 `model_request_parameters`、`final_result_event`、`provider_response_id`、`finish_reason`、`state`、`metadata` 等；抽象 `_get_event_iterator()`；具体 `__aiter__`（包裹原始迭代器，检测 final result、合成 part 结束、取消保护）、`cancel()`、`close_stream()`、`get()`、`usage`。`CompletedStreamedResponse` 包装已完成的 `ModelResponse`。

---

## 2. 模型字符串 → 具体 `Model`

`infer_model(model)`（`models/__init__.py`）：

1. 若已是 `Model` 直接返回；`'test'` 返回 `TestModel()`。
2. `parse_model_id(model)` 在首个 `:` 处拆分 `(provider_name, model_name)`。
3. 无 provider 前缀则抛 `UserError` 并给出模糊提示（`_suggest_known_model_name`）。
4. `infer_provider(provider_name)` → 构造 provider（如 `OpenAIProvider`）。
5. 长 `if/elif` 将 `model_kind` 映射到模型类并构造（如 `'openai'` → `OpenAIResponsesModel`；`'openai-chat'` → `OpenAIChatModel`；`'anthropic'` → `AnthropicModel`）。
6. **顺序重要**：OpenRouter/Cerebras/Crusoe/Snowflake/Ollama/Z.AI/GitHub Copilot/OpenAI Codex 等 OpenAI 兼容 provider 需在 `openai` 之前判断。

`infer_model_profile(model)` 做同样的 provider 解析但不构造模型，仅返回 `ModelProfile`。

`KnownModelName`（`models/_known_model_names.py`）是 `provider:model` 的巨型 `Literal`；`known_model_names()` 返回其元组形式。

---

## 3. `providers/` — 鉴权与客户端

### 3.1 `Provider` 抽象

`providers/__init__.py` 的 `class Provider(ABC, Generic[InterfaceClient])`：

- 抽象：`name -> str`（流入 `ModelMessage.provider_name`）、`base_url -> str`、`client -> InterfaceClient`。
- 具体：`model_id_namespace`、静态 `model_profile(model_name) -> ModelProfile | None`、`realtime_model_profile(...)`、管理 provider 自有 HTTP 客户端生命周期的异步上下文协议、`missing_api_key_error(...)`。

### 3.2 注册表

- `infer_provider_class(provider) -> type[Provider]`：名称 → provider 类的 `if/elif` 注册表（`'openai'|'openai-chat'|'openai-responses'` → `OpenAIProvider` 等），未知则 `ValueError`。`gateway/` 前缀会先被规范化。
- `infer_provider(provider) -> Provider`：实例化；`gateway/<upstream>` 返回 `gateway_provider(upstream)`。

模块示例：`openai.py`、`anthropic.py`、`google.py`、`google_cloud.py`、`azure.py`、`bedrock.py`、`bedrock_mantle.py`、`groq.py`、`mistral.py`、`cohere.py`、`xai.py`、`deepseek.py`、`openrouter.py`、`vercel.py`、`gateway.py`，以及一批 OpenAI 兼容 provider（`cerebras`、`crusoe`、`fireworks`、`github_copilot`、`heroku`、`litellm`、`moonshotai`、`nebius`、`ollama`、`ovhcloud`、`sambanova`、`snowflake`、`together`、`vllm`、`zai` 等）。

兼容性类型别名（`models/__init__.py`）：`OpenAIChatCompatibleProvider`、`OpenAIResponsesCompatibleProvider`。

---

## 4. `profiles/` — 模型家族事实

### 4.1 `ModelProfile`

`profiles/__init__.py` 的 `class ModelProfile(TypedDict, total=False)`：描述「特定模型/家族在构造与处理请求响应时的固有特性」，独立于模型与 provider 类。字段均为可选（缺省即文档化默认值）。代表性字段：

- 输出/结构化：`supports_tools`、`supports_text_output`、`supports_json_schema_output`、`supports_json_object_output`、`supports_image_output`、`default_structured_output_mode`、`prompted_output_template`、`json_schema_transformer`。
- 缓存：`supports_cache`、`supports_auto_cache`、`supported_cache_retentions`、`default_cache_retention`。
- 思考：`supports_thinking`、`thinking_always_enabled`、`thinking_enabled_by_default`、`thinking_tags`。
- 工具选择：`supports_forced_tool_choice`、`forced_tool_choice_disables_thinking`。
- Native/延迟工具：`supported_native_tools`、`tool_deferral_mode`、`tool_addition_mode`。
- 其他：`supports_audio_input`、`supports_inline_system_prompts`、`context_window`。

`DEFAULT_PROFILE` 是完整填充的基线层；`ModelProfileSpec = ModelProfile | Callable[[ModelProfile], ModelProfile]`；`merge_profile(base, *overrides)` 做字典展开合并。

### 4.2 按家族的模块

`profiles/` 下：`openai.py`、`anthropic.py`、`google.py`、`amazon.py`、`meta.py`、`grok.py`、`groq.py`、`mistral.py`、`cohere.py`、`deepseek.py`、`qwen.py`、`moonshotai.py`、`zai.py`、`openai_codex.py`、`harmony.py`、`decision.py`、`typesafe.py`。

规则（见 `pydantic_ai/AGENTS.md`）：把能力标志放进 profile 类而非散落的 `isinstance` 检查；provider 特有的 API 行为配在 `Provider.model_profile()` 中，而模型 profile 只含固有特性。

---

## 5. `settings.py` — 模型设置

类型别名：`ThinkingEffort`、`ThinkingLevel = bool | ThinkingEffort`、`CacheRetention`、`CacheSetting`、`ServiceTier`、`ToolChoice` 等。

`ModelSettings(TypedDict, total=False)`：跨 provider 设置，字段包括 `max_tokens`、`temperature`、`top_p`、`top_k`、`timeout`、`parallel_tool_calls`、`tool_choice`、`seed`、`presence_penalty`、`frequency_penalty`、`logit_bias`、`stop_sequences`、`extra_headers`、`thinking`、`cache`、`service_tier`、`extra_body`。`merge_model_settings(base, overrides)` 做合并（overrides 优先）。

provider 特有设置类定义在各自 model 模块中（如 `OpenAIChatModelSettings`、`AnthropicModelSettings`、`GoogleModelSettings`），遵循「provider 前缀的强类型字段替代 `extra_body` dict」的规则。

---

## 6. `messages.py` — 规范化协议（模型层视角）

完整内容见 [04 · 消息协议与输出](04-messages-and-output.md)。模型层需记住：

- `ModelMessage = Annotated[ModelRequest | ModelResponse, Discriminator('kind')]`。
- provider 适配器必须保证 `request()` 与 `request_stream()` 的响应处理结果一致（见 `models/AGENTS.md`）。
- provider 特有数据存入结构化 `provider_details` / `provider_metadata`，不要塞进 `id` / `content` / `args`。

---

## 7. `usage.py` 与 `prices.py` — 用量与成本

- `UsageBase`：共享字段 `input_tokens`、`cache_write_tokens`、`cache_read_tokens`、`output_tokens`、`input_audio_tokens`、`output_audio_tokens`、`cost` 等；属性 `total_tokens`、`cache_hit_ratio`。
- `RequestUsage`：单次请求（`requests` 恒为 1）；`extract(...)` 委托 genai-prices 的 `provider.extract_usage(...)`。
- `RunUsage`：整次运行，额外含 `requests`、`tool_calls`；支持 `+` / `-`。
- `UsageLimits`：`cost_limit`、`request_limit`（默认 50）及 token 限制。
- `prices.update_in_background()` 启动后台小时级刷新 genai-prices 数据。

---

## 8. `embeddings/` — 向量嵌入

- `EmbeddingModel(ABC)`（`base.py`）：抽象 `model_name`、`system`、`async embed(inputs, *, input_type, settings) -> EmbeddingResult`；具体 `prepare_embed(...)`、`max_input_tokens()`、`count_tokens(text)`。
- `Embedder`（`__init__.py`）：高层门面，`embed_query` / `embed_documents` / `embed`、同步变体、`override(...)`、`instrument_all(...)`。`infer_embedding_model(...)` 解析 `provider:model` 并分派。
- provider：`openai.py`、`google.py`、`cohere.py`、`bedrock.py`、`voyageai.py`、`sentence_transformers.py`；另有 `wrapper.py`、`instrumented.py`、`test.py`。
- `EmbeddingSettings(TypedDict, total=False)`：`dimensions`、`truncate`、`extra_headers`、`extra_body`。
- `EmbeddingResult`：`embeddings`、`inputs`、`input_type`、`model_name`、`provider_name`、`timestamp`、`usage`、`provider_details`、`provider_response_id`。

---

## 9. 函数签名 → JSON Schema

- `_function_schema.py`：`function_schema(...) -> FunctionSchema`（含 `name`、`description`、`validator: SchemaValidator`、`json_schema`、`takes_ctx`、`is_async` 等）把 Python 函数签名与 docstring 编译成工具 schema。
- `tools.py`：`GenerateToolJsonSchema(GenerateJsonSchema)` 用于工具；`ToolDefinition` 承载 `name`、`parameters_json_schema`、`kind: ToolKind`、`tool_kind: ToolPartKind | None` 等，是序列化到线上的形状。
