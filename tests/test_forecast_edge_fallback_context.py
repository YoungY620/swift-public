from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_forecast_edge_fallback_context.py"
SPEC = importlib.util.spec_from_file_location("build_forecast_edge_fallback_context", MODULE_PATH)
assert SPEC and SPEC.loader
context_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(context_module)


class ForecastEdgeFallbackContextTests(unittest.TestCase):
    def test_builds_secret_free_snapshot_progress_and_latest_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshots = root / "account-snapshots"
            snapshots.mkdir()
            (snapshots / "polymarket.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "adapter": "polymarket",
                        "account": {
                            "identifier": "0xprivate-account-identifier-is-not-copied"
                        },
                        "portfolio": {"currency": "pUSD", "available_cash": "134.7200"},
                        "positions": {
                            "count": 14,
                            "positions": [
                                {
                                    "question": "Will the sample policy pass?",
                                    "outcome": "no",
                                    "quantity": "12.5000",
                                    "displayed_value": "4.2500",
                                    "url": "https://polymarket.com/event/sample-policy",
                                    "market_identifier": "not-copied",
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            archive = root / "archive"
            old_report = archive / "old" / "report.json"
            latest_report = archive / "latest" / "report.json"
            old_report.parent.mkdir(parents=True)
            latest_report.parent.mkdir(parents=True)
            for report_path, completed_at, positions, candidates in (
                (old_report, "2026-08-19T03:00:00+00:00", 2, 3),
                (latest_report, "2026-08-20T03:00:00+00:00", 13, 10),
            ):
                report_path.write_text(
                    json.dumps(
                        {
                            "run": {
                                "account": {"adapter": "polymarket"},
                                "completed_at": completed_at,
                            },
                            "portfolio": {
                                "currency": "pUSD",
                                "available_collateral": 120,
                                "estimated_bankroll": 190,
                            },
                            "current_positions": [
                                {
                                    "market": {
                                        "id": "held-market",
                                        "question": "Will the held policy pass?",
                                        "url": "https://polymarket.com/event/held-policy",
                                        "outcome": "NO",
                                    },
                                    "model": {
                                        "yes_probability": 0.25,
                                        "conclusion": "官方投票日程支持 25% 的 YES 概率。",
                                    },
                                    "signal": {"action": "MAINTAIN"},
                                    "opportunity": {
                                        "executable_price": 0.7,
                                        "raw_edge": 0.05,
                                        "net_edge": 0.04,
                                        "reason": "未触发卖出交叉，维持持仓。",
                                    },
                                }
                            ]
                            + [{}] * (positions - 1),
                            "candidates": [
                                {
                                    "research_novelty": {
                                        "classification": "FIRST_RESEARCH"
                                    },
                                    "market": {
                                        "id": "candidate-market",
                                        "question": "Will the new bill pass?",
                                        "url": "https://polymarket.com/event/new-bill",
                                        "outcome": "YES",
                                    },
                                    "model": {
                                        "yes_probability": 0.6,
                                        "conclusion": "委员会票数支持 60% 的 YES 概率。",
                                    },
                                    "signal": {"action": "BUY"},
                                    "opportunity": {
                                        "executable_price": 0.4,
                                        "raw_edge": 0.2,
                                        "net_edge": 0.19,
                                        "reason": "费后优势超过硬门槛并获得组合分配。",
                                    },
                                    "allocation": {"allocated_cash": 5},
                                }
                            ]
                            + [{}] * (candidates - 1),
                            "actions": [
                                {
                                    "action": "BUY",
                                    "market_id": "candidate-market",
                                    "recommended_cash": 5,
                                    "recommended_shares": 12.5,
                                },
                                {"action": "MAINTAIN", "market_id": "held-market"},
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            baseline_list = root / "baseline.txt"
            baseline_list.write_text(
                f"{old_report}\n{latest_report}\n", encoding="utf-8"
            )
            runtime = root / "runtime"
            (runtime / "reports" / "reports" / "draft").mkdir(parents=True)
            (runtime / "reports" / "reports" / "draft" / "report.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (runtime / "kimi-attempt-1.stream.jsonl").write_text("{}\n", encoding="utf-8")
            (runtime / "validation-attempt-1.stdout").write_text("\n", encoding="utf-8")

            args = argparse.Namespace(
                research_date="2026-08-21T01:02:03Z",
                enabled_venues="polymarket",
                account_snapshots_dir=snapshots,
                baseline_reports_list=baseline_list,
                runtime_dir=runtime,
                current_reports_manifest=None,
                validation_summary=None,
            )
            context = context_module.build_context(args)

            self.assertEqual(context["research_date"], "2026-08-21")
            self.assertEqual(context["account_snapshots"][0]["available_cash"], "134.72")
            self.assertEqual(context["account_snapshots"][0]["current_position_count"], 14)
            self.assertNotIn("account", context["account_snapshots"][0])
            self.assertEqual(
                context["account_snapshots"][0]["positions"][0],
                {
                    "question": "Will the sample policy pass?",
                    "outcome": "NO",
                    "quantity": "12.5",
                    "displayed_value": "4.25",
                    "url": "https://polymarket.com/event/sample-policy",
                },
            )
            self.assertEqual(
                context["latest_validated_baselines"][0]["completed_at"],
                "2026-08-20T03:00:00+00:00",
            )
            baseline_markets = context["latest_validated_baselines"][0][
                "market_summaries"
            ]
            self.assertEqual(len(baseline_markets), 2)
            self.assertEqual(baseline_markets[0]["question"], "Will the held policy pass?")
            self.assertEqual(baseline_markets[1]["recommended_cash"], "5")
            self.assertNotIn("market_id", baseline_markets[1])
            self.assertEqual(
                context["progress"]["last_reached_stage"],
                "REPORT_GENERATED_UNVALIDATED",
            )
            self.assertEqual(context["progress"]["restored_historical_report_count"], 2)
            self.assertEqual(context["progress"]["research_agent_attempt_count"], 1)

    def test_missing_sources_remain_unknown_instead_of_zero_account_data(self) -> None:
        args = argparse.Namespace(
            research_date="2026-08-21",
            enabled_venues="polymarket",
            account_snapshots_dir=None,
            baseline_reports_list=None,
            runtime_dir=None,
            current_reports_manifest=None,
            validation_summary=None,
        )
        context = context_module.build_context(args)
        self.assertEqual(context["account_snapshots"], [])
        self.assertEqual(context["current_validated_reports"], [])
        self.assertEqual(context["progress"]["last_reached_stage"], "SETUP_NOT_REACHED")
        self.assertIsNone(context["progress"]["validated_trade_intent_count"])


if __name__ == "__main__":
    unittest.main()
