# GitHub Actions Agent 工作流设计记录

状态：父工作流、私有研究子图、Polymarket 执行子图和 post-run Agent Judge 已实现；生产交易开关已启用，并受自动熔断保护。

本文件记录基于 GitHub Actions 运行 Forecast Edge Agent 的已确定需求、当前设计和待确认事项。父 workflow 负责调度、发布与通知；研究和真实交易分别由 reusable 子 workflow 完成。

## 安全边界

- 第一工作流只读取已启用预测市场的公开账户、市场、订单簿和持仓数据，不保存或使用交易凭据，不提交订单。
- 各市场的执行子图必须重新校验 Actions handoff artifact 中的 JSONL、摘要、市场、持仓、价格、金额、数量和成交后状态，不能把研究结果直接当成可信订单。
- Kimi Code API Key 和通知凭据通过 GitHub Actions Secrets 注入；fork 不继承任何 Repository Secrets。Research 不需要 EC2 SSH key；Polymarket signer private key 与 CLOB credentials 不进入 GitHub。
- 研究子图只接收 Kimi Code API Key 和受保护的账户输入 Secret；执行子图不接收任何 Polymarket 签名/CLOB Secret，只在 EC2 本机调用权限 `0600` 的 `/home/ubuntu/poly-edge-live/runtime/secrets.json`。
- 研究报告、通知正文和 `trade-intents.jsonl` 在执行子图结束前只存在于 runner 临时目录和 private-fork Actions artifact，不进入 Release、Issue 或飞书消息，也不写入 EC2。研究诊断 artifact 包含 Kimi stream、stderr、session/sub-agent 事件和校验日志；执行诊断 artifact 包含执行链证据。两者上传前均拒绝私钥、密码、token、认证头和已知秘密值，且只供有仓库权限的操作者和 post-run Agent Judge 使用。
- 不使用 VPN 绕过交易平台的地域、条款或法律限制。真实交易只能运行在操作者依法获准使用 Polymarket 的网络和司法辖区中。

## 总体架构：统一研究，adapter 隔离

### 父图：统一编排与最终发布

`.github/workflows/forecast-edge.yml` 是唯一的 schedule/manual 入口。它先运行配置 gate，再调用研究子图；生产执行已请求时，研究成功后始终调用执行子图，不以交易意图数量为 gate。零意图也必须在 EC2 上完成撤单、preflight、空 plan、`NO_ACTIONS` 回执、verify 和诊断。EC2 本地执行器在下单前校验本地凭据文件；校验失败会使执行失败且禁止发布。父图等待执行成功，或确认本轮明确未请求生产执行后，才统一创建最终 Release、Issue；飞书通知在 post-run Judge 之后发送。

### 子图一：统一研究工作流

研究阶段使用一个统一 reusable workflow。它可以根据不同市场的参考文档和公开 API 获取数据，但对执行子图只交接统一的、经过校验的交易意图协议。

- Workflow 从必须显式配置的 `FORECAST_EDGE_MARKET_ADAPTERS` 读取已启用 adapter。只有登记在 `config/adapters.json` 且实现完整只读契约的 adapter 才能启用。
- 每个平台在仓库内提供自己的 API、标识、价格单位、订单簿、持仓和结算规则文档；必要时提供只读 adapter/helper。
- Agent 只能输出已启用且已登记的平台；未知平台必须使 Schema 校验失败。
- 组合筛选和资金分配发生在统一研究阶段，但每个 venue 使用真实、独立的账户快照和可用资金，不能假设资金可以在平台之间即时转移。
- 统一交易意图文件为 `trade-intents.jsonl`，可以同时包含多个 venue 的 item。研究报告、Manifest、校验摘要和交易意图由本轮唯一、SHA-256 绑定的 Actions handoff artifact 交接；研究子图不连接 EC2，也不创建外部发布。

当前注册表只内置一个 venue adapter，但这只是已安装实现清单，不是核心默认值。增加任何新 venue 时，必须补充 venue 文档、只读 adapter、标识约束和测试，不能修改共享 workflow 来增加 venue 分支，也不能仅在 Schema 中写入平台名。

### 子图二：按市场拆分的执行工作流

每个预测市场拥有独立 workflow，例如：

- `execute-polymarket.yml` 直接运行在 EC2 Linux/X64 self-hosted runner，下载摘要绑定的 Actions handoff artifact，只保留 `venue == "polymarket"` 的 item，并使用 EC2 本地凭据文件。
- 未来的 `execute-kalshi.yml` 下载同一个 handoff artifact，只保留 `venue == "kalshi"` 的 item，使用 Kalshi Environment Secrets 和执行逻辑。

父图用 `GITHUB_RUN_ID`、`GITHUB_RUN_ATTEMPT`、artifact 名称和 SHA-256 将所有子图绑定到同一轮结果，不存在“取最新文件”的竞态。将来增加多个 venue 时，由父图显式调用对应 reusable workflow，并在全部调用结束后统一发布。

每个执行器必须：

1. 下载本轮指定名称的 Actions handoff artifact，读取 `trade-intents.jsonl`，核对父图传入的 SHA-256，并重新执行 Schema 校验；
2. 再按固定 venue 过滤，禁止通过输入参数临时切换 venue；
3. 对过滤后的每项执行 venue 专属的市场、账户、持仓和价格复核；
4. 没有本 venue 指令时安全跳过；
5. 使用独立的 GitHub Environment 审批边界、EC2 本地凭据、并发锁和持久化幂等账本。

独立执行 workflow 不提供跨 venue 的原子成交保证。如果未来策略要求 Polymarket 与 Kalshi 两条腿必须同时成交，需要新增跨市场执行协调器和 leg-risk 处理，不能依赖两个并行 workflow 自然实现原子套利。

## 第一工作流：定时研究与交易意图

### 已确定需求

1. schedule gate 与完整 research job 都运行在 GitHub 托管的 `ubuntu-latest` runner；Kimi Code CLI 使用非交互模式。
2. Kimi Code API Key 和各 venue 的账户输入都来自 GitHub Actions Secrets；账户名、profile URL 与钱包地址不进入 Repository Variables。
3. 现有定时扫描 prompt 与新增固定 prompt 统一放进 `automation/prompts/`，并修改所有旧路径。
4. 子图用多个可观察的 step 完成安装、配置检查、滚动历史 Release 恢复、账户快照、Agent 运行、Schema 校验、卖单持仓校验、重试和 artifact handoff；最终发布与通知由父图完成，不由一个总控脚本隐藏全部流程。
5. Agent 生成可同时包含多个预测市场的 JSONL 交易意图。每一行必须是一个 JSON object，禁止 Schema 未声明的字段。
6. 初次结果不合格时，使用仓库内固定的修复 prompt 再次运行 Kimi Code。重试次数达到约定上限后 workflow 失败。
7. 只有通过全部校验且经过秘密扫描的 JSONL 才能进入 handoff artifact；执行子图成功结束或明确跳过之前，任何研究/意图内容都不能发布到 Release、Issue 或飞书。
8. 同一仓库的定时 iteration 不允许重叠运行。

### 建议的分步结构

1. Checkout 固定的默认分支提交。
2. 校验研究所需的全局变量和 Secret，但不接触或输出交易凭据。
3. 确认本次 `schedule` 事件具备完整生产配置并直接开始 iteration；`workflow_dispatch` 可显式选择完整执行或 `research-only`。
4. 安装固定版本的 Kimi Code CLI，并缓存下载依赖。
5. 为每个启用 adapter 获取一次不可变的标准化公开账户快照，供 Agent 和后续持仓校验共用。
6. 拼装现有扫描 prompt 与交易意图输出 prompt，启动新的非交互 Kimi Code session。
7. 校验 JSONL 可解析、逐项符合 JSON Schema，并验证跨字段数值关系。零字节文件明确表示本轮没有可执行操作，是合法结果。
8. 对所有 `sell` 项，按 item 的 venue 使用对应账户快照，确认对应 outcome 的真实可卖持仓不少于建议数量。
9. 校验失败时保存机器可读的错误信息，使用固定修复 prompt 让 Kimi Code 重写同一个输出文件，然后重复步骤 7 和 8。
10. 按配置的滚动窗口恢复最近若干个研究 Release，供 Agent 比较历史证据和近期机会；本轮报告仍只描述本轮重新研究的持仓和候选市场。
11. 将验证通过的 `trade-intents.jsonl`、报告包、校验摘要、provenance manifest 和临时通知正文写入本轮 handoff artifact；Kimi stream、stderr、session/sub-agent 事件和校验日志进入独立 research diagnostics artifact，账户快照不进入 handoff，执行器必须重新获取实时账户状态。
12. 父图等待可选执行子图结束，再将最终 Release 创建为本轮唯一发布点，之后才创建 Issue 并把同一份 `issue.md` 发送到飞书。

### Kimi Code 已确认能力

- 官方 CLI 可通过 npm 在 GitHub 托管的 Linux runner 上安装，并支持固定版本。
- `kimi -p "..."` 是非交互模式；该模式默认使用自动权限策略，不需要追加 `--auto` 或 `--yolo`，而且这些参数不能与 `-p` 同用。
- CI 在 Workflow 中公开固定 `KIMI_MODEL_NAME=kimi-for-coding` 和 `KIMI_MODEL_BASE_URL=https://api.kimi.com/coding/v1`，只通过 `KIMI_MODEL_API_KEY` 环境变量注入 Secret。Agent 启动前调用 provider 的模型列表接口确认鉴权与模型可用性。
- `--output-format stream-json` 输出的是 Kimi 的 Assistant/tool 运行事件流，不是业务交易 JSONL。因此交易 JSONL 必须由 prompt 指定 Agent 写到独立文件，不能直接把 CLI stdout 当成交易指令。
- Workflow 同时流式保存 stdout JSONL 和 stderr，并监测 Kimi Code 官方 session 目录中的主 Agent、sub-agent `wire.jsonl` 与后台任务 `output.log`。研究总时长仍由 job/step 上限约束；单次 attempt 只有在所有这些事件源连续 900 秒都没有任何字节增长时才以 `124` 终止并进入既有 repair 链，任一活跃事件都会持续重置空闲计时。
- Workflow 使用 Actions npm cache 加速下载，但每个 iteration 仍固定安装并验证约定的 CLI 版本。

## 单轮最终 Release 与滚动历史窗口

研究报告、研究结果和中间校验结果不进入代码树、不提交到任何 Git 数据分支，也不写入 EC2。运行期间它们位于 `runner.temp` 和当前 private fork 的 Actions artifacts：handoff 保留 7 天，research diagnostics 保留 90 天。执行诊断 artifact 在 execution 子图完成（成功或失败）后生成并保留 90 天。所有 artifact 都不是公开 Release，但仍必须通过秘密扫描。执行成功或明确跳过前不创建 Release；每个最终成功 iteration 只创建一个 GitHub Release。

每轮研究范围遵守 Forecast Edge Skill：每个启用 adapter 都重新研究其全部当前持仓，并深研至少 10 个此前未研究的合格事件，除非完整候选筛选后实际不足 10 个。每个 venue 的本轮 `report.json` 只记录这一轮研究和操作判断，不把旧报告复制进新报告。

Release tag 使用：

```text
forecast-edge-research/run-<workflow-run-id>-attempt-<run-attempt>
```

Release 发布前先通过 GitHub Git refs API 把 tag 显式创建在本轮真正执行的 `GITHUB_SHA`，随后使用 `--verify-tag` 发布；不能只依赖 Release API 的 `target_commitish` 元数据。历史恢复以 manifest 的 workflow run id、attempt、commit 与 GitHub Actions 源 run 元数据交叉验证。主分支更新 Workflow、Prompt、Schema 或 harness 后，下一轮自然锚定新 commit，同时仍可按滚动窗口读取旧版本报告，不需要合并、rebase 或维护数据分支。

每个 Release 至少包含：

- `forecast-edge-research-report.tar.gz`：只包含本轮各启用 adapter 的不可变报告目录及其 snapshots；
- `report-<adapter>.json`、`report-<adapter>.md`：方便直接下载查看的各 venue 本轮报告；
- `research-release-manifest.json`：Release tag、源 commit、本轮使用的历史 Release tags、文件摘要、Kimi 成功尝试序号和校验结果；
- `validation-summary.json`：本轮交易意图校验摘要。
- `trade-intents.jsonl`：执行前仅存在于 private-fork handoff artifact；有条目时，在执行子图结束后才进入最终 Release。合法的零字节无意图文件不作为 Release asset 上传，最终 manifest 记录其 SHA-256、`has_items=false`、`published=false` 和 `asset=null`；
- `iteration-release-manifest.json`：记录执行是否请求、配置是否齐全、子图结果与最终资产摘要；
- 执行成功时的 `execution-receipt.json` 和 `execution-verification.json`。

每轮开始时按创建时间选择 tag 前缀匹配的最近 N 个已发布 Release，逐个下载当轮报告包和 Manifest，校验仓库、tag、源 commit、lineage、报告清单和 SHA-256 后解压。恢复逻辑兼容历史单报告 manifest `1.0` 与新的多报告 manifest `2.0`。默认 N 为 `20`，用于完整报告对比和首次上线时的历史回填；同时每个新 Release 携带只含 event/multi-market/market id 的累积 `research-history.json`，因此超过 N 轮的旧研究也不会丢失。本轮执行前历史另以 `previously-researched.json` 进入诊断包，供研究校验与 Judge 逐项对照；Repository Variable `FORECAST_EDGE_HISTORY_RELEASE_COUNT` 可将其调整为 `2` 至 `20`。

Agent 对最近报告的使用必须保持 Skill 语义：历史概率不能直接成为本轮答案，全部当前持仓每轮都要重新研究；历史仅用于证据比较、近期机会质量、参考仓位规模和其他明确要求的滚动计算。每个新 Release 只保存本轮结果，旧 Release 永不由 workflow 覆盖或修改；若仓库支持 Immutable Releases，建议启用以保护已发布 tag 和 assets。

父 Workflow 的 release job 声明 `contents: write` 以创建 Release 和对应 tag。仓库的 `Settings → Actions → General → Workflow permissions` 也必须允许 `GITHUB_TOKEN` 写入；否则研究与执行可以完成，但最终发布会失败。

GitHub Release 继承仓库可见性。private repository 中的 Release 受仓库权限控制；public repository 会公开本轮研究报告，因此除 `ALLOW_PUBLIC_TRADE_INTENTS=true` 外还必须显式设置 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 才允许运行。

## 父图与研究子图配置边界

父图与研究子图需要用户配置以下 Secret；其中三个仅用于最终飞书通知：

| 名称 | 类型 | 原因 |
| --- | --- | --- |
| `KIMI_CODE_API_KEY` | Actions Secret，必需 | 可调用付费模型，泄露后可被滥用。 |
| `FORECAST_EDGE_MARKET_ACCOUNTS_JSON` | Actions Secret，必需 | adapter 到账户输入的 JSON object；账户名、profile URL 和钱包地址属于操作者身份信息。 |
| `FEISHU_APP_ID` | Actions Secret，必需 | 与 App Secret 配对获取应用身份 token。 |
| `FEISHU_APP_SECRET` | Actions Secret，必需 | 飞书应用凭据，泄露后可被滥用。 |
| `FEISHU_CHAT_ID` | Actions Secret，必需 | 将通知限定路由到机器人与接收人的私聊。 |
| `FORECAST_EDGE_BREAKER_TOKEN` | Actions Secret，必需 | Judge 发现严重异常时关闭交易、暂停调度并禁用父 workflow。 |

以下值不属于凭据，使用 Repository Variables；不得为了隐藏公开标识而增加 Secret：

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `FORECAST_EDGE_MARKET_ADAPTERS` | 无，必需 | 逗号分隔的已注册 adapter 列表。 |
| `FORECAST_EDGE_HISTORY_RELEASE_COUNT` | `20` | 恢复多少个最近完整单轮 Release，允许 2–20；更早标识符由累积 `research-history.json` 保留。 |
| `CREATE_TRADE_INTENT_ISSUE` | `true` | private repository 中是否创建交易意图 Issue。 |
| `ALLOW_PUBLIC_TRADE_INTENTS` | `false` | public repository 中对公开 JSONL 的显式风险确认。 |
| `ALLOW_PUBLIC_RESEARCH_RELEASES` | `false` | public repository 中对公开研究报告的显式风险确认。 |

稳定且公开的实现参数直接写在 Workflow：Kimi Code 为 `0.37.2`、模型名为 `kimi-for-coding`、provider URL 与类型、telemetry 开关、重试次数、总 timeout、900 秒无事件 timeout 和文件名。venue 不是实现常量，必须通过上述通用 Repository Variable 显式配置；账户输入只通过 Actions Secret 提供。`${{ github.token }}` 是 GitHub 为每次 Run 自动生成的短期 token，也不需要用户设置。

父图先做 fail-closed 配置 gate。任一必需 Secret 或 Variable 缺失、生产暂停，或 schedule 触发时 live trading 未开启，gate 都显式失败；不能用成功结论加 downstream `skipped` 掩盖配置错误。失败的父 run 仍进入 post-run audit 和最终通知。

父图、研究子图和执行子图都不接收任何 Polymarket 签名私钥、CLOB Secret、VPN Secret 或 Kalshi Secret。Polymarket 凭据只存在于 EC2 本地 mode-0600 文件；父图只判断交易开关与意图是否存在，服务器本地执行器负责凭据完整性校验。

## Research runner 网络边界

研究 workflow 只需要访问各 adapter 声明的公开市场、订单簿、账户和证据来源，不执行 venue 的交易地域探针，也不注入交易凭据。

真正的端到端交易由受保护的 `execute-polymarket.yml` 子图完成：它在实际执行 EC2 上检查 geoblock，再做 authenticated 账户、余额和 allowance 检查。任何创建后立即取消的订单仍然是真实订单，存在成交可能，必须经过生产交易开关、完整配置 gate 和受保护的 GitHub Environment，而不能作为普通健康检查。

## Post-run Agent Judge 与熔断

`.github/workflows/forecast-edge-audit.yml` 由父 workflow 的 `workflow_run` 事件触发。Kimi Code 使用专用只读 Agent profile：主 Agent 必须主动 `Read` 证据并调用只读 `explore` subagent，再由主 Agent 复核；`Bash`、写文件、凭据、登录态和交易工具均禁止。CLI 使用 `--output-format stream-json`，确定性 harness 要求实际出现完整的 `Read` 和 `Agent` tool call 链，单次普通模型回答不能算 Agent 判断。Judge 判断或飞书 Agent 简报未通过 schema、报告绑定和工具轨迹校验时，本轮审计 job 明确失败并发送带 Warning 的 fallback 通知，不能显示成业务成功；该失败本身不触发交易熔断。只有资金安全、订单保护、残留挂单或秘密泄露白名单中的确定性异常才关闭 live-trading、暂停调度并禁用父 workflow。

审计 workflow 另行保存私有 `forecast-edge-audit-diagnostics-*` artifact：包括 Judge/简报 Agent 的原始 stream、stderr、确定性 trace、Kimi Code session、最终结构化结果、报告来源绑定和业务验收布尔值。上传前会按模型 secret 精确值及凭据模式统一脱敏并生成 SHA-256 manifest；任何未通过秘密扫描的诊断包都不得上传。
熔断步骤使用专用 Actions Secret `FORECAST_EDGE_BREAKER_TOKEN`。该 secret 不是 Polymarket 凭据，不会传入 Judge Agent，不会被诊断打包器读取，也不会写入日志或 Release；未配置时父 workflow 和 audit 都只完成 no-op gate，不进入生产 iteration。

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

### 价格、金额和份额

“价格金额数量”需要拆成无歧义字段。建议使用：

- `reference_price`：研究时估计的平均成交价，范围 `(0, 1]`；
- `notional`：`currency` 计价的总金额；
- `quantity`：统一 outcome 合约数量；venue 原生单位语义由 adapter 文档解释。

研究子图使用最多 18 位小数的普通十进制字符串，禁止指数记数法；`notional` 与 `reference_price × quantity` 的绝对误差最多为 `0.01`。执行器不使用 `reference_price` 作为最终建议价格，而以 `notional / quantity` 重新得到建议价格。

买入的安全份额取以下三者的最小值，并向下舍入到交易份额精度：

1. 研究建议份额 `quantity`；
2. 建议金额按保护限价最多可买的份额 `notional / limit_price`；
3. 实时可用现金扣除最低现金储备、并为最大费用留足空间后最多可买的份额。

执行器逐档模拟在保护价格内的订单簿成交，计算 VWAP。模拟 VWAP 相对 `notional / quantity` 的不利偏差超过唯一的 `POLYMARKET_MAX_PRICE_DEVIATION_BPS` 阈值时拒绝提交；价格改善不会被误拒绝。名义金额偏差与该价格偏差在固定份额下是同一个比例，因此不再设置重复阈值。安全份额继续取建议金额与建议份额约束后的最小值；如果买单的签名金额低于 Polymarket CLOB 官方 1 pUSD 最低可成交 BUY 金额，则跳过该意图，不会扩大研究建议来凑单。执行开始前先取消账户全部未成交挂单并复查为空；然后对所有新订单做无提交的整批预验证，包括最新盘口重规划、预存 allowance、本地 FOK 签名、SDK 舍入数量、实时 `min_order_size` 和 1 pUSD BUY 门槛。只有全部通过才允许第一笔 POST，预验证签名不写入 ledger；逐笔正式提交前仍重复重规划和签名以处理盘口变化。新订单使用带 `max_price`/`min_price` 的 `FOK` 市价单，只允许立即全部成交或整笔不成交，不允许余量停留在订单簿，也不自动追价补单。

## 固定八小时间隔

### GitHub 限制

`on.schedule.cron` 是静态 YAML，不能引用 `${{ vars.* }}`。本生产目标已经确定为 8 小时，不再为任意 N 增加小时级 wake-up gate。

父图使用 `23 2,10,18 * * *`，在 UTC 02:23、10:23、18:23 直接启动完整 iteration。每个 schedule run 都必须依次到达 research、execution、release；零交易意图也执行完整 EC2 生命周期。固定 concurrency group 与 `cancel-in-progress: false` 使耗时超过 8 小时的前一轮排队而不是相互取消。

## 重试语义

研究子图采用初次运行加 3 次修复，共最多 4 次 Kimi 调用。每次修复启动新 session，读取滚动窗口内恢复的单轮历史报告、原 JSONL 与校验错误；Workflow 同时提供本轮开始前的历史报告路径清单，清单内报告只能作为滚动上下文，不能被误判为本轮新增报告。若本轮有效私有报告已经存在，修复不得重复研究或归档第二份报告。只有 Agent 中断、临时服务异常或报告/意图校验失败才进入 repair；明确的 Kimi 认证、计费额度或配额错误会在第一次失败后停止后续 prompt repair，保留诊断并显式失败，因为重复改写研究 prompt 无法修复账户额度。

## Issue 与飞书通知

父图在 private repository 中默认于最终 Release 成功后创建一个新 Issue。将 Repository Variable `CREATE_TRADE_INTENT_ISSUE` 设为 `false` 可以关闭 Issue，仅保留本轮最终 Release；public repository 不创建包含交易数量的 Issue。

父 workflow 不在研究或执行过程中发送飞书消息。post-run audit workflow 对每个已完成的父 iteration 只发送一张最终卡片：由只读 Kimi Code Agent 阅读已验证报告生成研究简报，并附父 run 各阶段结论、Judge 有效性、简报有效性、业务验收、严重检查项和熔断结果；执行失败或审计失败也必须通知。无熔断时卡片为绿色，实际触发或需要触发熔断时为红色；Warning 和 Error 详情分别放入折叠面板。若当轮报告未生成，fallback 读取秘密扫描后的账户摘要、上一份验证报告基线和实际研究阶段；未知数量使用 `null` 并显示“未获得”，只有验证报告明确证明零结果时才显示 0。Workflow 使用 App ID 与 App Secret 获取短期 tenant token，不保存该 token；发送请求带有基于 GitHub run 和 attempt 的幂等键。

如果仓库是 public，Issue 会公开交易方向、价格、金额、份额和账户线索，可能造成隐私、策略泄露或抢跑风险。默认建议只在 private repository 创建包含数量的 Issue；public repository 应使用主动降敏的公共简报格式。

GitHub Release 和 Actions artifact 都不是应用层加密存储。最终 Release 包含交易意图、账户研究、证据和仓位线索；public repository 默认拒绝运行，只有同时设置 `ALLOW_PUBLIC_TRADE_INTENTS=true` 和 `ALLOW_PUBLIC_RESEARCH_RELEASES=true` 接受执行后披露才放行，并且仍不会创建包含数量的 Issue。当前生产形态是 private fork，handoff 和诊断 artifacts 只对有仓库权限的主体可见；无论可见性如何都严禁保存密钥、密码或签名原文。

## 执行子图：真实交易（已实现并部署）

### 精确绑定父工作流的一次运行

每个 venue 执行器都实现为 `workflow_call` reusable workflow：

- 只由父图在研究成功、生产执行已请求且配置齐全时调用；零交易意图也必须运行完整 EC2 执行生命周期；
- 使用 `GITHUB_RUN_ID` 和 `GITHUB_RUN_ATTEMPT` 下载本轮唯一命名的 Actions handoff artifact；
- 使用父图传入的 SHA-256 校验 `trade-intents.jsonl`，再独立完成 JSONL Schema、市场、持仓和实时报价校验；
- 使用 `GITHUB_REPOSITORY`、run ID、attempt 和摘要派生 execution ID，并用长期 EC2 上的 SQLite ledger 保证重试幂等和共享单日限额；
- 成交后核对订单与账户变化，取消未成交余量，只把清洗后的 receipt/verification 和诊断包上传为本轮执行 artifact。

这样每个 venue 执行子图都锚定到唯一的父 run，不会误取其他定时或手动运行的文件。多个 venue workflow 可以锚定同一个 artifact，各自只处理自己的 item；父图负责等待全部子图结束。

不要把执行阶段改成依赖 `on: release`：Release 是全部子图完成后的发布结果，而不是执行触发源。反过来先发布再执行会泄露尚未成交的方向、价格与数量，增加抢跑和狙击风险。

### 待确认和延期项

- 标准 GitHub-hosted runner 不能保证位于爱尔兰或印度，也不能把某个共享出口 IP 当作稳定交易地域。真实交易应使用位于合规地区、由操作者控制的 self-hosted runner，并用专用 label/runner group 锁定。
- 账户钱包类型、签名方式和交易 Secret 已在 [Polymarket 账户认证与执行器配置](polymarket-authentication.md) 中确定并保存在 EC2；生产交易开关由 Repository Variable 控制，并由审计熔断自动关闭。
- 是否在真实交易 environment 上启用 GitHub required reviewers，形成最终人工批准门。
- 告警升级和人工介入策略。

## 后续需要确认的决策

1. 生产部署最终采用的价格偏差和风险阈值。
2. 新 venue adapter 的 market/instrument 映射和账户数据契约。
3. Issue 在公开仓库中的降敏策略。
