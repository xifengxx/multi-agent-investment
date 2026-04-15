"""CLI 入口模块。"""

from __future__ import annotations

import argparse

from app.config import AppConfig, load_config
from common.types import RunMode
from orchestrator.run_orchestrator import daily_run, weekly_run


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="investment-app")
    parser.add_argument("mode", choices=["daily", "weekly"], help="运行模式")
    return parser


def run(mode: RunMode, config: AppConfig) -> int:
    """执行入口逻辑。"""
    print(f"mode={mode}, env={config.app_env}, dry_run={config.dry_run}")
    if mode == "daily":
        run_id = daily_run(config=config, snapshot_date=None, providers=None, dry_run=config.dry_run)
        print(f"daily run_id={run_id}")
        return 0
    if mode == "weekly":
        run_id = weekly_run(config=config, week_end=None, dry_run=config.dry_run)
        print(f"weekly run_id={run_id}")
        return 0
    return 0


def main() -> int:
    """解析参数并触发应用运行。"""
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()
    return run(mode=args.mode, config=config)


if __name__ == "__main__":
    raise SystemExit(main())
