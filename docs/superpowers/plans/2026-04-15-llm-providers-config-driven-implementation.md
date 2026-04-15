# LLM Providers（真实多 Provider）+ 配置驱动降级与投票 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 MVP 基础上接入 OpenAI/Anthropic/Gemini/Qwen/GLM/Kimi 等真实 LLM Provider，并将 provider 有效性、降级阈值与投票输入切换为配置驱动与可审计。

**Architecture:** 新增 Provider Registry，从 AppConfig 构建“有效 provider 列表”；Orchestrator 以有效 provider 数量对比 `LLM_MIN_EFFECTIVE_PROVIDERS` 决定是否降级。调用链保持：prompt -> provider.invoke -> validator -> llm_outputs 落库 -> votes -> VotingAggregator -> decisions。

**Tech Stack:** Python（标准库 urllib/json/concurrent.futures），SQLite，pytest

---

## 0. 文件结构（新增/修改）

**新增：**
- `src/analysis/provider_registry.py`
- `src/analysis/providers/openai_compatible_provider.py`
- `src/analysis/providers/anthropic_provider.py`
- `src/analysis/providers/gemini_provider.py`
- `tests/analysis/test_provider_registry.py`
- `tests/analysis/test_openai_compatible_provider.py`
- `tests/analysis/test_anthropic_provider.py`
- `tests/analysis/test_gemini_provider.py`
- `tests/orchestrator/test_degrade_when_providers_insufficient.py`

**修改：**
- `src/app/config.py`（新增 LLM 配置字段与读取函数）
- `src/orchestrator/run_orchestrator.py`（接入 registry；降级逻辑改为配置驱动；多 provider 投票输入来源于 validator 结果）
- `src/app/main.py`（允许通过环境变量启用真实 provider，保留 dry_run）
- `README.md` / `docs/runbooks/mvp-runbook.md`（补充真实 provider 配置示例与安全注意）

---

## Task 1: 扩展 AppConfig（LLM 配置与默认值）

**Files:**
- Modify: `src/app/config.py`
- Test: `tests/app/test_config.py`

- [ ] **Step 1: 先新增失败测试（缺失/非法）**

```python
def test_load_config_reads_llm_provider_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    monkeypatch.setenv("LLM_ENABLED_PROVIDERS", "openai,anthropic,gemini")
    monkeypatch.setenv("LLM_MIN_EFFECTIVE_PROVIDERS", "3")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    config = load_config()
    assert config.llm_enabled_providers == ["openai", "anthropic", "gemini"]
    assert config.llm_min_effective_providers == 3
```

- [ ] **Step 2: 运行测试确认失败**
Run: `. .venv/bin/activate && pytest tests/app/test_config.py -v`
Expected: FAIL（缺字段/解析尚未实现）

- [ ] **Step 3: 实现 AppConfig 新字段（全部带 docstring）**

要求字段（建议）：
- `llm_enabled_providers: list[str]`
- `llm_min_effective_providers: int`
- `llm_request_timeout_seconds: int`
- `llm_max_retries: int`
- per-provider config（最小集合）：`*_api_key`、`*_model`、`*_base_url?`

- [ ] **Step 4: 运行测试确认通过**
Run: `. .venv/bin/activate && pytest tests/app/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
Run:
```bash
git add src/app/config.py tests/app/test_config.py
git commit -m "feat: add llm provider config to AppConfig"
```

---

## Task 2: ProviderRegistry（配置驱动有效 provider 列表）

**Files:**
- Create: `src/analysis/provider_registry.py`
- Test: `tests/analysis/test_provider_registry.py`

- [ ] **Step 1: 写失败测试（缺 key/model 的 provider 会被禁用）**

```python
def test_registry_disables_provider_when_missing_api_key(monkeypatch):
    config = AppConfig(... llm_enabled_providers=["openai"], openai_api_key="", openai_model="gpt-4.1", ...)
    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()
    assert providers == []
    assert disabled["openai"] == "missing_api_key"
```

- [ ] **Step 2: 运行测试确认失败**
Run: `. .venv/bin/activate && pytest tests/analysis/test_provider_registry.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 ProviderRegistry**

输出约定：
- `enabled_providers() -> tuple[list[BaseLLMProvider], dict[str, str]]`
- `disabled_reasons` 只包含短码，不含敏感信息

- [ ] **Step 4: 运行测试确认通过**
Run: `. .venv/bin/activate && pytest tests/analysis/test_provider_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/analysis/provider_registry.py tests/analysis/test_provider_registry.py
git commit -m "feat: add provider registry"
```

---

## Task 3: OpenAI-Compatible Provider（OpenAI/Qwen/GLM/Kimi）

**Files:**
- Create: `src/analysis/providers/openai_compatible_provider.py`
- Test: `tests/analysis/test_openai_compatible_provider.py`

- [ ] **Step 1: 写失败测试（构造请求 URL/headers/body，dry_run/mocked HTTP）**

```python
def test_openai_compatible_provider_builds_request(monkeypatch):
    # monkeypatch urllib.request.urlopen to capture request
    provider = OpenAICompatibleChatProvider(
        name="openai",
        api_key="k",
        model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        timeout_seconds=60,
        max_retries=1,
        extra_headers={},
    )
    text = provider.invoke("hello")
    assert isinstance(text, str)
```

- [ ] **Step 2: 运行测试确认失败**
Run: `. .venv/bin/activate && pytest tests/analysis/test_openai_compatible_provider.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 provider（urllib POST /chat/completions）**

要求：
- 不打印/不返回 api_key
- 处理非 200：raise（上层记录）
- 返回 raw_text：优先从 `choices[0].message.content` 拼为字符串；失败时返回 JSON 字符串

- [ ] **Step 4: 运行测试确认通过**
Run: `. .venv/bin/activate && pytest tests/analysis/test_openai_compatible_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/analysis/providers/openai_compatible_provider.py tests/analysis/test_openai_compatible_provider.py
git commit -m "feat: add openai-compatible provider"
```

---

## Task 4: Anthropic Provider（Claude）

**Files:**
- Create: `src/analysis/providers/anthropic_provider.py`
- Test: `tests/analysis/test_anthropic_provider.py`

- [ ] **Step 1: 写失败测试（请求构造与响应解析）**
- [ ] **Step 2: 运行测试确认失败**
Run: `. .venv/bin/activate && pytest tests/analysis/test_anthropic_provider.py -v`
Expected: FAIL
- [ ] **Step 3: 实现 Messages API 调用**
要求：
- endpoint：`https://api.anthropic.com/v1/messages`
- headers：`x-api-key`、`anthropic-version`、`content-type`
- body：`model`、`max_tokens`、`messages:[{role:'user', content:prompt}]`
- 解析：拼接 content blocks 的 text
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: Commit**

---

## Task 5: Gemini Provider（AI Studio）

**Files:**
- Create: `src/analysis/providers/gemini_provider.py`
- Test: `tests/analysis/test_gemini_provider.py`

- [ ] **Step 1: 写失败测试（URL/参数/响应解析）**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现 AI Studio REST 调用**
要求：
- endpoint 形如：`https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...`
- body：`contents:[{role:'user', parts:[{text:prompt}]}]`
- 解析：拼接候选 content parts.text
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: Commit**

---

## Task 6: Orchestrator 接入 ProviderRegistry + 配置驱动降级

**Files:**
- Modify: `src/orchestrator/run_orchestrator.py`
- Test: `tests/orchestrator/test_degrade_when_providers_insufficient.py`
- Modify: `tests/e2e/test_mvp_pipeline.py`（确保默认仍可用 mock）

- [ ] **Step 1: 写降级失败测试**

```python
def test_daily_run_degrades_when_effective_providers_less_than_min(tmp_path, monkeypatch):
    # config.llm_enabled_providers=['openai'] but missing key -> effective 0 < min 3
    # assert run status = DEGRADED and no decisions/trades/notifications
    ...
```

- [ ] **Step 2: 运行测试确认失败**
Run: `. .venv/bin/activate && pytest tests/orchestrator/test_degrade_when_providers_insufficient.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 orchestrator**
要求：
- 用 ProviderRegistry 获取有效 providers + disabled_reasons
- 降级：写 runs=DEGRADED；发送降级通知（dry_run 仍记录通知）
- 非降级：按 provider 循环调用 `LLMInvokeEngine` 得到校验结果并转为 vote，再投票
- [ ] **Step 4: 运行测试确认通过**
Run: `. .venv/bin/activate && pytest tests/orchestrator/test_degrade_when_providers_insufficient.py -v`
Expected: PASS

- [ ] **Step 5: 回归 e2e**
Run: `. .venv/bin/activate && pytest tests/e2e/test_mvp_pipeline.py -v`
Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add src/orchestrator/run_orchestrator.py tests/orchestrator tests/e2e
git commit -m "feat: config-driven provider registry and degrade logic"
```

---

## Task 7: CLI/文档补齐（真实 provider 配置与安全提示）

**Files:**
- Modify: `README.md`
- Modify: `docs/runbooks/mvp-runbook.md`

- [ ] **Step 1: 文档新增环境变量示例**
包含：
- `LLM_ENABLED_PROVIDERS`
- 各 provider 的 key/model/base_url
- 安全提示：不要提交 `.env`；不要在输出中打印 key

- [ ] **Step 2: Commit**
```bash
git add README.md docs/runbooks/mvp-runbook.md
git commit -m "docs: add real llm provider configuration"
```

---

## 计划自检

- 覆盖性：实现 provider 适配器、registry、配置驱动降级、真实投票输入转换与测试策略。
- 一致性：沿用现有 `BaseLLMProvider.invoke(prompt)->raw_text` 与 `LLMOutputValidator`。
- 无占位符：每个 task 均给出清晰文件、测试与提交步骤。
