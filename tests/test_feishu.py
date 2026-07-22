from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "integrations" / "feishu" / "send_brief.py"
SPEC = importlib.util.spec_from_file_location("send_brief", MODULE_PATH)
assert SPEC and SPEC.loader
feishu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feishu)


class FakeTransport:
    def __init__(self, name: str = "Reusable Bot") -> None:
        self.calls = []
        self.name = name

    def __call__(self, method, url, payload, token):
        self.calls.append((method, url, payload, token))
        if url.endswith("tenant_access_token/internal"):
            return {"code": 0, "tenant_access_token": "runtime-token"}
        if url.endswith("bot/v3/info"):
            return {"code": 0, "bot": {"app_name": self.name, "open_id": "runtime-id"}}
        if "/im/v1/messages?" in url:
            return {"code": 0, "data": {"message_id": "runtime-message"}}
        return {"code": 404, "msg": "unexpected"}


class FeishuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = json.loads(
            (ROOT / "tests" / "fixtures" / "public-brief.json").read_text(encoding="utf-8")
        )

    def test_verifies_any_bot_and_sends_interactive_card(self) -> None:
        transport = FakeTransport()
        client = feishu.FeishuClient(transport)
        token = client.tenant_token("runtime-app", "runtime-secret")
        bot = client.verify_bot(token)
        self.assertEqual(bot["app_name"], "Reusable Bot")
        card = feishu.build_card(self.brief)
        client.send_card(token, "runtime-chat", "chat_id", card)

        self.assertEqual([call[0] for call in transport.calls], ["POST", "GET", "POST"])
        sent = transport.calls[-1][2]
        self.assertEqual(sent["msg_type"], "interactive")
        content = json.loads(sent["content"])
        rendered = json.dumps(content, ensure_ascii=False)
        self.assertIn("买入 YES", rendered)
        self.assertIn("https://polymarket.com/event/example-market", rendered)
        self.assertNotIn("recommended_cash", rendered)

    def test_optional_expected_name_is_enforced(self) -> None:
        client = feishu.FeishuClient(FakeTransport("Actual Bot"))
        with self.assertRaisesRegex(feishu.FeishuError, "does not match"):
            client.verify_bot("runtime-token", "Expected Bot")

    def test_non_bot_response_is_rejected(self) -> None:
        def transport(method, url, payload, token):
            return {"code": 0, "data": "not-a-bot"}

        with self.assertRaisesRegex(feishu.FeishuError, "does not describe"):
            feishu.FeishuClient(transport).verify_bot("runtime-token")

    def test_card_contains_no_private_portfolio_values(self) -> None:
        rendered = json.dumps(feishu.build_card(self.brief), ensure_ascii=False)
        for forbidden in ("账户", "总资产", "建议金额", "可用余额"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
