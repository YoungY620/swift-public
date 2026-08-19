# 修复交易意图 JSONL

上一次 Kimi Code 运行没有产生通过校验的交易意图文件。不要提交真实订单。

- 原始完整任务 prompt：`{{ORIGINAL_PROMPT_PATH}}`
- 待修复 JSONL：`{{TRADE_INTENTS_PATH}}`
- 单项 JSON Schema：`{{TRADE_INTENT_SCHEMA_PATH}}`
- 标准化账户快照清单：`{{ACCOUNT_SNAPSHOTS_MANIFEST}}`
- 已启用 adapter 数量：`{{EXPECTED_REPORT_COUNT}}`
- 最近一次校验错误：`{{VALIDATION_ERRORS_PATH}}`
- 本轮开始前已经恢复的历史报告清单：`{{BASELINE_REPORTS_PATH}}`

先读取最近一次校验错误、Schema、标准化账户快照清单、待修复文件、历史报告清单和原始完整任务 prompt。“有效的本轮私有报告”只指当前 workflow run 新增、且路径不在历史报告清单中的报告；清单里的任何报告都只是滚动历史，绝不算本轮报告。每个已启用 adapter 必须恰好有一个本轮报告。已有且有效的 adapter 报告不得重复研究或再次归档；缺少哪个 adapter 的报告，就只补完该 adapter 的完整扫描；存在但未通过校验的报告必须先修好。不得把历史报告误当成本轮报告，也不得修改或重新归档历史报告。

必须修复全部错误并覆盖待修复 JSONL。每行只能包含一个符合 Schema 的 JSON object；没有可执行操作时创建空文件。不得为了通过校验而删除真实的可执行操作、编造标识或持仓、降低策略门槛，也不得登录、签名或提交订单。
