"""输入文件扫描测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from ingestion.file_scanner import scan_file_pair_candidates  # noqa: E402


def _touch_file(file_path: Path) -> None:
    """创建测试文件并确保父目录存在。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("demo", encoding="utf-8")


def test_scan_file_pair_candidates_supports_recursive_and_case_insensitive_etf(tmp_path: Path) -> None:
    """应支持递归扫描并兼容 ETF 大小写命名。"""
    _touch_file(tmp_path / "nested" / "2026-04-10_stock.xlsx")
    _touch_file(tmp_path / "nested" / "2026-04-10_ETF.xlsx")
    _touch_file(tmp_path / "nested" / "README.txt")

    candidates = scan_file_pair_candidates(root_dir=tmp_path)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.snapshot_date == "2026-04-10"
    assert candidate.stock_path is not None
    assert candidate.stock_path.name == "2026-04-10_stock.xlsx"
    assert candidate.etf_path is not None
    assert candidate.etf_path.name == "2026-04-10_ETF.xlsx"


def test_scan_file_pair_candidates_keeps_unpaired_file_for_later_validation(tmp_path: Path) -> None:
    """当仅有单边文件时，应仍保留候选结果供契约校验阶段处理。"""
    _touch_file(tmp_path / "2026-04-11_stock.xlsx")

    candidates = scan_file_pair_candidates(root_dir=tmp_path)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.snapshot_date == "2026-04-11"
    assert candidate.stock_path is not None
    assert candidate.etf_path is None
