# 自动化运行边界

## 父 workflow 与私有研究子图

`.github/workflows/forecast-edge.yml` 是仓库内唯一的定时/手动入口。GitHub Cron 固定在 UTC 02:23、10:23、18:23 触发，即每 8 小时一轮；每个 `schedule` 事件都代表完整生产 iteration，不再创建按小时唤醒后故意跳过的运行。父图调用 `.github/workflows/forecast-edge-research.yml` 和 `.github/workflows/execute-polymarket.yml` 两个 reusable workflow。

研究子图会：

1. 校验仓库变量、Secrets 与模型可用性。
2. 恢复近期研究 Release 并验证来源和摘要。
3. 按启用的只读 adapter 获取各 venue 的标准化账户快照，并在 GitHub 托管的 Linux runner 上运行完整研究。
4. 要求每个启用的 venue 恰好生成一份本轮报告和一份合并后的交易意图 JSONL，并用统一 Schema、adapter 标识规则和对应账户快照进行校验；没有操作时 JSONL 必须是合法空文件，不能省略。
5. 将各 venue 报告包和合并后的 `trade-intents.jsonl` 写入本轮 SHA-256 绑定的 Actions handoff artifact，供后续 execution/release job 下载；research 不连接或写入 EC2。

JSONL 的每一行都必须包含 `(venue, multi_market_identifier, instrument_identifier)` 路由元组。该元组保留 adapter 返回的 venue 原生标识，研究核心不解释或改写平台标识。

研究子图不创建 Release/Issue、不发送飞书；它上传两类 private-fork Actions artifact：保留 7 天的摘要绑定 handoff，以及保留 90 天的研究诊断。诊断包含 Kimi Code stream/stderr、主 Agent 与 sub-agent `wire.jsonl`、后台 task `output.log`、校验日志，以及只含持仓数、可用抵押资金、历史报告基线和已到达阶段的脱敏飞书 fallback context；不包含账户地址或持仓明细。上传前扫描并拒绝 API key、私钥、密码、token 和认证头。执行子图从 handoff artifact 下载交易意图，在 EC2 本地执行，随后上传无凭据、无签名原文的执行结果与诊断 artifact，供父图发布和 post-run Agent Judge 追踪。只有执行子图成功结束，或手工运行被明确指定为 `research-only`，父图才下载 artifact 并统一创建最终 Release、Issue；执行失败时不发布。自动审计对每个已完成的父 iteration 发送一条最终飞书状态，成功、失败和熔断都不能静默跳过。报告缺失时，飞书 fallback 以当轮账户快照和上一份验证报告作为明确标注的基线；无法验证的持仓研究数、候选数或意图数显示“未获得”，不得伪装成 0。

诊断包同时写入 EC2 的 `/home/ubuntu/poly-edge-live/diagnostics/<run-id>-<attempt>/`，并包含安装、校验、撤单、预检、规划、提交、核验的受限日志、逐阶段事件、原始计划/回执/核验和脱敏账本摘要。打包器会精确扫描 EC2 secret file 中的每个秘密值，并拒绝私钥、密码、token、cookie、认证头、原始 ECDSA 签名和符号链接；扫描失败时不上传、不发布。诊断 artifact 只在订单阶段结束后创建，保留 90 天，且仅存在于 private fork。

该 workflow 的提示词位于 `automation/prompts/`，通知模板位于 `automation/templates/`。不要把账户、凭据或聊天目标写入这些文件。研究子图只接收模型 API Key 和账户输入 Secret，不接收 EC2 地址、SSH Key、签名私钥或 CLOB 凭据。

必须显式设置 Repository Variable `FORECAST_EDGE_MARKET_ADAPTERS` 和 Actions Secret `FORECAST_EDGE_MARKET_ACCOUNTS_JSON`；前者是逗号分隔的已注册 adapter 列表，后者是 adapter 到账户输入的 JSON object。账户名、profile URL 和钱包地址按身份信息保护，即使 venue 可公开查询也不得进入代码或 Repository Variable。workflow 不默认启用任何市场，也没有 venue 专属变量回退。

自动审计的严重失败会触发三重熔断：关闭 `POLYMARKET_LIVE_TRADING_ENABLED`、设置 `FORECAST_EDGE_AUTOMATION_PAUSED=true` 并禁用父 workflow。修改仓库 Variables/禁用 workflow 的权限不依赖默认 `GITHUB_TOKEN`；生产仓库必须配置 GitHub Actions Secret `FORECAST_EDGE_BREAKER_TOKEN`（最小的 Actions 写权限）。该 token 只在配置 gate 与熔断步骤检查/使用，不传给 Kimi Code Agent，也不进入任何诊断、Release 或通知内容。

`.github/workflows/execute-polymarket.yml` 只消费成功研究子图生成、经 SHA-256 绑定并重新验证过的私有交易意图。它直接运行在 `poly-edge-ec2-eu-north-1` Linux/X64 self-hosted runner 上，不再从 Mac SSH 中转。只要本轮显式请求生产执行且 EC2 本地凭据文件安全可用，就必须进入完整执行路径，不以 `has-intents` 为 gate。零交易意图也要在 EC2 上完成 artifact 校验、取消旧挂单、preflight、空 plan、`NO_ACTIONS` 回执、verify 和诊断打包；执行代码位于 `execution/` 和对应的 `scripts/`。

Polymarket signer private key、account wallet、wallet type 和 CLOB credentials 只保存在 EC2 的 `/home/ubuntu/poly-edge-live/runtime/secrets.json`（`0600`），不保存为 GitHub Secret。父图和 research 都不读取或传递凭据值；execution 在 EC2 本机短暂读取凭据，外部业务输入只有 Actions artifact 中的交易意图及其 SHA-256。

买单以 `notional / quantity` 作为建议价格。安全份额取建议份额、建议金额按保护价格可买份额、可用现金在计入费用与现金储备后可买份额三者的最小值；实时逐档模拟 VWAP 只计算相对建议价格的不利偏差，超过阈值时整项拒绝提交，更优成交不会被误拒绝。Polymarket CLOB 要求可成交 BUY 的签名金额至少为 1 pUSD；若上述安全份额对应金额不足 1 pUSD，执行器会跳过该意图，不会放大研究建议金额或份额来凑单。执行子图先取消账户全部未成交挂单并复查账户没有 open order，再对整批尚未提交订单完成最新盘口重规划、额度检查、本地签名、SDK 舍入数量、实时最小份额和 1 pUSD BUY 门槛的无提交预验证；全部通过后才允许第一笔 POST，预验证签名不进入 ledger。真实提交前仍逐单重新验证并签名。新订单使用带 `max_price`/`min_price` 的 `FOK` 市价单；订单要么按保护价格立即全部成交，要么整笔不成交，不在订单簿留下余量。

执行器通过官方 SDK 自动识别 `EOA`、`POLY_PROXY`、`GNOSIS_SAFE` 和 `DEPOSIT_WALLET`，不能根据 Google/email 或浏览器钱包登录名称猜测签名类型。账户与 CLOB 凭证配置见 [Polymarket 账户认证与执行器配置](polymarket-authentication.md)。

## 本机边界

旧的本地 launchd 任务和 Mac self-hosted runner 已停用并从仓库注销。定时研究只由私有仓库父 workflow 发起，研究使用 GitHub-hosted Ubuntu，真实交易只在带专用标签的 EC2 runner 上执行。
