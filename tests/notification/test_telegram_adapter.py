"""Telegram 适配器单测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from notification.telegram_adapter import TelegramAdapter  # noqa: E402


class _FakeResponse:
    """urllib 响应对象最小替身。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """构造响应替身并准备 JSON bytes。"""
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        """模拟 urllib response.read()."""
        return self._body

    def __enter__(self) -> "_FakeResponse":
        """支持 with urlopen(...) as resp 语法。"""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """退出上下文管理器。"""
        return None


def test_telegram_adapter_dry_run_does_not_touch_network() -> None:
    """dry_run=True 时不应调用网络层 urlopen。"""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry_run 模式不应触发网络请求")

    adapter = TelegramAdapter(bot_token="t", chat_id="c", dry_run=True, urlopen=_boom)
    resp = adapter.send_message(text="hello")

    assert resp["ok"] is True
    assert resp["dry_run"] is True
    assert resp["request"]["chat_id"] == "c"
    assert resp["request"]["text"] == "hello"


def test_telegram_adapter_builds_urllib_request_payload() -> None:
    """非 dry_run 时应构造正确的 urllib 请求与编码 payload。"""
    captured: dict[str, Any] = {}

    def _fake_urlopen(req, timeout: float | None = None) -> _FakeResponse:  # type: ignore[no-untyped-def]
        captured["url"] = getattr(req, "full_url", "")
        captured["data"] = getattr(req, "data", b"")
        captured["timeout"] = timeout
        return _FakeResponse({"ok": True, "result": {"message_id": 123}})

    adapter = TelegramAdapter(bot_token="bot-xxx", chat_id="10001", dry_run=False, urlopen=_fake_urlopen)
    resp = adapter.send_message(text="hello world")

    assert resp["ok"] is True
    assert "https://api.telegram.org/botbot-xxx/sendMessage" in captured["url"]
    assert b"chat_id=10001" in captured["data"]
    assert b"text=hello+world" in captured["data"]

