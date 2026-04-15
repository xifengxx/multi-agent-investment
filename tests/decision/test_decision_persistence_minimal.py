"""最小落库测试：VotingAggregator -> DecisionRepository。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from decision.voting_aggregator import VotingAggregator  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.decision_repository import DecisionRepository  # noqa: E402


def test_can_persist_aggregated_decisions(tmp_path: Path) -> None:
    """聚合后的 decisions 记录应能写入并按 rank 查询返回。"""
    votes = [
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "provider": "agent-1",
            "recommendation": "BUY",
            "confidence": 0.8,
            "rationale": ["agent-1:buy"],
        },
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "provider": "agent-2",
            "recommendation": "BUY",
            "confidence": 0.7,
            "rationale": ["agent-2:buy"],
        },
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "provider": "agent-3",
            "recommendation": "BUY",
            "confidence": 0.6,
            "rationale": ["agent-3:buy"],
        },
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "provider": "agent-4",
            "recommendation": "BUY",
            "confidence": 0.5,
            "rationale": ["agent-4:buy"],
        },
    ]

    decisions = VotingAggregator().aggregate_for_run(run_id="run-001", votes=votes, top_n=10)
    assert len(decisions) == 1

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        repo = DecisionRepository(conn)
        repo.insert_decisions(decisions)
        rows = repo.list_decisions(run_id="run-001", instrument_type="stock")
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["votes"] == 4
    assert rows[0]["tier"] == 1
    assert rows[0]["rank_in_list"] == 1
