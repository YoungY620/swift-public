# 定时任务

## 运行契约

`automation/run_scheduled_scan.py` 不绑定具体 Agent 产品。调用者通过 `FORECAST_EDGE_RUNNER` 提供一个命令；执行器将只替换白名单占位符的提示词写入权限为 `0600` 的临时文件，把文件路径作为最后一个参数传给该命令，并在退出后删除文件。

Runner 必须：

1. 接受一个 UTF-8 提示词文件路径。
2. 在可以读取本仓库和已安装 `forecast-edge` Skill 的环境中运行。
3. 将退出码原样返回；失败时不得生成伪造的成功报告。

## 原生 Agent 定时任务

如果 Agent 平台自身提供定时任务能力，创建两个使用同一提示词的每日任务：

- `10:00 Asia/Shanghai`
- `22:00 Asia/Shanghai`

使用 `automation/prompts/scheduled-scan.zh-CN.md` 作为提示词正文。仅在运行时填入账户、私有输出目录、仓库目录和公共简报路径；不要复制或改写 Skill 内部规则，避免模板与 Skill 演化后互相矛盾。GitHub Actions 的交易意图附加契约和修复提示词也保存在同一目录。

GitHub Actions 入口是 `.github/workflows/forecast-edge-research.yml`。它按 workflow step 分别完成调度门控、配置检查、Kimi Code 安装、账户快照、prompt 渲染、Agent 调用、交易意图校验、最多三次修复、artifact 上传和 Issue 通知。`scripts/validate_trade_intents.py` 只负责 JSON Schema、十进制金额关系、venue、抵押资产与卖单公开持仓校验，不负责调度或调用 Agent。

## Linux systemd

```bash
mkdir -p "$HOME/.config/forecast-edge" "$HOME/.config/systemd/user"
cp automation/forecast-edge.env.example "$HOME/.config/forecast-edge/forecast-edge.env"
chmod 600 "$HOME/.config/forecast-edge/forecast-edge.env"
cp automation/systemd/* "$HOME/.config/systemd/user/"
# 编辑 service 中的仓库路径，然后启用：
systemctl --user daemon-reload
systemctl --user enable --now forecast-edge.timer
systemctl --user list-timers forecast-edge.timer
```

## cron

将 `automation/cron.example` 中唯一的仓库路径占位符替换为本机 checkout 路径，再加入 `crontab -e`。保持 `CRON_TZ=Asia/Shanghai`，以免主机时区变化造成偏移。

## 临时扫描

资金变化、突发事件或需要额外复核时，可直接运行：

```bash
FORECAST_EDGE_ENV_FILE="$HOME/.config/forecast-edge/forecast-edge.env" \
  ./automation/run_scan.sh
```

这只新增一轮不可变报告，不改变 timer 或 cron。

## 输出与失败

- 私有报告始终由 Skill 写到 `FORECAST_EDGE_OUTPUT_ROOT`。
- 只有设置 `FORECAST_EDGE_PUBLIC_BRIEF_DIR` 时，提示词才要求 Agent 生成公共 JSON。
- 只有公共 JSON 通过严格字段与语义校验后，才可能被复制到 Pages 或发送到飞书。
- Agent、公共数据校验、Pages 发布或飞书发送任一步失败，执行器都返回非零状态。
- 飞书或 Pages 未配置不是错误，也不会影响私有报告。
