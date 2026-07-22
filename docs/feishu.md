# 飞书机器人接入

`integrations/feishu/send_brief.py` 面向任何飞书自建应用机器人，不绑定机器人名称、租户或接收会话。它不会读取私有 Forecast Edge 报告，只接受通过公共协议校验的 JSON。

## 飞书侧准备

在飞书开放平台创建或选择一个应用：

1. 启用“机器人”能力。
2. 申请发送消息所需权限，并按飞书要求发布应用版本。
3. 将机器人加入目标群，或确保它可以向目标用户发送消息。
4. 获取应用 ID、应用密钥和目标的 receive ID。

仓库不验证或代替飞书平台上的权限审批。运行时会先获取 tenant token，再调用 `GET /open-apis/bot/v3/info`；只有响应确认这是一个机器人应用时才发送卡片。

## 配置

把以下变量放入仓库外、权限为 `0600` 的环境文件：

```text
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_RECEIVE_ID=...
FEISHU_RECEIVE_ID_TYPE=chat_id
FEISHU_EXPECTED_BOT_NAME=...  # 可选
```

`FEISHU_RECEIVE_ID_TYPE` 支持 `chat_id`、`open_id`、`user_id`、`union_id`、`email`。设置 `FEISHU_EXPECTED_BOT_NAME` 后，脚本要求 Bot Info 返回的名称完全一致；不设置则只验证机器人能力，不限制名字。

## 独立发送

```bash
set -a
. "$HOME/.config/forecast-edge/forecast-edge.env"
set +a
python3 integrations/feishu/send_brief.py path/to/public-brief.json
```

脚本不会打印 token、密钥或 receive ID。飞书 API 错误只输出操作阶段和非敏感错误消息。卡片中不包含建议金额和组合总量。
