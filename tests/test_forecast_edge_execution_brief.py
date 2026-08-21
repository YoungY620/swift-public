from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "forecast_edge_execution_brief.py"
SPEC = importlib.util.spec_from_file_location("forecast_edge_execution_brief", MODULE_PATH)
assert SPEC and SPEC.loader
brief = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brief)


class ForecastEdgeExecutionBriefTests(unittest.TestCase):
    def write_report(self, directory: Path) -> None:
        analyses = []
        for index, question in enumerate(
            ("Will policy A pass?", "Will policy B pass?", "Will policy C pass?"),
            start=1,
        ):
            analyses.append(
                {
                    "market": {
                        "instrument_identifier": f"token-{index}",
                        "question": question,
                        "url": f"https://polymarket.com/event/policy-{index}",
                        "outcome": "YES",
                    }
                }
            )
        (directory / "report-polymarket.json").write_text(
            json.dumps({"current_positions": [], "candidates": analyses}),
            encoding="utf-8",
        )

    @staticmethod
    def outcome(index: int, *, skipped: bool = False) -> dict[str, object]:
        value: dict[str, object] = {
            "intent_index": index,
            "disposition": "skipped" if skipped else "planned",
            "intent": {
                "instrument_identifier": f"token-{index}",
                "side": "sell" if index == 2 else "buy",
                "outcome": "yes",
                "quantity": str(index * 10),
                "notional": str(index * 5),
                "currency": "pUSD",
            },
        }
        if skipped:
            value["skip"] = {
                "code": "below_marketable_buy_minimum_amount",
                "message": "protected BUY amount is below the official minimum",
            }
        else:
            value["planned_order"] = {"quantity": str(index * 10)}
        return value

    def test_lists_fills_no_fills_and_skips_with_market_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports)
            receipt = {
                "schema_version": "1.0",
                "status": "COMPLETED_WITH_WARNINGS",
                "planning": {
                    "intent_outcomes": [
                        self.outcome(1),
                        self.outcome(2),
                        self.outcome(3, skipped=True),
                    ]
                },
                "receipts": [
                    {
                        "intent_index": 1,
                        "status": "FULL",
                        "planned_quantity": "10",
                        "filled_quantity": "10",
                        "actual_vwap": "0.5",
                        "collateral_effect": "5.1",
                    },
                    {
                        "intent_index": 2,
                        "status": "NO_FILL",
                        "code": "FOK_NOT_FILLED",
                        "message": "protected FOK order was killed without a fill",
                        "planned_quantity": "20",
                        "filled_quantity": "0",
                        "actual_vwap": "0",
                        "collateral_effect": "0",
                    },
                ],
            }
            document = brief.build_document(receipt, reports)

            self.assertEqual(document["item_count"], 3)
            self.assertEqual(document["status_counts"]["FULL"], 1)
            self.assertEqual(document["status_counts"]["NO_FILL"], 1)
            self.assertEqual(document["status_counts"]["SKIPPED"], 1)
            markdown = document["markdown"]
            self.assertIn("已成交｜买入 YES", markdown)
            self.assertIn("Will policy A pass?", markdown)
            self.assertIn("实际成交 10 股", markdown)
            self.assertIn("实际支出 5.1 pUSD", markdown)
            self.assertIn("FOK 未成交｜卖出 YES", markdown)
            self.assertIn("规划跳过｜买入 YES", markdown)
            self.assertIn("below\_marketable\_buy\_minimum\_amount", markdown)
            self.assertNotIn("token-1", markdown)

    def test_surfaces_allowlisted_exchange_failure_without_copying_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports)
            failure_log = root / "execute-command.log"
            failure_log.write_text(
                "private material must not be copied\n"
                "RequestRejectedError: invalid amount for a marketable BUY order "
                "($0.9), min size: 1\n",
                encoding="utf-8",
            )
            receipt = {
                "schema_version": "1.0",
                "status": "UNCERTAIN",
                "planning": {
                    "intent_outcomes": [self.outcome(1), self.outcome(2)]
                },
                "receipts": [],
            }

            document = brief.build_document(receipt, reports, failure_log)

            self.assertEqual(
                document["recognized_failure"]["code"],
                "MARKETABLE_ORDER_BELOW_MINIMUM",
            )
            self.assertIn("提交金额 0.9 pUSD，低于最小 1 pUSD", document["markdown"])
            self.assertIn("2 笔计划订单没有终态回执", document["markdown"])
            self.assertNotIn("private material", document["markdown"])

    def test_refuses_report_outcome_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports)
            report_path = reports / "report-polymarket.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["candidates"][0]["market"]["outcome"] = "NO"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            receipt = {
                "schema_version": "1.0",
                "status": "COMPLETED",
                "planning": {"intent_outcomes": [self.outcome(1)]},
                "receipts": [],
            }
            with self.assertRaisesRegex(
                brief.ExecutionBriefError, "differs from the verified report"
            ):
                brief.build_document(receipt, reports)


if __name__ == "__main__":
    unittest.main()
