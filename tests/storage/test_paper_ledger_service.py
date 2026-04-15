"""纸面账本服务与落库测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from ledger.paper_ledger_service import PaperLedgerService  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.ledger_repository import LedgerRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


def test_paper_ledger_service_records_buy_trades_with_qty_1_and_updates_positions(tmp_path: Path) -> None:
    """BUY 生成策略：每个入选标的应产生 qty=1 的 BUY 交易，并累加到持仓。"""
    decisions = [
        {"run_id": "run-001", "snapshot_date": "2026-04-15", "instrument_type": "stock", "symbol": "AAPL"},
        {"run_id": "run-001", "snapshot_date": "2026-04-15", "instrument_type": "etf", "symbol": "SPY"},
    ]

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-001",
            trigger_type="manual_daily",
            snapshot_date="2026-04-15",
            status="RUNNING",
            started_at="2026-04-15T09:00:00+08:00",
        )
        ledger_repo = LedgerRepository(conn)
        service = PaperLedgerService(ledger_repo)

        service.record_buys_for_decisions(run_id="run-001", snapshot_date="2026-04-15", decisions=decisions)

        trades = ledger_repo.list_paper_trades(run_id="run-001")
        positions = ledger_repo.list_positions()
    finally:
        conn.close()

    assert len(trades) == 2
    assert {(t["symbol"], t["side"], t["qty"]) for t in trades} == {("AAPL", "BUY", 1), ("SPY", "BUY", 1)}
    assert {(p["symbol"], p["qty"]) for p in positions} == {("AAPL", 1), ("SPY", 1)}


def test_positions_accumulate_across_multiple_calls(tmp_path: Path) -> None:
    """同一标的重复 BUY 时，positions.qty 应累加。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-001",
            trigger_type="manual_daily",
            snapshot_date="2026-04-15",
            status="RUNNING",
            started_at="2026-04-15T09:00:00+08:00",
        )
        RunRepository(conn).create_run(
            run_id="run-002",
            trigger_type="manual_daily",
            snapshot_date="2026-04-16",
            status="RUNNING",
            started_at="2026-04-16T09:00:00+08:00",
        )
        ledger_repo = LedgerRepository(conn)
        service = PaperLedgerService(ledger_repo)

        service.record_buys_for_decisions(
            run_id="run-001",
            snapshot_date="2026-04-15",
            decisions=[{"instrument_type": "stock", "symbol": "AAPL"}],
        )
        service.record_buys_for_decisions(
            run_id="run-002",
            snapshot_date="2026-04-16",
            decisions=[{"instrument_type": "stock", "symbol": "AAPL"}],
        )

        positions = ledger_repo.list_positions()
    finally:
        conn.close()

    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["qty"] == 2
