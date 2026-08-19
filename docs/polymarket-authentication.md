# Polymarket 账户认证与执行器配置

本执行器不按“Google 登录”或“MetaMask 登录”猜测签名格式。网页登录来源、订单签名者、持有资金的账户钱包和 CLOB API 凭证是四个不同概念，必须分别解析和核对。

## 官方钱包类型与版本边界

当前执行器固定使用官方 `polymarket-client==0.6.0` 和 CLOB order protocol `2`。该 SDK 根据 signer 与 account wallet 的确定性关系自动识别四种官方钱包类型：

| Wallet type | `signature_type` | 典型来源 | 订单 maker |
| --- | ---: | --- | --- |
| `EOA` | `0` | 被允许直接交易的 EOA | signer 本身 |
| `POLY_PROXY` | `1` | 旧 Magic Link、Google 或 email 账户 | 旧 Proxy Wallet |
| `GNOSIS_SAFE` | `2` | 旧 MetaMask、Rabby 等外部 signer 账户 | Safe Wallet |
| `DEPOSIT_WALLET` | `3` | 2026-05-04 以后部署的新账户钱包 | Deposit Wallet |

登录来源不能唯一决定 wallet type。同一个 Google 登录，旧账户可能是 `POLY_PROXY`，新账户可能是 `DEPOSIT_WALLET`。Deposit Wallet 在 2026-06-29 前后还分别存在 UUPS 和 beacon proxy 地址推导方式；固定的官方 SDK 同时处理这两种地址，不由配置手写推导逻辑。

SDK 版本不是部署参数。执行器启动时发现版本不是 `0.6.0` 会直接停止，升级 SDK 必须经过代码审查和测试，不能用环境变量临时切换协议。

官方参考：

- [Wallets and Authentication](https://docs.polymarket.com/trading/wallets-auth)
- [API Authentication](https://docs.polymarket.com/getting-started/api#authentication)
- [Set Up EOA Trading](https://docs.polymarket.com/trading/wallets-auth#set-up-eoa-trading)

## 逐项找到所需值

### `POLYMARKET_ACCOUNT_WALLET`

这是持有 pUSD 和 positions 的公开账户钱包，不一定等于 signer。

1. 登录 [polymarket.com](https://polymarket.com/)。
2. 打开右上角 profile menu。
3. 复制 Account Wallet 地址。
4. 与公开 profile/account API 显示的地址核对。

它本身是公开标识，但生产执行器仍从 EC2 本地 `secrets.json` 读取；GitHub workflow 不再传递该值。

### Signer address 与 `POLYMARKET_SIGNER_PRIVATE_KEY`

先在 `polymarket.com → Settings → API Keys → Relayer API Keys` 创建 Relayer API key，并记录页面显示的 **Signer Address**。Signer Address 是核对目标；Relayer API key 本身不能代替 signer 签 CLOB 订单。

- MetaMask、Rabby 或其他可导出 EVM 钱包：只导出与 Signer Address 完全相同的账户私钥。不要导出或提交助记词。
- 旧 Magic Link、Google 或 email 账户：使用官方 [recovery.polymarket.com](https://recovery.polymarket.com/) 恢复 signer，再确认恢复出的地址与 Signer Address 完全相同。
- 硬件钱包、WalletConnect 或不允许导出 signer 的嵌入式账户：不要把网页登录 cookie、Google 密码或一次性验证码交给服务器。当前 Python SDK 的无头执行路径需要本地 EVM private key；这类 signer 需要另行实现受认证的远程签名后端，或使用专用的程序化执行账户。

私钥只允许保存在 EC2 的 `/home/ubuntu/poly-edge-live/runtime/secrets.json`，文件归属 `ubuntu:ubuntu`、权限 `0600`。禁止保存为 GitHub Repository/Environment Secret，也绝不能粘贴到 Issue、日志、聊天或命令行参数中。

### `POLYMARKET_CLOB_API_KEY`、`POLYMARKET_CLOB_API_SECRET`、`POLYMARKET_CLOB_API_PASSPHRASE`

三项是 CLOB L2 credentials，与 Relayer API key、Builder API key都不同。它们由 signer 做一次 L1 EIP-712 签名后，通过 CLOB 的 create/derive API 获得，和网页登录来源无关。

在受控的 EC2 终端安装固定依赖后运行一次初始化：

```bash
cd /home/ubuntu/poly-edge-live/repo
python3 -m venv ../bootstrap-venv
../bootstrap-venv/bin/python -m pip install \
  --requirement automation/requirements-polymarket-execution.txt
../bootstrap-venv/bin/python scripts/bootstrap_polymarket_credentials.py \
  --wallet 0x_ACCOUNT_WALLET \
  --expected-wallet-type AUTO \
  --output /home/ubuntu/poly-edge-live/runtime/secrets.json
```

工具会在终端中隐藏读取 signer private key，创建或派生 CLOB credentials，自动识别 wallet type，并原子创建权限为 `0600` 的 JSON 文件。它不会：

- 在参数或 stdout 中打印密钥；
- 覆盖已有输出文件；
- 部署新钱包；
- 设置 allowance；
- 提交订单。

stdout 只包含 signer、account wallet、wallet type、signature type 和 SDK version 等公开核对信息。若 signer 与 account wallet 不属于同一官方确定性账户，初始化会失败。

## AWS 本地凭据边界

`/home/ubuntu/poly-edge-live/runtime/secrets.json` 是生产执行器唯一的 Polymarket 凭据来源，包含：

| 名称 | 来源 |
| --- | --- |
| `POLYMARKET_ACCOUNT_WALLET` | profile menu 中核对后的公开账户钱包 |
| `POLYMARKET_EXPECTED_WALLET_TYPE` | 初始化识别的官方 wallet type |
| `POLYMARKET_SIGNER_PRIVATE_KEY` | 与 Signer Address 对应的 EVM signer key |
| `POLYMARKET_CLOB_API_KEY` | 初始化生成的 CLOB L2 key |
| `POLYMARKET_CLOB_API_SECRET` | 初始化生成的 CLOB L2 secret |
| `POLYMARKET_CLOB_API_PASSPHRASE` | 初始化生成的 CLOB L2 passphrase |

GitHub workflow 不声明、读取或传递上述任何字段，只把 SHA-256 绑定的 `trade-intents.jsonl` 交给 EC2 self-hosted runner。执行器在 EC2 本机通过 `scripts/run_with_polymarket_secrets.py` 短暂注入子进程环境，值不进入 Actions 日志、workspace、artifact 或 Release。

`polymarket-production` Environment 仅用于 required reviewers 等执行审批，不保存 Polymarket 凭据。生产执行不提供兼容别名，只接受上表六个 canonical 字段。

## Relayer 与 Builder 凭证为什么不在运行时清单中

- Relayer API key 用于 gasless wallet deployment、approval、transfer 等链上钱包操作。
- Builder API key 用于集成方创建新 Deposit Wallet 和 builder attribution。
- CLOB L2 credentials 用于 authenticated reads、order submit 和 cancel。

当前定时执行器坚持 `preexisting-only` allowance policy，不部署钱包、不自动增加授权，所以常规下单不需要保存 Relayer 或 Builder credential。缺少 allowance 时 preflight 直接停止，由一次性的人工受控流程处理。
