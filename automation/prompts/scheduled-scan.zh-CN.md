# Forecast Edge 定时扫描

执行 `forecast-edge` 完整扫描。

- 已启用的预测市场 adapter：`{{ENABLED_PREDICTION_MARKETS}}`
- 标准化账户快照清单：`{{ACCOUNT_SNAPSHOTS_MANIFEST}}`
- Adapter 注册表：`{{MARKET_ADAPTER_REGISTRY}}`
- Adapter 契约说明：`{{MARKET_ADAPTER_GUIDE}}`
- 私有报告根目录：`{{OUTPUT_ROOT}}`
- 仓库目录：`{{REPOSITORY_ROOT}}`

先阅读 adapter 契约，随后对清单中的每个已启用 adapter 独立执行 Skill 的完整扫描。研究方法、概率模型和报告质量要求与 venue 无关；账户、币种、持仓、市场标识、instrument、订单簿和资金分配不得跨 venue 混用。读取真实的当前可用余额与全部持仓；不得使用假设本金，不得提交真实订单。

每个 adapter 都必须重新研究它的全部当前持仓，并深研至少 10 个滚动历史中未研究过的合格事件，除非该 adapter 完整筛选后实际不足 10 个。历史报告只作为 Skill 允许的滚动比较输入；不得直接沿用历史概率，也不得把历史报告复制进本轮报告。本轮必须为每个已启用 adapter 新增且只新增一个报告；`report.run.account.adapter` 必须等于对应 adapter。

调用仓库脚本时必须采用 Skill 中的精确命令格式，未列出的调用先运行该脚本的 `--help`，不得猜测参数。研究历史的固定调用是 `python3 scripts/previously_researched.py REPORTS_DIR --adapter ADAPTER --pretty`，其中 `REPORTS_DIR` 是位置参数；只解析命令 stdout 或明确的 `.json`/`.jsonl` 文件，绝不能把 `.py` 源文件当 JSON。对 Polymarket 当前持仓，先用持仓的 `market_slug` 查询 market metadata；仅当 `closed=false` 且 `accepting_orders=true` 时才用 `instrument_identifier` 查询 book。关闭或停止接单的市场没有 active book 时记录为结构化 `MAINTAIN` 机械原因，不得把 outcome token 传给 market，也不得把 condition id 或 slug 传给 book。

完成后简要说明私人报告位置、公开输出是否生成，以及是否存在需执行的买卖动作。不要因为可选的公开输出或通知未配置而跳过私人报告。
