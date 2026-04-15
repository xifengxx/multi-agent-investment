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
    monkeypatch.setenv("LLM_ENABLED_PROVIDERS", "openai,anthropic")
    monkeypatch.setenv("LLM_MIN_EFFECTIVE_PROVIDERS", "1")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_MAX_INSTRUMENTS_PER_TYPE", "5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_HTTP_REFERER", "http://localhost")
    monkeypatch.setenv("OPENAI_X_TITLE", "multi_agent_investment")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    config = load_config()

    assert config.telegram_bot_token == "bot-token"
    assert config.telegram_chat_id == "10001"
    assert config.data_root == "/tmp/stock_data"
    assert config.sqlite_path == "/tmp/app.db"
    assert config.analysis_parallelism == 6
    assert config.dry_run is True
    assert config.app_env == "prod"
    assert config.llm_enabled_providers == ("openai", "anthropic")
    assert config.llm_min_effective_providers == 1
    assert config.llm_request_timeout_seconds == 15
    assert config.llm_max_retries == 3
    assert config.llm_max_instruments_per_type == 5
    assert config.openai_api_key == "sk-test-openai"
    assert config.openai_model == "gpt-4o-mini"
    assert config.openai_base_url == "https://api.openai.com/v1"
    assert config.openai_http_referer == "http://localhost"
    assert config.openai_x_title == "multi_agent_investment"
    assert config.anthropic_api_key == "sk-test-anthropic"
    assert config.anthropic_model == "claude-3-5-sonnet-latest"
    assert config.anthropic_base_url == ""


def test_load_config_uses_defaults_for_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置可选环境变量时，应使用默认值。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "10001")
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    monkeypatch.delenv("ANALYSIS_PARALLELISM", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LLM_ENABLED_PROVIDERS", raising=False)
    monkeypatch.delenv("LLM_MIN_EFFECTIVE_PROVIDERS", raising=False)
    monkeypatch.delenv("LLM_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("LLM_MAX_INSTRUMENTS_PER_TYPE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("GLM_MODEL", raising=False)
    monkeypatch.delenv("GLM_BASE_URL", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_MODEL", raising=False)
    monkeypatch.delenv("KIMI_BASE_URL", raising=False)

    config = load_config()

    assert config.data_root == "stock_data"
    assert config.sqlite_path == "data/app.db"
    assert config.analysis_parallelism == 3
    assert config.dry_run is False
    assert config.app_env == "dev"
    assert config.llm_enabled_providers == ()
    assert config.llm_min_effective_providers == 0
    assert config.llm_request_timeout_seconds == 30
    assert config.llm_max_retries == 2
    assert config.llm_max_instruments_per_type == 20
    assert config.openai_api_key == ""
    assert config.openai_model == ""
    assert config.openai_base_url == ""
    assert config.openai_http_referer == ""
    assert config.openai_x_title == ""
    assert config.anthropic_api_key == ""
    assert config.anthropic_model == ""
    assert config.anthropic_base_url == ""
    assert config.gemini_api_key == ""
    assert config.gemini_model == ""
    assert config.gemini_base_url == ""
    assert config.qwen_api_key == ""
    assert config.qwen_model == ""
    assert config.qwen_base_url == ""
    assert config.glm_api_key == ""
    assert config.glm_model == ""
    assert config.glm_base_url == ""
    assert config.kimi_api_key == ""
    assert config.kimi_model == ""
    assert config.kimi_base_url == ""


@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [
        ("ANALYSIS_PARALLELISM", "0"),
        ("ANALYSIS_PARALLELISM", "abc"),
        ("DRY_RUN", "maybe"),
        ("APP_ENV", "staging"),
        ("LLM_ENABLED_PROVIDERS", "unknown_provider"),
        ("LLM_MIN_EFFECTIVE_PROVIDERS", "-1"),
        ("LLM_MIN_EFFECTIVE_PROVIDERS", "abc"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "0"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "abc"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_MAX_RETRIES", "abc"),
        ("LLM_MAX_INSTRUMENTS_PER_TYPE", "-1"),
        ("LLM_MAX_INSTRUMENTS_PER_TYPE", "abc"),
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


def test_load_config_rejects_min_effective_providers_greater_than_effective(monkeypatch: pytest.MonkeyPatch) -> None:
    """当最小可用 provider 数大于实际可用数时，应抛出 ConfigError。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "10001")
    monkeypatch.setenv("LLM_ENABLED_PROVIDERS", "openai,anthropic")
    monkeypatch.setenv("LLM_MIN_EFFECTIVE_PROVIDERS", "2")
    # 仅配置 OpenAI，Anthropic 不提供 api_key/model，因此 effective=1 < min=2
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")

    with pytest.raises(ConfigError):
        load_config()
