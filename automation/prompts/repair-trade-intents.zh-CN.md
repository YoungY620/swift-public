# 修复交易意图 JSONL

上一次 Kimi Code 运行没有产生通过校验的交易意图文件。不要提交真实订单。

- 原始完整任务 prompt：`{{ORIGINAL_PROMPT_PATH}}`
- 待修复 JSONL：`{{TRADE_INTENTS_PATH}}`
- 单项 JSON Schema：`{{TRADE_INTENT_SCHEMA_PATH}}`
- 可信账户快照：`{{ACCOUNT_SNAPSHOT_PATH}}`
- 最近一次校验错误：`{{VALIDATION_ERRORS_PATH}}`

先读取最近一次校验错误、Schema、可信账户快照、待修复文件和原始完整任务 prompt。若有效私有报告已经存在，不得重复研究或再归档一份报告，只根据该报告创建或修复交易意图；若私有报告缺失或未通过校验，才按原始完整任务 prompt 补完扫描、归档和输出。

必须修复全部错误并覆盖待修复 JSONL。每行只能包含一个符合 Schema 的 JSON object；没有可执行操作时创建空文件。不得为了通过校验而删除真实的可执行操作、编造标识或持仓、降低策略门槛，也不得登录、签名或提交订单。
