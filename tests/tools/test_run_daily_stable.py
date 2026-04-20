"""run_daily_stable 工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tools.run_daily_stable import load_env_exports  # noqa: E402


def test_load_env_exports_parses_quoted_and_ignores_trailing_comment(tmp_path: Path) -> None:
    """应能解析带引号的 export，并忽略引号后的注释。"""
    p = tmp_path / ".env.local"
    p.write_text(
        '\n'.join(
            [
                'export OPENAI_API_KEY="sk-xxx"  # comment',
                'export DRY_RUN="true"',
            ]
        ),
        encoding="utf-8",
    )
    env = load_env_exports(p)
    assert env["OPENAI_API_KEY"] == "sk-xxx"
    assert env["DRY_RUN"] == "true"


def test_load_env_exports_parses_unquoted_and_strips_comment(tmp_path: Path) -> None:
    """应能解析无引号值，并去除 # 注释。"""
    p = tmp_path / ".env.local"
    p.write_text('export DATA_ROOT=/tmp/stock_data # hi', encoding="utf-8")
    env = load_env_exports(p)
    assert env["DATA_ROOT"] == "/tmp/stock_data"


def test_load_env_exports_raises_on_unclosed_quote(tmp_path: Path) -> None:
    """引号不闭合应抛异常，避免静默读取错误值。"""
    p = tmp_path / ".env.local"
    p.write_text('export X="oops', encoding="utf-8")
    with pytest.raises(ValueError):
        load_env_exports(p)

