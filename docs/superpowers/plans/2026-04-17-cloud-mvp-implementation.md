# 云端 MVP（Nginx + Web Upload + Worker Queue）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阿里云单机（公网 IP、无域名）上实现 Web 上传 + SQLite Job Queue + 2 Worker 并发执行，并由 Nginx 暴露 `/reports/` 供 Telegram 点击查看 `summary.md`。

**Architecture:** Nginx 对外监听 80；`/reports/` 静态映射 `DATA_ROOT/reports`；`/api/` 反代到仅监听 `127.0.0.1:8000` 的 Web API。Web API 将上传文件落盘至 `DATA_ROOT/inbox/{upload_id}/...` 并写入 SQLite 的 `runs/file_batches/jobs`。Worker 进程（2 个 systemd 实例）通过 `jobs` 原子抢占任务，并用 `snapshot_locks` 实现同日互斥，然后调用 orchestrator 执行 daily 流程、产出报告并推送 Telegram（消息包含公网 reports 链接）。

**Tech Stack:** Python（现有项目）、SQLite（现有）、pytest（现有测试模式）、Nginx（部署侧）、systemd（部署侧）、HTTP Server（Python 标准库 `http.server`，MVP 不新增第三方依赖）。

**Note:** 计划中不包含任何 `git commit` 步骤（除非你明确要求），实现与验证以测试通过为准。

---

## 0. File/Module Map（将要新增/修改的文件）

**Create**
- `src/storage/repositories/job_repository.py`：`jobs` + `snapshot_locks` 的读写与原子抢占
- `src/web/__init__.py`
- `src/web/server.py`：Web 服务入口（ThreadingHTTPServer + 路由分发）
- `src/web/routes_upload.py`：`POST /api/upload`
- `src/web/routes_runs.py`：`GET /api/runs`、`GET /api/runs/{run_id}`
- `src/worker/__init__.py`
- `src/worker/job_worker.py`：worker 抢占/互斥/执行/回写
- `tests/storage/test_job_repository.py`
- `tests/web/test_upload_api.py`
- `tests/web/test_runs_api.py`
- `tests/worker/test_job_worker_claims_and_runs.py`
- `docs/runbooks/web-deploy.md`：Nginx + systemd 部署示例

**Modify**
- `src/storage/schema.sql`：新增 `jobs` 与 `snapshot_locks`
- `src/storage/repositories/run_repository.py`：新增 `list_runs(...)`
- `src/app/config.py`：新增 Web/Worker 相关 env（`API_TOKEN/WEB_*` 等）
- `src/orchestrator/run_orchestrator.py`：新增“执行既有 run_id 且使用指定文件路径”的入口（不影响现有 `daily_run`）

---

## Task 1: SQLite Schema + JobRepository（jobs + snapshot_locks）

**Files:**
- Modify: [schema.sql](file:///Users/mac/Documents/trae_projects/multi_agent_investment/.worktrees/feature-mvp-task1/src/storage/schema.sql)
- Create: `src/storage/repositories/job_repository.py`
- Test: `tests/storage/test_job_repository.py`

- [ ] **Step 1: Write failing tests for job/lock semantics**

Create `tests/storage/test_job_repository.py`：

```python
"""jobs/snapshot_locks 表仓储行为测试。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.job_repository import JobRepository, SnapshotLockRepository  # noqa: E402


def test_claim_next_job_is_exclusive(tmp_path: Path) -> None:
    """同一个 job 不应被两个 worker 同时 claim。"""
    db_path = tmp_path / "app.db"
    conn_a = open_sqlite_connection(db_path)
    conn_b = open_sqlite_connection(db_path)
    try:
        init_db(conn_a)
        repo_a = JobRepository(conn_a)
        repo_b = JobRepository(conn_b)

        job_id = repo_a.enqueue(run_id="run-1", snapshot_date="2026-04-17")

        claimed_a = repo_a.claim_next(worker_id="w1", max_attempts=3)
        claimed_b = repo_b.claim_next(worker_id="w2", max_attempts=3)
    finally:
        conn_a.close()
        conn_b.close()

    assert job_id
    assert claimed_a is not None
    assert str(claimed_a["job_id"]) == job_id
    assert claimed_b is None


def test_snapshot_lock_is_exclusive(tmp_path: Path) -> None:
    """同一 snapshot_date 只允许一个 worker 获取互斥锁。"""
    db_path = tmp_path / "app.db"
    conn_a = open_sqlite_connection(db_path)
    conn_b = open_sqlite_connection(db_path)
    try:
        init_db(conn_a)
        lock_a = SnapshotLockRepository(conn_a)
        lock_b = SnapshotLockRepository(conn_b)

        ok1 = lock_a.try_acquire(snapshot_date="2026-04-17", worker_id="w1", now_iso="2026-04-17T00:00:00+00:00")
        ok2 = lock_b.try_acquire(snapshot_date="2026-04-17", worker_id="w2", now_iso="2026-04-17T00:00:01+00:00")
    finally:
        conn_a.close()
        conn_b.close()

    assert ok1 is True
    assert ok2 is False


def test_snapshot_lock_can_be_released_only_by_owner(tmp_path: Path) -> None:
    """互斥锁只能由持有者释放。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        lock_repo = SnapshotLockRepository(conn)

        ok = lock_repo.try_acquire(snapshot_date="2026-04-17", worker_id="w1", now_iso="2026-04-17T00:00:00+00:00")
        released_wrong = lock_repo.release(snapshot_date="2026-04-17", worker_id="w2")
        released_right = lock_repo.release(snapshot_date="2026-04-17", worker_id="w1")
    finally:
        conn.close()

    assert ok is True
    assert released_wrong is False
    assert released_right is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/storage/test_job_repository.py
```

Expected: FAIL（模块不存在/表不存在）。

- [ ] **Step 3: Add schema tables**

Edit [schema.sql](file:///Users/mac/Documents/trae_projects/multi_agent_investment/.worktrees/feature-mvp-task1/src/storage/schema.sql) 末尾追加：

```sql
-- Job Queue（Web 上传后入队，Worker 抢占执行）
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT,
    locked_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_snapshot_date ON jobs(snapshot_date);

-- 同一 snapshot_date 互斥锁
CREATE TABLE IF NOT EXISTS snapshot_locks (
    snapshot_date TEXT PRIMARY KEY,
    locked_by TEXT NOT NULL,
    locked_at TEXT NOT NULL
);
```

- [ ] **Step 4: Implement repositories**

Create `src/storage/repositories/job_repository.py`：

```python
"""jobs / snapshot_locks 表仓储实现。"""

from __future__ import annotations

import sqlite3
import uuid


class JobRepository:
    """封装 jobs 表的入队、抢占与状态更新。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def enqueue(self, *, run_id: str, snapshot_date: str) -> str:
        """插入一条 PENDING job 并返回 job_id。"""
        job_id = f"job-{uuid.uuid4().hex}"
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs (job_id, run_id, snapshot_date, status, attempts)
                VALUES (?, ?, ?, 'PENDING', 0)
                """,
                (job_id, run_id, snapshot_date),
            )
        return job_id

    def claim_next(self, *, worker_id: str, max_attempts: int) -> sqlite3.Row | None:
        """原子抢占下一条可执行 job。

        约定：
        - 仅抢占 status='PENDING' 且 attempts < max_attempts 的任务
        - 抢占成功后更新为 RUNNING，并写入 locked_by/locked_at
        """
        now_iso = self._connection.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()["now"]
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT job_id
                FROM jobs
                WHERE status = 'PENDING'
                  AND attempts < ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (int(max_attempts),),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None

            job_id = str(row["job_id"])
            cur = self._connection.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', locked_by = ?, locked_at = ?
                WHERE job_id = ?
                  AND status = 'PENDING'
                  AND locked_by IS NULL
                """,
                (worker_id, str(now_iso), job_id),
            )
            if int(cur.rowcount or 0) != 1:
                self._connection.execute("COMMIT")
                return None
            claimed = self._connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            self._connection.execute("COMMIT")
            return claimed

    def mark_done(self, *, job_id: str) -> None:
        """标记 job 为 DONE。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'DONE'
                WHERE job_id = ?
                """,
                (job_id,),
            )

    def mark_failed(self, *, job_id: str, error: str) -> None:
        """标记 job 为 FAILED，并增加 attempts。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'FAILED', attempts = attempts + 1, last_error = ?
                WHERE job_id = ?
                """,
                (error, job_id),
            )

    def release_to_pending(self, *, job_id: str) -> None:
        """将 RUNNING job 释放回 PENDING（用于 snapshot_lock 未获取时的退回）。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'PENDING', locked_by = NULL, locked_at = NULL
                WHERE job_id = ? AND status = 'RUNNING'
                """,
                (job_id,),
            )


class SnapshotLockRepository:
    """封装 snapshot_locks 表的互斥锁获取与释放。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def try_acquire(self, *, snapshot_date: str, worker_id: str, now_iso: str) -> bool:
        """尝试获取互斥锁，成功返回 True，失败返回 False。"""
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO snapshot_locks (snapshot_date, locked_by, locked_at)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_date, worker_id, now_iso),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release(self, *, snapshot_date: str, worker_id: str) -> bool:
        """释放互斥锁，仅当 worker_id 匹配持有者才会删除并返回 True。"""
        with self._connection:
            cur = self._connection.execute(
                """
                DELETE FROM snapshot_locks
                WHERE snapshot_date = ? AND locked_by = ?
                """,
                (snapshot_date, worker_id),
            )
        return int(cur.rowcount or 0) == 1
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest -q tests/storage/test_job_repository.py
```

Expected: PASS。

---

## Task 2: RunRepository 增加 runs 列表查询（给 /api/runs 用）

**Files:**
- Modify: `src/storage/repositories/run_repository.py`
- Test: `tests/storage/test_run_repository.py`

- [ ] **Step 1: Write failing test**

Append to `tests/storage/test_run_repository.py`：

```python
def test_run_repository_can_list_recent_runs(tmp_path: Path) -> None:
    """应能按 started_at 倒序列出最近 runs。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        repo = RunRepository(conn)
        repo.create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date="2026-04-17",
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
        repo.create_run(
            run_id="run-2",
            trigger_type="manual_daily",
            snapshot_date="2026-04-17",
            status="RUNNING",
            started_at="2026-04-17T00:00:10+00:00",
        )

        rows = repo.list_runs(limit=10)
    finally:
        conn.close()

    assert [str(r["run_id"]) for r in rows[:2]] == ["run-2", "run-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/storage/test_run_repository.py::test_run_repository_can_list_recent_runs
```

Expected: FAIL（`list_runs` 不存在）。

- [ ] **Step 3: Implement list_runs**

Edit `src/storage/repositories/run_repository.py` add method：

```python
    def list_runs(self, *, limit: int = 50) -> list[sqlite3.Row]:
        """按 started_at 倒序列出最近 runs。"""
        rows = self._connection.execute(
            """
            SELECT *
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return list(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest -q tests/storage/test_run_repository.py::test_run_repository_can_list_recent_runs
```

Expected: PASS。

---

## Task 3: AppConfig 增加 Web/Worker 相关配置（env）

**Files:**
- Modify: `src/app/config.py`
- Test: `tests/app/test_config.py`

- [ ] **Step 1: Write failing tests for new env**

Append to `tests/app/test_config.py`：

```python
def test_load_config_reads_web_api_token_and_port(monkeypatch) -> None:
    """应读取 API_TOKEN 与 WEB_PORT。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    monkeypatch.setenv("API_TOKEN", "t")
    monkeypatch.setenv("WEB_PORT", "8000")
    monkeypatch.setenv("WEB_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("APP_ENV", "test")
    cfg = load_config()
    assert cfg.api_token == "t"
    assert cfg.web_port == 8000
    assert cfg.web_bind_host == "127.0.0.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/app/test_config.py::test_load_config_reads_web_api_token_and_port
```

Expected: FAIL（字段不存在）。

- [ ] **Step 3: Implement config fields + env readers**

Edit `src/app/config.py`：

1) 在 `AppConfig` dataclass 增加字段（给默认值，避免破坏现有调用）：

```python
    api_token: str = ""
    web_bind_host: str = "127.0.0.1"
    web_port: int = 8000
    worker_poll_interval_seconds: int = 2
    worker_max_attempts: int = 3
```

2) 在 `load_config()` 中读取 env 并传入：

```python
    api_token = _read_str_env("API_TOKEN", default="")
    web_bind_host = _read_str_env("WEB_BIND_HOST", default="127.0.0.1")
    web_port = _read_non_negative_int_env("WEB_PORT", default=8000) or 8000
    worker_poll_interval_seconds = _read_non_negative_int_env("WORKER_POLL_INTERVAL_SECONDS", default=2)
    worker_max_attempts = _read_non_negative_int_env("WORKER_MAX_ATTEMPTS", default=3)
```

并在 `return AppConfig(...)` 最后追加：

```python
        api_token=api_token,
        web_bind_host=web_bind_host,
        web_port=web_port,
        worker_poll_interval_seconds=worker_poll_interval_seconds,
        worker_max_attempts=worker_max_attempts,
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest -q tests/app/test_config.py::test_load_config_reads_web_api_token_and_port
```

Expected: PASS。

---

## Task 4: Web Server + Upload API（POST /api/upload）

**Files:**
- Create: `src/web/__init__.py`, `src/web/server.py`, `src/web/routes_upload.py`
- Test: `tests/web/test_upload_api.py`

- [ ] **Step 1: Write failing tests for Bearer auth + upload success**

Create `tests/web/test_upload_api.py`：

```python
"""Web Upload API 行为测试（标准库 HTTP Server）。"""

from __future__ import annotations

import http.client
import io
import json
import sys
import threading
from pathlib import Path

from openpyxl import Workbook

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.config import AppConfig  # noqa: E402
from storage.db import open_sqlite_connection  # noqa: E402
from web.server import run_http_server_in_thread  # noqa: E402


def _xlsx_bytes(*, sheet_date: str) -> bytes:
    """生成最小可保存的 xlsx 二进制内容。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_date
    ws.append(["Symbol", "Description", "X_D_Trend_State", "X_D_State_Bars", "X_W_Trend_State", "X_W_State_Bars", "X_M_Trend_State", "X_M_State_Bars"])
    ws.append(["AAPL", "Apple Inc.", 1, 1, 1, 1, 1, 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _multipart_body(fields: list[tuple[str, str, bytes]], boundary: str) -> bytes:
    """构造 multipart/form-data 请求体。"""
    parts: list[bytes] = []
    for name, filename, content in fields:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n")
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def test_upload_requires_bearer_token(tmp_path: Path) -> None:
    """未携带 Bearer Token 时应返回 401。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "app.db"

    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        api_token="secret",
        web_bind_host="127.0.0.1",
        web_port=8000,
    )

    server, thread, port = run_http_server_in_thread(config=cfg, port=0)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/upload", body=b"")
        resp = conn.getresponse()
        body = resp.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert resp.status == 401
    assert body


def test_upload_saves_files_and_enqueues_job(tmp_path: Path) -> None:
    """上传成功后应落盘 inbox、写 runs/file_batches/jobs，并返回 summary_url。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "app.db"

    public_base = "http://1.2.3.4"
    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        api_token="secret",
        web_bind_host="127.0.0.1",
        web_port=8000,
        reports_base_url=public_base,
    )

    boundary = "----boundary"
    snapshot_date = "2026-04-17"
    body = _multipart_body(
        [
            ("stock_file", f"{snapshot_date}_stock.xlsx", _xlsx_bytes(sheet_date=snapshot_date)),
            ("etf_file", f"{snapshot_date}_etf.xlsx", _xlsx_bytes(sheet_date=snapshot_date)),
        ],
        boundary,
    )

    server, thread, port = run_http_server_in_thread(config=cfg, port=0)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(
            "POST",
            "/api/upload",
            body=body,
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert resp.status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["snapshot_date"] == snapshot_date
    assert payload["run_id"].startswith("run-")
    assert payload["job_id"].startswith("job-")
    assert payload["summary_url"].startswith(f"{public_base}/reports/{snapshot_date}/{payload['run_id']}/summary.md")

    inbox_dir = data_root / "inbox"
    assert inbox_dir.exists()

    db = open_sqlite_connection(db_path)
    try:
        runs = db.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        batches = db.execute("SELECT COUNT(*) AS c FROM file_batches").fetchone()["c"]
        jobs = db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]
    finally:
        db.close()

    assert int(runs) == 1
    assert int(batches) == 1
    assert int(jobs) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/web/test_upload_api.py
```

Expected: FAIL（web 模块不存在 / server 启动函数不存在）。

- [ ] **Step 3: Implement web server skeleton**

Create `src/web/__init__.py`：

```python
"""Web 服务模块（上传与查询 API）。"""
```

Create `src/web/server.py`：

```python
"""Web Server 入口（标准库 http.server）。"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Callable
from urllib.parse import urlparse

from app.config import AppConfig
from web.routes_upload import handle_upload
from web.routes_runs import handle_runs


class _Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器（仅提供 /api/*）。"""

    server_version = "multi-agent-web/0.1"

    def _send_json(self, *, status: int, payload: dict) -> None:
        """返回 JSON 响应。"""
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, *, status: int, text: str) -> None:
        """返回纯文本响应。"""
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _require_bearer(self) -> bool:
        """校验 Bearer Token，不通过则直接返回 401。"""
        config: AppConfig = self.server.config  # type: ignore[attr-defined]
        expected = (config.api_token or "").strip()
        if not expected:
            self._send_text(status=500, text="server_misconfigured:API_TOKEN")
            return False
        auth = (self.headers.get("Authorization") or "").strip()
        if auth != f"Bearer {expected}":
            self._send_text(status=401, text="unauthorized")
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        """处理 POST 请求。"""
        if not self.path.startswith("/api/"):
            self._send_text(status=404, text="not_found")
            return
        if not self._require_bearer():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            handle_upload(self, config=self.server.config)  # type: ignore[attr-defined]
            return
        self._send_text(status=404, text="not_found")

    def do_GET(self) -> None:  # noqa: N802
        """处理 GET 请求。"""
        if not self.path.startswith("/api/"):
            self._send_text(status=404, text="not_found")
            return
        if not self._require_bearer():
            return
        handle_runs(self, config=self.server.config)  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        """避免在测试中输出过多日志；生产可改为结构化日志。"""
        return


def create_http_server(*, config: AppConfig, host: str, port: int) -> ThreadingHTTPServer:
    """创建并返回 HTTPServer 实例。"""
    server = ThreadingHTTPServer((host, int(port)), _Handler)
    server.config = config  # type: ignore[attr-defined]
    return server


def run_http_server_in_thread(*, config: AppConfig, port: int) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    """在后台线程运行 HTTPServer（用于测试）。"""
    server = create_http_server(config=config, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_port = int(server.server_address[1])
    return server, thread, actual_port
```

- [ ] **Step 4: Implement upload route**

Create `src/web/routes_upload.py`：

```python
"""上传路由：POST /api/upload。"""

from __future__ import annotations

import cgi
import hashlib
import json
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from app.config import AppConfig
from ingestion.file_scanner import FILE_NAME_PATTERN
from storage.db import init_db, open_sqlite_connection
from storage.repositories.job_repository import JobRepository
from storage.repositories.run_repository import RunRepository
from storage.repositories.snapshot_repository import SnapshotRepository


def _now_iso(conn) -> str:
    """通过 SQLite 取 CURRENT_TIMESTAMP，并组装为 ISO 风格字符串。"""
    row = conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()
    return str(row["now"]).replace(" ", "T") + "+00:00"


def _write_file_and_sha256(*, dst_path: Path, src_file) -> str:
    """将上传文件流写入目标路径，并返回 SHA256。"""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with dst_path.open("wb") as out:
        for chunk in iter(lambda: src_file.read(8192), b""):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _parse_snapshot_date(file_name: str) -> tuple[str, str]:
    """解析文件名并返回 (snapshot_date, kind)，不匹配则抛 ValueError。"""
    matched = FILE_NAME_PATTERN.match(file_name)
    if not matched:
        raise ValueError(f"invalid_file_name:{file_name}")
    return matched.group("date"), matched.group("kind").lower()


def handle_upload(handler: BaseHTTPRequestHandler, *, config: AppConfig) -> None:
    """处理上传：保存文件、写 runs/file_batches/jobs，并返回 JSON。"""
    content_type = (handler.headers.get("Content-Type") or "").strip()
    if "multipart/form-data" not in content_type:
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(b"bad_request:expected_multipart")
        return

    environ = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type}
    form = cgi.FieldStorage(fp=handler.rfile, headers=handler.headers, environ=environ, keep_blank_values=True)
    if "stock_file" not in form or "etf_file" not in form:
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(b"bad_request:missing_files")
        return

    stock_item = form["stock_file"]
    etf_item = form["etf_file"]
    stock_name = str(getattr(stock_item, "filename", "") or "")
    etf_name = str(getattr(etf_item, "filename", "") or "")
    if not stock_name or not etf_name:
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(b"bad_request:empty_filenames")
        return

    try:
        stock_date, stock_kind = _parse_snapshot_date(stock_name)
        etf_date, etf_kind = _parse_snapshot_date(etf_name)
    except ValueError as exc:
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(str(exc).encode("utf-8"))
        return

    if stock_kind != "stock" or etf_kind != "etf":
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(b"bad_request:invalid_kinds")
        return
    if stock_date != etf_date:
        handler.send_response(400)
        handler.end_headers()
        handler.wfile.write(b"bad_request:snapshot_date_mismatch")
        return

    upload_id = f"upload-{uuid.uuid4().hex}"
    run_id = f"run-{uuid.uuid4().hex}"
    snapshot_date = stock_date

    data_root = Path(config.data_root)
    inbox_dir = data_root / "inbox" / upload_id
    stock_dst = inbox_dir / f"{snapshot_date}_stock.xlsx"
    etf_dst = inbox_dir / f"{snapshot_date}_etf.xlsx"

    stock_sha = _write_file_and_sha256(dst_path=stock_dst, src_file=stock_item.file)
    etf_sha = _write_file_and_sha256(dst_path=etf_dst, src_file=etf_item.file)

    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)
        now_iso = _now_iso(conn)
        RunRepository(conn).create_run(
            run_id=run_id,
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at=now_iso,
        )
        SnapshotRepository(conn).create_file_batch(
            run_id=run_id,
            snapshot_date=snapshot_date,
            stock_file=str(stock_dst),
            etf_file=str(etf_dst),
            stock_sha256=stock_sha,
            etf_sha256=etf_sha,
        )
        job_id = JobRepository(conn).enqueue(run_id=run_id, snapshot_date=snapshot_date)
    finally:
        conn.close()

    base_url = str(getattr(config, "reports_base_url", "") or "").strip().rstrip("/")
    reports_url = f"{base_url}/reports/{snapshot_date}/{run_id}/"
    summary_url = f"{base_url}/reports/{snapshot_date}/{run_id}/summary.md"

    payload = {
        "run_id": run_id,
        "snapshot_date": snapshot_date,
        "job_id": job_id,
        "reports_url": reports_url,
        "summary_url": summary_url,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
```

- [ ] **Step 5: Implement placeholder runs route handler (for server import)**

Create `src/web/routes_runs.py` minimal first（Task 5 会补全）：

```python
"""运行查询路由：GET /api/runs 与 GET /api/runs/{run_id}。"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from app.config import AppConfig


def handle_runs(handler: BaseHTTPRequestHandler, *, config: AppConfig) -> None:
    """临时占位：后续任务补齐具体实现。"""
    handler.send_response(404)
    handler.end_headers()
    handler.wfile.write(b"not_implemented")
```

- [ ] **Step 6: Run tests**

Run:

```bash
pytest -q tests/web/test_upload_api.py
```

Expected: PASS。

---

## Task 5: Runs API（GET /api/runs 与 GET /api/runs/{run_id}）

**Files:**
- Modify: `src/web/routes_runs.py`
- Test: `tests/web/test_runs_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/web/test_runs_api.py`：

```python
"""Runs API 行为测试。"""

from __future__ import annotations

import http.client
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.config import AppConfig  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.decision_repository import DecisionRepository  # noqa: E402
from storage.repositories.notification_repository import NotificationRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402
from web.server import run_http_server_in_thread  # noqa: E402


def test_runs_list_returns_reports_urls(tmp_path: Path) -> None:
    """runs 列表应带 reports_url/summary_url。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date="2026-04-17",
            status="SUCCEEDED",
            started_at="2026-04-17T00:00:00+00:00",
        )
        RunRepository(conn).mark_run_end(
            run_id="run-1",
            status="SUCCEEDED",
            ended_at="2026-04-17T00:10:00+00:00",
            error_message=None,
        )
    finally:
        conn.close()

    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(tmp_path / "data_root"),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        api_token="secret",
        reports_base_url="http://1.2.3.4",
    )

    server, thread, port = run_http_server_in_thread(config=cfg, port=0)
    try:
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        client.request("GET", "/api/runs", headers={"Authorization": "Bearer secret"})
        resp = client.getresponse()
        raw = resp.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert resp.status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["items"][0]["run_id"] == "run-1"
    assert payload["items"][0]["summary_url"] == "http://1.2.3.4/reports/2026-04-17/run-1/summary.md"


def test_run_detail_includes_decisions_and_notifications(tmp_path: Path) -> None:
    """run 详情应包含 decisions 与 notifications。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date="2026-04-17",
            status="SUCCEEDED",
            started_at="2026-04-17T00:00:00+00:00",
        )
        DecisionRepository(conn).insert_decisions(
            [
                {
                    "run_id": "run-1",
                    "snapshot_date": "2026-04-17",
                    "instrument_type": "stock",
                    "symbol": "AAPL",
                    "votes": 3,
                    "tier": 2,
                    "rank_in_list": 1,
                    "summary_rationale": "r",
                }
            ]
        )
        NotificationRepository(conn).insert_notification(
            run_id="run-1",
            channel="telegram",
            message_text="m",
            dry_run=True,
            status="SENT",
            provider_response_json="{}",
        )
    finally:
        conn.close()

    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(tmp_path / "data_root"),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        api_token="secret",
        reports_base_url="http://1.2.3.4",
    )

    server, thread, port = run_http_server_in_thread(config=cfg, port=0)
    try:
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        client.request("GET", "/api/runs/run-1", headers={"Authorization": "Bearer secret"})
        resp = client.getresponse()
        raw = resp.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert resp.status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["run"]["run_id"] == "run-1"
    assert payload["decisions"]["stock"][0]["symbol"] == "AAPL"
    assert payload["notifications"][0]["status"] == "SENT"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/web/test_runs_api.py
```

Expected: FAIL（routes_runs 仍为占位 404）。

- [ ] **Step 3: Implement routes_runs**

Replace `src/web/routes_runs.py`：

```python
"""运行查询路由：GET /api/runs 与 GET /api/runs/{run_id}。"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from app.config import AppConfig
from storage.db import init_db, open_sqlite_connection
from storage.repositories.decision_repository import DecisionRepository
from storage.repositories.llm_report_repository import LLMReportRepository
from storage.repositories.notification_repository import NotificationRepository
from storage.repositories.run_repository import RunRepository


def _send_json(handler: BaseHTTPRequestHandler, *, status: int, payload: dict) -> None:
    """写入 JSON 响应。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _urls(*, config: AppConfig, snapshot_date: str | None, run_id: str) -> tuple[str, str]:
    """构造 reports_url 与 summary_url。"""
    base = str(getattr(config, "reports_base_url", "") or "").strip().rstrip("/")
    snap = str(snapshot_date or "").strip()
    return (
        f"{base}/reports/{snap}/{run_id}/",
        f"{base}/reports/{snap}/{run_id}/summary.md",
    )


def handle_runs(handler: BaseHTTPRequestHandler, *, config: AppConfig) -> None:
    """处理 runs 列表与详情查询。"""
    parsed = urlparse(handler.path)
    if parsed.path == "/api/runs":
        conn = open_sqlite_connection(config.sqlite_path)
        try:
            init_db(conn)
            rows = RunRepository(conn).list_runs(limit=50)
            items: list[dict] = []
            for r in rows:
                reports_url, summary_url = _urls(
                    config=config, snapshot_date=r["snapshot_date"], run_id=str(r["run_id"])
                )
                items.append(
                    {
                        "run_id": str(r["run_id"]),
                        "snapshot_date": r["snapshot_date"],
                        "status": r["status"],
                        "started_at": r["started_at"],
                        "ended_at": r["ended_at"],
                        "reports_url": reports_url,
                        "summary_url": summary_url,
                    }
                )
        finally:
            conn.close()
        _send_json(handler, status=200, payload={"items": items})
        return

    if parsed.path.startswith("/api/runs/"):
        run_id = parsed.path.split("/", 3)[3]
        conn = open_sqlite_connection(config.sqlite_path)
        try:
            init_db(conn)
            run_row = RunRepository(conn).get_run(run_id)
            if run_row is None:
                _send_json(handler, status=404, payload={"error": "run_not_found"})
                return

            reports_url, summary_url = _urls(
                config=config, snapshot_date=run_row["snapshot_date"], run_id=str(run_row["run_id"])
            )
            dec_repo = DecisionRepository(conn)
            notif_repo = NotificationRepository(conn)
            rep_repo = LLMReportRepository(conn)
            payload = {
                "run": {
                    "run_id": str(run_row["run_id"]),
                    "snapshot_date": run_row["snapshot_date"],
                    "status": run_row["status"],
                    "started_at": run_row["started_at"],
                    "ended_at": run_row["ended_at"],
                    "reports_url": reports_url,
                    "summary_url": summary_url,
                },
                "decisions": {
                    "stock": [dict(r) for r in dec_repo.list_decisions(run_id=run_id, instrument_type="stock")],
                    "etf": [dict(r) for r in dec_repo.list_decisions(run_id=run_id, instrument_type="etf")],
                },
                "notifications": [dict(r) for r in notif_repo.list_notifications(run_id=run_id)],
                "llm_reports": [dict(r) for r in rep_repo.list_reports(run_id=run_id)],
            }
        finally:
            conn.close()
        _send_json(handler, status=200, payload=payload)
        return

    handler.send_response(404)
    handler.end_headers()
    handler.wfile.write(b"not_found")
```

- [ ] **Step 4: Run tests**

Run:

```bash
pytest -q tests/web/test_runs_api.py
```

Expected: PASS。

---

## Task 6: Orchestrator 增加“执行既有 run_id + 指定文件路径”的入口

**Files:**
- Modify: `src/orchestrator/run_orchestrator.py`
- Test: `tests/orchestrator/test_daily_run_flow.py`（新增用例）

- [ ] **Step 1: Write failing test for run_id reuse**

Append to `tests/orchestrator/test_daily_run_flow.py`（若文件已较长，可新建 `tests/orchestrator/test_daily_run_existing_run.py`）：

```python
def test_daily_run_for_existing_run_id_uses_given_file_paths(tmp_path: Path) -> None:
    """应能复用既有 run_id，并从指定文件路径执行 daily 流程。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    snapshot_date = "2026-04-17"

    stock_file = data_root / "inbox" / "upload-1" / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / "inbox" / "upload-1" / f"{snapshot_date}_etf.xlsx"
    stock_file.parent.mkdir(parents=True, exist_ok=True)
    # 复用 e2e 里的写 xlsx 方法最简单：直接导入并调用（或在此处写最小 workbook）
    from openpyxl import Workbook

    def write_xlsx(path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = snapshot_date
        ws.append(["Symbol","Description","X_D_Trend_State","X_D_State_Bars","X_W_Trend_State","X_W_State_Bars","X_M_Trend_State","X_M_State_Bars"])
        ws.append(["AAPL","Apple Inc.",1,1,1,1,1,1])
        wb.save(path)

    write_xlsx(stock_file)
    write_xlsx(etf_file)

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        llm_min_effective_providers=1,
    )

    # 先创建 run（模拟 Web 上传已写入 runs）
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-existing",
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
    finally:
        conn.close()

    run_id = daily_run_for_existing_run(
        config=config,
        run_id="run-existing",
        snapshot_date=snapshot_date,
        stock_file_path=str(stock_file),
        etf_file_path=str(etf_file),
        providers=[_PanelMockProvider("p1")],
        dry_run=True,
    )

    assert run_id == "run-existing"
    assert (data_root / "reports" / snapshot_date / run_id / "summary.md").exists()
```

（测试用到的 `_PanelMockProvider` 可从 `tests/e2e/test_mvp_pipeline.py` 复制到该测试文件中，保持独立可运行。）

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/orchestrator/test_daily_run_flow.py -k existing_run
```

Expected: FAIL（`daily_run_for_existing_run` 不存在）。

- [ ] **Step 3: Implement daily_run_for_existing_run**

在 `src/orchestrator/run_orchestrator.py` 增加一个新入口函数（不修改现有 `daily_run` 行为）：

```python
def daily_run_for_existing_run(
    *,
    config: AppConfig,
    run_id: str,
    snapshot_date: str,
    stock_file_path: str,
    etf_file_path: str,
    providers: list[BaseLLMProvider] | None = None,
    dry_run: bool | None = None,
) -> str:
    """复用既有 run_id 执行日常编排流程，并使用指定文件路径作为输入。

    约定：
    - runs 行应已存在且 status=RUNNING（由 Web 上传创建）
    - 文件名必须符合契约：{snapshot_date}_stock.xlsx / {snapshot_date}_etf.xlsx
    """
    # 实现策略：
    # 1) 打开 DB，init_db
    # 2) 校验 run 存在
    # 3) 用 stock/etf 指定路径构造 FilePairCandidate 并 validate_file_pair_candidate
    # 4) 后续流程复用 daily_run 主体：parse_panel_xlsx -> 写入 file_batches/instrument_snapshots -> LLM -> decisions -> ledger -> summary/report -> notifications -> mark_run_end
    #    为避免复制大量代码，可将 daily_run 内部“从 validated_pair 往下”的逻辑抽到一个私有函数，并被两个入口复用。
    ...
```

拆分建议（避免重复逻辑）：

- 新增私有函数：
  - `_run_daily_pipeline_with_validated_pair(..., run_id: str, validated_pair: ValidatedFilePair, ...) -> str`
  - 让现有 `daily_run` 在 `_pick_validated_file_pair` 后调用它
  - 让新函数 `daily_run_for_existing_run` 在 `validate_file_pair_candidate` 后调用它

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest -q tests/orchestrator/test_daily_run_flow.py -k existing_run
```

Expected: PASS。

---

## Task 7: Worker（claim job + snapshot lock + 执行 orchestrator + 回写 jobs）

**Files:**
- Create: `src/worker/__init__.py`, `src/worker/job_worker.py`
- Test: `tests/worker/test_job_worker_claims_and_runs.py`

- [ ] **Step 1: Write failing test**

Create `tests/worker/test_job_worker_claims_and_runs.py`：

```python
"""Worker 抢占与执行行为测试。"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from openpyxl import Workbook

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.providers.base import BaseLLMProvider  # noqa: E402
from app.config import AppConfig  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.job_repository import JobRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402
from storage.repositories.snapshot_repository import SnapshotRepository  # noqa: E402
from worker.job_worker import JobWorker  # noqa: E402


class _PanelMockProvider(BaseLLMProvider):
    """返回固定 panel report JSON 的 Provider（用于 Worker 测试）。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并固定 provider 名称。"""
        self._provider_name = provider_name

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return self._provider_name

    def invoke(self, prompt: str) -> str:
        """根据 prompt 判定 instrument_type 并输出符合 panel schema 的 JSON。"""
        instrument_type = "stock" if "instrument_type=stock" in prompt else "etf"
        snapshot_date = "2026-04-17"
        report = {
            "schema_version": "v1",
            "instrument_type": instrument_type,
            "snapshot_date": snapshot_date,
            "provider": self._provider_name,
            "data_overview": {
                "sheet_count": 1,
                "date_range": {"start": snapshot_date, "end": snapshot_date},
                "symbol_count": 1,
                "data_completeness": {
                    "missing_price_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_volume_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_trend_ratio": 0.0,
                },
                "notes": [],
            },
            "reversal_patterns": [],
            "multi_timeframe_analysis": {"needs_week_month_confirmation": [], "resonance_is_stronger": [], "lead_lag_relationships": []},
            "signals": {"strong_entry_signals": [], "watch_entry_signals": [], "risk_signals": []},
            "top10": [
                {"symbol": ("S1" if instrument_type == "stock" else "E1"), "signal_strength": "strong", "confidence": 0.8, "entry_logic_brief": "b", "entry_logic_detail": "d"}
            ],
            "followups": [],
        }
        return json.dumps(report, ensure_ascii=False)


def _write_xlsx(path: Path, *, sheet_date: str) -> None:
    """写入最小 xlsx 文件。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_date
    ws.append(["Symbol","Description","X_D_Trend_State","X_D_State_Bars","X_W_Trend_State","X_W_State_Bars","X_M_Trend_State","X_M_State_Bars"])
    ws.append(["AAPL","Apple Inc.",1,1,1,1,1,1])
    wb.save(path)


def test_worker_claims_job_and_marks_done(tmp_path: Path) -> None:
    """worker 应能 claim job、执行 orchestrator，并标记 job DONE。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    snapshot_date = "2026-04-17"
    stock = data_root / "inbox" / "upload-1" / f"{snapshot_date}_stock.xlsx"
    etf = data_root / "inbox" / "upload-1" / f"{snapshot_date}_etf.xlsx"
    stock.parent.mkdir(parents=True, exist_ok=True)
    _write_xlsx(stock, sheet_date=snapshot_date)
    _write_xlsx(etf, sheet_date=snapshot_date)

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
        SnapshotRepository(conn).create_file_batch(
            run_id="run-1",
            snapshot_date=snapshot_date,
            stock_file=str(stock),
            etf_file=str(etf),
            stock_sha256="x",
            etf_sha256="y",
        )
        JobRepository(conn).enqueue(run_id="run-1", snapshot_date=snapshot_date)
    finally:
        conn.close()

    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        llm_min_effective_providers=1,
        worker_poll_interval_seconds=1,
        worker_max_attempts=3,
    )

    worker = JobWorker(worker_id="w1", config=cfg, providers=[_PanelMockProvider("p1")])
    worker.run_once()

    db = open_sqlite_connection(db_path)
    try:
        row = db.execute("SELECT status FROM jobs LIMIT 1").fetchone()
        assert row is not None
        assert row["status"] == "DONE"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest -q tests/worker/test_job_worker_claims_and_runs.py
```

Expected: FAIL（worker 模块不存在）。

- [ ] **Step 3: Implement worker**

Create `src/worker/__init__.py`：

```python
"""Worker 模块（job queue 执行）。"""
```

Create `src/worker/job_worker.py`：

```python
"""SQLite Job Queue Worker 实现。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from analysis.providers.base import BaseLLMProvider
from app.config import AppConfig
from orchestrator.run_orchestrator import daily_run_for_existing_run
from storage.db import init_db, open_sqlite_connection
from storage.repositories.job_repository import JobRepository, SnapshotLockRepository


def _now_iso() -> str:
    """生成 UTC 秒级时间戳。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobWorker:
    """从 jobs 表抢占任务并执行 daily run。"""

    def __init__(self, *, worker_id: str, config: AppConfig, providers: list[BaseLLMProvider] | None = None) -> None:
        """初始化 worker。"""
        self._worker_id = worker_id
        self._config = config
        self._providers = providers

    def run_once(self) -> None:
        """执行一次抢占与处理（用于测试与可控执行）。"""
        conn = open_sqlite_connection(self._config.sqlite_path)
        try:
            init_db(conn)
            jobs = JobRepository(conn)
            locks = SnapshotLockRepository(conn)

            claimed = jobs.claim_next(worker_id=self._worker_id, max_attempts=int(self._config.worker_max_attempts))
            if claimed is None:
                return

            job_id = str(claimed["job_id"])
            snapshot_date = str(claimed["snapshot_date"])

            if not locks.try_acquire(snapshot_date=snapshot_date, worker_id=self._worker_id, now_iso=_now_iso()):
                jobs.release_to_pending(job_id=job_id)
                return
        finally:
            conn.close()

        try:
            # 读取 file_batches，作为 worker 的输入（由 Web 上传写入）
            conn2 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn2)
                row = conn2.execute(
                    """
                    SELECT stock_file, etf_file
                    FROM file_batches
                    WHERE run_id = ?
                    ORDER BY batch_id DESC
                    LIMIT 1
                    """,
                    (str(claimed["run_id"]),),
                ).fetchone()
            finally:
                conn2.close()

            if row is None:
                raise RuntimeError("missing_file_batch_for_run")

            daily_run_for_existing_run(
                config=self._config,
                run_id=str(claimed["run_id"]),
                snapshot_date=snapshot_date,
                stock_file_path=str(row["stock_file"]),
                etf_file_path=str(row["etf_file"]),
                providers=self._providers,
                dry_run=self._config.dry_run,
            )

            conn3 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn3)
                JobRepository(conn3).mark_done(job_id=job_id)
            finally:
                conn3.close()
        except Exception as exc:  # noqa: BLE001
            conn3 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn3)
                JobRepository(conn3).mark_failed(job_id=job_id, error=str(exc))
            finally:
                conn3.close()
        finally:
            conn4 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn4)
                SnapshotLockRepository(conn4).release(snapshot_date=snapshot_date, worker_id=self._worker_id)
            finally:
                conn4.close()

    def run_forever(self) -> None:
        """循环执行 worker（生产环境）。"""
        interval = float(getattr(self._config, "worker_poll_interval_seconds", 2) or 2)
        while True:
            self.run_once()
            time.sleep(interval)
```

- [ ] **Step 4: Run test**

Run:

```bash
pytest -q tests/worker/test_job_worker_claims_and_runs.py
```

Expected: PASS。

---

## Task 8: 部署 runbook（Nginx + systemd）

**Files:**
- Create: `docs/runbooks/web-deploy.md`

- [ ] **Step 1: Write runbook**

Create `docs/runbooks/web-deploy.md`：

```markdown
# 云端部署（Nginx + systemd）MVP Runbook

## 1. 端口与安全组

- 对外：80（HTTP）
- Web API：127.0.0.1:8000（仅本机）
- 建议：只对外暴露 80；不要在安全组暴露 8000

## 2. 目录与权限

建议：

- DATA_ROOT=/var/lib/multi-agent-investment
- SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite

目录：

```text
/var/lib/multi-agent-investment/
  inbox/
  reports/
  app.sqlite
```

## 3. Nginx 配置示例

将 `/reports/` 映射到 `${DATA_ROOT}/reports/`，`/api/` 反代到 `127.0.0.1:8000`。

```nginx
server {
  listen 80;
  server_name _;

  location /reports/ {
    alias /var/lib/multi-agent-investment/reports/;
    autoindex on;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

## 4. systemd 单元文件示例

### 4.1 Web API（仅监听 127.0.0.1:8000）

`/etc/systemd/system/multi-agent-web.service`：

```ini
[Unit]
Description=multi-agent web api
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/multi_agent_investment
Environment=APP_ENV=prod
Environment=WEB_BIND_HOST=127.0.0.1
Environment=WEB_PORT=8000
Environment=DATA_ROOT=/var/lib/multi-agent-investment
Environment=SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite
Environment=API_TOKEN=REPLACE_ME
Environment=REPORTS_BASE_URL=http://PUBLIC_IP
Environment=TELEGRAM_BOT_TOKEN=REPLACE_ME
Environment=TELEGRAM_CHAT_ID=REPLACE_ME
ExecStart=/opt/multi_agent_investment/.venv/bin/python -m app.main web
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

### 4.2 Worker（两个实例：worker@1 + worker@2）

`/etc/systemd/system/multi-agent-worker@.service`：

```ini
[Unit]
Description=multi-agent worker %i
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/multi_agent_investment
Environment=APP_ENV=prod
Environment=DATA_ROOT=/var/lib/multi-agent-investment
Environment=SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite
Environment=WORKER_POLL_INTERVAL_SECONDS=2
Environment=WORKER_MAX_ATTEMPTS=3
Environment=REPORTS_BASE_URL=http://PUBLIC_IP
Environment=TELEGRAM_BOT_TOKEN=REPLACE_ME
Environment=TELEGRAM_CHAT_ID=REPLACE_ME
ExecStart=/opt/multi_agent_investment/.venv/bin/python -m app.main worker --worker-id worker-%i
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## 5. 启动顺序

1) 启动 Web：`systemctl enable --now multi-agent-web`
2) 启动 Worker：`systemctl enable --now multi-agent-worker@1 multi-agent-worker@2`
3) 检查 Nginx：`systemctl enable --now nginx`

## 6. 联调

上传：

- URL：`http://PUBLIC_IP/api/upload`
- Header：`Authorization: Bearer <API_TOKEN>`
- multipart 字段：`stock_file`、`etf_file`

查看报告：

- `http://PUBLIC_IP/reports/{snapshot_date}/{run_id}/summary.md`
```
```

- [ ] **Step 2:（实现阶段）确保 CLI 入口支持 web/worker 子命令**

如果 `python -m app.main web/worker` 还不存在，则在执行本计划时需要新增相应 CLI 子命令入口（见 `src/app/main.py`）。

---

## Spec Coverage Self-Review（自检）

- 无域名 + 公网 IP + 80：由 runbook 的 Nginx 配置覆盖
- `/api` Bearer Token：由 `web/server.py::_require_bearer` 覆盖
- Worker 并发=2：由 systemd `multi-agent-worker@1` + `@2` 覆盖
- 同日互斥：`snapshot_locks` + `try_acquire/release` 覆盖
- 上传落盘 + inbox：`routes_upload.py` 覆盖
- TG 链接：复用现有 `REPORTS_BASE_URL` 拼接 `/reports/...`（云端设置为公网 IP）

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-cloud-mvp-implementation.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

