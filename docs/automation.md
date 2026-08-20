# 自动化运行边界

## 父 workflow 与私有研究子图

`.github/workflows/forecast-edge.yml` 是仓库内唯一的定时/手动入口。GitHub Cron 每小时触发轻量 gate；实际研究间隔由 `FORECAST_EDGE_INTERVAL_HOURS` 和 `FORECAST_EDGE_ANCHOR_UTC_HOUR` 控制，未配置时分别使用 8 小时和 UTC 02:00。父图调用 `.github/workflows/forecast-edge-research.yml` 和 `.github/workflows/execute-polymarket.yml` 两个 reusable workflow。

研究子图会：

1. 校验仓库变量、Secrets 与模型可用性。
2. 恢复近期研究 Release 并验证来源和摘要。
3. 按启用的只读 adapter 获取各 venue 的标准化账户快照，并在 GitHub 托管的 Linux runner 上运行完整研究。
4. 要求每个启用的 venue 恰好生成一份本轮报告和一份合并后的交易意图 JSONL，并用统一 Schema、adapter 标识规则和对应账户快照进行校验；没有操作时 JSONL 必须是合法空文件，不能省略。
5. 将各 venue 报告包和合并后的 `trade-intents.jsonl` 写入本轮 SHA-256 绑定的 Actions handoff artifact，供后续 execution/release job 下载；research 不连接或写入 EC2。

JSONL 的每一行都必须包含 `(venue, multi_market_identifier, instrument_identifier)` 路由元组。该元组保留 adapter 返回的 venue 原生标识，研究核心不解释或改写平台标识。

研究子图不创建 Release/Issue、不发送飞书；它上传两类 private-fork Actions artifact：保留 7 天的摘要绑定 handoff，以及保留 90 天的研究诊断。诊断包含 Kimi Code stream/stderr、主 Agent 与 sub-agent `wire.jsonl`、后台 task `output.log` 和校验日志，上传前扫描并拒绝 API key、私钥、密码、token 和认证头。执行子图从 handoff artifact 下载交易意图，在 EC2 本地执行，随后上传无凭据、无签名原文的执行结果与诊断 artifact，供父图发布和 post-run Agent Judge 追踪。只有执行子图成功结束，或父图确认本轮未请求/无需执行后，父图才下载 artifact，统一创建最终 Release、Issue 和飞书通知。执行失败时不发布。自动审计对每个真正进入 gate 的生产迭代发送一条最终飞书状态；纯 gate-off 的小时唤醒不发送。

诊断包同时写入 EC2 的 `/home/ubuntu/poly-edge-live/diagnostics/<run-id>-<attempt>/`，并包含安装、校验、撤单、预检、规划、提交、核验的受限日志、逐阶段事件、原始计划/回执/核验和脱敏账本摘要。打包器会精确扫描 EC2 secret file 中的每个秘密值，并拒绝私钥、密码、token、cookie、认证头、原始 ECDSA 签名和符号链接；扫描失败时不上传、不发布。诊断 artifact 只在订单阶段结束后创建，保留 90 天，且仅存在于 private fork。

该 workflow 的提示词位于 `automation/prompts/`，通知模板位于 `automation/templates/`。不要把账户、凭据或聊天目标写入这些文件。研究子图只接收模型 API Key，不接收 EC2 地址、SSH Key、签名私钥或 CLOB 凭据。

必须显式设置 `FORECAST_EDGE_MARKET_ADAPTERS` 和 `FORECAST_EDGE_MARKET_ACCOUNTS_JSON`；前者是逗号分隔的已注册 adapter 列表，后者是 adapter 到公开账户输入的 JSON object。workflow 不默认启用任何市场，也没有 venue 专属变量回退。

自动审计的严重失败会触发三重熔断：关闭 `POLYMARKET_LIVE_TRADING_ENABLED`、设置 `FORECAST_EDGE_AUTOMATION_PAUSED=true` 并禁用父 workflow。修改仓库 Variables/禁用 workflow 的权限不能依赖默认 `GITHUB_TOKEN`；生产仓库应额外配置 GitHub Actions Secret `FORECAST_EDGE_BREAKER_TOKEN`（最小的 Actions 写权限）。该 token 只在熔断步骤使用，不传给 Kimi Code Agent，也不进入任何诊断、Release 或通知内容。

`.github/workflows/execute-polymarket.yml` 只消费成功研究子图生成、经 SHA-256 绑定并重新验证过的私有交易意图。它直接运行在 `poly-edge-ec2-eu-north-1` Linux/X64 self-hosted runner 上，不再从 Mac SSH 中转。只有显式启用交易、存在交易意图且 EC2 本地凭据文件安全可用时才进入执行路径；执行代码位于 `execution/` 和对应的 `scripts/`。

Polymarket signer private key、account wallet、wallet type 和 CLOB credentials 只保存在 EC2 的 `/home/ubuntu/poly-edge-live/runtime/secrets.json`（`0600`），不保存为 GitHub Secret。父图和 research 都不读取或传递凭据值；execution 在 EC2 本机短暂读取凭据，外部业务输入只有 Actions artifact 中的交易意图及其 SHA-256。

买单以 `notional / quantity` 作为建议价格。安全份额取建议份额、建议金额按保护价格可买份额、可用现金在计入费用与现金储备后可买份额三者的最小值；实时逐档模拟 VWAP 相对建议价格的绝对偏差超过阈值时，整项拒绝提交。执行子图先取消账户全部未成交挂单并复查账户没有 open order，再提交带 `max_price`/`min_price` 的 `FOK` 市价单；订单要么按保护价格立即全部成交，要么整笔不成交，不在订单簿留下余量。

执行器通过官方 SDK 自动识别 `EOA`、`POLY_PROXY`、`GNOSIS_SAFE` 和 `DEPOSIT_WALLET`，不能根据 Google/email 或浏览器钱包登录名称猜测签名类型。账户与 CLOB 凭证配置见 [Polymarket 账户认证与执行器配置](polymarket-authentication.md)。

## 本机 launchd

本机另有独立的 `forecast-edge-scan` launchd 任务。它执行 `~/.forecast-edge-cron/run.sh`，使用 `~/.forecast-edge-cron/prompt.txt`、`~/.agents/skills/forecast-edge`、`~/.agents/skills/feishu-larva` 和 `~/.forecast-edge-runs`，不调用本仓库内已移除的旧通用调度器。
