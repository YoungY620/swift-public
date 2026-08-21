# Forecast Edge 生产交接执行提示词

> 用途：把本文件的完整内容交给另一台服务器上的执行 Agent（必须使用 **Kimi Code**，不是 Kimi CLI），让它从一个没有本地业务文件的环境接管、核对、修复并验证 `YoungY620/poly-edge`。
>
> 安全边界：本文件故意不包含任何 API key、私钥、密码、PAT、SSH key、钱包地址、账户标识或飞书 chat id。执行 Agent 只能从受控的 Secret 管理、GitHub Actions Secret、EC2 本地权限文件和 GitHub API 读取它们；不得要求用户把秘密粘贴到聊天、Issue、日志、命令行参数或代码中。

## 0. 你的角色和最终目标

你是 Forecast Edge 的交接执行 Agent。你的职责不是写一份分析报告，而是实际完成以下闭环，并用可复核证据证明每一步：

```text
配置核对
  -> GitHub-hosted research
  -> SHA-256 绑定的私有 handoff artifact
  -> EC2 self-hosted Polymarket execution
  -> 只有子图完成后才 Release / Issue / Feishu
  -> workflow_run 触发的 Kimi Code Agent Judge
  -> 按 rubric 分级；严重资金安全异常才熔断
  -> 每个真正进入 gate 的轮次都通知 Feishu
```

除非下面的验收条件全部满足，否则不得声称“已完成”。“gate 成功但正常跳过”“只生成了研究”“只做了模拟”“只有一次普通 LLM 回答”“Release 失败”都不算完成。

如果发现交易安全边界不成立、真实提交结果不确定、部分成交、价格保护越界、残留挂单、诊断/秘密扫描失败，立即停止继续下单；不要通过关闭校验、放宽保护、伪造证据或重放未知订单来“修复”。

## 1. 不可违反的安全和产品约束

1. 仓库是 `https://github.com/YoungY620/poly-edge.git`，当前生产形态是 private fork/repository。研究、执行诊断和中间交易意图在 execution 结束前不得进入公开 Release、Issue、飞书或 public mirror。
2. GitHub Actions 的 research 必须运行在 `ubuntu-latest`；不能把 research、Kimi Code 或研究数据转发到 EC2，也不能通过 Mac SSH 中转。
3. Polymarket 真实执行只允许运行在长期 Ubuntu EC2 的 self-hosted runner，runner labels 必须同时包含：

   ```text
   self-hosted
   Linux
   X64
   forecast-edge
   polymarket-execution
   ec2
   eu-north-1
   ```

4. Polymarket signer private key、account wallet、wallet type 和 CLOB L2 credentials 只允许存在于 EC2：

   ```text
   /home/ubuntu/poly-edge-live/runtime/secrets.json
   ```

   文件必须属于 `ubuntu:ubuntu`、权限 `0600`、不能是符号链接。它是执行器唯一的 Polymarket 凭据来源。禁止把这些值保存到 GitHub Actions Secret、Git、artifact、Release、Issue、飞书、shell history 或命令行参数中。

5. Relayer API key 不能代替 CLOB signer；Builder key 也不是普通订单运行时凭据。不要因为网页登录方式是 Google、email、Magic Link 或 wallet，就猜测 wallet type；必须使用官方 SDK 根据 signer 与 account wallet 的确定性关系校验 `EOA`、`POLY_PROXY`、`GNOSIS_SAFE` 或 `DEPOSIT_WALLET`。
6. Kimi Code 和 Kimi CLI 是两个不同概念。本项目使用 `@moonshot-ai/kimi-code` 提供的 `kimi` Agent runtime，固定版本和 provider 见仓库 workflow；不要把其他 Kimi CLI、普通 OpenAI 调用或一次性 HTTP LLM 请求替换进来。
7. Judge 必须是 Agent judge，不是一次普通模型调用。它必须实际使用 `Read` 读取证据，至少调用一次只读 `explore` subagent，再由主 Agent 复核；必须能检查 Kimi Code session/sub-agent/tool 事件和执行链证据。Judge 禁止 Bash、Write、Edit、登录态、钱包、交易 API、凭据和下单。
8. 不使用 VPN、代理或地域绕过来规避 Polymarket 条款、地区限制或合规要求。执行前如 geoblock/账户健康不满足，安全失败并通知，不要绕过。
9. 交易使用受价格保护的 FOK 市价单：先取消全部未成交挂单并复查为零，再按实时订单簿逐档模拟；不追价、不留下余量、不自动补单。
10. 只有确定性且已验证的 FOK `NO_FILL` 才能作为 warning；余额未变化、没有成交、没有开放挂单必须有证据。`UNCERTAIN`、`PARTIAL`、`REJECTED`、保护越界、残留挂单、秘密/诊断校验失败仍是 fatal。

## 2. 空服务器接管流程

假定当前服务器没有本地业务文件，也不存在 `/Users/example/...` 这类路径。绝不依赖另一台机器的工作区、未提交修改或临时目录。

### 2.1 先检查工具和身份，不要打印凭据

先执行只读检查：

```bash
set -euo pipefail
uname -a
git --version
python3 --version
node --version || true
gh --version || true
gh auth status
```

如果 GitHub CLI 未登录，使用组织批准的设备登录、环境注入或 Secret Manager。不要把 PAT 放在 URL、脚本、命令参数、shell history 或输出中。验证时只输出账号/仓库权限摘要，不输出 token 内容。

### 2.2 从远端干净克隆交接分支

不要在未知目录覆盖文件。使用新的工作目录，并明确指定交接分支：

```bash
set -euo pipefail
REPO_DIR="${REPO_DIR:-$PWD/poly-edge}"
if [[ -e "$REPO_DIR" ]]; then
  echo "Refusing to reuse an existing directory: $REPO_DIR" >&2
  exit 1
fi
git clone --single-branch --branch handoff/forecast-edge-go-20260820 \
  https://github.com/YoungY620/poly-edge.git "$REPO_DIR"
cd "$REPO_DIR"
git status --short --branch
git log -1 --oneline --decorate
git remote -v
```

如果交接分支尚未出现在远端，不要自行猜测或从旧服务器复制业务目录；先报告远端分支缺失，等待分支被推送或由维护者明确批准使用指定 commit。克隆后必须确认 `git status` 干净、远端 URL 正确，并记录当前 `HEAD` SHA。

### 2.3 处理旧目录和本地修改

不要使用旧的 `repo/`、`poly-edge-smoke/`、旧 runner 工作区或手工复制的交易脚本。它们若存在，只能先做非破坏性清单和校验；不要让 workflow 引用它们。未经明确批准，不要递归删除用户目录。

如果接管服务器上已经有未提交修改：

1. 停止任何可能运行的旧交易进程和旧 runner service，但先记录 service 状态；
2. 对明确的仓库目录执行 `git status --short`、`git diff`、`git diff --cached`；
3. 将需要保留的修改保存为受限权限的 patch 或 Git bundle，放到仓库之外的备份位置；
4. 不把秘密、`.env`、私钥、shell history 或本地 `secrets.json` 放入 patch/bundle；
5. 只有得到维护者确认后才合并修改到交接分支；不直接覆盖 `main`。

可使用以下无秘密备份方法（目标路径必须是明确的小目录，不得指向 `/`、`$HOME` 或仓库根目录的递归删除目标）：

```bash
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-$PWD/../poly-edge-handoff-backup}"
install -d -m 700 "$BACKUP_DIR"
git diff --binary > "$BACKUP_DIR/uncommitted.patch"
git diff --cached --binary > "$BACKUP_DIR/index.patch"
git bundle create "$BACKUP_DIR/poly-edge-repository.bundle" --all
chmod 600 "$BACKUP_DIR"/*
```

备份完成后再次扫描 patch/bundle 旁的文件名和内容，确认没有凭据；不要把备份路径写入 Release。

## 3. 读取仓库并建立事实基线

进入仓库后先阅读这些文件，不要根据本提示词臆测实现：

```text
README.md
docs/automation.md
docs/github-actions-agent-design.md
docs/polymarket-authentication.md
.github/workflows/forecast-edge.yml
.github/workflows/forecast-edge-research.yml
.github/workflows/execute-polymarket.yml
.github/workflows/forecast-edge-audit.yml
automation/agents/forecast-edge-auditor.md
automation/prompts/forecast-edge-audit.zh-CN.md
automation/audit/forecast-edge-audit-rubric.json
schemas/trade-intent.schema.json
execution/
scripts/
tests/
```

执行以下静态检查并保存结果（只保存不含秘密的摘要）：

```bash
set -euo pipefail
python3 scripts/forecast_edge_workflow_contract.py > /tmp/forecast-edge-source-contract.json
python3 -m compileall -q execution scripts skills/forecast-edge
git diff --check
```

核对以下事实：

- `.github/workflows/forecast-edge.yml` 是唯一 schedule/manual parent；
- parent 在 UTC 02:23、10:23、18:23 直接启动完整 iteration，不使用小时唤醒或取模 gate；
- research 与 audit 在 GitHub-hosted runner；execution 在 EC2 self-hosted runner；
- child workflows 使用 `workflow_call`，不能自行发布 Release/Issue；
- Release、Issue、Feishu 的顺序严格为 Release → Issue → Feishu；
- audit 由 parent 完成后通过 `workflow_run` 触发；每个已完成 parent run 都必须审计并发送最终通知；
- research 诊断和 execution 诊断上传前都经过秘密扫描，执行诊断要包含安装、validate、cancel、preflight、plan、execute、verify 的逐阶段日志；
- trade intent 通过本轮唯一 handoff artifact 和 SHA-256 交接，不读取“最新 artifact”。

## 4. GitHub 配置边界

### 4.1 Actions Secrets（只配置名称，不在输出中显示值）

必须核对下列名称是否存在。只显示存在/缺失，不显示值，不把值传给不需要的 job：

```text
KIMI_CODE_API_KEY
FORECAST_EDGE_MARKET_ACCOUNTS_JSON
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_CHAT_ID
FORECAST_EDGE_BREAKER_TOKEN
```

其中：

- `KIMI_CODE_API_KEY` 只给 research 和 audit 的 Kimi Code 进程；
- `FORECAST_EDGE_MARKET_ACCOUNTS_JSON` 只给 research 的只读账户快照；
- 三个 Feishu secret 只给通知 job；
- `FORECAST_EDGE_BREAKER_TOKEN` 只给 gate/熔断步骤，不传给 Judge；
- 任何 `POLYMARKET_SIGNER_PRIVATE_KEY`、`POLYMARKET_CLOB_API_*`、EC2 SSH key、AWS secret 都不应出现在 workflow secret 清单中。

### 4.2 Repository Variables

只核对名称和值的安全范围，不在日志中打印账户、钱包、chat id 或凭据：

```text
FORECAST_EDGE_MARKET_ADAPTERS          必填；逗号分隔，必须来自 adapters.json
FORECAST_EDGE_HISTORY_RELEASE_COUNT    2..20；默认 2
POLYMARKET_LIVE_TRADING_ENABLED        生产目标为 true
FORECAST_EDGE_AUTOMATION_PAUSED        生产目标为 false
POLYMARKET_MAX_PRICE_DEVIATION_BPS     受控风险参数；不要无审查放宽
CREATE_TRADE_INTENT_ISSUE              按 private repo 策略决定
ALLOW_PUBLIC_TRADE_INTENTS              private repo 通常保持 false
ALLOW_PUBLIC_RESEARCH_RELEASES          private repo 通常保持 false
```

如果缺少配置，parent gate 应成功并安全跳过 downstream；不要为了“让它跑起来”把缺少的 Secret 写进代码或把 live trading 强行打开。

### 4.3 Environment 和权限

确认 execution 使用 GitHub Environment `polymarket-production`，必要时启用 required reviewers。确认仓库 Actions workflow permissions 允许 parent 创建 Release；熔断 token 具有最小的 Actions 写权限，可修改 repository variables 并禁用 parent workflow，但不能被 Agent 读取为交易凭据。

## 5. EC2 self-hosted runner 配置

只在依法获准用于真实 Polymarket 执行的 EC2 上做这一步。研究不要安装到 EC2。

### 5.1 Runner 目录和服务

runner service 可以使用自己的 runner 工作目录；业务仓库建议固定在：

```text
/home/ubuntu/poly-edge-live/repo
```

运行时目录和长期诊断目录是：

```text
/home/ubuntu/poly-edge-live/runtime
/home/ubuntu/poly-edge-live/diagnostics
```

不要把 `secrets.json` 放在 runner workspace。检查 runner service 用户必须是 `ubuntu`，并确认该 service 使用 labels：

```bash
set -euo pipefail
id
uname -s
uname -m
command -v jq
command -v python3
command -v sha256sum
command -v bash
```

通过 GitHub API 或 Actions job evidence 核对 labels，不要把 runner registration token 写入仓库或日志。registration token 是短期值，不能当作永久凭据。

### 5.2 EC2 本地 Polymarket secret file

只通过受控终端在 EC2 写入以下 JSON 文件；不要通过 GitHub、SSH 命令行拼接、聊天或 workflow 传输私钥：

```text
/home/ubuntu/poly-edge-live/runtime/secrets.json
```

文件包含且只包含当前执行器接受的 canonical 字段：

```text
POLYMARKET_ACCOUNT_WALLET
POLYMARKET_EXPECTED_WALLET_TYPE
POLYMARKET_SIGNER_PRIVATE_KEY
POLYMARKET_CLOB_API_KEY
POLYMARKET_CLOB_API_SECRET
POLYMARKET_CLOB_API_PASSPHRASE
```

文件约束：

```bash
set -euo pipefail
SECRET_FILE=/home/ubuntu/poly-edge-live/runtime/secrets.json
test -f "$SECRET_FILE"
test "$(stat -c '%U' "$SECRET_FILE")" = ubuntu
test "$(stat -c '%a' "$SECRET_FILE")" = 600
test ! -L "$SECRET_FILE"
```

使用仓库的 `scripts/bootstrap_polymarket_credentials.py` 初始化或派生 CLOB credentials 时，必须先核对 account wallet 与 signer address；工具不会部署钱包、增加 allowance 或下单。初始化输出只允许公开的 wallet type/signature type/sdk version，禁止输出任何 secret 字段。

如果账户是 Google/email/旧 Magic Link，使用官方 recovery 流程得到 signer，并核对 signer address；不要把 Google 密码、cookie、验证码或助记词交给 Agent。硬件钱包或不可导出的 signer 不适合当前无头 Python SDK 路径，应停下并请求专门的远程签名设计。

## 6. 代码和交易语义验收

### 6.1 Research

- 每个启用 adapter 必须生成且只生成一份当前轮报告；历史 Release 只能作为滚动证据，不能冒充本轮报告；
- 必须重新研究全部当前持仓，并按 Skill 规则研究足够的新事件；
- `trade-intents.jsonl` 必须始终存在：零字节代表无动作，是合法结果；
- 每行必须包含 `venue`、`multi_market_identifier`、`instrument_identifier`，并通过 schema、账户快照、持仓和跨字段数值校验；
- `book.min_order_size` 必须读取官方订单簿，不允许 Agent 自定义或覆盖最小订单；
- research 只上传私有 handoff 和研究诊断 artifact，不创建 Release/Issue，不发送 Feishu；
- Kimi Code stream、stderr、session/sub-agent `wire.jsonl`、后台 task `output.log` 和校验日志要保留在诊断中；上传前使用脱敏器和二次扫描，不能为了通过扫描而放宽秘密模式。

### 6.2 Execution

执行器必须按以下顺序，并在 diagnostics 中逐阶段留下证据：

```text
validate-artifact
  -> cancel-open-orders
  -> preflight（open_order_count 必须为 0）
  -> plan（价格/金额/份额/现金/费用保护）
  -> execute（FOK protected market）
  -> verify（余额、成交、持仓、费用、健康、开放挂单）
```

买单的安全份额取研究建议份额、保护价格下可买份额、可用现金扣除储备和费用后的可买份额三者最小值，并按精度向下取整。实时逐档 VWAP 与研究建议价格的不利偏差超过 `POLYMARKET_MAX_PRICE_DEVIATION_BPS` 时拒绝提交；不设置重复的金额偏差阈值。

每张回执的安全规则：

- 正常完成：`status=FULL`；
- 可警告无成交：`status=NO_FILL` 且 `code=FOK_NOT_FILLED`，并有余额/成交/挂单未变化证明；
- 顶层 `COMPLETED_WITH_WARNINGS` 只允许上述两类回执；
- `UNCERTAIN`、`PARTIAL`、`REJECTED`、未知 no-fill、价格/金额/份额保护失败或任意残留挂单都不允许发布为成功。

### 6.3 Release 和通知

父 workflow 只有在：

1. research 成功；
2. production execution 成功；零意图也必须完成 EC2 撤单、preflight、空 plan、`NO_ACTIONS` 回执、verify 和诊断，不能 skipped；
3. 私有 handoff、digest、receipt、verification 和诊断校验成功；

之后才能创建唯一最终 Release。报告文件清单必须用排序文件和 `cmp`/`diff` 比较，不能用容易受 Bash 数组空格影响的字符串比较。Release 成功后才允许创建 Issue 和发送 post-release Feishu；执行失败时不得发布未成交的研究/意图。

每个父轮次无论成功、warning、失败或熔断，都要有一条 Feishu 状态通知；不存在正常的 hourly gate-off。

## 7. 已知基线问题和修复要求

原 `main` 基线为 `7077151`。Run `32362778833` 已证明 research 和 EC2 真实 execution 成功，但 Release 在 `Build final iteration manifest` 前的报告清单比较处失败：

```text
Private handoff direct reports differ from the signed manifest
```

交接分支应包含以下修复；接管后必须确认它们仍在源码中并测试覆盖：

1. 把 actual/expected direct report 名称分别写入排序文件，使用 `LC_ALL=C sort -u` 和 `cmp -s`，失败时输出 `diff -u`；
2. Release 接受经过 execution verification 的 `COMPLETED_WITH_WARNINGS`，但只接受 `FULL` 和带 `FOK_NOT_FILLED` 证明的 `NO_FILL`；
3. 增加 workflow contract 和单元测试，覆盖 warning receipt、拒绝未知/部分状态、报告清单比较和直接八小时 schedule（不得出现正常 gate skip）；
4. 不删除 Release 完整性校验，不把所有 execution failure 改成 warning。

该运行的历史结论只能作为诊断证据，不能直接宣布当前部署成功。修复后必须重新跑完整 parent。

## 8. 测试、提交和推送

先在干净 checkout 中运行：

```bash
set -euo pipefail
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
python3 scripts/forecast_edge_workflow_contract.py
python3 -m compileall -q execution scripts skills/forecast-edge
git diff --check
```

还要验证 workflow YAML、内嵌 Bash 和 GitHub Actions expression；优先使用仓库已有 contract/test，若环境提供 `actionlint` 可额外运行。所有测试失败必须记录真实原因并修复，不得删测试或仅修改断言。

提交规则：

- 交接修改优先提交到 `handoff/forecast-edge-go-20260820`，不要把未验证代码覆盖 `main`；
- commit message 简洁描述实际修改；
- 不得包含 `Co-Authored-By`、`Signed-off-by`、Codex、AI 或自动化工具署名；
- 不修改 Git author/committer 配置；
- 绝不提交 secret、私钥、密码、token、`.env`、真实账户文件或诊断原文；
- push 前执行 `git diff --cached --check`、文件名检查和秘密扫描；
- push 后用 `git ls-remote origin handoff/forecast-edge-go-20260820` 验证远端分支存在。

## 9. 手动真实验证

在确认 GitHub Secret/Variable、Environment、EC2 secret file、runner labels 和合规网络均正确后，才允许手动触发一次真实 execution：

```bash
set -euo pipefail
gh workflow run forecast-edge.yml \
  --repo YoungY620/poly-edge \
  --ref handoff/forecast-edge-go-20260820 \
  -f execute-live-trades=true \
  -f research-only=false
```

随后只通过 GitHub API/CLI 查看 run id、job conclusion、runner label、step 名称和不含秘密的失败摘要：

```bash
set -euo pipefail
gh run list --repo YoungY620/poly-edge \
  --workflow forecast-edge.yml --limit 5
gh run watch RUN_ID --repo YoungY620/poly-edge --exit-status
gh run view RUN_ID --repo YoungY620/poly-edge --json status,conclusion,jobs,url
gh run view RUN_ID --repo YoungY620/poly-edge --log-failed
```

验证以下链路：

```text
gate=success
research=success（ubuntu-latest）
execution=success（EC2 self-hosted labels 全部匹配）
release=success
audit select/audit/notify=success
```

如果 execution 失败，优先下载 private execution diagnostics 和 research diagnostics，在本地受限目录检查：

```text
phase-events.ndjson
install-command.log
validate-command.log
cancel-command.log
preflight-command.log
plan-command.log
execute-command.log
verify-command.log
diagnostic-summary.json
diagnostic-manifest.json
receipt.json
verification.json
ledger-snapshot.json
```

禁止把这些文件原样复制到公开 Issue、聊天或本提示词。事后诊断时逐条追踪：原始 intent index → planner disposition → CLOB/ledger → receipt → verification；不能因为顶层 `COMPLETED` 就假定所有意图都成交。

## 10. 验证 Judge 是真正的 Agent

检查 audit job 使用：

```text
@moonshot-ai/kimi-code（固定版本）
kimi --agent-file ...
--output-format stream-json
--trace-output ...
```

并满足：

- `automation/agents/forecast-edge-auditor.md` 明确列出 `Read`、`Grep`、`Glob`、`Agent`，并只允许 `explore` subagent；
- stream 中实际出现 `Read` tool call 和 `Agent` tool call，以及相应 tool result；
- 主 Agent 在 subagent 返回后自行复核；
- 只有一次普通 assistant JSON、没有工具事件时，必须判为 `INSUFFICIENT_EVIDENCE`，不能判 PASS；
- Judge 不能接收交易 private key、CLOB secret、SSH key、AWS secret、环境凭据或登录态；
- Judge 可用 `WebSearch`/`FetchURL` 仅核对公开技术文档，不能访问 authenticated trading service；
- Judge 输出必须是符合 schema 的单一 JSON object，不能包含秘密或 Markdown 包装。

审计分级：

```text
PASS       所有适用检查有证据且通过
WARN       可操作但不构成资金安全异常，例如确定性 FOK NO_FILL、Judge timeout、证据不足或质量问题
FAIL       明确违反关键安全/执行/发布契约
INSUFFICIENT_EVIDENCE 适用证据缺失，不能当作 PASS
NOT_APPLICABLE         仅用于显式 research-only 等确实不适用的检查，不能用于 schedule 的必要阶段
```

只有经过 Agent 判断且被分类为 critical 的资金安全 FAIL，或确定性的 `UNCERTAIN`、部分成交、保护越界、残留挂单、非法 no-fill、回执/诊断/秘密校验失败，才允许自动执行三重熔断：

```text
POLYMARKET_LIVE_TRADING_ENABLED=false
FORECAST_EDGE_AUTOMATION_PAUSED=true
禁用 .github/workflows/forecast-edge.yml
```

Judge timeout、无效模型输出、证据不足和普通研究质量问题只能 warning；不能因为这些问题把定时任务永久停掉，也不能为了让任务“顺利”把资金安全问题降级。

## 11. 定时任务验收

手动验证成功后不要把它当作 scheduled 成功。定时任务在 UTC 02:23、10:23、18:23 直接进入完整 production iteration。必须等待并检查一轮真实 scheduled run，至少看到：

```text
event=schedule
gate success 且 should-run=true
research success
execution success（live trading 必须开启；即使零 intents 也执行完整生命周期）
release success
audit success
Feishu notification success
```

如果 scheduled run 的 research、execution 或 release 被 skip，这是异常，不能当作正常时间 gate。若上一轮 audit 触发熔断，先停止下单并根据证据修复；只有维护者确认后才恢复两个生产变量和 workflow。

## 12. 最终交接报告格式

结束时输出一份不含秘密的交接报告，必须包含：

1. `repository`、交接分支、最终 commit SHA、是否推送远端；
2. 服务器是否从空目录 clone，旧目录是否被使用（应为否）；
3. runner labels、研究 runner 与执行 runner 的分离证据；
4. 只列出 Secret/Variable **名称及存在性**，不列值；
5. EC2 `secrets.json` 是否存在、owner/mode/symlink 检查结果；绝不输出字段值；
6. 测试命令和通过数量；
7. 手动 run URL/id，各 job conclusion、Release、Audit、Feishu 结果；
8. 实际发生的每个交易结果只能引用脱敏 receipt/verification：intent index、side、状态、filled/no-fill、价格保护结论；不要输出私钥、签名、CLOB secret、cookie、Authorization header 或完整账户标识；
9. scheduled run 是否真实执行全部必要阶段；任一必要 job skipped 都要判为未通过；
10. 仍未完成的事项、风险和下一步；不能用“看起来正常”替代证据。

如果任一关键证据缺失，报告必须明确写 `BLOCKED` 或 `INSUFFICIENT_EVIDENCE`，并说明需要谁提供什么外部状态；不要编造成功。

## 13. 结束条件

只有当以下条件同时成立时才结束交接：

- 交接分支已经推送，现有代码和修复可从远端重新 clone；
- 所有测试和静态 contract 通过；
- research 在 GitHub-hosted runner 成功；
- execution 在指定 EC2 runner 成功，且真实 FOK 结果有 receipt、verification、ledger 和诊断证据；
- Release 在 execution 结束后成功创建；
- Judge 真正使用 `Read` + `Agent/explore`，按 rubric 输出合法结果；
- Feishu 收到该轮最终状态；
- `POLYMARKET_LIVE_TRADING_ENABLED=true`、`FORECAST_EDGE_AUTOMATION_PAUSED=false` 仅在没有 critical safety finding 时保持；
- 至少一轮直接触发且没有必要阶段 skipped 的 8 小时 scheduled iteration 被观测并验证；
- 没有任何秘密被提交、上传、发布或输出。

任何一项不满足，都要停在可恢复状态并把真实阻塞原因交接给维护者。
