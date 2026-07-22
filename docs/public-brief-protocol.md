# 公共操作简报协议

公共简报是私有 `forecast-edge` 报告经过主动降敏后的发布层，不是私有报告的镜像。规范文件是 `schemas/public-brief.schema.json`，语义校验器是 `scripts/validate_public_brief.py`。

## 边界

公共简报只允许 `BUY`、`ADD`、`SELL`，不发布 `HOLD`。它不得包含账户地址、总资产、现金、持仓价值、持仓数量、建议金额、私有报告路径、飞书目标或任何凭据。Schema 对每一级对象都禁用额外字段；校验器还会拒绝钱包地址、电子邮箱和本机用户目录。

## 字段语义

- `fair_price`：Agent 对当前 `outcome` 最终结算为真的概率，范围 0–1。
- `current_price`：本轮产生动作时，该 `outcome` 对应的可执行价格；买入/加仓使用 ask，卖出使用 bid，范围 0–1。
- `price_gap`：严格等于 `fair_price - current_price`。正值表示该方向低估，负值表示该方向高估。
- `link_verified_at`：本轮实际打开并核对 Polymarket 链接的时间。链接必须为对应的 `/event/` 页面，不接受个人主页或搜索页。
- `confidence`：只取 `HIGH`、`MEDIUM`、`LOW`。
- `source_content`：来源中与判断直接相关的核心内容摘要，不复制无关全文。
- `probability_impact`：说明该证据影响哪个条件或因果因素，以及提高或降低了哪一方向的最终概率。
- `evidence[].id`：本条证据在同一操作项内的稳定 ID，供概率树引用。
- `probability_tree.root`：最终采用的可重新计算概率树；根概率必须等于 `fair_price`。

## 概率树

协议 `1.1` 支持四类递归节点：

- `estimate`：直接点估计。
- `partition`：互斥且完备的情景分解，分支权重之和必须为 1。
- `bayes_update`：从完整 `prior` 子树开始，按顺序应用似然。
- `complement`：子节点概率的补集。

每个节点都包含稳定 `id`、标题、说明、概率和 `evidence_refs`。情景分支还要用 `evidence_refs` 说明其权重依据。嵌套的 `child`、`prior`、`branches` 就是节点关系，不再维护一份容易失真的平铺关系表。每条证据必须至少被一个节点、分支或更新步骤引用；所有节点及更新步骤 ID 在同一棵树内必须唯一。

完整示例见 `tests/fixtures/public-brief.json`。网页在“支撑信息”的“概率关系”页签中递归绘制概率树；节点内的证据编号可以切回证据列表并定位对应来源。

Agent 生成实际数据后必须运行：

```bash
python3 scripts/validate_public_brief.py path/to/brief.json
```
