# LLM Providers（真实多 Provider）+ 配置驱动降级与投票 — 设计规格

## 1. 目标

- 将当前 MVP 中的 `MockLLMProvider` 扩展为可配置的真实多 Provider：OpenAI、Anthropic(Claude)、Gemini(AI Studio)、Qwen3.5-plus、GLM、Kimi。
- 将 “provider < 3 降级逻辑” 改为配置驱动，并记录可审计证据（run 状态、通知、原因）。
- 将投票输入从“手工构造/测试 DTO”为主，切换为“真实 provider 输出经统一校验后的结构化结果”，保持确定性排序与可回放。
- 不在仓库中存储任何密钥；不在日志/DB 中泄露密钥。

## 2. 范围与非范围

### 2.1 范围（本次）

- Provider 适配器：
  - OpenAI-Compatible 统一适配：用于 OpenAI / Kimi / Qwen / GLM（通过 `base_url` 与 header 适配差异）。
  - Anthropic Messages API 适配。
  - Gemini AI Studio REST 适配。
- Provider Registry：读取配置，生成“有效 provider 列表”，并给出“不可用原因摘要”（不含密钥）。
- Orchestrator：用 Registry 的有效 provider 数量判断是否进入分析与投票；不足阈值则降级为 `DEGRADED` 并发送降级通知。
- 投票：从 `LLMOutputValidator` 的规范化结果生成 vote 数据，再交给既有 `VotingAggregator`。
- 测试：全链路仍可在 `dry_run` + mock HTTP 下通过，不依赖真实网络与真实 key。

### 2.2 非范围（本次不做）

- 不做真实收益/回撤/行情接入与基于收益的 provider 加权。
- 不做更复杂的 provider 有效性定义（例如“调用失败率导致有效 provider 数下降”）的动态降级；本次先按“配置有效 provider 数”判断。
- 不实现全量 7 大模型矩阵配置 UI 或 Web 管理后台。

## 3. 总体方案

采用 **方案 A（推荐）**：

- `OpenAICompatibleChatProvider`：统一实现 `invoke(prompt)->raw_text`，通过配置 `base_url/model/api_key/extra_headers` 支持多家 OpenAI 风格 API。
- `AnthropicProvider`：实现 Claude Messages API。
- `GeminiProvider`：实现 AI Studio REST（API Key）。
- 统一走现有 `PromptBuilder`、`LLMOutputValidator`、`LLMRepository` 落库与审计。
- Orchestrator：
  - `providers = ProviderRegistry.from_config(config).enabled_providers()`
  - `effective_provider_count = len(providers)`
  - 若 `effective_provider_count < config.llm_min_effective_providers`：`DEGRADED`，只入库、不分析/不投票/不推送/不账本。
  - 否则：对每个 provider 分别执行 stock+etf 的分析落库 -> 从落库/验证结果生成 vote -> 投票与后续流程。

## 4. 模块与边界

### 4.1 新增模块

- `src/analysis/providers/openai_compatible_provider.py`
  - `OpenAICompatibleChatProvider(BaseLLMProvider)`
  - 负责：HTTP 调用、超时、重试（最小实现）、返回 raw_text

- `src/analysis/providers/anthropic_provider.py`
  - `AnthropicProvider(BaseLLMProvider)`

- `src/analysis/providers/gemini_provider.py`
  - `GeminiProvider(BaseLLMProvider)`

- `src/analysis/provider_registry.py`
  - `ProviderRegistry`
  - 负责：从 `AppConfig` 解析启用列表与逐家配置，构建 provider 实例
  - 输出：
    - `providers: list[BaseLLMProvider]`
    - `disabled_reasons: dict[str, str]`（用于通知/审计摘要，不含敏感信息）

### 4.2 需要修改的模块

- `src/app/config.py`：扩展 `AppConfig`，增加 LLM 配置项
- `src/orchestrator/run_orchestrator.py`：使用 ProviderRegistry；将降级阈值改为配置驱动；将投票输入改为来源于真实 LLM 输出的结构化结果
- `src/analysis/llm_invoke_engine.py`：当前引擎是“单 provider + 并发按标的”；保持不变，由 orchestrator 迭代 provider 列表调用即可

## 5. 配置设计（环境变量）

### 5.1 通用项

- `LLM_ENABLED_PROVIDERS`：逗号分隔列表，例如：`openai,anthropic,gemini,qwen,glm,kimi`
- `LLM_MIN_EFFECTIVE_PROVIDERS`：整数，默认 `3`
- `LLM_REQUEST_TIMEOUT_SECONDS`：默认 `60`
- `LLM_MAX_RETRIES`：默认 `1`（最小重试）

### 5.2 Provider 专属项（建议命名）

OpenAI-compatible（OpenAI / Qwen / GLM / Kimi）：
- `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`（可选；默认官方）
- `QWEN_API_KEY`、`QWEN_MODEL`（例：`qwen3.5-plus`）、`QWEN_BASE_URL`
- `GLM_API_KEY`、`GLM_MODEL`、`GLM_BASE_URL`
- `KIMI_API_KEY`、`KIMI_MODEL`、`KIMI_BASE_URL`

Anthropic：
- `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`

Gemini AI Studio：
- `GEMINI_API_KEY`、`GEMINI_MODEL`

### 5.3 密钥安全约束

- 所有 key 只从环境变量读取，不写入配置文件与数据库。
- 通知/日志只允许输出 provider 名称、model 名称（可选）与错误摘要，不允许输出 key 或完整 Authorization header。

## 6. Provider Registry 规则

### 6.1 有效 provider 定义（配置层）

当且仅当以下条件满足时，provider 视为“有效”：
- 出现在 `LLM_ENABLED_PROVIDERS` 中；
- 必需配置齐全（api_key、model、base_url 若必需）；
- 构造 provider 实例不抛异常。

缺失配置时：
- provider 不进入 enabled 列表；
- `disabled_reasons[provider] = "missing_api_key" / "missing_model" / "missing_base_url"` 等短码。

### 6.2 降级判定

- `effective_provider_count = len(enabled_providers)`
- 若 `effective_provider_count < LLM_MIN_EFFECTIVE_PROVIDERS`：
  - run 标记为 `DEGRADED`
  - 只执行 ingestion 入库与审计记录
  - 发送降级通知，包含：
    - `run_id`、`snapshot_date`
    - `effective_provider_count / min_required`
    - `disabled_reasons` 汇总（不含敏感信息）

## 7. 真实投票数据生成

### 7.1 结构化来源

每个 provider 对每个 symbol 的输出：
- 由 `PromptBuilder` 构造 prompt
- 经 provider 调用获得 `raw_text`
- 由 `LLMOutputValidator.validate(raw_text)` 解析为规范化结构：
  - `symbol`
  - `recommendation`
  - `confidence`
  - `scorecard`
  - `rationale: list[str]`
  - `quality_flags`

### 7.2 vote 生成规则

从验证结果转换成 vote：
- `symbol = instrument.symbol`
- `instrument_type = stock|etf`
- `provider = provider.name`
- `recommendation = parsed.recommendation or ""`
- `confidence = parsed.confidence or None`
- `rationale = parsed.rationale or []`

投票聚合：
- 复用 `VotingAggregator`：
  - BUY 票计数规则沿用（推荐/大小写/别名容错）
  - tie-break：票数 -> mean_confidence -> symbol

## 8. HTTP 调用与错误处理（最小一致性）

- 超时：使用 `LLM_REQUEST_TIMEOUT_SECONDS`
- 重试：`LLM_MAX_RETRIES`（仅对网络异常/5xx；对 4xx 不重试）
- 返回解析失败：
  - provider 返回的 raw_text 仍落库
  - `LLMOutputValidator` 标记 `json_parse_error` 等质量标签
- 单 provider 失败不影响其它 provider；失败以 raw_text 或错误摘要形式进入审计（错误摘要不含敏感信息）

## 9. 测试策略

- Provider 单测：
  - 用 monkeypatch/mock 替换 urllib 请求，验证请求 URL/headers/body 结构正确
  - 覆盖非 200、超时、返回非 JSON 的容错路径
- Registry 单测：
  - 缺 key/model 时 provider 被禁用，disabled_reasons 正确
  - `LLM_MIN_EFFECTIVE_PROVIDERS` 与降级逻辑联动
- Orchestrator 单测/e2e：
  - 在 `dry_run=True` 与 mock HTTP 下，能走完整链路并写入审计表
  - 在 `enabled_providers < min_required` 时，run=DEGRADED 且不产生 decisions/trades

## 10. 迁移与兼容

- 保持 `MockLLMProvider` 仍可用（用于本地无网络/无 key 的快速回归）。
- `LLM_ENABLED_PROVIDERS` 默认值可包含 `mock`，便于开发环境最小可跑。
- 真实 provider 的引入不改变 DB schema 的既有字段，只新增配置与 provider 适配器代码。
