"""输入命名契约校验模块。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ingestion.file_scanner import FilePairCandidate


class ContractViolation(ValueError):
    """输入契约违反错误。"""


@dataclass(frozen=True)
class ValidatedFilePair:
    """通过命名契约校验后的文件对。"""

    snapshot_date: str
    stock_path: Path
    etf_path: Path
    stock_sha256: str
    etf_sha256: str


def _build_contract_error_message(snapshot_date: str, actual_files: tuple[str, ...]) -> str:
    """构造契约错误信息，包含期望文件名与实际文件名。"""
    expected_stock = f"{snapshot_date}_stock.xlsx"
    expected_etf = f"{snapshot_date}_etf.xlsx"
    actual_text = ", ".join(actual_files) if actual_files else "无"
    return (
        "输入契约校验失败，"
        f"期望文件名: {expected_stock}, {expected_etf}；"
        f"实际文件名: {actual_text}"
    )


def _calc_file_sha256(file_path: Path) -> str:
    """计算文件 SHA256 哈希。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as file_pointer:
        for chunk in iter(lambda: file_pointer.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_side_file(file_path: Path, expected_file_name: str) -> None:
    """校验文件存在且命名符合约定（大小写不敏感）。"""
    if not file_path.exists():
        raise ContractViolation(f"输入文件不存在: {file_path}")
    if file_path.name.lower() != expected_file_name.lower():
        raise ContractViolation(f"文件命名不符合契约，期望: {expected_file_name}，实际: {file_path.name}")


def validate_file_pair_candidate(candidate: FilePairCandidate) -> ValidatedFilePair:
    """校验扫描候选并返回通过校验的文件对。"""
    actual_files = candidate.actual_files
    if not actual_files:
        actual_file_names = [
            path.name for path in (candidate.stock_path, candidate.etf_path) if path is not None
        ]
        actual_files = tuple(sorted(actual_file_names))

    if candidate.stock_path is None or candidate.etf_path is None:
        raise ContractViolation(_build_contract_error_message(candidate.snapshot_date, actual_files))

    expected_stock = f"{candidate.snapshot_date}_stock.xlsx"
    expected_etf = f"{candidate.snapshot_date}_etf.xlsx"
    _validate_side_file(candidate.stock_path, expected_stock)
    _validate_side_file(candidate.etf_path, expected_etf)

    return ValidatedFilePair(
        snapshot_date=candidate.snapshot_date,
        stock_path=candidate.stock_path,
        etf_path=candidate.etf_path,
        stock_sha256=_calc_file_sha256(candidate.stock_path),
        etf_sha256=_calc_file_sha256(candidate.etf_path),
    )
