"""CLI 入口模块。"""

from __future__ import annotations

import argparse

from app.config import AppConfig, load_config
from common.types import RunMode
from orchestrator.run_orchestrator import daily_run, weekly_run


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="investment-app")
    parser.add_argument("mode", choices=["daily", "weekly", "web", "worker"], help="运行模式")
    parser.add_argument("--worker-id", default="", help="worker 模式下的实例标识")
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
    if mode == "web":
        from web.server import create_http_server

        server = create_http_server(config=config, host=config.web_bind_host, port=config.web_port)
        host, port = server.server_address[0], server.server_address[1]
        print(f"web listening on http://{host}:{port}")
        server.serve_forever()
        return 0
    if mode == "worker":
        from worker.job_worker import JobWorker

        return 0
    return 0


def main() -> int:
    """解析参数并触发应用运行。"""
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()
    if args.mode == "worker":
        worker_id = str(args.worker_id or "").strip()
        if not worker_id:
            raise SystemExit("--worker-id is required for worker mode")
        from worker.job_worker import JobWorker

        print(f"mode=worker, env={config.app_env}, dry_run={config.dry_run}, worker_id={worker_id}")
        worker = JobWorker(worker_id=worker_id, config=config, providers=None)
        worker.run_forever()
        return 0
    return run(mode=args.mode, config=config)


if __name__ == "__main__":
    raise SystemExit(main())
