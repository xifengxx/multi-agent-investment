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
            status="RUNNING",
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

