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

说明（dry_run）：
 - `DRY_RUN=true` 仅影响通知发送：不会发起真实 Telegram 网络请求，但仍会落库 `notifications`（状态为 `SENT`，并记录 request 摘要）。
 - `DRY_RUN=true` 不会自动关闭 LLM 调用：若你启用了真实 LLM providers，仍会实际请求（可能产生计费）。

## 可选：启用真实 LLM Providers（配置驱动）

支持的 provider：`openai / anthropic / gemini / qwen / glm / kimi / minimax`。

关键环境变量：

- `LLM_ENABLED_PROVIDERS`：逗号分隔的候选 provider 列表（顺序即调用顺序，例如 `openai,anthropic,gemini`）。
- `LLM_MIN_EFFECTIVE_PROVIDERS`：本次运行至少需要的“有效 provider”数量；不足时会标记为 `DEGRADED` 并仅记录降级通知。
  - “有效 provider” = 具备最小调用所需字段（通常为 `*_API_KEY + *_MODEL`；其中 `qwen/glm/kimi` 还要求 `*_BASE_URL` 非空）。
- `LLM_MAX_INSTRUMENTS_PER_TYPE`：单次运行每类（stock/etf）最多分析的标的数（用于联调/控成本），默认 `20`。

联调期提示：

- 当 `LLM_MIN_EFFECTIVE_PROVIDERS <= 2` 时，投票门槛会放宽（允许 1 票 BUY 进入榜单），便于先把全链路跑通；当你把门槛调回 `>=3` 时会自动恢复更严格的默认规则。

安全提示：

- 不要把 `.env` / key 写入 git（建议本地用 `export ...` 或私有的 `.env` 文件，并确保不提交）。
- 不要在日志/截图/Issue 中粘贴 API Key（包括 CI 输出）。

示例（占位符请替换为你自己的值）：

```bash
# 选择要启用的 provider（逗号分隔）
export LLM_ENABLED_PROVIDERS="openai,anthropic,gemini"
export LLM_MIN_EFFECTIVE_PROVIDERS="3"

# OpenAI（base_url 可省略，默认 https://api.openai.com/v1）
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4.1-mini"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_HTTP_REFERER="http://localhost"   # 可选：OpenRouter 常用
export OPENAI_X_TITLE="multi_agent_investment"  # 可选：OpenRouter 常用

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-3-5-sonnet-latest"

# Gemini（Google AI Studio）
export GEMINI_API_KEY="..."
export GEMINI_MODEL="gemini-2.0-flash"

# Qwen / GLM / Kimi（OpenAI-compatible；BASE_URL 以各厂商官方文档为准）
export QWEN_API_KEY="..."
export QWEN_MODEL="qwen-max"
export QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"

export GLM_API_KEY="..."
export GLM_MODEL="glm-4"
export GLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"  # 示例

export KIMI_API_KEY="..."
export KIMI_MODEL="moonshot-v1-8k"
export KIMI_BASE_URL="https://api.moonshot.cn/v1"

# MiniMax（OpenAI-compatible）
export MINIMAX_API_KEY="..."
export MINIMAX_MODEL="MiniMax-M2.5"
export MINIMAX_BASE_URL="https://api.example.com/v1"  # 示例
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
