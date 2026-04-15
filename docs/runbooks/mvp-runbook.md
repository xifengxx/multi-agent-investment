# MVP Runbook（本地运行手册）

本手册面向个人本地运行/排障，覆盖：

- 创建虚拟环境（.venv）
- 必要环境变量
- daily / weekly 运行方式（含 dry_run）
- 常见故障（输入契约失败 / 降级 / 推送失败）
- 如何按 run_id 回放与定位

---

## 0. 前置条件

- 系统：macOS
- Python：建议 3.11+（3.10 也通常可用）
- 依赖：项目未内置依赖锁文件；最小需要：
  - 运行：`openpyxl`（解析 xlsx）
  - 测试：`pytest`

---

## 1. 创建 .venv

在项目根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install openpyxl pytest
```

---

## 2. 环境变量

### 2.1 必填

- `TELEGRAM_BOT_TOKEN`：Telegram Bot Token（dry_run 下也需要填充一个非空值）
- `TELEGRAM_CHAT_ID`：目标 Chat ID（dry_run 下也需要填充一个非空值）

### 2.2 可选（有默认值）

- `DATA_ROOT`：输入数据目录，默认 `stock_data`
- `SQLITE_PATH`：SQLite DB 文件路径，默认 `data/app.db`
- `ANALYSIS_PARALLELISM`：分析并发数，默认 `3`
- `DRY_RUN`：是否 dry run，默认 `false`（可用 `1/true/yes/on`）
- `APP_ENV`：`dev/test/prod`，默认 `dev`

### 2.3 推荐（本地 dry_run）

```bash
export TELEGRAM_BOT_TOKEN="x"
export TELEGRAM_CHAT_ID="x"
export APP_ENV="test"
export DRY_RUN="true"
export DATA_ROOT="stock_data"
export SQLITE_PATH="data/app.db"
export ANALYSIS_PARALLELISM="3"
```

---

## 3. 输入数据契约（daily 必备）

`DATA_ROOT` 目录下需要一对 xlsx，文件名必须匹配：

- `YYYY-MM-DD_stock.xlsx`
- `YYYY-MM-DD_etf.xlsx`

并且每个 xlsx 的第一行必须包含以下列名（大小写敏感、需完全一致）：

- `Symbol`
- `Description`
- `X_D_Trend_State`
- `X_D_State_Bars`
- `X_W_Trend_State`
- `X_W_State_Bars`
- `X_M_Trend_State`
- `X_M_State_Bars`

---

## 4. Daily 运行方式

### 4.1 CLI（最简：会落库输入证据与快照）

```bash
python -m app.main daily
```

说明：

- 当前 CLI 不会注入 LLM providers，因此会进入 `DEGRADED`（不会产生 decisions / 通知 / ledger）。
- 仍会写入 `runs`、`file_batches`、`instrument_snapshots`，用于验证输入与落库链路。

### 4.2 完整流水线（推荐本地：Mock providers + dry_run）

方式 A：直接跑 E2E 测试（会生成临时 xlsx 并跑完整链路）

```bash
pytest -q tests/e2e/test_mvp_pipeline.py -q
```

方式 B：用一段 Python 代码跑完整 daily（需要你的 `DATA_ROOT` 已放入当日 xlsx 文件对）

```bash
python - <<'PY'
from analysis.providers.mock_provider import MockLLMProvider
from app.config import load_config
from orchestrator.run_orchestrator import daily_run

class Named(MockLLMProvider):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
    @property
    def name(self) -> str:
        return self._name

cfg = load_config()
run_id = daily_run(
    config=cfg,
    snapshot_date=None,   # 为空则自动挑选 DATA_ROOT 中最新可用日期
    providers=[Named("p1"), Named("p2"), Named("p3")],
    dry_run=True,
)
print("run_id=", run_id)
PY
```

---

## 5. Weekly 运行方式

```bash
python -m app.main weekly
```

说明：

- 周报统计基于 `paper_trades.snapshot_date` 的区间过滤（默认 7 天）。
- 若 daily 从未产生 paper_trades（例如一直处于 DEGRADED），周报会是全 0，但仍会落库 `weekly_reviews` 并写入 `notifications`（dry_run 下）。

---

## 6. 常见故障与处理

### 6.1 输入契约失败（ContractViolation / ExcelParseError）

现象：

- `runs.status=FAILED`，`runs.error_message` 包含 “未找到任何符合命名模式” 或 “文件缺少必填列”

排查：

- 确认 `DATA_ROOT` 指向正确目录
- 确认文件名严格为 `YYYY-MM-DD_stock.xlsx / YYYY-MM-DD_etf.xlsx`
- 确认第一行列名完整且匹配（见第 3 节）

### 6.2 降级（DEGRADED）

触发条件：

- `providers` 数量不足（<3）

结果：

- 会落库 runs/file_batches/instrument_snapshots（以及若 providers>0 还会写 llm_outputs）
- 不会生成 decisions / 不会写 paper_trades / positions / 推送消息

处理：

- 本地建议使用 Mock providers（见 4.2）

### 6.3 推送失败（notifications.status=FAILED）

现象：

- `notifications.status=FAILED`
- `notifications.provider_response_json` 可能包含错误信息

处理：

- dry_run 下应始终 `SENT`
- 非 dry_run 时检查：
  - `TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID` 是否正确
  - 网络连通性

---

## 7. 按 run_id 回放与定位

### 7.1 快速查看 run 状态

```bash
sqlite3 "$SQLITE_PATH" "select run_id, snapshot_date, status, started_at, ended_at, error_message from runs order by started_at desc limit 10;"
```

### 7.2 用 run_id 定位输入证据（文件路径/哈希）

```bash
sqlite3 "$SQLITE_PATH" "select batch_id, snapshot_date, stock_file, etf_file, stock_sha256, etf_sha256 from file_batches where run_id = '<RUN_ID>';"
```

### 7.3 回放（重跑同一 snapshot_date）

说明：当前实现的 `daily_run` 会生成新的 `run_id`；“回放”指使用同一对输入文件与同一 `snapshot_date` 重跑流水线以复现问题。

步骤：

1. 从 `file_batches` 查到该次运行的 `snapshot_date` 以及输入文件路径
2. 确保这对文件仍在 `DATA_ROOT` 下（或临时把 `DATA_ROOT` 指到它们所在目录）
3. 执行：

```bash
python - <<'PY'
from analysis.providers.mock_provider import MockLLMProvider
from app.config import load_config
from orchestrator.run_orchestrator import daily_run

class Named(MockLLMProvider):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
    @property
    def name(self) -> str:
        return self._name

cfg = load_config()
run_id = daily_run(
    config=cfg,
    snapshot_date="<SNAPSHOT_DATE>",
    providers=[Named("p1"), Named("p2"), Named("p3")],
    dry_run=True,
)
print("replay run_id=", run_id)
PY
```

### 7.4 用 SQL 定位每一阶段的数据是否写入

```bash
sqlite3 "$SQLITE_PATH" "
select
  (select count(*) from file_batches where run_id='<RUN_ID>') as file_batches,
  (select count(*) from llm_outputs where run_id='<RUN_ID>') as llm_outputs,
  (select count(*) from decisions where run_id='<RUN_ID>') as decisions,
  (select count(*) from notifications where run_id='<RUN_ID>') as notifications,
  (select count(*) from paper_trades where run_id='<RUN_ID>') as paper_trades
;"
```

