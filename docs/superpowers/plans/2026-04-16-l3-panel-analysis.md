# L3 Panel Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 L3 从逐标的调用重构为“按文件面板调用一次”，每个 provider 对 stock/etf 各调用一次，输出结构化报告 + Top10，并复用现有投票/推送/账本闭环。

**Architecture:** 新增 Panel Parser + Panel Prompt Builder + Panel Output Validator + 报告级落库（llm_reports）与 Markdown 报告落盘。投票输入由报告 Top10 展开为等权 BUY 票，沿用现有 VotingAggregator/decisions/paper_trades。

**Tech Stack:** Python 3、openpyxl、sqlite3、urllib.request（已有）、pytest。

---

## Scope Check（本计划覆盖范围）

- ✅ 仅重构 L3 调用粒度与数据流（按文件面板分析）
- ✅ ETF 无 Price：收益类指标不做（validator 强制 metrics= null，并在 notes 解释）
- ✅ Telegram 仅展示简版逻辑；完整内容写 Markdown
- ❌ 不做 ETF/Stock 外部行情拉取与收益回测（后续）
- ❌ 不做加权投票（后续）

---

## File Map（将创建/修改的文件）

**Create**
- `src/analysis/panel_schema.py`（枚举 + schema 常量）
- `src/analysis/panel_parser.py`（多子表 Excel → 面板数据）
- `src/analysis/panel_prompt_builder.py`（用户 Prompt + 面板 JSON → 统一 prompt）
- `src/analysis/panel_output_validator.py`（报告级 JSON 校验 + Top10 展开）
- `src/storage/repositories/llm_report_repository.py`（llm_reports 表仓储）
- `src/reporting/markdown_report_writer.py`（写入 `${DATA_ROOT}/reports/...`）

**Modify**
- `src/storage/schema.sql`（新增 llm_reports 表）
- `src/orchestrator/run_orchestrator.py`（daily_run/weekly_run：替换 L3 调用链路）
- `src/analysis/providers/mock_provider.py`（支持 panel prompt 返回报告 JSON）
- （可能）`src/analysis/llm_output_validator.py`（若需要兼容 detail 字段）

**Tests**
- `tests/analysis/test_panel_parser.py`
- `tests/analysis/test_panel_output_validator.py`
- `tests/storage/test_schema_new_tables.py`（扩展断言 llm_reports）
- `tests/orchestrator/test_daily_run_flow.py`（更新为 panel 调用）

---

### Task 1: DB Schema + Repository（llm_reports）

**Files:**
- Modify: `src/storage/schema.sql`
- Create: `src/storage/repositories/llm_report_repository.py`
- Modify: `tests/storage/test_schema_new_tables.py`

- [ ] **Step 1: Write failing schema test**

在 `tests/storage/test_schema_new_tables.py` 增加断言：

```python
assert _table_exists(conn, "llm_reports")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_schema_new_tables.py -v`  
Expected: FAIL（llm_reports 不存在）

- [ ] **Step 3: Implement schema.sql llm_reports**

在 `src/storage/schema.sql` 增加：

```sql
CREATE TABLE IF NOT EXISTS llm_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    provider TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    json_text TEXT,
    is_valid INTEGER NOT NULL CHECK (is_valid IN (0, 1)),
    quality_flags TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_reports_run_id ON llm_reports(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_reports_lookup ON llm_reports(run_id, snapshot_date, instrument_type, provider);
```

- [ ] **Step 4: Add repository**

创建 `LLMReportRepository`，接口最小化：

```python
class LLMReportRepository:
    def insert_report(...)->int: ...
    def list_reports(run_id: str)->list[sqlite3.Row]: ...
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/storage/test_schema_new_tables.py -v`  
Expected: PASS

- [ ] **Step 6: Commit（可选）**

```bash
git add src/storage/schema.sql src/storage/repositories/llm_report_repository.py tests/storage/test_schema_new_tables.py
git commit -m "feat: add llm_reports audit table"
```

---

### Task 2: Panel Schema（pattern_id 枚举 + 输出结构常量）

**Files:**
- Create: `src/analysis/panel_schema.py`

- [ ] **Step 1: Write failing test skeleton**

新增 `tests/analysis/test_panel_output_validator.py`（先只断言 enum 集合存在）：

```python
from analysis.panel_schema import PATTERN_IDS
assert "D:-1->0" in PATTERN_IDS
```

- [ ] **Step 2: Run test (expect fail import)**

Run: `pytest tests/analysis/test_panel_output_validator.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement panel_schema.py**

包括：
- `PATTERN_IDS: frozenset[str]`
- `SIGNAL_STRENGTHS: frozenset[str] = {"strong","watch","risk"}`
- `SCHEMA_VERSION="v1"`

- [ ] **Step 4: Run test (expect pass)**

Run: `pytest tests/analysis/test_panel_output_validator.py -v`  
Expected: PASS（仅 enum 断言）

---

### Task 3: Panel Parser（多子表 Excel → 面板数据）

**Files:**
- Create: `src/analysis/panel_parser.py`
- Test: `tests/analysis/test_panel_parser.py`

- [ ] **Step 1: Write failing test**

构造一个带 2 张日期子表的 workbook，验证：
- 子表名被按日期排序
- stock 面板包含 Price/Volume
- etf 面板缺 Price/Volume 时保持 None

```python
def test_parse_panel_workbook_reads_all_date_sheets(tmp_path: Path) -> None:
    # 使用 openpyxl 写入两个 sheet: 2026-04-08 / 2026-04-09
    # 每个 sheet 写入 header + 2 行 symbol
    # 调用 parse_panel_xlsx(...) 后断言 sheet_count/date_range/symbol_count
    ...
```

- [ ] **Step 2: Run test (expect fail)**

Run: `pytest tests/analysis/test_panel_parser.py -v`  
Expected: FAIL（函数不存在）

- [ ] **Step 3: Implement parser**

建议 API：

```python
def parse_panel_xlsx(*, file_path: Path, instrument_type: str, max_sheets: int) -> dict:
    # 返回面板 dict：sheet_count/date_range/symbol_count/symbols[...]
```

规则：
- sheet 名能 parse 为 `YYYY-MM-DD` 才纳入（其它 sheet 忽略）
- series 按日期升序
- symbol/description 以最新日期为准（便于展示）

- [ ] **Step 4: Run test (expect pass)**

Run: `pytest tests/analysis/test_panel_parser.py -v`  
Expected: PASS

---

### Task 4: Panel Prompt Builder（用户 Prompt + 面板数据 → Prompt）

**Files:**
- Create: `src/analysis/panel_prompt_builder.py`
- Test: `tests/analysis/test_panel_prompt_builder.py`（新增）

- [ ] **Step 1: Write failing test**

断言 prompt 包含：
- 用户提供的固定 Prompt 文案
- `instrument_type`、`snapshot_date`
- “输出约束 JSON schema_version=v1”片段

- [ ] **Step 2: Implement builder**

建议：

```python
class PanelPromptBuilder:
  def build(self, *, snapshot_date: str, instrument_type: str, panel_json: dict) -> str:
      ...
```

---

### Task 5: Panel Output Validator（报告校验 + Top10 展开）

**Files:**
- Create: `src/analysis/panel_output_validator.py`
- Test: `tests/analysis/test_panel_output_validator.py`（扩展）

- [ ] **Step 1: Write failing tests**

覆盖：
- 合法 stock 报告：is_valid True，top10 长度=10
- etf 报告：metrics 收益字段必须为 null，否则 is_valid False + quality_flags
- pattern_id 不在枚举：is_valid False + quality_flags
- signal_strength 非 strong/watch/risk：is_valid False

- [ ] **Step 2: Implement validator**

建议返回结构：

```python
@dataclass(frozen=True)
class PanelValidationResult:
  raw_text: str
  json_text: str | None
  parsed: dict[str, Any]
  is_valid: bool
  quality_flags: list[str]
  top10_items: list[dict[str, Any]]  # 每项含 symbol/confidence/brief/detail/signal_strength
```

并提供展开函数：

```python
def expand_top10_to_llm_output_payloads(...)->list[dict]:
  # 返回用于写入 llm_outputs 的 {symbol, json_text, raw_text, is_valid, quality_flags}
```

其中写入 llm_outputs 的 json_text（兼容 VotingAggregator）：
- `symbol`
- `recommendation="BUY"`
- `confidence`
- `rationale=[entry_logic_brief]`

- [ ] **Step 3: Run tests**

Run: `pytest tests/analysis/test_panel_output_validator.py -v`  
Expected: PASS

---

### Task 6: Markdown Report Writer（DATA_ROOT/reports/...）

**Files:**
- Create: `src/reporting/markdown_report_writer.py`
- Test: `tests/reporting/test_markdown_report_writer.py`

- [ ] **Step 1: Write failing test**

写入临时 data_root，调用 `write_report(...)` 后断言：
- 文件路径存在：`{data_root}/reports/{snapshot_date}/{run_id}/{instrument_type}_{provider}.md`
- 文件包含：标题、Top10、pattern 表（至少 pattern_id 列表）

- [ ] **Step 2: Implement writer**

渲染策略：
- 用 Markdown 标题分节（Data Overview / Patterns / Signals / Top10 / Followups）
- Top10 里同时写 brief 与 detail

---

### Task 7: Orchestrator 重构（daily_run：按文件面板调用）

**Files:**
- Modify: `src/orchestrator/run_orchestrator.py`
- Modify: `tests/orchestrator/test_daily_run_flow.py`
- Modify: `tests/orchestrator/test_degrade_when_runtime_provider_fails.py`（如需要）

- [ ] **Step 1: Write failing integration test**

目标：验证调用次数与落库形态改变。

思路：
- 注入 2 个 mock provider（name 固定），每次 invoke 返回固定的合法 panel JSON（含 top10 10 个）
- 跑 `daily_run(..., dry_run=True)`：
  - llm_reports 应有：providers * 2 条
  - llm_outputs 应有：providers * 2 * 10 条
  - decisions/paper_trades/notifications 正常产生

- [ ] **Step 2: Implement daily_run 新链路**

替换原来的：
- parse_validated_file_pair → per-symbol llm_invoke_engine

为：
- 仍然：挑文件对 + 写 file_batches
- 构建 panel_json（stock/etf）
- for provider in providers:
  - 对 stock: build prompt → provider.invoke → panel_validator.validate → 写 llm_reports → 展开 top10 写 llm_outputs → 生成 votes
  - 对 etf: 同上
- 聚合 votes → decisions → telegram → paper ledger
- 写 Markdown 报告（每 provider×type）

保留：
- provider 不足/运行期失败降级逻辑（min_effective_providers）

- [ ] **Step 3: Run orchestrator tests**

Run: `pytest tests/orchestrator -v`  
Expected: PASS

---

### Task 8: E2E 回归

**Files:**
- Modify: `tests/e2e/test_mvp_pipeline.py`（若依赖 llm_outputs 数量）

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`  
Expected: PASS

- [ ] **Step 2: Local sanity run (dry_run)**

Run (示例)：

```bash
set -a && source ./.env.local && set +a
. .venv/bin/activate
export DRY_RUN=true
export LLM_REQUEST_TIMEOUT_SECONDS=120
export ANALYSIS_PARALLELISM=2
export PANEL_MAX_SHEETS=60
PYTHONPATH=src python -m app.main daily
```

Expected:
- `runs.status=SUCCEEDED`
- `${DATA_ROOT}/reports/{snapshot_date}/{run_id}/summary.md` 存在

