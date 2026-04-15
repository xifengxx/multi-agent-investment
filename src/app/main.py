"""CLI 入口模块。"""

from __future__ import annotations

import argparse

from app.config import AppConfig, load_config
from common.types import RunMode


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="investment-app")
    parser.add_argument("mode", choices=["daily", "weekly"], help="运行模式")
    return parser


def run(mode: RunMode, config: AppConfig) -> int:
    """执行入口逻辑（Task1 仅做占位返回）。"""
    print(f"mode={mode}, env={config.app_env}, dry_run={config.dry_run}")
    return 0


def main() -> int:
    """解析参数并触发应用运行。"""
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()
    return run(mode=args.mode, config=config)


if __name__ == "__main__":
    raise SystemExit(main())
