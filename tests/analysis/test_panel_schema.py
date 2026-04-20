"""Panel Analysis Schema 常量测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def test_pattern_id_enum_contains_required_values() -> None:
    """pattern_id 枚举应包含基础 D/W/M 反转与共振模式。"""
    from analysis.panel_schema import PATTERN_IDS  # noqa: E402

    assert "D:-1->0" in PATTERN_IDS
    assert "D:-1->1" in PATTERN_IDS
    assert "W:-1->0" in PATTERN_IDS
    assert "W:-1->1" in PATTERN_IDS
    assert "M:-1->0" in PATTERN_IDS
    assert "M:-1->1" in PATTERN_IDS
    assert "DWM:-1->(0|1)" in PATTERN_IDS

