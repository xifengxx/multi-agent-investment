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
                    "stock": [
                        dict(r)
                        for r in DecisionRepository(conn).list_decisions(
                            run_id=run_id, instrument_type="stock"
                        )
                    ],
                    "etf": [
                        dict(r)
                        for r in DecisionRepository(conn).list_decisions(
                            run_id=run_id, instrument_type="etf"
                        )
                    ],
                },
                "notifications": [
                    dict(r) for r in NotificationRepository(conn).list_notifications(run_id=run_id)
                ],
                "llm_reports": [dict(r) for r in LLMReportRepository(conn).list_reports(run_id=run_id)],
            }
        finally:
            conn.close()
        _send_json(handler, status=200, payload=payload)
        return

    handler.send_response(404)
    handler.end_headers()
    handler.wfile.write(b"not_found")


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
