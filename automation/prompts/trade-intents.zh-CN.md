# GitHub Actions 交易意图输出

本段是上述完整扫描的附加输出契约，不改变 `forecast-edge` 的研究、筛选、组合分配或禁止真实下单规则。

- 已启用预测市场：`{{ENABLED_PREDICTION_MARKETS}}`
- 本轮可信账户快照：`{{ACCOUNT_SNAPSHOT_PATH}}`
- 交易意图 JSONL：`{{TRADE_INTENTS_PATH}}`
- 单项 JSON Schema：`{{TRADE_INTENT_SCHEMA_PATH}}`

读取并使用本轮可信账户快照。完成并验证私有 schema `3.0` 报告后，把其中需要实际执行的增量操作另存为 UTF-8 JSONL：每行一个独立 JSON object，不得使用 Markdown 代码围栏，不得输出 Schema 之外的字段。

只转换最终报告中的可执行操作：`BUY` 和 `ADD` 映射为 `side=buy`，`SELL` 和 `TRIM` 映射为 `side=sell`；`MAINTAIN` 不输出。没有可执行操作时也必须创建空的 JSONL 文件，不得为了产生输出而降低策略门槛或虚构交易。

当前 v1 只允许 `venue=polymarket`：

- `schema_version` 固定为 `1.0`。
- `multi_market_identifier` 必须是具体二元市场的 Polymarket `conditionId`，不得使用 event ID、event slug 或 Gamma event ID。
- `instrument_identifier` 必须是所选 `outcome` 对应的 CLOB token ID。
- `outcome` 只能是小写 `yes` 或 `no`。
- `reference_price` 是研究时订单簿给出的该 outcome 预估平均成交价，按每份合约 `0 < price <= 1` 表示，不包含费用。
- `reference_price` 必须等于私有报告对应 action 的 `execution.vwap`。
- `notional` 是 `reference_price × quantity` 的预估名义金额，不包含费用，并与私有报告对应 action 的 `execution.gross_cash` 一致。
- `quantity` 是该 outcome 的份额数量。
- `currency` 必须使用账户快照动态发现的真实 active collateral asset，不得假设固定抵押资产。
- `reference_price`、`notional` 和 `quantity` 必须使用普通十进制字符串，禁止指数记数法。

对每个 `sell` 项，必须先在可信账户快照中用 `conditionId`、CLOB token ID 和 outcome 三者共同匹配真实持仓，并确保建议 `quantity` 不超过快照持仓。第一阶段仍然不得登录、签名或提交订单。

写完后自行逐行检查 JSON 可解析、字段完备、标识属于同一市场和 outcome、金额关系成立且没有重复或互相冲突的指令。最终回复只需说明文件已写入以及 item 数量；交易意图以文件内容为唯一机器输入。
