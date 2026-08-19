# GitHub Actions 交易意图输出

本段是上述完整扫描的附加输出契约，不改变 `forecast-edge` 的研究、筛选、组合分配或禁止真实下单规则。

- 已启用预测市场：`{{ENABLED_PREDICTION_MARKETS}}`
- 标准化账户快照清单：`{{ACCOUNT_SNAPSHOTS_MANIFEST}}`
- Adapter 注册表：`{{MARKET_ADAPTER_REGISTRY}}`
- 交易意图 JSONL：`{{TRADE_INTENTS_PATH}}`
- 单项 JSON Schema：`{{TRADE_INTENT_SCHEMA_PATH}}`

读取每个已启用 adapter 的标准化可信账户快照。完成并验证各 adapter 的私有 schema `3.0` 报告后，把所有需要实际执行的增量操作合并为一个 UTF-8 JSONL：每行一个独立 JSON object，不得使用 Markdown 代码围栏，不得输出 Schema 之外的字段。

只转换最终报告中的可执行操作：`BUY` 和 `ADD` 映射为 `side=buy`，`SELL` 和 `TRIM` 映射为 `side=sell`；`MAINTAIN` 不输出。没有可执行操作时也必须创建空的 JSONL 文件，不得为了产生输出而降低策略门槛或虚构交易。

- `schema_version` 固定为 `1.0`。
- `venue` 必须等于产生该操作的报告中的 `report.run.account.adapter`，且必须属于已启用列表。
- `multi_market_identifier` 必须逐字等于对应报告 analysis 的 `market.multi_market_identifier`，它是 adapter 返回的具体二元市场标识，不得使用父 event 标识。
- `instrument_identifier` 必须逐字等于同一个 analysis 的 `market.instrument_identifier`，它是该 adapter 返回的所选 outcome 可直接交易的 instrument 标识。
- `outcome` 只能是小写 `yes` 或 `no`。
- `reference_price` 是研究时订单簿给出的该 outcome 预估平均成交价，按每份合约 `0 < price <= 1` 表示，不包含费用。
- `reference_price` 必须等于私有报告对应 action 的 `execution.vwap`。
- `notional` 是 `reference_price × quantity` 的预估名义金额，不包含费用，并与私有报告对应 action 的 `execution.gross_cash` 一致。
- `quantity` 是该 outcome 的合约数量；venue 原生单位语义由对应 adapter 文档解释。
- `currency` 必须使用对应 adapter 标准化账户快照中的真实 `portfolio.currency`，不得假设固定抵押资产或跨 venue 复用币种。
- `reference_price`、`notional` 和 `quantity` 必须使用普通十进制字符串，禁止指数记数法。

对每个 `sell` 项，必须先在同一 venue 的可信账户快照中用 `market_identifier`、`instrument_identifier` 和 outcome 三者共同匹配真实持仓，并确保建议 `quantity` 不超过快照持仓。第一阶段仍然不得登录、签名或提交订单。

写完后自行逐行检查 JSON 可解析、字段完备、标识属于同一市场和 outcome、金额关系成立且没有重复或互相冲突的指令。最终回复只需说明文件已写入以及 item 数量；交易意图以文件内容为唯一机器输入。
