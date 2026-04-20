"""Panel Analysis schema 常量与枚举集合。"""

from __future__ import annotations


SCHEMA_VERSION = "v1"

SIGNAL_STRENGTHS: frozenset[str] = frozenset({"strong", "watch", "risk"})

PATTERN_IDS: frozenset[str] = frozenset(
    {
        "D:-1->0",
        "D:-1->1",
        "W:-1->0",
        "W:-1->1",
        "M:-1->0",
        "M:-1->1",
        "DW:-1->(0|1)",
        "DM:-1->(0|1)",
        "WM:-1->(0|1)",
        "DWM:-1->(0|1)",
    }
)

