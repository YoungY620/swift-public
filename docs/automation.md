# 自动化运行边界

## 父 workflow 与私有研究子图

`.github/workflows/forecast-edge.yml` 是仓库内唯一的定时/手动入口。GitHub Cron 每小时触发轻量 gate；实际研究间隔由 `FORECAST_EDGE_INTERVAL_HOURS` 和 `FORECAST_EDGE_ANCHOR_UTC_HOUR` 控制，未配置时分别使用 12 小时和 UTC 02:00。父图调用 `.github/workflows/forecast-edge-research.yml` 和 `.github/workflows/execute-polymarket.yml` 两个 reusable workflow。

研究子图会：

1. 校验仓库变量、Secrets 与模型可用性。
2. 恢复近期研究 Release 并验证来源和摘要。
3. 用 `skills/forecast-edge` 获取账户快照并运行完整研究。
4. 用 `schemas/trade-intent.schema.json` 和 `scripts/validate_trade_intents.py` 校验交易意图。
5. 将报告包和 `trade-intents.jsonl` 以 `0700` 目录、`0600` 文件暂存到长期 EC2 的逐次私有 handoff 目录。

研究子图不创建 Release/Issue，不发送飞书，也不使用 Actions artifact；模型和校验的详细诊断也不写入 Actions 日志。执行子图把计划、金额和订单诊断保留在 EC2 私有逐次目录，Actions 只看到步骤状态。只有执行子图成功结束，或父图确认本轮未请求/无需执行后，父图才读取私有 handoff，统一创建最终 Release、Issue 和飞书通知；最终 Release 可以包含 `trade-intents.jsonl`。执行失败时不发布，随后仍清理逐次 handoff 目录。

该 workflow 的提示词位于 `automation/prompts/`，通知模板位于 `automation/templates/`。不要把账户、凭据或聊天目标写入这些文件。研究子图只接收模型 API Key 和 EC2 SSH Key，不继承签名私钥或 CLOB 凭据。

## 交易执行 workflow

`.github/workflows/execute-polymarket.yml` 只消费成功研究子图生成、经 SHA-256 绑定并重新验证过的私有交易意图。它直接运行在 `poly-edge-ec2-eu-north-1` Linux/X64 self-hosted runner 上，不再从 Mac SSH 中转。只有显式启用交易、存在交易意图且 EC2 本地凭据文件安全可用时才进入执行路径；执行代码位于 `execution/` 和对应的 `scripts/`。

Polymarket signer private key、account wallet、wallet type 和 CLOB credentials 只保存在 EC2 的 `/home/ubuntu/poly-edge-live/runtime/secrets.json`（`0600`），不保存为 GitHub Secret。父图和执行子图都不读取或传递凭据值；workflow 交给 EC2 的业务输入只有逐次 handoff 路径及交易意图 SHA-256。

买单以 `notional / quantity` 作为建议价格。安全份额取建议份额、建议金额按保护限价可买份额、可用现金在计入费用与现金储备后可买份额三者的最小值；实时逐档模拟 VWAP 相对建议价格的绝对偏差超过阈值时，整项拒绝提交。所有订单按保护限价提交，未成交余量会取消。

执行器通过官方 SDK 自动识别 `EOA`、`POLY_PROXY`、`GNOSIS_SAFE` 和 `DEPOSIT_WALLET`，不能根据 Google/email 或浏览器钱包登录名称猜测签名类型。账户与 CLOB 凭证配置见 [Polymarket 账户认证与执行器配置](polymarket-authentication.md)。

## 本机 launchd

本机另有独立的 `forecast-edge-scan` launchd 任务。它执行 `~/.forecast-edge-cron/run.sh`，使用 `~/.forecast-edge-cron/prompt.txt`、`~/.agents/skills/forecast-edge`、`~/.agents/skills/feishu-larva` 和 `~/.forecast-edge-runs`，不调用本仓库内已移除的旧通用调度器。
