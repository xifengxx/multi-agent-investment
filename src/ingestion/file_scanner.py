"""输入文件扫描模块。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


FILE_NAME_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<kind>stock|etf)\.xlsx$", re.IGNORECASE)


@dataclass(frozen=True)
class FilePairCandidate:
    """同一 snapshot_date 的文件对候选结构。"""

    snapshot_date: str
    stock_path: Path | None
    etf_path: Path | None
    actual_files: tuple[str, ...]


def _iter_candidate_xlsx_files(root_dir: Path) -> list[Path]:
    """递归遍历目录并返回所有 xlsx 文件（稳定排序）。"""
    return sorted(root_dir.rglob("*.xlsx"), key=lambda item: str(item))


def scan_file_pair_candidates(root_dir: str | Path, snapshot_date: str | None = None) -> list[FilePairCandidate]:
    """递归扫描目录，识别并聚合 stock/etf 文件对候选。"""
    root_path = Path(root_dir)
    grouped: dict[str, dict[str, Path | set[str] | None]] = {}

    for file_path in _iter_candidate_xlsx_files(root_path):
        matched = FILE_NAME_PATTERN.match(file_path.name)
        if not matched:
            continue
        current_date = matched.group("date")
        if snapshot_date is not None and current_date != snapshot_date:
            continue

        current_kind = matched.group("kind").lower()
        bucket = grouped.setdefault(
            current_date,
            {"stock": None, "etf": None, "actual_files": set()},
        )
        if bucket[current_kind] is None:
            bucket[current_kind] = file_path
        actual_files = bucket["actual_files"]
        if isinstance(actual_files, set):
            actual_files.add(file_path.name)

    candidates: list[FilePairCandidate] = []
    for current_date in sorted(grouped):
        bucket = grouped[current_date]
        actual_files = bucket["actual_files"]
        candidates.append(
            FilePairCandidate(
                snapshot_date=current_date,
                stock_path=bucket["stock"] if isinstance(bucket["stock"], Path) else None,
                etf_path=bucket["etf"] if isinstance(bucket["etf"], Path) else None,
                actual_files=tuple(sorted(actual_files)) if isinstance(actual_files, set) else tuple(),
            )
        )
    return candidates
