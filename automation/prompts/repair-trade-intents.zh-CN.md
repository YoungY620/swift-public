# 修复研究报告与交易意图 JSONL

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

如果错误来自报告研究质量，必须修复对应报告本身，再重新渲染其 `report.md` 和交易意图。不得为了凑 Schema，在多个候选间复制通用套话。对每个 `candidates` 项逐一回读它自己的 market、probability tree 和全部 evidence：先重写市场专属的 `model.conclusion`、`signal.rationale` 研究部分以及 evidence summary/conclusion，再用该项真实的 `executable_price`、`raw_edge`、`net_edge`、`minimum_raw_edge`、`entry_eligible` 和 allocation 重写 `opportunity.reason`。不得用“基于最新订单簿重新计算”“综合公开信息重新评估”“未触发硬门槛，维持现状”替代研究结论。

同时用历史集合重新核对候选的 `event_identifier`、`multi_market_identifier` 和 `market.id`。真正的新事件标记 `FIRST_RESEARCH`；既有事件的新子市场标记 `EXISTING_EVENT_NEW_MARKET`；具体市场已研究过时不得标记为新增候选。关闭/已结算/停止接单/无 active book 的机械重复只有在结构化 `opportunity.market_status` 和逐市场状态证据齐全时才能保留。研究质量错误是可修复校验错误；不要把它改写或升级成资金安全、订单保护、残留挂单或秘密泄露熔断。
