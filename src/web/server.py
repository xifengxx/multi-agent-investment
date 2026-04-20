"""Web Server 入口（标准库 http.server）。"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urlparse

from app.config import AppConfig
from web.routes_runs import handle_runs
from web.routes_upload import handle_upload


class _Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器（仅提供 /api/*）。"""

    server_version = "multi-agent-web/0.1"

    def _send_json(self, *, status: int, payload: dict) -> None:
        """返回 JSON 响应。"""
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, *, status: int, text: str) -> None:
        """返回纯文本响应。"""
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _require_bearer(self) -> bool:
        """校验 Bearer Token，不通过则直接返回 401。"""
        config: AppConfig = self.server.config  # type: ignore[attr-defined]
        expected = (config.api_token or "").strip()
        if not expected:
            self._send_text(status=500, text="server_misconfigured:API_TOKEN")
            return False
        auth = (self.headers.get("Authorization") or "").strip()
        if auth != f"Bearer {expected}":
            self._send_text(status=401, text="unauthorized")
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        """处理 POST 请求。"""
        if not self.path.startswith("/api/"):
            self._send_text(status=404, text="not_found")
            return
        if not self._require_bearer():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            handle_upload(self, config=self.server.config)  # type: ignore[attr-defined]
            return
        self._send_text(status=404, text="not_found")

    def do_GET(self) -> None:  # noqa: N802
        """处理 GET 请求。"""
        if not self.path.startswith("/api/"):
            self._send_text(status=404, text="not_found")
            return
        if not self._require_bearer():
            return
        handle_runs(self, config=self.server.config)  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        """避免在测试中输出过多日志；生产可改为结构化日志。"""
        return


def create_http_server(*, config: AppConfig, host: str, port: int) -> ThreadingHTTPServer:
    """创建并返回 HTTPServer 实例。"""
    server = ThreadingHTTPServer((host, int(port)), _Handler)
    server.config = config  # type: ignore[attr-defined]
    return server


def run_http_server_in_thread(
    *, config: AppConfig, port: int
) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    """在后台线程运行 HTTPServer（用于测试）。"""
    server = create_http_server(config=config, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_port = int(server.server_address[1])
    return server, thread, actual_port

