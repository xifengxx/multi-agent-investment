"""本地静态文件服务：用于浏览 reports 产物。"""

from __future__ import annotations

import argparse
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="serve-reports")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8888")
    parser.add_argument("--root", default="", help="静态服务根目录（默认使用 DATA_ROOT 或 stock_data）")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    host = str(args.host).strip() or "127.0.0.1"
    port = int(str(args.port).strip() or "8888")

    root = str(args.root).strip() or os.getenv("DATA_ROOT", "stock_data")
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit(f"invalid_root: {root_path}")

    handler = lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=str(root_path), **kw)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"serving {root_path} at http://{host}:{port}/")
    print(f"reports index: http://{host}:{port}/reports/")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

