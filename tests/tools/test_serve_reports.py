from __future__ import annotations

from tools.serve_reports import build_parser


def test_serve_reports_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == "8888"
