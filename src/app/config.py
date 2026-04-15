"""应用配置加载模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from common.types import AppEnv


class ConfigError(ValueError):
    """配置错误类型。"""


@dataclass(frozen=True)
class AppConfig:
    """应用运行时配置。"""

    telegram_bot_token: str
    telegram_chat_id: str
    data_root: str
    sqlite_path: str
    analysis_parallelism: int
    dry_run: bool
    app_env: AppEnv


def _read_required_env(key: str) -> str:
    """读取必填环境变量，缺失时抛出 ConfigError。"""
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(f"缺失必填环境变量: {key}")
    return value


def _read_int_env(key: str, default: int) -> int:
    """读取整型环境变量，无法转换时抛出 ConfigError。"""
    raw = os.getenv(key, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 不是合法整数: {raw}") from exc
    if value <= 0:
        raise ConfigError(f"{key} 必须大于 0: {raw}")
    return value


def _read_bool_env(key: str, default: bool) -> bool:
    """读取布尔环境变量，支持常见真值与假值。"""
    raw = os.getenv(key, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} 不是合法布尔值: {raw}")


def _read_app_env() -> AppEnv:
    """读取 APP_ENV 并校验枚举值。"""
    raw = os.getenv("APP_ENV", "dev").strip().lower()
    if raw not in {"dev", "test", "prod"}:
        raise ConfigError(f"APP_ENV 不合法: {raw}")
    return cast(AppEnv, raw)


def load_config() -> AppConfig:
    """加载应用配置并返回不可变配置对象。"""
    return AppConfig(
        telegram_bot_token=_read_required_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_read_required_env("TELEGRAM_CHAT_ID"),
        data_root=os.getenv("DATA_ROOT", "stock_data"),
        sqlite_path=os.getenv("SQLITE_PATH", "data/app.db"),
        analysis_parallelism=_read_int_env("ANALYSIS_PARALLELISM", default=3),
        dry_run=_read_bool_env("DRY_RUN", default=False),
        app_env=_read_app_env(),
    )
