"""Gemini Provider（Google AI Studio / Generative Language API）的 HTTP 调用与容错测试。

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

from analysis.providers.gemini_provider import GeminiProvider  # noqa: E402


def _fake_http_response(*, payload: dict) -> object:
    """构造一个最小的 urlopen 返回对象：仅实现 read() -> bytes。"""

    def _read() -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    return SimpleNamespace(read=_read)


def test_invoke_returns_concatenated_parts_text_and_sends_expected_request() -> None:
    """invoke 应拼接 candidates[0].content.parts[*].text，并发送 generateContent 请求。"""
    api_key = "AIza-VERY-SECRET"
    provider = GeminiProvider(
        model="gemini-1.5-flash",
        api_key=api_key,
        timeout_seconds=3,
    )

    urlopen_mock = Mock(
        return_value=_fake_http_response(
            payload={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Hello"},
                                {"text": " "},
                                {"text": "World"},
                            ]
                        }
                    }
                ]
            }
        )
    )
    with patch("urllib.request.urlopen", urlopen_mock):
        text = provider.invoke("say hi")

    assert text == "Hello World"
    assert urlopen_mock.call_count == 1

    request = urlopen_mock.call_args.args[0]
    assert request.get_method() == "POST"
    assert (
        request.full_url
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=AIza-VERY-SECRET"
    )

    sent = json.loads(request.data.decode("utf-8"))
    assert sent["contents"][0]["role"] == "user"
    assert sent["contents"][0]["parts"][0]["text"] == "say hi"

    header_items = {k.lower(): v for k, v in request.header_items()}
    assert header_items["content-type"] == "application/json"


def test_invoke_raises_and_does_not_leak_api_key_even_if_url_in_error() -> None:
    """当请求失败时应抛异常，异常信息不得包含 api_key（包括 URL 中的 key=...）。"""
    api_key = "AIza-VERY-SECRET"
    provider = GeminiProvider(
        model="gemini-1.5-flash",
        api_key=api_key,
        timeout_seconds=1,
    )

    url_with_key = (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        f"?key={api_key}"
    )
    urlopen_mock = Mock(side_effect=URLError(f"boom {url_with_key} boom {api_key}"))
    with patch("urllib.request.urlopen", urlopen_mock):
        with pytest.raises(RuntimeError) as excinfo:
            provider.invoke("hello")

    assert api_key not in str(excinfo.value)
    assert "boom" in str(excinfo.value)


def test_invoke_with_files_uses_inline_file_parts_and_prompt(tmp_path: Path) -> None:
    """invoke_with_files 应先上传文件获取 file_uri，再在 generateContent 引用 file_uri。"""
    provider = GeminiProvider(
        model="gemini-1.5-flash",
        api_key="AIza-VERY-SECRET",
        timeout_seconds=3,
    )

    xlsx_path = tmp_path / "sample.xlsx"
    file_bytes = b"fake-xlsx-binary"
    xlsx_path.write_bytes(file_bytes)

    def _fake_upload_start_response() -> object:
        def _read() -> bytes:
            return b"{}"

        headers = {"x-goog-upload-url": "https://upload.example/upload-session"}
        return SimpleNamespace(read=_read, headers=headers)

    def _fake_upload_finalize_response() -> object:
        return _fake_http_response(payload={"file": {"uri": "files/abc123"}})

    urlopen_mock = Mock(
        side_effect=[
            _fake_upload_start_response(),
            _fake_upload_finalize_response(),
            _fake_http_response(payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
        ]
    )
    with patch("urllib.request.urlopen", urlopen_mock):
        text = provider.invoke_with_files(prompt="analyze now", file_paths=[str(xlsx_path)])

    assert provider.supports_file_input is True
    assert text == "ok"
    assert urlopen_mock.call_count == 3

    request = urlopen_mock.call_args_list[2].args[0]
    sent = json.loads(request.data.decode("utf-8"))
    parts = sent["contents"][0]["parts"]
    assert parts[0]["text"] == "analyze now"
    assert "inlineData" not in parts[1]
    assert parts[1]["file_data"]["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert parts[1]["file_data"]["file_uri"] == "files/abc123"
