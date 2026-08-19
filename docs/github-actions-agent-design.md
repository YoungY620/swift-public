# GitHub Actions Agent 工作流设计记录

状态：多市场 adapter 研究架构已实现；仓库只负责研究、交易意图、归档和通知。

本文件记录基于 GitHub Actions 运行 Forecast Edge Agent 的研究 workflow 设计。每次 research 必须生成交易意图 JSONL，并完成校验、归档和通知；仓库不包含真实下单实现。

## 安全边界

- 研究 workflow 只读取已启用预测市场的公开账户、市场、订单簿和持仓数据，不保存或使用交易凭据，不提交订单。
- Kimi Code API Key 和通知凭据只通过 GitHub Actions Secrets 注入；fork 不继承任何 Repository Secrets。
- 交易意图是研究产物而不是订单。workflow 不认证 venue、不签名、不提交订单，也不注入任何交易凭据。

## 总体架构：统一研究，adapter 隔离

### 统一研究工作流

研究阶段使用一个统一 workflow。它可以根据不同市场的 adapter 和参考文档获取数据，并发布统一的、经过校验的交易意图协议。

- Workflow 从必须显式配置的 `FORECAST_EDGE_MARKET_ADAPTERS` 读取已启用 adapter。只有登记在 `config/adapters.json` 且实现完整只读契约的 adapter 才能启用。
- 每个平台在仓库内提供自己的 API、标识、价格单位、订单簿、持仓和结算规则文档；必要时提供只读 adapter/helper。
- Agent 只能输出已启用且已登记的平台；未知平台必须使 Schema 校验失败。
- 组合筛选和资金分配发生在统一研究阶段，但每个 venue 使用真实、独立的账户快照和可用资金，不能假设资金可以在平台之间即时转移。
- 统一 artifact 仅包含一个 `trade-intents.jsonl` 文件，该文件可以同时包含多个 venue 的 item；研究报告、Manifest、校验摘要和其他中间结果禁止放入 artifact。

当前注册表只内置一个 venue adapter，但这只是已安装实现清单，不是核心默认值。增加任何新 venue 时，必须补充 venue 文档、只读 adapter、标识约束和测试，不能修改共享 workflow 来增加 venue 分支，也不能仅在 Schema 中写入平台名。

## 定时研究与交易意图

### 已确定需求

1. schedule gate 与完整 research job 都运行在 GitHub 托管的 `ubuntu-latest` runner；Kimi Code CLI 使用非交互模式。
2. Kimi Code API Key 来自 GitHub Actions Secret；各 venue 的公开账户输入来自 Repository Variables。
3. 现有定时扫描 prompt 与新增固定 prompt 统一放进 `automation/prompts/`，并修改所有旧路径。
4. 工作流用多个可观察的 step 完成安装、配置检查、滚动历史 Release 恢复、账户快照、Agent 运行、Schema 校验、卖单持仓校验、重试、Release 发布、artifact 上传和 Issue 通知；不由一个总控脚本隐藏全部流程。
5. Agent 生成可同时包含多个预测市场的 JSONL 交易意图。每一行必须是一个 JSON object，禁止 Schema 未声明的字段。
6. 初次结果不合格时，使用仓库内固定的修复 prompt 再次运行 Kimi Code。重试次数达到约定上限后 workflow 失败。
7. 只有通过全部校验的 JSONL 才能上传为 artifact 或发布通知。
8. 同一仓库的定时 iteration 不允许重叠运行。

### 建议的分步结构

1. Checkout 固定的默认分支提交。
2. 校验全局变量和必需 Secret，但不输出 Secret 值。
3. 判断本次 `schedule` 事件是否应该开始一次 iteration；`workflow_dispatch` 手动运行不受定时门控限制。
4. 安装固定版本的 Kimi Code CLI，并缓存下载依赖。
5. 为每个启用 adapter 获取一次不可变的标准化公开账户快照，供 Agent 和后续持仓校验共用。
6. 拼装现有扫描 prompt 与交易意图输出 prompt，启动新的非交互 Kimi Code session。
7. 校验 JSONL 可解析、逐项符合 JSON Schema，并验证跨字段数值关系。零字节文件明确表示本轮没有可执行操作，是合法结果。
8. 对所有 `sell` 项，按 item 的 venue 使用对应账户快照，确认对应 outcome 的真实可卖持仓不少于建议数量。
9. 校验失败时保存机器可读的错误信息，使用固定修复 prompt 让 Kimi Code 重写同一个输出文件，然后重复步骤 7 和 8。
10. 按配置的滚动窗口恢复最近若干个研究 Release，供 Agent 比较历史证据和近期机会；本轮报告仍只描述本轮重新研究的持仓和候选市场。
11. artifact 仅上传验证通过的 `trade-intents.jsonl`。校验摘要和 provenance manifest 进入研究 Release；账户快照和 Kimi session 不进入 artifact。
12. 用固定 Markdown 模板生成通知，在本仓库创建 Issue，并把同一份 `issue.md` 原文作为 Feishu Card 2.0 markdown 发送到飞书私聊。

### Kimi Code 已确认能力

- 官方 CLI 可通过 npm 在 GitHub 托管的 Linux runner 上安装，并支持固定版本。
- `kimi -p "..."` 是非交互模式；该模式默认使用自动权限策略，不需要追加 `--auto` 或 `--yolo`，而且这些参数不能与 `-p` 同用。
- CI 在 Workflow 中公开固定 `KIMI_MODEL_NAME=kimi-for-coding` 和 `KIMI_MODEL_BASE_URL=https://api.kimi.com/coding/v1`，只通过 `KIMI_MODEL_API_KEY` 环境变量注入 Secret。Agent 启动前调用 provider 的模型列表接口确认鉴权与模型可用性。
- `--output-format stream-json` 输出的是 Kimi 的 Assistant/tool 运行事件流，不是业务交易 JSONL。因此交易 JSONL 必须由 prompt 指定 Agent 写到独立文件，不能直接把 CLI stdout 当成交易指令。
- Workflow 使用 Actions npm cache 加速下载，但每个 iteration 仍固定安装并验证约定的 CLI 版本。

## 单轮研究 Release 与滚动历史窗口

研究报告、研究结果和中间校验结果不进入代码树、不提交到任何 Git 数据分支，也不使用 Actions Artifact 做历史存储。运行期间的所有这些文件都位于 `runner.temp`；每个成功 iteration 最终创建一个独立的 GitHub Release。

每轮研究范围遵守 Forecast Edge Skill：每个启用 adapter 都重新研究其全部当前持仓，并深研至少 10 个此前未研究的合格事件，除非完整候选筛选后实际不足 10 个。每个 venue 的本轮 `report.json` 只记录这一轮研究和操作判断，不把旧报告复制进新报告。

Release tag 使用：

```text
forecast-edge-research/run-<workflow-run-id>-attempt-<run-attempt>
```

Release tag 的 target 是本轮真正执行的 `GITHUB_SHA`。主分支更新 Workflow、Prompt、Schema 或 harness 后，下一轮自然锚定新 commit，同时仍可按滚动窗口读取旧版本报告，不需要合并、rebase 或维护数据分支。

每个 Release 至少包含：

- `forecast-edge-research-report.tar.gz`：只包含本轮各启用 adapter 的不可变报告目录及其 snapshots；
- `report-<adapter>.json`、`report-<adapter>.md`：方便直接下载查看的各 venue 本轮报告；
- `research-release-manifest.json`：Release tag、源 commit、本轮使用的历史 Release tags、文件摘要、Kimi 成功尝试序号和校验结果；
- `validation-summary.json`：本轮交易意图校验摘要。

每轮开始时按创建时间选择 tag 前缀匹配的最近 N 个已发布 Release，逐个下载当轮报告包和 Manifest，校验仓库、tag、源 commit、lineage、报告清单和 SHA-256 后解压。恢复逻辑兼容历史单报告 manifest `1.0` 与新的多报告 manifest `2.0`。默认 N 为 `2`；Repository Variable `FORECAST_EDGE_HISTORY_RELEASE_COUNT` 可将其调整为 `2` 至 `20`。

Agent 对最近报告的使用必须保持 Skill 语义：历史概率不能直接成为本轮答案，全部当前持仓每轮都要重新研究；历史仅用于证据比较、近期机会质量、参考仓位规模和其他明确要求的滚动计算。每个新 Release 只保存本轮结果，旧 Release 永不由 workflow 覆盖或修改；若仓库支持 Immutable Releases，建议启用以保护已发布 tag 和 assets。

Workflow 声明 `contents: write` 以创建 Release 和对应 tag。仓库的 `Settings → Actions → General → Workflow permissions` 也必须允许 `GITHUB_TOKEN` 写入；否则研究和校验可以完成，但发布 Release 时会失败，交易 artifact 不会上传。

GitHub Release 继承仓库可见性。private repository 中的 Release 受仓库权限控制；public repository 会公开本轮研究报告，因此除 `ALLOW_PUBLIC_TRADE_INTENTS=true` 外还必须显式设置 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 才允许运行。

## 研究配置与 Secret 边界

研究 workflow 需要用户配置四个 Secret；其中三个仅用于飞书通知：

| 名称 | 类型 | 原因 |
| --- | --- | --- |
| `KIMI_CODE_API_KEY` | Actions Secret，必需 | 可调用付费模型，泄露后可被滥用。 |
| `FEISHU_APP_ID` | Actions Secret，必需 | 与 App Secret 配对获取应用身份 token。 |
| `FEISHU_APP_SECRET` | Actions Secret，必需 | 飞书应用凭据，泄露后可被滥用。 |
| `FEISHU_CHAT_ID` | Actions Secret，必需 | 将通知限定路由到机器人与接收人的私聊。 |

以下值不属于凭据，使用 Repository Variables；不得为了隐藏公开标识而增加 Secret：

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `FORECAST_EDGE_MARKET_ADAPTERS` | 无，必需 | 逗号分隔的已注册 adapter 列表。 |
| `FORECAST_EDGE_MARKET_ACCOUNTS_JSON` | 无，必需 | adapter 到公开账户输入的 JSON object，必须与启用列表精确对应。 |
| `FORECAST_EDGE_INTERVAL_HOURS` | `12` | 定时 iteration 间隔，整数且至少 6。 |
| `FORECAST_EDGE_ANCHOR_UTC_HOUR` | `2` | 定时窗口的 UTC 锚点。 |
| `FORECAST_EDGE_HISTORY_RELEASE_COUNT` | `2` | 恢复多少个最近单轮 Release，允许 2–20。 |
| `CREATE_TRADE_INTENT_ISSUE` | `true` | private repository 中是否创建交易意图 Issue。 |
| `ALLOW_PUBLIC_TRADE_INTENTS` | `false` | public repository 中对公开 JSONL 的显式风险确认。 |
| `ALLOW_PUBLIC_RESEARCH_RELEASES` | `false` | public repository 中对公开研究报告的显式风险确认。 |

稳定且公开的实现参数直接写在 Workflow：Kimi Code CLI 为 `0.37.0`、模型名为 `kimi-for-coding`、provider URL 与类型、telemetry 开关、重试次数、timeout 和文件名。venue 和账户不是实现常量，必须通过上述通用 Repository Variables 显式配置。`${{ github.token }}` 是 GitHub 为每次 Run 自动生成的短期 token，也不需要用户设置。

研究 workflow 不配置任何 venue 交易凭据、钱包私钥、签名 Secret 或 VPN Secret。

## Research runner 网络边界

研究 workflow 只需要访问各 adapter 声明的公开市场、订单簿、账户和证据来源，不执行 venue 的交易地域探针，也不注入交易凭据。

## JSONL 交易意图契约

### 统一 envelope

建议所有 venue 共用以下字段：

- `schema_version`：单条 item 的协议版本，使拆分后的 JSONL 仍可独立解释。
- `venue`：稳定的小写 adapter 标识；必须属于本次启用列表和可信 adapter 注册表。
- `multi_market_identifier`：venue 原生的具体 market 标识，不能使用 event ID。
- `instrument_identifier`：该 outcome 可直接交易的合约/资产标识。
- `outcome`：`yes` 或 `no`。
- `side`：`buy` 或 `sell`。
- `reference_price`：统一为每份 outcome 合约 `0 < price <= 1` 的研究时参考价格。
- `notional`：以 item 声明的结算/抵押资产计价的预估名义金额。
- `quantity`：选择 outcome 的合约数量；各 venue 的原生单位在协议层统一称为 quantity。
- `currency`：`notional` 的计价资产/货币，避免跨 venue 时把 USD、USDC 或其他抵押资产静默混用。

Schema 使用 `additionalProperties: false`，并校验价格范围、正数金额、正数数量以及 `notional ≈ reference_price × quantity`。`reference_price` 明确只是研究快照，不代表已提交或保证成交的价格。

不同平台的原生 ID 可能碰撞，同一市场的 YES/NO instrument 也不同，因此全局交易路由 key 定义为：

```text
(venue, multi_market_identifier, instrument_identifier)
```

如果要求一个可打印的全局 ID，应由可信 workflow 派生 `venue + ":" + multi_market_identifier`，而不是让 Agent 自由生成第二份可能不一致的数据。

### market 与 instrument 的区别

一个 market ID 不一定足以唯一确定 outcome instrument，因此统一协议必须区分：

- `multi_market_identifier`：具体二元市场本身的 ID；
- `instrument_identifier`：所选 outcome 的可交易 instrument。

具体平台有哪些原生 ID、哪个 ID 对应 market、哪个 ID 对应 outcome instrument，全部由 `config/adapters.json`、venue adapter 和其 reference 文档共同定义。共享研究文档、Schema 和 workflow 不包含平台专属映射。人类可核查的 market URL 不进入交易协议；通知层通过对应 adapter 独立解析并验证 URL。

### 待确认：价格、金额和份额

“价格金额数量”需要拆成无歧义字段。建议使用：

- `reference_price`：研究时估计的平均成交价，范围 `(0, 1]`；
- `notional`：`currency` 计价的总金额；
- `quantity`：统一 outcome 合约数量；venue 原生单位语义由 adapter 文档解释。

研究阶段使用最多 18 位小数的普通十进制字符串，禁止指数记数法；`notional` 与 `reference_price × quantity` 的绝对误差最多为 `0.01`。

## 定时间隔 N

### GitHub 限制

`on.schedule.cron` 是静态 YAML，不能引用 `${{ vars.* }}`。此外，Cron 的 `*/7` 小时会在每天午夜重置，并不能保证跨天严格每 7 小时运行。

研究 workflow 采用任意整数 N：Workflow 每小时第 `23` 分钟触发一次，首个 gate step 校验 `FORECAST_EDGE_INTERVAL_HOURS` 是不小于 6 的整数，并按 UTC epoch hour 与可配置 anchor 决定是否开始 iteration。未命中的 run 很快结束，但 Actions 页面会出现按小时的跳过记录。研究 job 使用 `concurrency` 防止长时间 Agent iteration 重叠；`workflow_dispatch` 手动运行绕过时间 gate。

## 重试语义

研究阶段采用初次运行加 3 次修复，共最多 4 次 Kimi 调用。每次修复启动新 session，读取滚动窗口内恢复的单轮历史报告、原 JSONL 与校验错误；Workflow 同时提供本轮开始前的历史报告路径清单，清单内报告只能作为滚动上下文，不能被误判为本轮新增报告。若本轮有效私有报告已经存在，修复不得重复研究或归档第二份报告。

## Issue 与飞书通知

研究 workflow 在 private repository 中默认每个成功 iteration 创建一个新 Issue。将 Repository Variable `CREATE_TRADE_INTENT_ISSUE` 设为 `false` 可以关闭 Issue，仅保留本轮研究 Release 和只含 JSONL 的交易 artifact；public repository 不创建包含交易数量的 Issue。

无论是否通过配置关闭 GitHub Issue，成功 iteration 都会把为 Issue 生成的同一个临时 `issue.md` 原样放进 Feishu Card 2.0 的 markdown element，并发送到 `FEISHU_CHAT_ID`。Workflow 使用 App ID 与 App Secret 获取短期 tenant token，不保存该 token；发送请求带有基于 GitHub run 和 attempt 的幂等键。飞书应用只需机器人发送消息权限，不引入 receive ID 类型、机器人名称或 webhook 等额外配置。

如果仓库是 public，Issue 会公开交易方向、价格、金额、份额和账户线索，可能造成隐私、策略泄露或抢跑风险。默认建议只在 private repository 创建包含数量的 Issue；public repository 应使用主动降敏的公共简报格式。

GitHub Artifact 和 Release 都不是应用层加密存储。统一交易意图 artifact 包含方向和数量，本轮研究 Release 还可能包含账户研究、证据和仓位线索。public repository 默认拒绝运行；只有同时设置 `ALLOW_PUBLIC_TRADE_INTENTS=true` 和 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 接受两类披露后才放行，并且仍不会创建包含数量的 Issue。

## 后续需要确认的决策

1. 新 venue adapter 的 market/instrument 映射和账户数据契约。
2. Issue 在公开仓库中的降敏策略。
