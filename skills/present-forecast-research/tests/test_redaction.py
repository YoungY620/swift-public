"""Public allowlist and privacy checks."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import present_report  # noqa: E402
import report_contract  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "report.json"


class RedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report, _, source_hash = report_contract.load_report(FIXTURE)
        self.public = present_report.build_public_signals(
            self.report, source_hash, "2026-07-19T01:00:00Z"
        )

    def test_public_output_omits_private_financial_and_identity_fields(self) -> None:
        serialized = json.dumps(self.public, ensure_ascii=False)
        for forbidden in (
            "@fixture-user",
            "0x2222222222222222222222222222222222222222",
            "available_collateral",
            "recommended_cash",
            "recommended_shares",
            "/Users/",
            "polymarket.com/@",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_maintain_is_not_public(self) -> None:
        self.assertEqual([action["action"] for action in self.public["actions"]], ["BUY"])

    def test_public_extra_field_is_rejected(self) -> None:
        self.public["actions"][0]["recommended_cash"] = 25
        with self.assertRaisesRegex(report_contract.PresentationContractError, "forbidden fields"):
            report_contract.validate_public_signals(self.public)

    def test_public_resolution_extra_field_is_rejected(self) -> None:
        self.public["actions"][0]["resolution"]["private_note"] = "不得公开"
        with self.assertRaisesRegex(report_contract.PresentationContractError, "forbidden fields"):
            report_contract.validate_public_signals(self.public)

    def test_public_sensitive_content_is_rejected_even_in_allowed_field(self) -> None:
        self.public["actions"][0]["reason"] = "联系 analyst@example.com 获取详情"
        with self.assertRaisesRegex(report_contract.PresentationContractError, "sensitive data"):
            report_contract.validate_public_signals(self.public)


if __name__ == "__main__":
    unittest.main()
