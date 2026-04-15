"""OpenAI-compatible Provider 的 HTTP 调用与容错测试。

注意：这里通过 mock urllib.request.urlopen 来避免真实网络请求。
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

from analysis.providers.openai_compatible_provider import OpenAICompatibleProvider  # noqa: E402


def _fake_http_response(*, payload: dict) -> object:
    """构造一个最小的 urlopen 返回对象：仅实现 read() -> bytes。"""

    def _read() -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    return SimpleNamespace(read=_read)


def test_invoke_returns_choices_message_content_and_sends_expected_request() -> None:
    """invoke 应返回 choices[0].message.content，并发送 chat/completions 请求。"""
    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        model="gpt-test",
        api_key="sk-secret",
        timeout_seconds=3,
        max_retries=0,
        extra_headers={"X-Test": "1"},
    )

    urlopen_mock = Mock(
        return_value=_fake_http_response(payload={"choices": [{"message": {"content": "OK"}}]})
    )
    with patch("urllib.request.urlopen", urlopen_mock):
        text = provider.invoke("hello")

    assert text == "OK"
    assert urlopen_mock.call_count == 1

    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "https://example.com/v1/chat/completions"
    assert request.get_method() == "POST"

    sent = json.loads(request.data.decode("utf-8"))
    assert sent["model"] == "gpt-test"
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == "hello"

    # 关键头部：Authorization 与自定义 header
    header_items = {k.lower(): v for k, v in request.header_items()}
    assert header_items["authorization"] == "Bearer sk-secret"
    assert header_items["content-type"] == "application/json"
    assert header_items["x-test"] == "1"


def test_invoke_retries_on_error_then_succeeds() -> None:
    """当 urlopen 临时失败时，invoke 应按 max_retries 重试并最终成功。"""
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        model="gpt-test",
        api_key="sk-secret",
        timeout_seconds=1,
        max_retries=1,
        extra_headers={},
    )

    urlopen_mock = Mock(
        side_effect=[
            URLError("temporary network error"),
            _fake_http_response(payload={"choices": [{"message": {"content": "OK"}}]}),
        ]
    )
    with patch("urllib.request.urlopen", urlopen_mock):
        text = provider.invoke("hello")

    assert text == "OK"
    assert urlopen_mock.call_count == 2


def test_invoke_raises_and_does_not_leak_api_key_on_error() -> None:
    """当请求失败时应抛异常，异常信息不得包含 api_key。"""
    api_key = "sk-VERY-SECRET"
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        model="gpt-test",
        api_key=api_key,
        timeout_seconds=1,
        max_retries=0,
        extra_headers={},
    )

    urlopen_mock = Mock(side_effect=URLError(f"boom {api_key} boom"))
    with patch("urllib.request.urlopen", urlopen_mock):
        with pytest.raises(RuntimeError) as excinfo:
            provider.invoke("hello")

    assert api_key not in str(excinfo.value)
    assert "boom" in str(excinfo.value)
