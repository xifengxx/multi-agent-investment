# OpenAI-Compatible Providers：File-Prompt + Fallback 设计补充

## 背景

当前 L3 混合链路已支持：

- provider 支持文件输入时：优先 `invoke_with_files(...)`
- 失败时：自动回退到 `invoke(summary_prompt)`

但在 OpenAI-compatible 网关场景（qwen/glm/kimi/minimax 等），“文件优先”若仍使用全量 `panel_json` 作为 prompt，会带来两类问题：

- 输入体积巨大：即使文件上传失败也会先发送超大 prompt，导致 400/413
- 冗余与偏差：既传文件又传全量 JSON，模型可能忽略文件或被 JSON 误导

## 目标

- 为 `invoke_with_files` 路径提供“精简 file-prompt”，不再内嵌 `panel_json`
- 将 qwen/glm/kimi/minimax 的策略明确为：**先试附件，再回退 summary 文本**
- 继续保持输出 schema 校验与审计落库一致（`llm_reports/llm_outputs/runtime_errors`）

## 设计

### 1) 新增 file-prompt 构建函数

在 `PanelPromptBuilder` 新增：

- `build_file_prompt(snapshot_date, instrument_type, provider_name)`：
  - 仅包含任务描述、context、输出 schema 约束（schema_version、Top10=10、signal_strength 枚举、pattern_id 枚举、ETF metrics=null 等）
  - 明确告知“请基于附件中的 xlsx（按日期子表面板）进行整体分析”

### 2) Orchestrator 调用分流

对 `supports_file_input=True` 的 provider：

- `invoke_with_files(prompt=file_prompt, file_paths=[stock_or_etf.xlsx])`
- 若失败：记录 `runtime_errors[provider.instrument.file]=...`
- 回退 `invoke(summary_prompt)`，若失败记录 `runtime_errors[provider.instrument.text]=...`

对 `supports_file_input=False` 的 provider：

- 直接 `invoke(prompt_builder.build(...))`（保留原语义）

### 3) 验证点

- 单测：file-first provider 被调用时，传入的 prompt 不包含 `input_panel_json:`
- 集成/E2E：file-first 成功与 file->summary 回退两条链路均可产出 `llm_outputs/decisions`
- 非 dry_run：在 qwen/glm/kimi/minimax 场景下，至少能通过 summary 回退拿到有效输出（避免 400/413）

