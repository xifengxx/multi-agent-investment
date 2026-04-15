"""Excel 解析与标准化测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from ingestion.excel_parser import ExcelParseError, parse_validated_file_pair  # noqa: E402
from ingestion.input_contract_validator import ValidatedFilePair  # noqa: E402


HEADERS = [
    "Symbol",
    "Description",
    "X_D_Trend_State",
    "X_D_State_Bars",
    "X_W_Trend_State",
    "X_W_State_Bars",
    "X_M_Trend_State",
    "X_M_State_Bars",
]


def _create_xlsx(file_path: Path, rows: list[list[object]]) -> None:
    """创建最小可解析的 xlsx 文件。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(HEADERS)
    for row in rows:
        worksheet.append(row)
    workbook.save(file_path)


def test_parse_validated_file_pair_maps_stock_and_etf_to_unified_rows(tmp_path: Path) -> None:
    """应将 stock 与 etf 两类文件统一映射为 InstrumentRow。"""
    stock_path = tmp_path / "2026-04-10_stock.xlsx"
    etf_path = tmp_path / "2026-04-10_ETF.xlsx"
    _create_xlsx(
        stock_path,
        [
            ["AAPL", "Apple Inc", "UP", 3, "UP", 2, "DOWN", 1],
        ],
    )
    _create_xlsx(
        etf_path,
        [
            ["SPY", "S&P 500 ETF", "UP", "4", "UP", "3", "UP", "2"],
        ],
    )

    validated = ValidatedFilePair(
        snapshot_date="2026-04-10",
        stock_path=stock_path,
        etf_path=etf_path,
        stock_sha256="stock-hash",
        etf_sha256="etf-hash",
    )

    stock_rows, etf_rows = parse_validated_file_pair(validated)

    assert len(stock_rows) == 1
    assert len(etf_rows) == 1
    assert stock_rows[0].instrument_type == "stock"
    assert stock_rows[0].snapshot_date == "2026-04-10"
    assert stock_rows[0].symbol == "AAPL"
    assert stock_rows[0].x_d_state_bars == 3
    assert etf_rows[0].instrument_type == "etf"
    assert etf_rows[0].symbol == "SPY"
    assert etf_rows[0].x_d_state_bars == 4


def test_parse_validated_file_pair_raises_error_when_required_column_missing(tmp_path: Path) -> None:
    """当缺少必填列时，应抛出 ExcelParseError。"""
    stock_path = tmp_path / "2026-04-10_stock.xlsx"
    etf_path = tmp_path / "2026-04-10_ETF.xlsx"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Symbol", "Description"])
    worksheet.append(["AAPL", "Apple Inc"])
    workbook.save(stock_path)

    _create_xlsx(
        etf_path,
        [["SPY", "S&P 500 ETF", "UP", 1, "UP", 1, "UP", 1]],
    )

    validated = ValidatedFilePair(
        snapshot_date="2026-04-10",
        stock_path=stock_path,
        etf_path=etf_path,
        stock_sha256="stock-hash",
        etf_sha256="etf-hash",
    )

    with pytest.raises(ExcelParseError):
        parse_validated_file_pair(validated)
