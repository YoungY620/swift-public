# GitHub Actions Agent 工作流设计记录

状态：第一阶段已实现；真实交易工作流尚未实现或启用。

本文件记录基于 GitHub Actions 运行 Forecast Edge Agent 的已确定需求、建议设计和待确认事项。第一阶段只实现跨市场研究、生成交易意图、校验、归档和通知；真实交易由各预测市场独立的第二阶段工作流完成。

## 安全边界

- 第一工作流只读取已启用预测市场的公开账户、市场、订单簿和持仓数据，不保存或使用交易凭据，不提交订单。
- 各市场的第二阶段工作流才允许接触该市场自己的交易凭据。它必须重新校验 artifact 中唯一的 JSONL 文件、市场、持仓、价格、金额、数量和成交后状态，不能把第一工作流的结果直接当成可信订单。
- Kimi Code API Key、交易凭据和通知凭据只通过 GitHub Actions Secrets 注入；fork 不继承任何 Repository Secrets。
- 不使用 VPN 绕过交易平台的地域、条款或法律限制。真实交易只能运行在操作者依法获准使用 Polymarket 的网络和司法辖区中。

## 总体架构：统一研究，分市场执行

### 阶段一：统一研究工作流

第一阶段使用一个统一 workflow。它可以根据不同市场的参考文档和公开 API 获取数据，但对下游只发布统一的、经过校验的交易意图协议。

- v1 在 Workflow 中固定 `ENABLED_PREDICTION_MARKETS=polymarket`；实现 Kalshi adapter 和契约后再由代码变更扩展，不把尚未实现的平台作为部署开关暴露。
- 每个平台在仓库内提供自己的 API、标识、价格单位、订单簿、持仓和结算规则文档；必要时提供只读 adapter/helper。
- Agent 只能输出已启用且已登记的平台；未知平台必须使 Schema 校验失败。
- 组合筛选和资金分配发生在统一研究阶段，但每个 venue 使用真实、独立的账户快照和可用资金，不能假设资金可以在平台之间即时转移。
- 统一 artifact 仅包含一个 `trade-intents.jsonl` 文件，该文件可以同时包含多个 venue 的 item；研究报告、Manifest、校验摘要和其他中间结果禁止放入 artifact。

当前 `forecast-edge` Skill 明确写有 “Use Polymarket only in v1”，现有数据工具也只有 `polymarket_data.py`。因此第一版仍只启用 Polymarket；支持 Kalshi 时必须同时补充 venue 文档、只读数据能力、Skill 约束和测试，不能仅在 Schema 中提前放行一个尚未实现的 venue。

### 阶段二：按市场拆分的执行工作流

每个预测市场拥有独立 workflow，例如：

- `execute-polymarket.yml` 下载统一 artifact，只保留 `venue == "polymarket"` 的 item，使用 Polymarket Environment Secrets 和执行逻辑。
- `execute-kalshi.yml` 下载同一个统一 artifact，只保留 `venue == "kalshi"` 的 item，使用 Kalshi Environment Secrets 和执行逻辑。

多个执行 workflow 可以监听同一个研究 workflow 的 `workflow_run: completed`。它们都使用同一个上游 `workflow_run.id` 下载该次运行的精确 artifact，然后各自过滤、重新校验和执行；不存在“取最新文件”的竞态。

每个执行器必须：

1. 通过上游 `workflow_run.id` 下载精确 artifact，并重新校验其中唯一的 `trade-intents.jsonl`；如需核对 provenance 和 digest，再读取同一 run 对应的研究 Release Manifest；
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
4. 工作流用多个可观察的 step 完成安装、配置检查、滚动历史 Release 恢复、账户快照、Agent 运行、Schema 校验、卖单持仓校验、重试、Release 发布、artifact 上传和 Issue 通知；不由一个总控脚本隐藏全部流程。
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
10. 按配置的滚动窗口恢复最近若干个研究 Release，供 Agent 比较历史证据和近期机会；本轮报告仍只描述本轮重新研究的持仓和候选市场。
11. artifact 仅上传验证通过的 `trade-intents.jsonl`。校验摘要和 provenance manifest 进入研究 Release；账户快照和 Kimi session 不进入 artifact，未来执行器必须重新获取实时账户状态。
12. 用固定 Markdown 模板生成通知，在本仓库创建 Issue，并把同一份 `issue.md` 原文作为纯文本发送到飞书私聊。

### Kimi Code 已确认能力

- 官方 CLI 可在 Linux 上安装，并支持固定 npm 版本。
- `kimi -p "..."` 是非交互模式；该模式默认使用自动权限策略，不需要追加 `--auto` 或 `--yolo`，而且这些参数不能与 `-p` 同用。
- CI 在 Workflow 中公开固定 `KIMI_MODEL_NAME` 和 Kimi Code 专用的 `KIMI_MODEL_BASE_URL=https://api.kimi.com/coding/v1`，只通过 `KIMI_MODEL_API_KEY` 环境变量注入 Secret，避免把 API Key 写入仓库。该专用 Key 不能使用普通的 `api.moonshot.ai/v1` 端点。
- `--output-format stream-json` 输出的是 Kimi 的 Assistant/tool 运行事件流，不是业务交易 JSONL。因此交易 JSONL 必须由 prompt 指定 Agent 写到独立文件，不能直接把 CLI stdout 当成交易指令。
- GitHub-hosted runner 是临时环境。可以缓存 npm 下载或固定版本二进制，但每个 iteration 仍需要恢复/安装；若要完全预装，需要自托管 runner 或自定义 runner image。

## 单轮研究 Release 与滚动历史窗口

研究报告、研究结果和中间校验结果不进入代码树、不提交到任何 Git 数据分支，也不使用 Actions Artifact 做历史存储。运行期间的所有这些文件都位于 `runner.temp`；每个成功 iteration 最终创建一个独立的 GitHub Release。

每轮研究范围遵守 Forecast Edge Skill：重新研究全部当前持仓，并深研至少 10 个此前未研究的合格事件，除非完整候选筛选后实际不足 10 个。本轮 `report.json` 只记录这一轮研究和操作判断，不把旧报告复制进新报告。

Release tag 使用：

```text
forecast-edge-research/run-<workflow-run-id>-attempt-<run-attempt>
```

Release tag 的 target 是本轮真正执行的 `GITHUB_SHA`。主分支更新 Workflow、Prompt、Schema 或 harness 后，下一轮自然锚定新 commit，同时仍可按滚动窗口读取旧版本报告，不需要合并、rebase 或维护数据分支。

每个 Release 至少包含：

- `forecast-edge-research-report.tar.gz`：只包含本轮一个不可变报告目录及其 snapshots；
- `report.json`、`report.md`：方便直接下载查看的本轮报告；
- `research-release-manifest.json`：Release tag、源 commit、本轮使用的历史 Release tags、文件摘要、Kimi 成功尝试序号和校验结果；
- `validation-summary.json`：本轮交易意图校验摘要。

每轮开始时按创建时间选择 tag 前缀匹配的最近 N 个已发布 Release，逐个下载单轮报告包和 Manifest，校验仓库、tag、tag 指向的源 commit、lineage、单报告约束和 SHA-256 后解压。默认 N 为 Skill 指定的 `2`；Repository Variable `FORECAST_EDGE_HISTORY_RELEASE_COUNT` 可将其调整为 `2` 至 `20`。实际不足 N 个时读取全部已有 Release；首次运行从空历史开始。

Agent 对最近报告的使用必须保持 Skill 语义：历史概率不能直接成为本轮答案，全部当前持仓每轮都要重新研究；历史仅用于证据比较、近期机会质量、参考仓位规模和其他明确要求的滚动计算。每个新 Release 只保存本轮结果，旧 Release 永不由 workflow 覆盖或修改；若仓库支持 Immutable Releases，建议启用以保护已发布 tag 和 assets。

Workflow 声明 `contents: write` 以创建 Release 和对应 tag。仓库的 `Settings → Actions → General → Workflow permissions` 也必须允许 `GITHUB_TOKEN` 写入；否则研究和校验可以完成，但发布 Release 时会失败，交易 artifact 不会上传。

GitHub Release 继承仓库可见性。private repository 中的 Release 受仓库权限控制；public repository 会公开本轮研究报告，因此除 `ALLOW_PUBLIC_TRADE_INTENTS=true` 外还必须显式设置 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 才允许运行。

## 第一阶段配置与 Secret 边界

第一阶段需要用户配置四个 Secret；其中三个仅用于飞书通知：

| 名称 | 类型 | 原因 |
| --- | --- | --- |
| `KIMI_CODE_API_KEY` | Actions Secret，必需 | 可调用付费模型，泄露后可被滥用。 |
| `FEISHU_APP_ID` | Actions Secret，必需 | 与 App Secret 配对获取应用身份 token。 |
| `FEISHU_APP_SECRET` | Actions Secret，必需 | 飞书应用凭据，泄露后可被滥用。 |
| `FEISHU_CHAT_ID` | Actions Secret，必需 | 将通知限定路由到机器人与接收人的私聊。 |

以下值不属于凭据，使用 Repository Variables；不得为了隐藏公开标识而增加 Secret：

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `POLYMARKET_ACCOUNT` | 无，必需 | 公开的 Polymarket profile/address；不同部署需要不同值，所以不硬编码。 |
| `FORECAST_EDGE_INTERVAL_HOURS` | `12` | 定时 iteration 间隔，整数且至少 6。 |
| `FORECAST_EDGE_ANCHOR_UTC_HOUR` | `2` | 定时窗口的 UTC 锚点。 |
| `FORECAST_EDGE_HISTORY_RELEASE_COUNT` | `2` | 恢复多少个最近单轮 Release，允许 2–20。 |
| `CREATE_TRADE_INTENT_ISSUE` | `true` | private repository 中是否创建交易意图 Issue。 |
| `ALLOW_PUBLIC_TRADE_INTENTS` | `false` | public repository 中对公开 JSONL 的显式风险确认。 |
| `ALLOW_PUBLIC_RESEARCH_RELEASES` | `false` | public repository 中对公开研究报告的显式风险确认。 |

稳定且公开的实现参数直接写在 Workflow：v1 venue 为 `polymarket`、Kimi Code CLI 为 `0.37.0`、模型名为 `kimi-for-coding`、provider 类型、telemetry 开关、重试次数、timeout 和文件名。它们不是部署凭据，不需要 Repository Secret 或 Variable。`${{ github.token }}` 是 GitHub 为每次 Run 自动生成的短期 token，也不需要用户设置。

第一阶段不配置任何 Polymarket 交易凭据、钱包私钥、签名 Secret、VPN Secret 或 Kalshi Secret；这些只可能属于未来独立的第二阶段执行 Workflow。

## Polymarket Runner 可交易性探针

研究 job 在安装 Agent 前执行一个无凭据、无状态修改的网络探针：

1. 调用官方 [`GET https://polymarket.com/api/geoblock`](https://docs.polymarket.com/api-reference/geoblock)，读取本次 Runner 出口 IP 对应的 `blocked`、country 和 region；
2. 调用 `https://clob.polymarket.com/`，要求公开 CLOB health 返回 `OK`；
3. 把结果写入该 Run 的 Step Summary。`blocked=true` 产生 warning，但不阻止只读研究继续运行。

该探针只能回答“本次 Runner 的公开网络和地域前置条件是否通过”。`blocked=false` 是下单的必要条件，不代表账户认证、签名、余额、allowance、价格或订单提交一定成功；GitHub-hosted Runner 的出口位置也可能在下一轮改变。第一阶段不得为了证明可交易而注入钱包凭据或提交测试订单。

真正的端到端交易验证应留给未来独立的手动执行 Workflow：先在实际执行 Runner 上重复 geoblock，再做 authenticated read-only 账户、余额和 allowance 检查。任何创建后立即取消的订单仍然是真实订单，存在成交可能，必须另行获得明确授权并经过受保护的 GitHub Environment，而不能作为普通健康检查。

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

第一阶段采用初次运行加 3 次修复，共最多 4 次 Kimi 调用。每次修复启动新 session，读取滚动窗口内恢复的单轮历史报告、原 JSONL 与校验错误；Workflow 同时提供本轮开始前的历史报告路径清单，清单内报告只能作为滚动上下文，不能被误判为本轮新增报告。若本轮有效私有报告已经存在，修复不得重复研究或归档第二份报告。

## Issue 与飞书通知

第一阶段在 private repository 中默认每个成功 iteration 创建一个新 Issue。将 Repository Variable `CREATE_TRADE_INTENT_ISSUE` 设为 `false` 可以关闭 Issue，仅保留本轮研究 Release 和只含 JSONL 的交易 artifact；public repository 不创建包含交易数量的 Issue。

无论是否通过配置关闭 GitHub Issue，成功 iteration 都会把为 Issue 生成的同一个临时 `issue.md` 原样作为纯文本发送到 `FEISHU_CHAT_ID`。Workflow 使用 App ID 与 App Secret 获取短期 tenant token，不保存该 token；发送请求带有基于 GitHub run 和 attempt 的幂等键。飞书应用只需机器人发送消息权限，不引入 receive ID 类型、机器人名称或 webhook 等额外配置。

如果仓库是 public，Issue 会公开交易方向、价格、金额、份额和账户线索，可能造成隐私、策略泄露或抢跑风险。默认建议只在 private repository 创建包含数量的 Issue；public repository 应使用主动降敏的公共简报格式。

GitHub Artifact 和 Release 都不是应用层加密存储。统一交易意图 artifact 包含方向和数量，本轮研究 Release 还可能包含账户研究、证据和仓位线索。public repository 默认拒绝运行；只有同时设置 `ALLOW_PUBLIC_TRADE_INTENTS=true` 和 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 接受两类披露后才放行，并且仍不会创建包含数量的 Issue。

## 第二阶段工作流：真实交易（延期实现）

### 精确绑定第一工作流的一次运行

每个 venue workflow 都可以使用 `workflow_run`：

- 只监听第一工作流的 `completed` 事件；
- 要求 `conclusion == success`；
- 如果只接受定时 iteration，要求上游事件是 `schedule`；
- 使用 `github.event.workflow_run.id` 下载该次运行上传的精确 artifact，而不是“最新 artifact”；artifact 中必须恰好只有 `trade-intents.jsonl`；
- 由上游 run ID 和 run attempt 构造对应 Release tag，读取 `research-release-manifest.json` 核对源 commit 和 JSONL digest；即使不读取 Release，执行器仍必须独立完成 JSONL Schema、市场、持仓和实时报价校验。

这样每个 venue 的执行工作流都锚定到唯一的研究 run，不会误取其他定时或手动运行的文件。多个 venue workflow 可以锚定同一个研究 run，各自只处理自己的 item。

不要把第二阶段改成依赖 `on: release`：第一阶段使用默认 `GITHUB_TOKEN` 创建 Release 时，该写操作通常不会递归触发新的 workflow。`workflow_run` 才是执行阶段的触发源；Release 只提供本轮研究报告、滚动历史来源和可核对的 provenance。

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
