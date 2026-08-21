# Forecast Edge 生产验收契约

本文定义“完整成功”的唯一含义。静态测试、手工运行、历史 Release 或某个子阶段成功都不能替代真实 `schedule` 生产轮次。

## 调度与必经图

- 父 workflow 是 `.github/workflows/forecast-edge.yml`。
- cron 固定为 `23 2,10,18 * * *`，即 UTC 02:23、10:23、18:23，每 8 小时一次。
- 每个 `schedule` 事件都是完整生产 iteration；不存在正常的小时 wake-up/gate-off。
- `concurrency.cancel-in-progress=false`；上一轮耗时较长时下一轮排队，不取消任何一轮。
- schedule 必须启用 live trading。配置缺失、生产暂停或 live trading 关闭时 gate 明确失败，不能以成功结论加 downstream `skipped` 结束。

一次合格 schedule 必须按顺序得到以下 job 结论：

| 阶段 | Runner | 必须结论 | 权威证据 |
| --- | --- | --- | --- |
| gate | GitHub-hosted Ubuntu | `success` | parent jobs API、manifest schedule |
| research | GitHub-hosted Ubuntu | `success` | research handoff、validation summary、研究诊断 |
| execution | EC2 self-hosted Linux/X64 | `success` | receipt、verification、执行诊断、SQLite ledger 摘要 |
| release | GitHub-hosted Ubuntu | `success` | 唯一 Release 与 iteration manifest |
| audit | GitHub-hosted Ubuntu | `success` | Agent Judge JSON、tool trace、audit contract、诊断 artifact |
| notify | GitHub-hosted Ubuntu | `success` | 飞书 API 返回有效 message id |

零交易意图不改变这个图。execution 仍须完成 artifact 校验、取消全部旧挂单、authenticated preflight、空 plan、`NO_ACTIONS` 回执、verify 和诊断上传。只有显式手工 `research-only` 才允许 execution 为 `skipped/NOT_APPLICABLE`，且不能用来证明 schedule 成功。

## 确定性运行验收

`scripts/verify_forecast_edge_production_run.py` 对同一父 run 的以下输入做绑定检查：

1. parent conclusion 及 gate/research/execution/release 四个必要 job；
2. manifest 的 run id、attempt、commit 和 schedule/execution 字段；
3. receipt 只含 `FULL` 或有 `FOK_NOT_FILLED` 证明的 `NO_FILL`，或合法 `NO_ACTIONS`；
4. 事前撤单后 `remaining_open_order_count=0`；
5. `install`、`validate`、`cancel`、`preflight`、`plan`、`execute`、`verify` 七阶段均存在且 exit code 为 0；
6. 最终 verification 的 `open_order_count=0` 且每个订单 `open=false`；
7. 执行诊断通过秘密扫描且没有 failed phase。

结果写入 audit evidence 的 `production_acceptance`。`accepted=false` 会使 audit 明确失败，Judge 不得用文字判断覆盖它。

## 历史讨论结论的验收矩阵

| 需求 | 必须成立的实现 | 运行时证明 |
| --- | --- | --- |
| 研究与执行隔离 | research 只用 `ubuntu-latest`；EC2 只执行交易 | job runner/labels |
| 私钥边界 | Polymarket 私钥/CLOB 凭据只在 EC2 mode-0600 `secrets.json` | EC2 本地 owner/mode/symlink 检查；GitHub Secret 名称清单无交易私钥 |
| 下单前撤单 | 首个交易动作前取消账户全部 open orders 并复查为 0 | cancellation receipt、phase 顺序 |
| 市价成交保护 | 使用 FOK protected market order，不留下挂单、不追价 | receipt order type/status、verification |
| 金额/份额/滑点 | 份额取建议份额、建议金额约束和现金约束最小值；只计算不利价格偏差 | plan、单元测试、receipt |
| 官方最小额 | 只执行 Polymarket 官方 marketable BUY 最小 1 pUSD；不足则 skip，不扩大建议金额 | plan skipped code |
| 提交前整批预验证 | 所有新订单的重规划、allowance、本地签名、SDK 舍入及实时最小额全部通过后才允许第一笔 POST；预签名不落 ledger | execution regression tests + execution diagnostics |
| 确定性拒绝 | CLOB 明确拒绝记录为 `REJECTED/FAILED`，不能误判 `UNCERTAIN` | receipt/ledger/诊断 |
| 报告质量 | current positions 与 candidates 分开；候选理由市场专属；研究理由和执行理由分离 | report validator、最终报告、Judge |
| 历史新颖性 | event/market/multi-market 与累积 previously-researched 集合核对 | research history、报告 novelty 字段 |
| 发布顺序 | research/意图在执行前不发布；execution 成功后统一 Release/Issue | job 时间、Release published_at、manifest |
| 诊断 | 研究与执行的原始 stream/阶段日志经秘密扫描后保留 90 天 | diagnostics artifact 与 manifest |
| 飞书 | 每轮成功或失败都通知；无熔断绿色、实际熔断红色；Warning/Error 可折叠 | notify job 与卡片 artifact/API 结果 |
| 实际交易说明 | 逐市场列出成交、NO_FILL、SKIPPED、REJECTED、缺失回执和白名单失败根因 | execution brief |
| Judge Agent | 使用 Kimi Code Agent，真实 `Read` + `Agent/explore` 工具链，不是单次 LLM 文本 | raw stream、trace、schema-valid JSON |
| 熔断 | 只有资金安全、订单保护、残留挂单或秘密泄露触发；研究质量/基础设施为 Warning/Error 但不熔断 | rubric classification、变量与 workflow 状态 |
| 本机去重 | Mac launchd 和 Mac runner 不参与调度 | 本机服务清单、GitHub runner API |

任何密钥、密码、私钥、签名原文、Authorization header 或完整凭据不得进入上述证据。

## 最终完成条件

只有一轮由当前 `main` 触发的真实 schedule 同时满足以下条件，才可以宣布完成：

- parent、gate、research、execution、release、audit、notify 都成功；
- `production_acceptance.accepted=true` 且 `failed_checks=[]`；
- Agent Judge 和飞书 Agent 的业务契约均为 true；
- Release 在 execution 后创建且报告/意图/receipt lineage 完整；
- EC2 最终 open orders 为 0，receipt 与账户变化可对账；
- 飞书收到完整研究摘要、实际交易明细以及折叠的 Warning/Error；
- 所有静态 contract、单元测试、workflow 语法检查通过；
- 没有秘密泄露，生产变量保持 live=true、paused=false，且没有需要熔断的安全异常。

如果任何必要 job 被 skipped、任一证据缺失或只能由旧运行间接推断，结论必须是未完成。
