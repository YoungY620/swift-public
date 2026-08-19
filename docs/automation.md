# 自动化运行边界

## 定时研究 workflow

`.github/workflows/forecast-edge-research.yml` 是仓库内的定时研究入口。GitHub Cron 每小时触发轻量 gate；实际研究间隔由 `FORECAST_EDGE_INTERVAL_HOURS` 和 `FORECAST_EDGE_ANCHOR_UTC_HOUR` 控制，未配置时分别使用 12 小时和 UTC 02:00。

研究任务会：

1. 校验仓库变量、Secrets 与模型可用性。
2. 恢复近期研究 Release 并验证来源和摘要。
3. 按启用的只读 adapter 获取各 venue 的标准化账户快照，并在 GitHub 托管的 Linux runner 上运行完整研究。
4. 要求每个启用的 venue 恰好生成一份本轮报告和一份合并后的交易意图 JSONL，并用统一 Schema、adapter 标识规则和对应账户快照进行校验；没有操作时 JSONL 必须是合法空文件，不能省略。
5. 将本轮各 venue 报告归档到同一个 GitHub Release；Artifact 仅上传一个 `trade-intents.jsonl`。
6. 创建 Issue，并把完全相同的正文作为 Feishu Card 2.0 发送。

JSONL 的每一行都必须包含 `(venue, multi_market_identifier, instrument_identifier)` 路由元组。该元组保留 adapter 返回的 venue 原生标识，研究核心不解释或改写平台标识。

该 workflow 的提示词位于 `automation/prompts/`，通知模板位于 `automation/templates/`。不要把账户、凭据或聊天目标写入这些文件。

必须显式设置 `FORECAST_EDGE_MARKET_ADAPTERS` 和 `FORECAST_EDGE_MARKET_ACCOUNTS_JSON`；前者是逗号分隔的已注册 adapter 列表，后者是 adapter 到公开账户输入的 JSON object。workflow 不默认启用任何市场，也没有 venue 专属变量回退。

本仓库只生成、校验、归档和通知交易意图，不包含真实下单 workflow、交易凭据或执行代码。

## 本机 launchd

本机另有独立的 `forecast-edge-scan` launchd 任务。它执行 `~/.forecast-edge-cron/run.sh`，使用 `~/.forecast-edge-cron/prompt.txt`、`~/.agents/skills/forecast-edge`、`~/.agents/skills/feishu-larva` 和 `~/.forecast-edge-runs`，不调用本仓库内已移除的旧通用调度器。
