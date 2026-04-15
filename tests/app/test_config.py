"""配置模块测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.config import ConfigError, load_config  # noqa: E402


def test_load_config_missing_required_values_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """当缺失必填环境变量时，应抛出 ConfigError。"""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_reads_and_converts_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """应正确读取环境变量并完成类型转换。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "10001")
    monkeypatch.setenv("DATA_ROOT", "/tmp/stock_data")
    monkeypatch.setenv("SQLITE_PATH", "/tmp/app.db")
    monkeypatch.setenv("ANALYSIS_PARALLELISM", "6")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("APP_ENV", "prod")

    config = load_config()

    assert config.telegram_bot_token == "bot-token"
    assert config.telegram_chat_id == "10001"
    assert config.data_root == "/tmp/stock_data"
    assert config.sqlite_path == "/tmp/app.db"
    assert config.analysis_parallelism == 6
    assert config.dry_run is True
    assert config.app_env == "prod"


def test_load_config_uses_defaults_for_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置可选环境变量时，应使用默认值。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "10001")
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    monkeypatch.delenv("ANALYSIS_PARALLELISM", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)

    config = load_config()

    assert config.data_root == "stock_data"
    assert config.sqlite_path == "data/app.db"
    assert config.analysis_parallelism == 3
    assert config.dry_run is False
    assert config.app_env == "dev"


@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [
        ("ANALYSIS_PARALLELISM", "0"),
        ("ANALYSIS_PARALLELISM", "abc"),
        ("DRY_RUN", "maybe"),
        ("APP_ENV", "staging"),
    ],
)
def test_load_config_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_value: str,
) -> None:
    """当环境变量格式非法时，应抛出 ConfigError。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "10001")
    monkeypatch.setenv(env_key, env_value)

    with pytest.raises(ConfigError):
        load_config()
