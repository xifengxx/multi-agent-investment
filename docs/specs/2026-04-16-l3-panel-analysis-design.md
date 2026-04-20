# L3 分析层重构：按文件面板分析（Panel Analysis）设计稿（V1）

## 1. 背景

当前实现把 LLM 调用粒度设为“逐标的（symbol）逐次调用”，并把每个标的输出汇总投票。该逻辑与目标需求不一致：需求是让每个 LLM 基于 Excel 中“按天子表的面板数据”做一次整体分析，总结趋势反转规律与建仓信号，并给出 Top10 标的清单。

本设计稿将 L3 分析层调整为“每个 provider 对每个文件调用一次”（stock/etf 各一次），LLM 输出结构化报告 + Top10，系统再将 Top10 展开为投票输入，以复用现有决策与执行链路。

## 2. 输入数据事实（来自真实文件扫描）

### 2.1 Stock 文件

- 文件：`2026-04-09_stock.xlsx`
- 结构：多个子表，子表名为日期（如 `2026-02-14`），表示按天的截面面板
- 子表列（核心字段）：
  - `Symbol, Description`
  - `X_D_Trend_State, X_D_State_Bars`
  - `X_W_Trend_State, X_W_State_Bars`
  - `X_M_Trend_State, X_M_State_Bars`
  - `Price, Volume`

### 2.2 ETF 文件

- 文件：`2026-04-09_ETF.xlsx`
- 结构：同样为多个日期子表
- 子表列（核心字段）：
  - `Symbol, Description`
  - `X_*_Trend_State, X_*_State_Bars`
- 约束：ETF 文件缺少 `Price/Volume`

## 3. 目标与非目标（V1）

### 3.1 目标

- 分析粒度：每个 provider 对每个文件调用一次（stock/etf），而非逐 symbol 调用
- 输出：每次调用输出严格 JSON（schema_version=v1），包含数据概览、反转模式统计、信号总结、Top10 及每支标的逻辑
- 投票：Top10 等权计票（每个 provider 的 top10 中每个 symbol 计 1 票 BUY）
- 通知：Telegram 展示简版逻辑；同时生成完整 Markdown 报告供审计与查看
- ETF：V1 不做 Price 补全与收益/回撤计算（收益类字段为 null，并解释原因）

### 3.2 非目标

- V1 不实现 ETF/Stock 的外部行情拉取与收益率回测
- V1 不做 LLM 权重化投票（后续可演进）
- V1 不做 Word 输出（先 Markdown；后续可扩展）

## 4. 新的 L3 调用方式（A 方案）

对每次 daily：

- 输入文件：`stock.xlsx` 与 `ETF.xlsx`（符合命名契约）
- enabled_providers 列表：按配置启用的 provider 顺序
- 调用次数：`len(enabled_providers) * 2`
  - 每个 provider：
    - 调用一次分析 stock 面板 → 生成 stock 报告 + stock Top10
    - 调用一次分析 etf 面板 → 生成 etf 报告 + etf Top10

## 5. 数据面板压缩（Panel Builder）

### 5.1 为什么需要压缩

Excel 为“按天子表的截面面板”，直接全文注入到 prompt 会导致输入过大、调用慢、失败率高。需要把输入转换为紧凑、结构化、对模型友好的表示。

### 5.2 建议的数据结构

对每个文件（stock/etf），构建 JSON：

- `sheet_count`
- `date_range`
- `symbol_count`
- `symbols`: 列表，每项包含：
  - `symbol`
  - `description`
  - `series`: 按日期升序的数组，每项：
    - `date`
    - `x_d_trend_state, x_d_state_bars`
    - `x_w_trend_state, x_w_state_bars`
    - `x_m_trend_state, x_m_state_bars`
    - `price`（stock 可能有；etf 为空）
    - `volume`（stock 可能有；etf 为空）

### 5.3 截断策略（可配置）

- 默认只取最近 `PANEL_MAX_SHEETS` 个日期子表（建议默认 60）
- 仍保留 `date_range`，让 LLM 在 data_overview 中说明时间范围

## 6. LLM 输出 JSON（schema_version=v1）

### 6.1 输出硬约束

- 必须只输出单个 JSON（无 markdown/解释文字）
- 必须严格符合字段结构，禁止新增字段、禁止缺字段
- `instrument_type=etf` 时，收益类 metrics 字段必须为 null，并在 notes 说明“ETF 缺 Price”

### 6.2 反转模式 pattern_id 枚举（V1 固定）

基础反转：

- `D:-1->0`, `D:-1->1`
- `W:-1->0`, `W:-1->1`
- `M:-1->0`, `M:-1->1`

同日共振（同日多周期同时从 -1 转为 0/1）：

- `DW:-1->(0|1)`
- `DM:-1->(0|1)`
- `WM:-1->(0|1)`
- `DWM:-1->(0|1)`

### 6.3 Top10 字段（简版 + 详版）

为了同时满足 Telegram 简版展示与完整审计，Top10 每项包含：

- `entry_logic_brief`：用于 TG（1-2 句）
- `entry_logic_detail`：用于报告与审计（不做长度限制，建议段落化）

## 7. 落库与数据模型调整

### 7.1 新增表：llm_reports（报告级别审计）

目的：保存每次“provider×instrument_type”的整份报告原文与 JSON，便于回放与审计。

建议字段：

- `report_id`（自增）
- `run_id`
- `snapshot_date`
- `instrument_type`（stock/etf）
- `provider`
- `raw_text`
- `json_text`
- `is_valid`
- `quality_flags`
- `created_at`

### 7.2 复用表：llm_outputs（Top10 展开为票源）

现有系统已基于 `llm_outputs` 做投票与决策。V1 复用该表：

- 将每次报告中的 `top10` 展开写入 `llm_outputs`
- 每个 symbol 写一条输出：
  - `symbol`：top10 的 symbol
  - `json_text`：保存统一结构（见下）
  - `raw_text`：保存该 symbol 的简版/详版逻辑拼接（或原 JSON 片段）
  - `is_valid=true`
  - `quality_flags`：例如 `["panel_top10"]`

### 7.3 llm_outputs.json_text 的统一结构（用于投票与通知）

为了复用现有投票器与通知，建议每条 `llm_outputs.json_text` 使用以下字段：

- `symbol`
- `recommendation`: 固定 `"BUY"`（Top10 入选即视为 BUY 票）
- `confidence`: 来自报告 `top10[].confidence`
- `rationale`: 数组，至少包含 `entry_logic_brief`
- `detail`: string，保存 `entry_logic_detail`（用于后续写报告或审计）

若现有校验器不接受 `detail` 字段，则在实现阶段同步扩展校验器/DTO。

## 8. 详细报告落盘（Markdown）

输出目录（用户选择 A）：

- `${DATA_ROOT}/reports/{snapshot_date}/{run_id}/`

建议文件：

- `{instrument_type}_{provider}.md`：每个 provider 的完整报告（渲染 JSON）
- `summary.md`：汇总所有 provider 的 Top10，并给出投票结果榜单（股票/ETF 分开）

Telegram 中只展示 `entry_logic_brief`，完整内容在 md 中可查。

## 9. Orchestrator 流程调整

### 9.1 daily_run（核心差异）

- 仍沿用：run 建立 → 文件契约校验 → instrument_snapshots 入库
- L3 调整为：
  - 构建 stock 面板 JSON（压缩）
  - 构建 etf 面板 JSON（压缩）
  - 对每个 provider：
    - 调用一次 stock 面板 → 写 llm_reports + 展开 top10 写 llm_outputs
    - 调用一次 etf 面板 → 写 llm_reports + 展开 top10 写 llm_outputs
- L4 投票：
  - 从 llm_outputs 生成 votes（每条即 1 票 BUY）
  - 使用现有 tier 规则（并保留 min_effective_providers<=2 的联调模式）

## 10. 测试策略（V1）

- 单测：panel builder（从多子表提取日期范围、symbol_count、字段缺失率）
- 单测：JSON 校验（etf 收益字段必须为 null；pattern_id 必须在枚举内；top10 数量限制）
- 集成测：mock provider 返回固定 JSON，验证：
  - llm_reports 插入
  - llm_outputs 展开写入数量=providers*top10
  - decisions/paper_trades 产生
  - Markdown 报告文件生成到 data_root/reports

## 11. 风险与后续演进

- 输入体积仍可能过大：通过 PANEL_MAX_SHEETS 与（必要时）symbol 抽样/分桶进一步压缩
- ETF 无收益指标：后续引入行情补全与收益计算后，可切换 ETF metrics 由 null → 实值
- 投票权重化：后续可将 `confidence` 与 provider_scores 结合做加权

