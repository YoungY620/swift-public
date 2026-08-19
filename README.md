# Poly-edge

Poly-edge 是面向多预测市场的长期概率优势研究与交易意图生成项目。研究核心通过只读 venue adapter 获取市场数据，不默认绑定任何市场；当前注册表只内置 Polymarket adapter，后续可以在不修改共享研究流程或交易意图 envelope 的前提下增加其他市场。

## 自动化入口

- `.github/workflows/forecast-edge-research.yml`：定时研究、报告归档、交易意图校验、Release、Issue 与飞书通知。

研究 workflow 在 GitHub 托管的 `ubuntu-latest` runner 上，通过 `kimi --skills-dir "$GITHUB_WORKSPACE/skills"` 直接加载 `skills/forecast-edge`。它依赖：

```text
automation/prompts/                 扫描与修复提示词
automation/templates/               通知模板
automation/requirements-*.txt       workflow Python 依赖
schemas/trade-intent.schema.json    交易意图协议
scripts/validate_trade_intents.py   研究产物校验
scripts/validate_research_iteration.py 多 venue 单轮交付校验
skills/forecast-edge/               核心研究 Skill
```

部署时必须显式配置 `FORECAST_EDGE_MARKET_ADAPTERS`，并通过 `FORECAST_EDGE_MARKET_ACCOUNTS_JSON` 为每个启用的 adapter 提供一个公开账户输入。核心 workflow 不提供默认 venue，也不读取任何 venue 专属的兼容账户变量。

每轮 research 必须生成并校验 `trade-intents.jsonl`；即使没有满足策略门槛的操作，也必须产出合法的空文件。只有通过统一 Schema、adapter 标识和账户快照校验的 JSONL 才会上传为 artifact，并用于 Issue 与飞书通知。

## 本机定时任务

本机 `launchd` 任务 `forecast-edge-scan` 每天北京时间 08:00 和 20:00 执行 `~/.forecast-edge-cron/run.sh`。这条本机链路读取已安装的 `~/.agents/skills/forecast-edge`，不读取本仓库中的前端、旧展示 Skill或旧通用调度器。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
```

每条交易意图都显式包含 `venue`、`multi_market_identifier` 和 `instrument_identifier`；三者共同确定意图指向的 venue 原生标的。账户标识、凭据、聊天 ID 和私有报告必须通过运行时配置提供，不得写入仓库。仓库不包含真实下单 workflow 或执行代码。
