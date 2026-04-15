# multi_agent_investment（MVP）

一个面向个人使用的“多 Agent 投票 + 纸面交易 + 通知”的最小可用流水线。

- 输入：按日期命名的一对 xlsx（stock/etf）
- 处理：解析 ->（可选）LLM 分析 -> 投票聚合 -> 决策榜单 -> 纸面交易/持仓 -> 通知
- 落库：SQLite（可审计、可回放）

---

## 快速开始（dry_run）

> 提示：项目当前未提供锁定的依赖文件；最小运行需要 `openpyxl`，测试需要 `pytest`。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install openpyxl pytest
```

设置环境变量（dry_run 下 Telegram 仍要求非空配置）：

```bash
export TELEGRAM_BOT_TOKEN="x"
export TELEGRAM_CHAT_ID="x"
export DRY_RUN="true"
export APP_ENV="test"
```

运行最小 E2E（会在 tmp_path 生成一对 xlsx，跑完整 daily 流水线并断言落库表行数）：

```bash
pytest -q tests/e2e/test_mvp_pipeline.py
```

更多运行方式与排障见：[mvp-runbook.md](file:///Users/mac/Documents/trae_projects/multi_agent_investment/.worktrees/feature-mvp-task1/docs/runbooks/mvp-runbook.md)

---

## 目录结构

- src/app：配置与 CLI 入口
- src/ingestion：输入文件扫描/契约校验/Excel 解析
- src/analysis：Prompt 构建、LLM 调用引擎、输出校验、Mock Provider
- src/decision：投票聚合器（出 decisions 榜单）
- src/ledger：纸面交易与持仓更新
- src/notification：消息格式化与 Telegram 发送（支持 dry_run）
- src/orchestrator：daily/weekly 编排流程
- src/storage：SQLite schema 与各表 repository
- tests：单测与端到端用例（含 tests/e2e）

