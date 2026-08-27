# Poly-edge

Poly-edge 是面向多预测市场的长期概率优势研究与交易意图生成项目。研究核心通过只读 venue adapter 获取市场数据，不默认绑定任何市场；当前注册表只内置 Polymarket adapter，后续可以在不修改共享研究流程或交易意图 envelope 的前提下增加其他市场。

## 自动化入口

- `.github/workflows/forecast-edge.yml`：定时/手动父流程，按 `gate → research → execution → release → audit → notify / breaker` 编排一轮运行。
- `.github/workflows/forecast-edge-research.yml`：reusable 私有研究子图，只生成、校验并暂存报告与交易意图，不发布。
- `.github/workflows/execute-polymarket.yml`：reusable 执行子图；仅在显式启用且交易配置齐全时消费私有交易意图。
- `.github/workflows/forecast-edge-audit.yml`：父流程在 Release 之后直接调用的 reusable 审计子图；Kimi Code Judge 检查完整成果，随后发送最终飞书扫描简报，并在严重安全异常时阻止下一轮。未熔断卡片为绿色，触发或需要熔断时为红色。

研究子图在 GitHub 托管的 `ubuntu-latest` runner 上，通过 `kimi --skills-dir "$GITHUB_WORKSPACE/skills"` 直接加载 `skills/forecast-edge`。研究产物先写入 private-fork 的逐次 Actions handoff artifact；该 artifact 只是同一轮内部的受控传输，不是正式发布。交易执行完成或被明确跳过前，不创建 Release、Issue 或飞书通知。它依赖：

```text
automation/prompts/                 扫描与修复提示词
automation/templates/               通知模板
automation/requirements-*.txt       workflow Python 依赖
schemas/trade-intent.schema.json    交易意图协议
scripts/validate_trade_intents.py   研究产物校验
scripts/validate_research_iteration.py 多 venue 单轮交付校验
skills/forecast-edge/               核心研究 Skill
```

执行 workflow 直接运行在长期 Ubuntu EC2 的 Linux/X64 self-hosted runner 上，并依赖 `execution/`、`scripts/polymarket_execution.py`、`scripts/run_with_polymarket_secrets.py` 和对应 requirements。每次执行先取消账户全部未成交挂单，再使用有价格保护的 FOK 市价单立即成交。Polymarket 私钥与 CLOB credentials 只保存在 EC2 本地 mode-0600 文件中，不进入 GitHub Secrets；workflow 只交接交易意图与摘要。

Polymarket 的 Google/email、外部 wallet、legacy Proxy/Safe 与新 Deposit Wallet 认证映射、凭证初始化和 GitHub Environment 配置见 [docs/polymarket-authentication.md](docs/polymarket-authentication.md)。

部署时必须显式配置 `FORECAST_EDGE_MARKET_ADAPTERS`，并通过同名 Actions Secret `FORECAST_EDGE_MARKET_ACCOUNTS_JSON` 为每个启用的 adapter 提供一个账户输入。账户名、profile URL 和钱包地址即使可公开查询，也属于操作者身份信息，不得写入代码、Repository Variable、日志或 public mirror。核心 workflow 不提供默认 venue，也不读取任何 venue 专属的兼容账户变量。

每轮 research 必须生成并校验 `trade-intents.jsonl`；即使没有满足策略门槛的操作，也必须产出合法的零字节文件。只有通过统一 Schema、adapter 标识和账户快照校验的 JSONL 才能进入私有 handoff。研究与可选执行结束后，有条目的 JSONL 由父 workflow 发布；无条目轮次在最终 manifest 中保留空文件摘要和 `has_items=false`，不向拒绝零字节上传的 GitHub Release API 提交空 asset。

## 本机定时任务

定时研究只通过私有 GitHub 仓库中的工作流运行，不依赖个人电脑上的本地定时任务。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
```

每条交易意图都显式包含 `venue`、`multi_market_identifier` 和 `instrument_identifier`；三者共同确定意图指向的 venue 原生标的。账户标识、凭据、聊天 ID 和私有报告必须通过运行时配置提供，不得写入仓库。真实下单仅由长期 EC2 上的 Polymarket 执行子图完成。
