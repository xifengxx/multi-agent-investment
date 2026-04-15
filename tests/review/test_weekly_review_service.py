"""周度复盘统计与落库测试（Task8）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from storage.db import init_db, open_sqlite_connection  # noqa: E402
from review.weekly_review_service import compute_weekly_report  # noqa: E402
from storage.repositories.review_repository import ReviewRepository  # noqa: E402


def test_compute_weekly_report_counts_and_persists_weekly_review(tmp_path: Path) -> None:
    """应能统计周度交易信息，并把 report 落库到 weekly_reviews。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)

        # paper_trades.run_id 有外键约束，需要先插入 runs
        conn.execute(
            """
            INSERT INTO runs (run_id, trigger_type, snapshot_date, status, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-weekly-test", "scheduled_weekly", None, "SUCCEEDED", "2026-04-15T00:00:00+00:00"),
        )

        # 插入一周内 3 笔交易，另插入 1 笔周外交易用于排除验证
        trades = [
            ("run-weekly-test", "2026-04-09", "stock", "AAPL", "BUY", 1, 100.0),
            ("run-weekly-test", "2026-04-10", "etf", "SPY", "BUY", 2, 200.0),
            ("run-weekly-test", "2026-04-15", "stock", "MSFT", "BUY", 3, 300.0),
            ("run-weekly-test", "2026-04-08", "stock", "TSLA", "BUY", 1, 400.0),  # week_start 前一天
        ]
        for t in trades:
            conn.execute(
                """
                INSERT INTO paper_trades (
                    run_id, snapshot_date, instrument_type, symbol, side, qty, price
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                t,
            )

        # 插入 decisions（本测试不依赖其统计，但作为周度复盘的“证据数据”）
        conn.execute(
            """
            INSERT INTO decisions (
                run_id, snapshot_date, instrument_type, symbol, votes, tier, rank_in_list, summary_rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-weekly-test", "2026-04-15", "stock", "AAPL", 3, 1, 1, "test rationale"),
        )

        report = compute_weekly_report(conn, week_end="2026-04-15", days=7)
        assert report["week_start"] == "2026-04-09"
        assert report["week_end"] == "2026-04-15"
        assert int(report["trade_count"]) == 3
        assert int(report["unique_symbols"]) == 3

        by_type = report["by_instrument_type"]
        assert by_type["stock"]["trade_count"] == 2
        assert by_type["stock"]["unique_symbols"] == 2
        assert by_type["etf"]["trade_count"] == 1
        assert by_type["etf"]["unique_symbols"] == 1

        # 落库 weekly_reviews
        repo = ReviewRepository(conn)
        repo.insert_weekly_review(
            review_id="wr-001",
            week_start=report["week_start"],
            week_end=report["week_end"],
            report_json=json.dumps(report, ensure_ascii=False),
        )
        row = conn.execute("SELECT * FROM weekly_reviews WHERE review_id = ?", ("wr-001",)).fetchone()
        assert row is not None
        assert row["week_start"] == "2026-04-09"
        assert row["week_end"] == "2026-04-15"
        parsed = json.loads(str(row["report_json"]))
        assert parsed["trade_count"] == 3
    finally:
        conn.close()

