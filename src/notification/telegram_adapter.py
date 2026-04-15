"""Telegram 通知适配器（urllib 实现）。"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable


UrlopenFn = Callable[..., Any]


class TelegramAdapter:
    """Telegram 发送消息适配器。

    - 使用 urllib（标准库）实现 HTTP 请求
    - 支持 dry_run：不触发真实网络请求，仅返回可落库的请求摘要
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        dry_run: bool,
        timeout_seconds: float = 10.0,
        urlopen: UrlopenFn | None = None,
    ) -> None:
        """初始化适配器。

        参数：
        - bot_token/chat_id：Telegram Bot API 的鉴权与目标会话
        - dry_run：True 时不发送网络请求
        - timeout_seconds：urllib 超时时间（秒）
        - urlopen：可注入的网络函数，便于单测替身
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._dry_run = bool(dry_run)
        self._timeout_seconds = float(timeout_seconds)
        self._urlopen = urlopen or urllib.request.urlopen

    def send_message(self, *, text: str) -> dict[str, Any]:
        """发送一条文本消息到 Telegram。

        返回值尽量贴近 Telegram API 的 JSON 响应结构，并附带 request 字段，便于落库审计。
        """
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text}

        if self._dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "request": payload,
                "result": {"message_id": "dry-run"},
            }

        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with self._urlopen(req, timeout=self._timeout_seconds) as resp:
            body = resp.read()
        parsed = json.loads(body.decode("utf-8") or "{}")
        if isinstance(parsed, dict):
            parsed.setdefault("request", payload)
            parsed.setdefault("dry_run", False)
            return parsed
        return {"ok": False, "dry_run": False, "request": payload, "error": "invalid_response"}

