# Web Upload + Worker Queue (Same-Host Disk + Nginx) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在云端提供 Web 上传入口与并发 Worker 队列，上传 stock/etf xlsx 后自动触发 daily run，并通过 Nginx 暴露 reports 供 TG 点击查看。

**Architecture:** Web 负责上传与查询；任务进入 SQLite job 表；多个 worker 进程抢占执行 daily_run；报告写入同机磁盘 `DATA_ROOT/reports/...`，由 Nginx 暴露为公网 `/reports/` 静态路径。

**Tech Stack:** Python（现有项目）、SQLite（现有）、Nginx（部署侧）、HTTP API（轻量服务：建议 FastAPI/Starlette，如仓库已使用则沿用；若未使用则采用标准库 wsgi/简单框架需在实现前确认依赖）。

---

## 0. File/Module Map（将要新增/修改的文件）

**Create**
- `src/web/__init__.py`
- `src/web/server.py`：Web 入口（启动 HTTP 服务）
- `src/web/routes_upload.py`：上传 API
- `src/web/routes_runs.py`：runs 列表/详情 API
- `src/worker/__init__.py`
- `src/worker/job_worker.py`：worker 主循环与 job 抢占执行
- `src/storage/repositories/job_repository.py`：jobs 表读写与抢占锁
- `src/storage/repositories/upload_repository.py`（可选）：uploads 表读写（若需要）
- `src/storage/schema.sql`：新增 jobs/snapshot_locks（或等价）表
- `docs/runbooks/web-deploy.md`：Nginx + systemd 部署示例（MVP）

**Modify**
- `src/app/config.py`：新增 Web/Worker 配置（端口、worker 并发、public base url）
- `src/orchestrator/run_orchestrator.py`：允许从“上传目录”读取指定文件路径（避免并发覆盖标准文件名），或改造 file_batches/scan 逻辑以支持 inbox 路径。
- `src/notification/message_formatter.py`：TG 消息里放公网 reports URL（已具备基础字段，需改成真正 URL）

**Test**
- `tests/web/test_upload_api.py`
- `tests/worker/test_job_worker_claims_and_runs.py`
- `tests/storage/test_job_repository.py`

---

## Task 1: 设计并创建 jobs 表 + snapshot_date 锁表

**Files:**
- Modify: `src/storage/schema.sql`
- Create: `src/storage/repositories/job_repository.py`
- Test: `tests/storage/test_job_repository.py`

- [ ] **Step 1: Write failing tests for job claim semantics**

```python
def test_claim_next_job_is_exclusive(tmp_path):
    # 1) insert two jobs
    # 2) claim from worker A and worker B
    # 3) ensure same job_id cannot be claimed twice
    assert True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_job_repository.py -q`  
Expected: FAIL (module/function missing)

- [ ] **Step 3: Add schema tables**

Add to schema:
- `jobs`：`job_id TEXT PK, run_id TEXT, snapshot_date TEXT, status TEXT, attempts INT, locked_by TEXT NULL, locked_at TEXT NULL, last_error TEXT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP`
- `snapshot_locks`：`snapshot_date TEXT PK, locked_by TEXT, locked_at TEXT`（或把锁合并到 jobs 表，保持简单）

- [ ] **Step 4: Implement JobRepository**

Core API:
- `enqueue(run_id, snapshot_date) -> job_id`
- `claim_next(worker_id, max_attempts) -> job_row | None`（原子更新 locked_by/locked_at）
- `mark_done(job_id)`
- `mark_failed(job_id, error)`

- [ ] **Step 5: Run tests**

Run: `pytest tests/storage/test_job_repository.py -q`  
Expected: PASS

---

## Task 2: Web 上传 API（接收两份文件 + 校验 + 入队）

**Files:**
- Create: `src/web/server.py`, `src/web/routes_upload.py`
- Modify: `src/app/config.py`
- Test: `tests/web/test_upload_api.py`

- [ ] **Step 1: Decide framework**

If repository already uses FastAPI/Starlette: use it.  
If not: prefer `http.server` + minimal JSON API (multipart parsing is harder) OR add FastAPI dependency (must confirm).

- [ ] **Step 2: Write failing test for upload endpoint**

Test behaviors:
- accepts multipart (stock+etf)
- validates naming & same snapshot_date
- stores to `DATA_ROOT/inbox/{upload_id}/...`
- creates `runs` row (RUNNING)
- enqueues job row

- [ ] **Step 3: Implement upload endpoint**

Pseudo flow:
1) parse multipart
2) validate file names contain `_stock.xlsx` and `_etf.xlsx`
3) extract snapshot_date and ensure equal
4) persist files to inbox directory
5) create run_id, insert run (RUNNING)
6) insert file_batches row referencing inbox paths
7) enqueue job
8) return JSON: `{run_id, snapshot_date, job_id, reports_url}`

- [ ] **Step 4: Run tests**

Run: `pytest tests/web/test_upload_api.py -q`  
Expected: PASS

---

## Task 3: Worker 执行（并发）+ snapshot_date 互斥

**Files:**
- Create: `src/worker/job_worker.py`
- Modify: `src/orchestrator/run_orchestrator.py`（支持从 file_batches 的路径直接读取，不依赖扫描目录）
- Test: `tests/worker/test_job_worker_claims_and_runs.py`

- [ ] **Step 1: Write failing test for worker processing one job**

Expected:
- worker claims job
- runs daily_run using file paths from file_batches
- writes reports + summary.md
- marks job DONE and run SUCCEEDED/DEGRADED/FAILED

- [ ] **Step 2: Implement snapshot_date lock**

Worker should:
- before running: acquire lock for snapshot_date (insert/update snapshot_locks)
- after run: release lock

- [ ] **Step 3: Implement worker loop**

Key knobs from config:
- `worker_id`
- poll interval
- max attempts

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_job_worker_claims_and_runs.py -q`  
Expected: PASS

---

## Task 4: Runs 查询 API（列表/详情）+ reports URL

**Files:**
- Create: `src/web/routes_runs.py`
- Modify: `src/notification/message_formatter.py`, `src/app/config.py`

- [ ] **Step 1: Add config**

Add:
- `PUBLIC_BASE_URL`（例如 `https://your-domain`）
- `REPORTS_PUBLIC_PATH`（默认 `/reports`）

- [ ] **Step 2: Implement runs list endpoint**

Return: `{run_id, snapshot_date, status, started_at, ended_at, reports_url}`

- [ ] **Step 3: Implement run detail endpoint**

Return:
- decisions (stock/etf)
- notification status + error
- provider reports list (from llm_reports)
- `summary_url` and `reports_url`

---

## Task 5: 部署 runbook（Nginx + systemd）

**Files:**
- Create: `docs/runbooks/web-deploy.md`

- [ ] **Step 1: Write Nginx example**

Requirements:
- `/reports/` -> `DATA_ROOT/reports/` (autoindex on for MVP)
- `/api/` -> web server upstream

- [ ] **Step 2: Write systemd examples**

Units:
- `multi-agent-web.service`
- `multi-agent-worker@.service` (multiple instances for concurrency)

---

## Spec Coverage Self-Review

- 允许并发：通过 `jobs` + `claim_next` + `snapshot_locks` 实现
- 同机磁盘+Nginx：reports 由 Nginx 暴露，TG 发送公网 URL
- 两段式：Web 上传入 inbox（upload），worker 执行 daily（analyze）

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-web-upload-and-worker-queue.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

