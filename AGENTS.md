# SWIFT 部署 Agent 规则

本文件是运行/接管 Agent 的最小操作手册；项目背景和研究方法见 `README.md`，实现细节见 `docs/`。生产仓库是私有的 `YoungY620/swift`。不要把本文件扩展成新的交接长文档。

## 零、项目事实与代码入口

- SWIFT 是 venue-neutral 的长期预测市场研究与交易系统。研究核心只通过 `skills/forecast-edge/config/adapters.json` 注册的只读 adapter 访问市场；目前只内置 Polymarket。新增 venue 不得修改共享研究逻辑或交易意图 envelope。
- 生产部署强烈建议使用私有 fork/私有仓库（当前为 `YoungY620/swift`），不要使用公开仓库部署。`YoungY620/swift-public` 仅是关闭 Actions 的只读代码镜像，不得连接 Secrets、Runner、EC2 或真实交易账户；没有私有 fork 时先停止并请求维护者准备，不要把公开仓库临时改成生产入口。
- `.github/workflows/forecast-edge.yml` 是唯一的定时/手动父流程；`forecast-edge-research.yml`、`execute-polymarket.yml`、`forecast-edge-audit.yml` 是 reusable 子流程。本机不运行定时任务。
- Research 用 `kimi --skills-dir "$GITHUB_WORKSPACE/skills"` 加载 `skills/forecast-edge/`。提示词和通知模板在 `automation/`，交易意图 Schema 在 `schemas/trade-intent.schema.json`，研究校验入口是 `scripts/validate_trade_intents.py` 与 `scripts/validate_research_iteration.py`。
- 执行代码位于 `execution/`、`scripts/polymarket_execution.py` 和 `scripts/run_with_polymarket_secrets.py`；账户认证与凭据初始化规则见 `docs/polymarket-authentication.md`。
- 每轮都必须生成并校验 `trade-intents.jsonl`；无动作时保留合法零字节文件。每条意图必须包含 `venue`、`multi_market_identifier`、`instrument_identifier`，并通过 Schema、adapter 与账户快照校验。
- 中间 handoff 只在同一私有 workflow 内传输；有条目的意图只在 execution 完成后随最终 Release 发布。零意图轮次只在 manifest 中记录摘要和 `has_items=false`，不上传空 Release asset。
- 账户标识、profile URL、钱包地址、聊天目标、凭据与私有报告全部由运行时受控配置提供，不得写入代码、Repository Variable、日志或公开镜像。

### 运行时依赖和产物

- Research 使用 `automation/prompts/`、`automation/templates/`、`automation/requirements-github-actions.txt`、`schemas/trade-intent.schema.json`、`scripts/validate_trade_intents.py`、`scripts/validate_research_iteration.py` 和 `skills/forecast-edge/`。
- Execution 使用 `execution/`、`scripts/polymarket_execution.py`、`scripts/run_with_polymarket_secrets.py` 与 `automation/requirements-polymarket-execution.txt`。
- 每条意图的 `venue`、`multi_market_identifier`、`instrument_identifier` 共同确定 venue 原生标的；账户、凭据、聊天 ID 和私有报告不得写入代码。
- 定时研究只由私有 GitHub workflow 运行，不依赖个人电脑的 launchd、cron 或 Mac runner。

## 一、不可违反的边界

- 生产 workflow、Secrets、Artifacts、诊断和 Release 只能位于私有 fork/私有仓库；公开镜像保持 Actions 关闭且永不承载生产配置或运行。
- 使用 **Kimi Code Agent**（`@moonshot-ai/kimi-code` 及 workflow 固定版本），不是 Kimi CLI、普通 LLM HTTP 调用或一次性模型回答。
- 父流程固定为：`gate → research → execution → release → audit → breaker/notify`。Research 和 audit 在 GitHub-hosted `ubuntu-latest`；真实交易只在长期 Ubuntu EC2 self-hosted runner。
- Research 不连接 EC2，不创建 Release/Issue，不发 Feishu。交易意图只通过同一 parent run 的 SHA-256 绑定私有 Actions artifact 传递；execution 结束前不得公开报告、意图或交易结果。
- Release 必须在 execution 成功（或明确的非生产 research-only）后创建；audit 在 Release 之后运行且不能阻止本轮已完成的发布。每轮无论成功、warning、失败或熔断都必须通知 Feishu。
- Feishu 最终状态卡未触发熔断时使用绿色；已触发或需要熔断时使用红色。
- 不用 VPN、代理、地域绕过或网页登录 cookie 规避 Polymarket 地区/账户限制。geoblock、账户健康或认证不满足时安全失败。

### 凭据边界

- Polymarket 私钥、账户钱包、wallet type 和 CLOB L2 credentials 只允许存在 EC2：
  `/home/ubuntu/poly-edge-live/runtime/secrets.json`。
- 文件必须是 `ubuntu:ubuntu`、权限 `0600`、非符号链接；不得进入 GitHub Secret、Git、artifact、Release、Issue、Feishu、shell history 或命令参数。
- 该文件只接受 canonical 字段：
  `POLYMARKET_ACCOUNT_WALLET`、`POLYMARKET_EXPECTED_WALLET_TYPE`、
  `POLYMARKET_SIGNER_PRIVATE_KEY`、`POLYMARKET_CLOB_API_KEY`、
  `POLYMARKET_CLOB_API_SECRET`、`POLYMARKET_CLOB_API_PASSPHRASE`。
- Relayer API key、Builder key 不能代替 signer 或 CLOB credentials；Google/email/wallet 登录方式不能直接推断 wallet type。使用仓库的 `scripts/bootstrap_polymarket_credentials.py` 和官方 SDK 校验 `EOA`、`POLY_PROXY`、`GNOSIS_SAFE`、`DEPOSIT_WALLET`。
- Judge 绝不接收上述任何凭据、SSH key、AWS secret、环境凭据或登录态。

### EC2 执行边界

- Runner labels 必须同时包含：`self-hosted`, `Linux`, `X64`, `forecast-edge`, `polymarket-execution`, `ec2`, `eu-north-1`。
- 业务目录、运行目录、诊断目录分别为：
  `/home/ubuntu/poly-edge-live/repo`、`/home/ubuntu/poly-edge-live/runtime`、
  `/home/ubuntu/poly-edge-live/diagnostics`。不要让 Mac SSH、Mac runner 或旧复制目录参与生产执行。
- 执行顺序必须是：校验 artifact → 取消全部未成交挂单并复查为零 → preflight → 最新盘口规划 → 价格保护 FOK 市价单 → verify（余额、成交、持仓、费用、健康、开放挂单）。
- `FULL` 是完整成交；只有有余额/成交/挂单未变化证据的 FOK `NO_FILL` 可为 warning。`UNCERTAIN`、`PARTIAL`、保护越界、未知拒单、残留挂单或秘密扫描失败不得伪装成功。
- 零意图也必须完成上述生命周期并生成 `NO_ACTIONS` 回执和诊断；不能以 `has-intents=false` 跳过 execution。

### Judge 与熔断

- Judge 必须是真正的 Kimi Code Agent：用 `Read`/`Grep`/`Glob` 读取证据，至少调用一次只读 `explore` subagent，主 Agent 在返回后复核；无工具事件的一次 JSON 回答不是有效审计。
- Judge 只能读取私有诊断和公开技术文档，不能执行命令、访问交易 API、签名、下单或撤单。
- 只有明确的资金/订单安全异常（不确定成交、部分成交、价格保护失败、残留挂单、秘密泄露等）才触发三重熔断：
  `POLYMARKET_LIVE_TRADING_ENABLED=false`、`FORECAST_EDGE_AUTOMATION_PAUSED=true`、禁用父 workflow。
- 配额/认证错误、Judge timeout、证据不足、普通研究质量问题和确定性 FOK `NO_FILL` 默认是 warning；不能为了“跑通”放宽安全校验。

## 二、空服务器部署

1. 先做不泄密的工具/身份检查：

   ```bash
   uname -a; git --version; python3 --version; node --version || true
   gh --version || true; gh auth status
   ```

   未登录时使用组织批准的设备登录或受控凭据注入；不要把 PAT 放在 URL、参数、脚本或日志中。

2. 使用新目录从私有 fork/私有仓库的批准分支（通常 `main`）克隆；确认远端不是 `swift-public`。发现旧目录、旧 runner、`repo/` 或 `poly-edge-smoke/` 时只列清单，不复用或覆盖。需要保留的未提交修改先以不含秘密的 patch/bundle 备份到仓库外的 `0700` 目录。

   ```bash
   git clone --single-branch --branch main \
     https://github.com/YoungY620/swift.git /home/ubuntu/poly-edge-live/repo
   cd /home/ubuntu/poly-edge-live/repo
   git status --short --branch
   ```

3. 在 EC2 安装 `jq`、`python3`、`sha256sum`、Bash 4+；注册 GitHub self-hosted runner，确认服务用户为 `ubuntu`、labels 完整，registration token 只用于注册且不持久化。Runner 目录由 GitHub runner 安装器/服务使用；业务目录和 `secrets.json` 不是 runner 自动生成的。

4. 仅在 EC2 受控终端写入 `secrets.json`，用官方 SDK 初始化/派生 CLOB credentials；检查 owner/mode/symlink，绝不打印字段值。生产 execution 不需要把 Polymarket 凭据保存到 GitHub。

5. GitHub Actions 只配置名称和存在性，不在日志输出值：

   **Secrets**：`KIMI_CODE_API_KEY`、`FORECAST_EDGE_MARKET_ACCOUNTS_JSON`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_CHAT_ID`、`FORECAST_EDGE_BREAKER_TOKEN`。

   **Variables**：`FORECAST_EDGE_MARKET_ADAPTERS`、`FORECAST_EDGE_HISTORY_RELEASE_COUNT`、`POLYMARKET_LIVE_TRADING_ENABLED`、`FORECAST_EDGE_AUTOMATION_PAUSED`、`POLYMARKET_MAX_PRICE_DEVIATION_BPS`、`FORECAST_EDGE_KIMI_MODEL_NAME`，以及 workflow 中声明的可选发布开关。`FORECAST_EDGE_BREAKER_TOKEN` 只给 gate/熔断，使用最小仓库写权限。

6. 先验证源码契约和测试，再做手动 production run；只允许在所有配置、runner、网络合规和 EC2 凭据检查通过后开启真实下单。检查每个 job 的 runner、结论、Release、audit 和 Feishu，不以顶层成功代替交易回执/核验。

   ```bash
   python3 scripts/forecast_edge_workflow_contract.py
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
   bash skills/forecast-edge/tests/test_cli.sh
   python3 -m compileall -q execution scripts skills/forecast-edge
   git diff --check
   ```

## 三、历史问题与固定修复

| 问题 | 症状 | 现在的规则 |
| --- | --- | --- |
| Kimi Code 与 Kimi CLI 混淆 | 错误命令、endpoint、模型或额度判断失真 | 固定 Kimi Code 版本/provider；保留模型 probe、canary、stream/stderr/session/sub-agent 诊断 |
| 研究长时间无输出 | 被误判卡死或重复触发 | 研究可持续较久；检查 stream、task output 和 session 事件，使用 idle timeout，不用短超时杀掉正常研究 |
| Research/执行跑错机器 | Research 被放到 EC2，或 Mac SSH 中转 | Research 只用 GitHub Ubuntu；execution 只用指定 EC2 labels |
| 定时任务“跳过” | 小时唤醒后被 gate 正常跳过 | 直接使用 UTC `23 2,10,18 * * *`，每次都是完整 8 小时 iteration；schedule 的必要 job 不得 skipped |
| 研究有意图却跳过执行 | `has-intents` 成为 execution gate | 零意图也执行撤单、preflight、空 plan、`NO_ACTIONS`、verify、诊断 |
| 看到卖出却找不到买入/顶层显示成功 | 逐意图状态没有和 planner、ledger、receipt 对齐 | 事后沿 `intent index → planner → CLOB/ledger → receipt → verification` 追踪；每张订单必须有唯一终态 |
| 未成交挂单残留 | 下一轮受到旧订单影响 | 每轮第一步取消全部 open orders，preflight 必须为零 |
| 人工设置无意义最小额度 | 小额意图被错误放大或拒绝 | 只遵守官方订单簿最小值和 BUY 约 1 pUSD 门槛；风险上限与交易所门槛分开，不为凑单放大意图 |
| 价格保护误拒绝更好成交 | 用绝对偏差判断价格 | 只计算不利方向偏差；FOK protected market 不追价、不留余量 |
| 研究/意图过早泄露 | execution 前创建 Release/Issue 或通知 | 只在同轮私有 artifact 交接；execution 结束后才 Release，最后 audit/notify |
| Audit 只是一次 LLM 调用 | 没有读证据或独立复核 | 使用 Kimi Code Agent + `Read` + `explore`，按 rubric 输出单一 JSON |
| 熔断标准过严 | quota、timeout、普通质量问题停掉定时任务 | 安全异常 fatal；可恢复质量/基础设施问题 warning，资金安全不能降级 |
| Release 清单比较失败 | Bash 数组/空格导致“报告不一致” | 对排序后的文件名使用 `LC_ALL=C sort -u` + `cmp -s`，失败再 `diff -u` |
| Actions Node 20 弃用告警 | workflow 运行环境提示旧 Node | 使用仓库当前 Node 24 兼容的 action 版本；告警不等于业务失败 |
| 诊断信息缺失 | 只能看到顶层失败，无法定位买卖差异 | research/execution 都保存 stream、phase events、plan、preflight、receipt、verification、ledger 摘要；上传前秘密扫描 |
| 账户认证猜错 | Relayer key 被当作下单 key，或按 Google/email 猜 wallet type | signer、account wallet、wallet type、CLOB L2 分开核对；不把网页登录密码/cookie交给 Agent |
| AWS 网络/地区限制 | geoblock、closed-only、API 不可用 | 记录健康检查并安全失败；不使用 VPN/代理绕过合规限制 |
| 公开镜像残留旧对象 | 旧 commit、运行记录或同步脚本仍可达 | 公开仓库重建后只保留 `main`，Actions 关闭、无 secrets/variables/runs；不使用旧同步脚本 |

## 四、排障最小证据集

- Research：Kimi Code stream/stderr、session/sub-agent `wire.jsonl`、task output、校验日志、artifact SHA-256。
- Execution：`phase-events.ndjson`、install/validate/cancel/preflight/plan/execute/verify 日志、`receipt.json`、`verification.json`、`ledger-snapshot.json`、诊断 manifest/summary。
- 只在私有 artifact 或 EC2 受限目录查看；任何报告、Issue、Feishu 或回复都只能引用脱敏摘要、intent index 和状态，不得输出私钥、签名、token、Authorization header、cookie 或完整账户标识。
- 缺少必要证据时写 `INSUFFICIENT_EVIDENCE`，不得猜测为 0、成功或已成交。

## 五、完成标准

只有同时满足以下条件才可报告部署完成：测试和源码 contract 通过；research 在 GitHub Ubuntu 成功；execution 在正确 EC2 成功且有完整 receipt/verification/ledger/诊断；Release 在 execution 后创建；Judge 实际使用 Agent 工具并通过 rubric；Feishu 收到最终状态；没有秘密泄露；至少观察到一轮 schedule 的所有必要阶段均成功。否则报告 `BLOCKED` 或 `INSUFFICIENT_EVIDENCE`，并写明缺失证据。
