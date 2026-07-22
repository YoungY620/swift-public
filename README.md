# Prediction Arb

一个面向 Polymarket 的长期概率优势研究项目。仓库内的 `forecast-edge` Skill 负责只读数据查询、结算规则审查、贝叶斯点估计、组合管理、半 Kelly 仓位分配和中文报告；本仓库还提供可复用的定时任务、通用飞书机器人通知，以及可选的 GitHub Pages 公开简报。

> 本项目不会登录账户、签名或提交真实订单。私有报告只保存在本地，公开简报不包含账户、资产、持仓金额或建议金额。

## Agent：怎样安排定时任务

读到这里的 Agent 可以直接按以下约定创建任务，无需把 Skill 内部规则复制进提示词：

- 时区：`Asia/Shanghai`
- 常规频率：每天 `10:00` 和 `22:00`
- 每次任务：读取 [automation/prompt.zh-CN.md](automation/prompt.zh-CN.md)，只替换其中声明的占位符，然后执行一次 `forecast-edge` 完整扫描
- 必需输入：公开的 Polymarket 个人主页 URL
- 默认报告目录：`$HOME/.forecast-edge-runs`
- 禁止：真实下单、假设本金、在仓库中写入账户地址、凭据、聊天 ID、私有报告或本机绝对路径
- 可选输出：通用飞书机器人卡片、GitHub Pages 公共 JSON；未配置时不得阻塞私人报告

若当前 Agent 平台支持原生定时任务，创建两个每日任务，分别在 10:00 和 22:00 运行同一份模板。任务提示词只需包含账户、输出目录和可选公开输出开关，研究流程与私有报告格式以 Skill 为准。若平台不支持原生定时任务，使用本仓库的执行器：

```bash
cp automation/forecast-edge.env.example "$HOME/.config/forecast-edge/forecast-edge.env"
# 编辑该文件，填写公开账户 URL、Agent runner 和所需的可选集成。
FORECAST_EDGE_ENV_FILE="$HOME/.config/forecast-edge/forecast-edge.env" \
  ./automation/run_scan.sh
```

`FORECAST_EDGE_RUNNER` 是用户自行提供的可执行命令。定时执行器会把渲染后的临时提示词文件作为最后一个参数传给它，并在进程结束后删除临时文件。示例包装器：

```sh
#!/bin/sh
# $1 是临时提示词文件；在此调用你所使用的 Agent CLI。
exec your-agent-cli run --prompt-file "$1"
```

Linux 可直接采用仓库中的 [systemd timer](automation/systemd/forecast-edge.timer) 或 [cron 示例](automation/cron.example)。完整安装和故障处理见 [定时任务文档](docs/automation.md)。临时加跑一轮时，直接执行 `./automation/run_scan.sh`，不会改变既有计划。

## 安装 Skill

将 `skills/forecast-edge` 安装或链接到 Agent 的 Skill 目录。Agent 应从 [SKILL.md](skills/forecast-edge/SKILL.md) 开始，按需读取 references；脚本的单元测试也是可执行文档。

```bash
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" \
  skills/forecast-edge
python3 -m unittest discover -s skills/forecast-edge/tests -p 'test_*.py'
bash skills/forecast-edge/tests/test_cli.sh
```

## 通用飞书机器人通知（可选）

通知模块不绑定任何机器人名称。它使用调用者运行时提供的飞书应用凭据，先调用飞书 Bot Info API 验证该应用确实启用了机器人能力；若配置了 `FEISHU_EXPECTED_BOT_NAME`，再校验实际机器人名称完全一致，随后才发送卡片。

运行时变量只有通用字段：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_RECEIVE_ID
FEISHU_RECEIVE_ID_TYPE
FEISHU_EXPECTED_BOT_NAME   # 可选
```

凭据和接收目标只放在仓库外的权限受限环境文件中。卡片仅由通过验证的公共简报 JSON 生成，因此不会包含私有组合字段。配置和权限说明见 [飞书接入文档](docs/feishu.md)。

## GitHub Pages 公共看板（可选）

`site/` 是无构建依赖的静态前端。它从 `site/data/index.json` 和 `site/data/*.json` 读取每日研究信号，展示买卖方向、Agent 合理价格、当前可执行价格、价差、理由和证据。页面的“概率轨道”会同时标记市场价与 Agent 估值，便于快速判断差距。

发布一份公开简报：

```bash
python3 scripts/validate_public_brief.py path/to/public-brief.json
python3 scripts/publish_public_brief.py \
  path/to/public-brief.json --site-data site/data
```

将 `site/data/` 的公开文件提交到启用了 Pages 的 GitHub 仓库后，[Pages workflow](.github/workflows/pages.yml) 会先验证数据，再部署 `site/`。协议 `1.1` 会随操作保存可重新计算的贝叶斯/全概率树，网页暂不绘制。启用步骤见 [GitHub Pages 文档](docs/github-pages.md)，字段语义见 [公共简报协议](docs/public-brief-protocol.md)。

## 仓库结构

```text
skills/forecast-edge/       核心研究 Skill
automation/                 提示词、执行器、cron 与 systemd 模板
integrations/feishu/        通用飞书机器人验证和发送
schemas/                    公共 JSON Schema
scripts/                    公共简报验证与发布工具
site/                       可选 GitHub Pages 前端和公开数据
tests/                      仓库级自动化、协议和集成测试
```

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/validate_public_brief.py site/data
```

任何真实账户连通测试都必须通过运行时环境变量注入，不能写进测试、fixture、README、提交历史或日志。
