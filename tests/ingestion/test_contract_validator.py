"""输入命名契约校验测试。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from ingestion.file_scanner import FilePairCandidate  # noqa: E402
from ingestion.input_contract_validator import (  # noqa: E402
    ContractViolation,
    validate_file_pair_candidate,
)


def _write_binary(file_path: Path, content: bytes) -> None:
    """写入二进制文件并自动创建目录。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)


def test_validate_file_pair_candidate_returns_expected_actual_message_when_pair_missing(tmp_path: Path) -> None:
    """当同日 stock/etf 不成对时，应给出期望文件名和实际文件名。"""
    stock_path = tmp_path / "2026-04-10_stock.xlsx"
    _write_binary(stock_path, b"stock-content")
    candidate = FilePairCandidate(
        snapshot_date="2026-04-10",
        stock_path=stock_path,
        etf_path=None,
        actual_files=(stock_path.name,),
    )

    with pytest.raises(ContractViolation) as error_info:
        validate_file_pair_candidate(candidate)

    message = str(error_info.value)
    assert "期望文件名" in message
    assert "2026-04-10_stock.xlsx" in message
    assert "2026-04-10_etf.xlsx" in message
    assert "实际文件名" in message


def test_validate_file_pair_candidate_accepts_uppercase_etf_and_generates_sha256(tmp_path: Path) -> None:
    """当 ETF 文件后缀为大写时应通过校验并生成哈希。"""
    stock_path = tmp_path / "2026-04-10_stock.xlsx"
    etf_path = tmp_path / "2026-04-10_ETF.xlsx"
    stock_content = b"stock-content"
    etf_content = b"etf-content"
    _write_binary(stock_path, stock_content)
    _write_binary(etf_path, etf_content)

    candidate = FilePairCandidate(
        snapshot_date="2026-04-10",
        stock_path=stock_path,
        etf_path=etf_path,
        actual_files=(stock_path.name, etf_path.name),
    )

    validated = validate_file_pair_candidate(candidate)

    assert validated.snapshot_date == "2026-04-10"
    assert validated.stock_path == stock_path
    assert validated.etf_path == etf_path
    assert validated.stock_sha256 == hashlib.sha256(stock_content).hexdigest()
    assert validated.etf_sha256 == hashlib.sha256(etf_content).hexdigest()
