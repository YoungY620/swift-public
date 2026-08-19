# Poly-edge

Poly-edge 是面向 Polymarket 的长期概率优势研究与交易意图生成项目。仓库只保留自动化 workflow 所需的研究 Skill、提示词、数据/报告工具、交易意图协议与执行代码。

## 自动化入口

- `.github/workflows/forecast-edge-research.yml`：定时研究、报告归档、交易意图校验、Release、Issue 与飞书通知。
- `.github/workflows/execute-polymarket.yml`：在显式启用并配置生产环境后消费已验证的交易意图。
- `.github/workflows/pages.yml`：按要求保留原实现；当前仓库未配置 `ENABLE_FORECAST_EDGE_PAGES`，不会执行。

研究 workflow 通过 `kimi --skills-dir "$GITHUB_WORKSPACE/skills"` 直接加载 `skills/forecast-edge`。它依赖：

```text
automation/prompts/                 扫描与修复提示词
automation/templates/               通知模板
automation/requirements-*.txt       workflow Python 依赖
schemas/trade-intent.schema.json    交易意图协议
scripts/validate_trade_intents.py   研究产物校验
skills/forecast-edge/               核心研究 Skill
```

执行 workflow 还依赖 `execution/`、`scripts/polymarket_execution.py`、`scripts/run_with_polymarket_secrets.py` 和对应 requirements。

## 本机定时任务

本机 `launchd` 任务 `forecast-edge-scan` 每天北京时间 08:00 和 20:00 执行 `~/.forecast-edge-cron/run.sh`。这条本机链路读取已安装的 `~/.agents/skills/forecast-edge`，不读取本仓库中的前端、旧展示 Skill或旧通用调度器。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
```

账户地址、凭据、聊天 ID 和私有报告必须通过运行时配置提供，不得写入仓库。
