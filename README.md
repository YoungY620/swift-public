# Poly-edge

Poly-edge 是面向 Polymarket 的长期概率优势研究与交易意图生成项目。仓库只保留自动化 workflow 所需的研究 Skill、提示词、数据/报告工具、交易意图协议与执行代码。

## 自动化入口

- `.github/workflows/forecast-edge.yml`：定时/手动父流程，串联私有研究、可选交易执行，并在子图全部结束后统一发布 Release、Issue 与飞书通知。
- `.github/workflows/forecast-edge-research.yml`：reusable 私有研究子图，只生成、校验并暂存报告与交易意图，不发布。
- `.github/workflows/execute-polymarket.yml`：reusable 执行子图；仅在显式启用且交易配置齐全时消费私有交易意图。
- `.github/workflows/pages.yml`：按要求保留原实现；当前仓库未配置 `ENABLE_FORECAST_EDGE_PAGES`，不会执行。

研究子图通过 `kimi --skills-dir "$GITHUB_WORKSPACE/skills"` 直接加载 `skills/forecast-edge`，完整研究固定运行在带有 `forecast-edge` 标签的 macOS ARM64 self-hosted runner 上。研究产物先通过 SSH 写入长期 EC2 的逐次私有目录；交易执行完成或被明确跳过前，不创建 Release、Issue、Actions artifact，也不发送飞书通知。它依赖：

```text
automation/prompts/                 扫描与修复提示词
automation/templates/               通知模板
automation/requirements-*.txt       workflow Python 依赖
schemas/trade-intent.schema.json    交易意图协议
scripts/validate_trade_intents.py   研究产物校验
skills/forecast-edge/               核心研究 Skill
```

执行 workflow 直接运行在长期 Ubuntu EC2 的 Linux/X64 self-hosted runner 上，并依赖 `execution/`、`scripts/polymarket_execution.py`、`scripts/run_with_polymarket_secrets.py` 和对应 requirements。Polymarket 私钥与 CLOB credentials 只保存在 EC2 本地 mode-0600 文件中，不进入 GitHub Secrets；workflow 只交接交易意图与摘要。

Polymarket 的 Google/email、外部 wallet、legacy Proxy/Safe 与新 Deposit Wallet 认证映射、凭证初始化和 GitHub Environment 配置见 [docs/polymarket-authentication.md](docs/polymarket-authentication.md)。

## 本机定时任务

本机 `launchd` 任务 `forecast-edge-scan` 每天北京时间 08:00 和 20:00 执行 `~/.forecast-edge-cron/run.sh`。这条本机链路读取已安装的 `~/.agents/skills/forecast-edge`，不读取本仓库中的前端、旧展示 Skill或旧通用调度器。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
```

账户地址、凭据、聊天 ID 和私有报告必须通过运行时配置提供，不得写入仓库。
