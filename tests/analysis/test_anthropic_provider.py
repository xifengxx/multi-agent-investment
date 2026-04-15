"""Anthropic Provider 的 HTTP 调用与容错测试。

注意：通过 mock urllib.request.urlopen 来避免真实网络请求。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.providers.anthropic_provider import AnthropicProvider  # noqa: E402


def _fake_http_response(*, payload: dict) -> object:
    """构造一个最小的 urlopen 返回对象：仅实现 read() -> bytes。"""

    def _read() -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    return SimpleNamespace(read=_read)


def test_invoke_returns_joined_text_and_sends_expected_request() -> None:
    """invoke 应拼接 content blocks 中的 text，并发送 messages 请求。"""
    provider = AnthropicProvider(
        model="claude-test",
        api_key="sk-ant-secret",
        max_tokens=128,
        timeout_seconds=3,
        anthropic_version="2023-06-01",
    )

    urlopen_mock = Mock(
        return_value=_fake_http_response(
            payload={"content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " World"}]}
        )
    )
    with patch("urllib.request.urlopen", urlopen_mock):
        text = provider.invoke("hi")

    assert text == "Hello World"
    assert urlopen_mock.call_count == 1

    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"

    sent = json.loads(request.data.decode("utf-8"))
    assert sent["model"] == "claude-test"
    assert sent["max_tokens"] == 128
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == "hi"

    header_items = {k.lower(): v for k, v in request.header_items()}
    assert header_items["x-api-key"] == "sk-ant-secret"
    assert header_items["anthropic-version"] == "2023-06-01"
    assert header_items["content-type"] == "application/json"


def test_invoke_raises_and_does_not_leak_api_key_on_error() -> None:
    """当请求失败时应抛异常，异常信息不得包含 api_key。"""
    api_key = "sk-ANTHROPIC-VERY-SECRET"
    provider = AnthropicProvider(
        model="claude-test",
        api_key=api_key,
        max_tokens=16,
        timeout_seconds=1,
        anthropic_version="2023-06-01",
    )

    urlopen_mock = Mock(side_effect=URLError(f"boom {api_key} boom"))
    with patch("urllib.request.urlopen", urlopen_mock):
        with pytest.raises(RuntimeError) as excinfo:
            provider.invoke("hi")

    assert api_key not in str(excinfo.value)
    assert "boom" in str(excinfo.value)


def test_invoke_raises_on_unexpected_response_shape() -> None:
    """当响应结构不符合预期时应抛异常。"""
    provider = AnthropicProvider(
        model="claude-test",
        api_key="sk-ant-secret",
        max_tokens=16,
        timeout_seconds=1,
        anthropic_version="2023-06-01",
    )

    urlopen_mock = Mock(return_value=_fake_http_response(payload={"content": [{"type": "text"}]}))
    with patch("urllib.request.urlopen", urlopen_mock):
        with pytest.raises(RuntimeError):
            provider.invoke("hi")

