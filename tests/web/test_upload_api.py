"""Web Upload API 行为测试（标准库 HTTP Server）。"""

from __future__ import annotations

import http.client
import io
import json
import sys
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
    ws.append(
        [
            "Symbol",
            "Description",
            "X_D_Trend_State",
            "X_D_State_Bars",
            "X_W_Trend_State",
            "X_W_State_Bars",
            "X_M_Trend_State",
            "X_M_State_Bars",
        ]
    )
    ws.append(["AAPL", "Apple Inc.", 1, 1, 1, 1, 1, 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _multipart_body(fields: list[tuple[str, str, bytes]], boundary: str) -> bytes:
    """构造 multipart/form-data 请求体。"""
    parts: list[bytes] = []
    for name, filename, content in fields:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(
                "utf-8"
            )
        )
        parts.append(
            b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
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
    assert payload["summary_url"].startswith(
        f"{public_base}/reports/{snapshot_date}/{payload['run_id']}/summary.md"
    )

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

