# 自动化运行边界

## 定时研究 workflow

`.github/workflows/forecast-edge-research.yml` 是仓库内的定时研究入口。GitHub Cron 每小时触发轻量 gate；实际研究间隔由 `FORECAST_EDGE_INTERVAL_HOURS` 和 `FORECAST_EDGE_ANCHOR_UTC_HOUR` 控制，未配置时分别使用 12 小时和 UTC 02:00。

研究任务会：

1. 校验仓库变量、Secrets 与模型可用性。
2. 恢复近期研究 Release 并验证来源和摘要。
3. 用 `skills/forecast-edge` 获取账户快照并运行完整研究。
4. 用 `schemas/trade-intent.schema.json` 和 `scripts/validate_trade_intents.py` 校验交易意图。
5. 归档当前报告、上传精确 artifact、创建通知并发送飞书消息。

该 workflow 的提示词位于 `automation/prompts/`，通知模板位于 `automation/templates/`。不要把账户、凭据或聊天目标写入这些文件。

## 交易执行 workflow

`.github/workflows/execute-polymarket.yml` 只消费成功研究 workflow 生成且重新验证过的交易意图。只有显式启用生产变量、配置受保护环境和全部凭据后才会进入执行路径；执行代码位于 `execution/` 和对应的 `scripts/`。

## 本机 launchd

本机另有独立的 `forecast-edge-scan` launchd 任务。它执行 `~/.forecast-edge-cron/run.sh`，使用 `~/.forecast-edge-cron/prompt.txt`、`~/.agents/skills/forecast-edge`、`~/.agents/skills/feishu-larva` 和 `~/.forecast-edge-runs`，不调用本仓库内已移除的旧通用调度器。
