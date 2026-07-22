#!/usr/bin/env python3
"""Validate and send a public Forecast Edge brief with any Feishu app bot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
from validate_public_brief import PublicBriefError, load_json, validate_brief  # noqa: E402


API_ROOT = "https://open.feishu.cn/open-apis"
RECEIVE_ID_TYPES = {"chat_id", "open_id", "user_id", "union_id", "email"}
Transport = Callable[[str, str, Optional[dict[str, Any]], Optional[str]], dict[str, Any]]


class FeishuError(RuntimeError):
    """Raised when bot verification or delivery fails."""


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise FeishuError(f"Feishu API request failed: {type(exc).__name__}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FeishuError("Feishu API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise FeishuError("Feishu API returned an invalid object")
    return result


def require_success(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if response.get("code") != 0:
        message = response.get("msg") or "unknown error"
        raise FeishuError(f"{operation} failed: {message}")
    return response


def bot_record(response: dict[str, Any]) -> dict[str, Any]:
    candidates: list[Any] = [response.get("bot")]
    data = response.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("bot"), data])
    bot_keys = {"bot_name", "app_name", "name", "open_id", "app_id", "activate_status"}
    for candidate in candidates:
        if isinstance(candidate, dict) and bot_keys.intersection(candidate):
            return candidate
    raise FeishuError("Bot Info response does not describe a Feishu bot")


def bot_name(bot: dict[str, Any]) -> str | None:
    for key in ("bot_name", "app_name", "name"):
        value = bot.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class FeishuClient:
    def __init__(self, transport: Transport = request_json) -> None:
        self.transport = transport

    def tenant_token(self, app_id: str, app_secret: str) -> str:
        response = require_success(
            self.transport(
                "POST",
                f"{API_ROOT}/auth/v3/tenant_access_token/internal",
                {"app_id": app_id, "app_secret": app_secret},
                None,
            ),
            "tenant token request",
        )
        token = response.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuError("tenant token response is missing tenant_access_token")
        return token

    def verify_bot(self, token: str, expected_name: str | None = None) -> dict[str, Any]:
        response = require_success(
            self.transport("GET", f"{API_ROOT}/bot/v3/info", None, token),
            "bot verification",
        )
        bot = bot_record(response)
        actual_name = bot_name(bot)
        if expected_name is not None:
            if actual_name is None:
                raise FeishuError("Bot Info response does not expose a name to verify")
            if actual_name != expected_name:
                raise FeishuError("configured bot name does not match Bot Info")
        return bot

    def send_card(
        self,
        token: str,
        receive_id: str,
        receive_id_type: str,
        card: dict[str, Any],
    ) -> dict[str, Any]:
        if receive_id_type not in RECEIVE_ID_TYPES:
            raise FeishuError(f"unsupported FEISHU_RECEIVE_ID_TYPE: {receive_id_type}")
        query = urlencode({"receive_id_type": receive_id_type})
        response = self.transport(
            "POST",
            f"{API_ROOT}/im/v1/messages?{query}",
            {
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            token,
        )
        return require_success(response, "message delivery")


def action_label(action: str, outcome: str) -> str:
    verb = {"BUY": "买入", "ADD": "加仓", "SELL": "卖出"}[action]
    icon = "🛒" if action in {"BUY", "ADD"} else "💰"
    color = "🟢" if outcome == "YES" else "🔴"
    return f"{icon}{color} {verb} {outcome}"


def confidence_label(value: str) -> str:
    return {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}[value]


def build_card(brief: dict[str, Any]) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    if not brief["actions"]:
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**今日无新增交易信号**"},
            }
        )
    for action in brief["actions"]:
        text = (
            f"**{action_label(action['action'], action['outcome'])}｜{action['market_name']}**\n"
            f"Agent 估值 **{action['fair_price']:.1%}** · 当前 **{action['current_price']:.1%}** · "
            f"价差 **{action['price_gap']:+.1%}** · 置信度 **{confidence_label(action['confidence'])}**\n"
            f"{action['reason']}\n支撑信息 **{len(action['evidence'])}** 条"
        )
        elements.extend(
            [
                {"tag": "div", "text": {"tag": "lark_md", "content": text}},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {"tag": "plain_text", "content": "打开 Polymarket"},
                            "url": action["polymarket_url"],
                        }
                    ],
                },
                {"tag": "hr"},
            ]
        )
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"数据截止 {brief['as_of']} · 仅供研究，不自动下单",
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": brief["title"]},
        },
        "elements": elements,
    }


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise FeishuError(f"missing required environment variable: {name}")
    return value


def send_from_environment(brief: dict[str, Any], client: FeishuClient | None = None) -> None:
    service = client or FeishuClient()
    token = service.tenant_token(required_env("FEISHU_APP_ID"), required_env("FEISHU_APP_SECRET"))
    service.verify_bot(token, os.environ.get("FEISHU_EXPECTED_BOT_NAME") or None)
    service.send_card(
        token,
        required_env("FEISHU_RECEIVE_ID"),
        os.environ.get("FEISHU_RECEIVE_ID_TYPE", "chat_id"),
        build_card(brief),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", type=Path)
    args = parser.parse_args(argv)
    try:
        brief = validate_brief(load_json(args.brief))
        send_from_environment(brief)
    except (FeishuError, PublicBriefError) as exc:
        print(f"Feishu delivery failed: {exc}", file=sys.stderr)
        return 2
    print("Feishu card delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
