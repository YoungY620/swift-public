# GitHub Actions Agent 工作流设计记录

状态：第一阶段已实现；真实交易工作流尚未实现或启用。

本文件记录基于 GitHub Actions 运行 Forecast Edge Agent 的已确定需求、建议设计和待确认事项。第一阶段只实现跨市场研究、生成交易意图、校验、归档和通知；真实交易由各预测市场独立的第二阶段工作流完成。

## 安全边界

- 第一工作流只读取已启用预测市场的公开账户、市场、订单簿和持仓数据，不保存或使用交易凭据，不提交订单。
- 各市场的第二阶段工作流才允许接触该市场自己的交易凭据。它必须重新校验 artifact、市场、持仓、价格、金额、数量和成交后状态，不能把第一工作流的结果直接当成可信订单。
- Kimi Code API Key、交易凭据和通知凭据只通过 GitHub Actions Secrets 注入；fork 不继承任何 Repository Secrets。
- 不使用 VPN 绕过交易平台的地域、条款或法律限制。真实交易只能运行在操作者依法获准使用 Polymarket 的网络和司法辖区中。

## 总体架构：统一研究，分市场执行

### 阶段一：统一研究工作流

第一阶段使用一个统一 workflow。它可以根据不同市场的参考文档和公开 API 获取数据，但对下游只发布统一的、经过校验的交易意图协议。

- 用 `ENABLED_PREDICTION_MARKETS` 配置本轮允许研究的平台，例如 `polymarket` 或 `polymarket,kalshi`。
- 每个平台在仓库内提供自己的 API、标识、价格单位、订单簿、持仓和结算规则文档；必要时提供只读 adapter/helper。
- Agent 只能输出已启用且已登记的平台；未知平台必须使 Schema 校验失败。
- 组合筛选和资金分配发生在统一研究阶段，但每个 venue 使用真实、独立的账户快照和可用资金，不能假设资金可以在平台之间即时转移。
- 统一 artifact 可以同时包含多个 venue 的 JSONL item。

当前 `forecast-edge` Skill 明确写有 “Use Polymarket only in v1”，现有数据工具也只有 `polymarket_data.py`。因此第一版仍只启用 Polymarket；支持 Kalshi 时必须同时补充 venue 文档、只读数据能力、Skill 约束和测试，不能仅在 Schema 中提前放行一个尚未实现的 venue。

### 阶段二：按市场拆分的执行工作流

每个预测市场拥有独立 workflow，例如：

- `execute-polymarket.yml` 下载统一 artifact，只保留 `venue == "polymarket"` 的 item，使用 Polymarket Environment Secrets 和执行逻辑。
- `execute-kalshi.yml` 下载同一个统一 artifact，只保留 `venue == "kalshi"` 的 item，使用 Kalshi Environment Secrets 和执行逻辑。

多个执行 workflow 可以监听同一个研究 workflow 的 `workflow_run: completed`。它们都使用同一个上游 `workflow_run.id` 下载该次运行的精确 artifact，然后各自过滤、重新校验和执行；不存在“取最新文件”的竞态。

每个执行器必须：

1. 先校验整个 artifact、manifest、上游 run ID、commit SHA 和 digest；
2. 再按固定 venue 过滤，禁止通过输入参数临时切换 venue；
3. 对过滤后的每项执行 venue 专属的市场、账户、持仓和价格复核；
4. 没有本 venue 指令时安全跳过；
5. 使用独立的 GitHub Environment、Secrets、并发锁和幂等记录。

独立执行 workflow 不提供跨 venue 的原子成交保证。如果未来策略要求 Polymarket 与 Kalshi 两条腿必须同时成交，需要新增跨市场执行协调器和 leg-risk 处理，不能依赖两个并行 workflow 自然实现原子套利。

## 第一工作流：定时研究与交易意图

### 已确定需求

1. 工作流在 GitHub 托管的 Linux runner 上运行，使用 Kimi Code CLI 的非交互模式。
2. Kimi Code API Key 来自 GitHub Actions Secret；公开的 Polymarket 账户标识来自全局配置。
3. 现有定时扫描 prompt 与新增固定 prompt 统一放进 `automation/prompts/`，并修改所有旧路径。
4. 工作流用多个可观察的 step 完成安装、配置检查、账户快照、Agent 运行、Schema 校验、卖单持仓校验、重试、artifact 上传和 Issue 通知；不由一个总控脚本隐藏全部流程。
5. Agent 生成可同时包含多个预测市场的 JSONL 交易意图。每一行必须是一个 JSON object，禁止 Schema 未声明的字段。
6. 初次结果不合格时，使用仓库内固定的修复 prompt 再次运行 Kimi Code。重试次数达到约定上限后 workflow 失败。
7. 只有通过全部校验的 JSONL 才能上传为下游 artifact 或发布通知。
8. 同一仓库的定时 iteration 不允许重叠运行。

### 建议的分步结构

1. Checkout 固定的默认分支提交。
2. 校验全局变量和必需 Secret，但不输出 Secret 值。
3. 判断本次 `schedule` 事件是否应该开始一次 iteration；`workflow_dispatch` 手动运行不受定时门控限制。
4. 安装固定版本的 Kimi Code CLI，并缓存下载依赖。
5. 获取一次不可变的公开账户快照，供 Agent 和后续持仓校验共用。
6. 拼装现有扫描 prompt 与交易意图输出 prompt，启动新的非交互 Kimi Code session。
7. 校验 JSONL 可解析、逐项符合 JSON Schema，并验证跨字段数值关系。零字节文件明确表示本轮没有可执行操作，是合法结果。
8. 对所有 `sell` 项，按 item 的 venue 使用对应账户快照，确认对应 outcome 的真实可卖持仓不少于建议数量。
9. 校验失败时保存机器可读的错误信息，使用固定修复 prompt 让 Kimi Code 重写同一个输出文件，然后重复步骤 7 和 8。
10. 上传验证通过的 JSONL、校验摘要和 provenance manifest。账户快照和 Kimi session 不进入 artifact；未来执行器必须重新获取实时账户状态。private repository 另存本轮私有报告 artifact，并在下一轮恢复最近两份；public repository 不上传私有报告。
11. 用固定 Markdown 模板生成通知，在本仓库创建 Issue 或评论。

### Kimi Code 已确认能力

- 官方 CLI 可在 Linux 上安装，并支持固定 npm 版本。
- `kimi -p "..."` 是非交互模式；该模式默认使用自动权限策略，不需要追加 `--auto` 或 `--yolo`，而且这些参数不能与 `-p` 同用。
- CI 可以通过 `KIMI_MODEL_NAME` 和 `KIMI_MODEL_API_KEY` 临时定义模型，避免把 API Key 写入仓库。
- `--output-format stream-json` 输出的是 Kimi 的 Assistant/tool 运行事件流，不是业务交易 JSONL。因此交易 JSONL 必须由 prompt 指定 Agent 写到独立文件，不能直接把 CLI stdout 当成交易指令。
- GitHub-hosted runner 是临时环境。可以缓存 npm 下载或固定版本二进制，但每个 iteration 仍需要恢复/安装；若要完全预装，需要自托管 runner 或自定义 runner image。

## JSONL 交易意图契约

### 统一 envelope

建议所有 venue 共用以下字段：

- `schema_version`：单条 item 的协议版本，使拆分后的 JSONL 仍可独立解释。
- `venue`：稳定的小写平台标识；v1 仅允许 `polymarket`，增加 Kalshi 实现后才加入 `kalshi`。
- `multi_market_identifier`：venue 原生的具体 market 标识，不能使用 event ID。
- `instrument_identifier`：该 outcome 可直接交易的合约/资产标识。
- `outcome`：`yes` 或 `no`。
- `side`：`buy` 或 `sell`。
- `reference_price`：统一为每份 outcome 合约 `0 < price <= 1` 的研究时参考价格。
- `notional`：以 item 声明的结算/抵押资产计价的预估名义金额。
- `quantity`：选择 outcome 的合约数量；Polymarket shares 和 Kalshi contracts 在协议层统一称为 quantity。
- `currency`：`notional` 的计价资产/货币，避免跨 venue 时把 USD、USDC 或其他抵押资产静默混用。

Schema 应使用 `additionalProperties: false`，并校验价格范围、正数金额、正数数量以及 `notional ≈ reference_price × quantity`。最终执行工作流仍需用新订单簿重新计算，不能依赖研究时的参考价格。

`venue` 和 `multi_market_identifier` 共同组成复合主键。不同平台的原生 ID 可能碰撞，因此 `multi_market_identifier` 单独不承诺跨平台全局唯一；全局 key 定义为：

```text
(venue, multi_market_identifier)
```

如果要求一个可打印的全局 ID，应由可信 workflow 派生 `venue + ":" + multi_market_identifier`，而不是让 Agent 自由生成第二份可能不一致的数据。

### market 与 instrument 的区别

一个 market ID 不一定足以执行订单，因此统一协议必须区分：

- `multi_market_identifier`：具体二元市场本身的 ID；
- `instrument_identifier`：所选 outcome 的可交易 instrument。

例如：

- Polymarket 同时存在 Gamma market `id`、`conditionId`、event ID 和每个 outcome 的 CLOB token/asset ID。执行 CLOB 订单最终需要 outcome 的 token ID。
- Kalshi 区分 event ticker 与 market ticker。具体 market ticker 才是可交易市场标识；其执行 API 再结合 `outcome` 解释订单方向。

v1 采用以下 venue 映射：

1. Polymarket 的 `multi_market_identifier` 使用 `conditionId`；
2. Polymarket 的 `instrument_identifier` 固定为所选 outcome 的 CLOB token ID；
3. Kalshi 的映射在实现 Kalshi adapter 时再确定；
4. 人类可核查的 market URL 不进入交易协议，通知层可以独立补充。URL 对下单不是必需字段。

### 待确认：价格、金额和份额

“价格金额数量”需要拆成无歧义字段。建议使用：

- `reference_price`：研究时估计的平均成交价，范围 `(0, 1]`；
- `notional`：`currency` 计价的总金额；
- `quantity`：统一 outcome 合约数量，执行器再映射为 venue 的 shares/contracts。

第一阶段使用最多 18 位小数的普通十进制字符串，禁止指数记数法；`notional` 与 `reference_price × quantity` 的绝对误差最多为 `0.01`。第二工作流可接受的实时滑点阈值仍待确认。

## 定时间隔 N

### GitHub 限制

`on.schedule.cron` 是静态 YAML，不能引用 `${{ vars.* }}`。此外，Cron 的 `*/7` 小时会在每天午夜重置，并不能保证跨天严格每 7 小时运行。

第一阶段采用任意整数 N：Workflow 每小时第 `23` 分钟触发一次，首个 gate step 校验 `FORECAST_EDGE_INTERVAL_HOURS` 是不小于 6 的整数，并按 UTC epoch hour 与可配置 anchor 决定是否开始 iteration。未命中的 run 很快结束，但 Actions 页面会出现按小时的跳过记录。研究 job 使用 `concurrency` 防止长时间 Agent iteration 重叠；`workflow_dispatch` 手动运行绕过时间 gate。

## 重试语义

第一阶段采用初次运行加 3 次修复，共最多 4 次 Kimi 调用。每次修复启动新 session，读取已归档私有报告、原 JSONL 与校验错误；若有效私有报告已经存在，修复不得重复研究或归档第二份报告。

## Issue 通知

第一阶段在 private repository 中默认每个成功 iteration 创建一个新 Issue。将 Repository Variable `CREATE_TRADE_INTENT_ISSUE` 设为 `false` 可以关闭 Issue，仅保留 artifact；public repository 不创建包含交易数量的 Issue。

如果仓库是 public，Issue 会公开交易方向、价格、金额、份额和账户线索，可能造成隐私、策略泄露或抢跑风险。默认建议只在 private repository 创建包含数量的 Issue；public repository 应使用主动降敏的公共简报格式。

GitHub artifact 不是应用层加密存储。private report artifact 只依赖 private repository 的访问控制，因此不得在 public repository 上传；统一交易意图 artifact 本身也包含方向和数量。public repository 默认拒绝运行，只有明确设置 `ALLOW_PUBLIC_TRADE_INTENTS=true` 接受披露后才放行，并且仍不会创建包含数量的 Issue 或上传私有报告。

## 第二阶段工作流：真实交易（延期实现）

### 精确绑定第一工作流的一次运行

每个 venue workflow 都可以使用 `workflow_run`：

- 只监听第一工作流的 `completed` 事件；
- 要求 `conclusion == success`；
- 如果只接受定时 iteration，要求上游事件是 `schedule`；
- 使用 `github.event.workflow_run.id` 下载该次运行上传的精确 artifact，而不是“最新 artifact”；
- 校验 artifact 内 manifest 的上游 run ID、commit SHA、生成时间和文件 digest。

这样每个 venue 的执行工作流都锚定到唯一的研究 run，不会误取其他定时或手动运行的文件。多个 venue workflow 可以锚定同一个研究 run，各自只处理自己的 item。

### 待确认和延期项

- 标准 GitHub-hosted runner 不能保证位于爱尔兰或印度，也不能把某个共享出口 IP 当作稳定交易地域。真实交易应使用位于合规地区、由操作者控制的 self-hosted runner，并用专用 label/runner group 锁定。
- 交易 Secret 的具体清单、钱包类型、签名方式和最小权限。
- 最大允许价格偏差、金额偏差、最小订单、余额保护和单次/单日风险上限。
- buy 与 sell 分别以 notional 还是 quantity 为权威输入，以及二者不一致时的舍入规则。
- 部分成交后的取消、补单、退出和人工介入策略。
- 是否在真实交易 environment 上启用 GitHub required reviewers，形成最终人工批准门。
- 交易后的持仓核对、审计日志、告警和幂等键设计。

## 后续需要确认的决策

1. 第二阶段执行器的价格偏差、金额偏差和风险阈值。
2. Kalshi 的 market/instrument 映射和账户数据契约。
3. Issue 在公开仓库中的降敏策略。
