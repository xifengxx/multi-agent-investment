# L3 File-Upload Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 L3 分析链路改为“优先附件上传、失败降级文本摘要”，复刻人工网页端流程并保持全链路可审计。

**Architecture:** 在 provider 抽象层新增“文件输入能力”，Orchestrator 先尝试 `invoke_with_files`（stock+etf 文件与 prompt），若 provider 不支持或上传失败则自动降级到文本摘要 prompt。两条链路统一走现有 `panel_output_validator -> llm_reports -> llm_outputs -> voting -> decision -> notification`。

**Tech Stack:** Python 3.14、urllib、sqlite3、pytest、openpyxl

---

### Task 1: 扩展 Provider 抽象与能力声明

**Files:**
- Modify: `src/analysis/providers/base.py`
- Test: `tests/analysis/test_provider_registry.py`

- [ ] **Step 1: 写失败测试，验证 provider 能力元信息可读**

```python
def test_provider_capability_default_flags() -> None:
    provider = MockLLMProvider()
    assert provider.supports_file_input is False
```

- [ ] **Step 2: 运行测试确认失败（当前无 supports_file_input）**

Run: `pytest tests/analysis/test_provider_registry.py -k capability -v`  
Expected: `AttributeError` 或断言失败

- [ ] **Step 3: 在抽象基类增加文件能力接口（带默认实现）**

```python
class BaseLLMProvider(ABC):
    @property
    def supports_file_input(self) -> bool:
        """声明 provider 是否支持附件输入。"""
        return False

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """默认不支持附件输入；子类按需覆盖。"""
        raise NotImplementedError("file_input_not_supported")
```

- [ ] **Step 4: 运行相关测试确认通过**

Run: `pytest tests/analysis/test_provider_registry.py -v`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/analysis/providers/base.py tests/analysis/test_provider_registry.py
git commit -m "feat: add file-input capability to base provider"
```

### Task 2: 实现 Gemini/OpenAI 双通道调用（附件优先 + 文本保底）

**Files:**
- Modify: `src/analysis/providers/gemini_provider.py`
- Modify: `src/analysis/providers/openai_compatible_provider.py`
- Test: `tests/analysis/test_gemini_provider.py`
- Test: `tests/analysis/test_openai_compatible_provider.py`

- [ ] **Step 1: 写失败测试，覆盖 `invoke_with_files` 行为**

```python
def test_gemini_invoke_with_files_uses_file_parts(monkeypatch):
    provider = GeminiProvider(model="gemini-2.0-flash", api_key="k")
    # mock _post 返回 candidates[0].content.parts[0].text
    # 断言请求体包含 inlineData 或 fileData 结构
```

```python
def test_openai_invoke_with_files_adds_input_file_parts(monkeypatch):
    provider = OpenAICompatibleProvider(base_url="https://openrouter.ai/api/v1", model="openai/gpt-oss-20b", api_key="k")
    # mock _post 并断言 messages[*].content 包含 type=file/inline file payload
```

- [ ] **Step 2: 跑测试确认失败**

Run:  
`pytest tests/analysis/test_gemini_provider.py -k with_files -v`  
`pytest tests/analysis/test_openai_compatible_provider.py -k with_files -v`  
Expected: 缺少方法或请求结构不符

- [ ] **Step 3: 实现附件调用与降级错误码封装**

```python
@property
def supports_file_input(self) -> bool:
    return True

def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
    if not file_paths:
        raise RuntimeError("file_upload_failed:empty_file_paths")
    # 读取文件 -> 构造 multipart/inline payload（provider-specific）
    # 成功返回文本
    # 失败抛 RuntimeError("file_upload_failed:<reason>")
```

- [ ] **Step 4: 保持 `invoke(prompt)` 文本接口不变**

Run:  
`pytest tests/analysis/test_gemini_provider.py -v`  
`pytest tests/analysis/test_openai_compatible_provider.py -v`  
Expected: 原有测试继续通过

- [ ] **Step 5: 提交**

```bash
git add src/analysis/providers/gemini_provider.py src/analysis/providers/openai_compatible_provider.py tests/analysis/test_gemini_provider.py tests/analysis/test_openai_compatible_provider.py
git commit -m "feat: add file-input invoke for gemini and openai-compatible providers"
```

### Task 3: Orchestrator 分流策略（附件主链路，文本摘要降级）

**Files:**
- Modify: `src/orchestrator/run_orchestrator.py`
- Modify: `src/analysis/panel_prompt_builder.py`
- Test: `tests/orchestrator/test_daily_run_flow.py`
- Test: `tests/orchestrator/test_degrade_when_runtime_provider_fails.py`

- [ ] **Step 1: 写失败测试，验证“附件失败后自动降级文本”**

```python
def test_daily_run_fallback_to_text_when_file_upload_fails(tmp_path):
    # provider.supports_file_input=True
    # invoke_with_files 抛 file_upload_failed
    # invoke(prompt) 返回有效 JSON
    # 断言 run=SUCCEEDED 且 llm_outputs>0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/orchestrator/test_daily_run_flow.py -k fallback -v`  
Expected: 没有分流逻辑导致失败

- [ ] **Step 3: 实现分流逻辑与 runtime_errors 分类**

```python
if provider.supports_file_input:
    try:
        raw = provider.invoke_with_files(prompt=prompt, file_paths=[stock_path, etf_path])
    except Exception as exc:
        runtime_errors[f"{provider.name}.{inst_type}.file"] = str(exc)
        raw = provider.invoke(prompt)  # 文本降级
else:
    raw = provider.invoke(prompt)
```

- [ ] **Step 4: 文本降级输入改为“摘要 prompt”而非全量面板**

```python
summary_prompt = prompt_builder.build_summary(...)
raw = provider.invoke(summary_prompt)
```

- [ ] **Step 5: 回归 orchestrator 测试**

Run: `pytest tests/orchestrator -v`  
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/orchestrator/run_orchestrator.py src/analysis/panel_prompt_builder.py tests/orchestrator/test_daily_run_flow.py tests/orchestrator/test_degrade_when_runtime_provider_fails.py
git commit -m "feat: add file-first then text-fallback orchestration flow"
```

### Task 4: 文本降级压缩策略（避免超大 prompt 400）

**Files:**
- Modify: `src/analysis/panel_prompt_builder.py`
- Modify: `src/app/config.py`
- Test: `tests/analysis/test_panel_prompt_builder.py`
- Test: `tests/app/test_config.py`

- [ ] **Step 1: 写失败测试，验证摘要 prompt 规模受控**

```python
def test_build_summary_prompt_has_size_budget():
    prompt = builder.build_summary(...)
    assert len(prompt) < 120_000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/analysis/test_panel_prompt_builder.py -k summary -v`  
Expected: 无 summary builder 或长度超预算

- [ ] **Step 3: 增加摘要构建函数与预算配置**

```python
def build_summary(..., max_symbols: int, max_patterns: int) -> str:
    # 输出 data_overview + reversal candidates + top symbol candidates
    # 不包含完整 series 明细
```

```python
panel_fallback_max_symbols: int = 120
panel_fallback_max_patterns: int = 40
```

- [ ] **Step 4: 跑配置与 prompt 单测**

Run:  
`pytest tests/app/test_config.py -v`  
`pytest tests/analysis/test_panel_prompt_builder.py -v`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/analysis/panel_prompt_builder.py src/app/config.py tests/analysis/test_panel_prompt_builder.py tests/app/test_config.py
git commit -m "feat: add bounded summary prompt for text fallback"
```

### Task 5: 端到端验证（真实非 dry_run）

**Files:**
- Modify: `docs/runbooks/mvp-runbook.md`
- Test: `tests/e2e/test_mvp_pipeline.py`

- [ ] **Step 1: 更新 E2E 测试覆盖 file-first/fallback 两种路径**

```python
def test_mvp_pipeline_file_first_then_fallback_paths(tmp_path):
    # case1: invoke_with_files 成功
    # case2: invoke_with_files 失败但 invoke(summary) 成功
```

- [ ] **Step 2: 跑 E2E + 全量回归**

Run:  
`pytest tests/e2e/test_mvp_pipeline.py -v`  
`pytest -q`  
Expected: PASS

- [ ] **Step 3: 本地真实验证（你当前环境）**

Run:

```bash
set -a && source ./.env.local && set +a
export DRY_RUN=false
export LLM_ENABLED_PROVIDERS="gemini,openai"
PYTHONPATH=src .venv/bin/python -m app.main daily
```

Expected:
- `runs.status=SUCCEEDED`
- `notifications.status=SENT AND dry_run=0`
- `llm_reports/llm_outputs/decisions/paper_trades` 均有新增

- [ ] **Step 4: 更新 runbook**

写明：
- file-first/fallback 机制
- 常见错误码说明
- 建议的联调参数（`PANEL_MAX_SHEETS` 与 fallback budget）

- [ ] **Step 5: 提交**

```bash
git add tests/e2e/test_mvp_pipeline.py docs/runbooks/mvp-runbook.md
git commit -m "test/docs: cover hybrid file-first fallback flow and runbook"
```

---

## Spec 覆盖自检

- 已覆盖“主附件链路 + 文本降级链路”核心需求（Task 1-3）。
- 已覆盖“避免输入过大”需求（Task 4）。
- 已覆盖“非 dry_run 真推送与落库验证”需求（Task 5）。
- 已覆盖“保持统一 schema 校验审计链路”需求（Task 3 + Task 5）。

## 占位符自检

- 无 `TODO/TBD/implement later`。
- 每个任务含明确文件、命令、预期结果与提交动作。

## 一致性自检

- 统一使用 `supports_file_input` / `invoke_with_files`。
- 统一采用 `file_upload_failed:*` 运行期错误分组思路。
- Orchestrator 始终以 `validator` 作为结构化输出入口。
