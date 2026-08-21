from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_forecast_edge_production_run.py"
SPEC = importlib.util.spec_from_file_location("verify_forecast_edge_production_run", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class ForecastEdgeProductionRunTests(unittest.TestCase):
    def inputs(self) -> tuple[dict, list, dict, dict, dict, dict]:
        run = {
            "id": "123",
            "run_attempt": 1,
            "head_sha": "a" * 40,
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
        }
        jobs = [
            {"name": "gate", "conclusion": "success"},
            {"name": "research / research", "conclusion": "success"},
            {"name": "execution / execute", "conclusion": "success"},
            {"name": "release", "conclusion": "success"},
        ]
        manifest = {
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "commit_sha": "a" * 40,
            "schedule": {
                "event_name": "schedule",
                "interval_hours": 8,
                "anchor_utc_hour": 2,
                "gate_should_run": True,
                "automation_paused": False,
            },
            "execution": {
                "requested": True,
                "configuration_ready": True,
                "workflow_result": "success",
            },
        }
        receipt = {
            "execution_id": "execution-123",
            "status": "COMPLETED_WITH_WARNINGS",
            "pre_execution_cancellation": {"remaining_open_order_count": 0},
            "receipts": [
                {
                    "intent_index": 1,
                    "status": "FULL",
                    "side": "sell",
                    "order_type": "FOK",
                    "execution_style": "protected_market",
                    "planned_quantity": "10",
                    "filled_quantity": "10",
                    "planned_limit_price": "0.48",
                    "actual_vwap": "0.5",
                    "planned_cash_limit": "4.8",
                    "collateral_effect": "5",
                },
                {
                    "intent_index": 2,
                    "status": "NO_FILL",
                    "code": "FOK_NOT_FILLED",
                    "side": "buy",
                    "order_type": "FOK",
                    "execution_style": "protected_market",
                    "planned_quantity": "2",
                    "filled_quantity": "0",
                    "planned_limit_price": "0.5",
                    "actual_vwap": "0",
                    "planned_cash_limit": "1",
                    "collateral_effect": "0",
                },
            ],
        }
        verification = {
            "execution_id": "execution-123",
            "open_order_count": 0,
            "orders": [{"open": False}, {"open": False}],
        }
        diagnostic = {
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "commit_sha": "a" * 40,
            "execution_id": "execution-123",
            "secret_scan_passed": True,
            "failed_phase": None,
            "source_intent_count": 2,
            "planned_order_count": 2,
            "skipped_intent_count": 0,
            "receipt_count": 2,
            "intent_trace": [
                {
                    "intent_index": 1,
                    "planning_disposition": "planned",
                    "execution_disposition": "full",
                    "receipt": {"status": "FULL"},
                },
                {
                    "intent_index": 2,
                    "planning_disposition": "planned",
                    "execution_disposition": "no_fill",
                    "receipt": {"status": "NO_FILL"},
                },
            ],
            "phase_events": [
                {"phase": phase, "exit_code": 0}
                for phase in (
                    "install",
                    "validate",
                    "cancel",
                    "preflight",
                    "plan",
                    "execute",
                    "verify",
                )
            ],
        }
        return run, jobs, manifest, receipt, verification, diagnostic

    def test_accepts_complete_scheduled_production_iteration(self) -> None:
        document = verifier.build_document(*self.inputs())
        self.assertTrue(document["accepted"])
        self.assertEqual(document["failed_checks"], [])
        self.assertEqual(
            document["stage_conclusions"],
            {
                "gate": "success",
                "research": "success",
                "execution": "success",
                "release": "success",
            },
        )

    def test_rejects_any_skipped_required_schedule_stage(self) -> None:
        values = list(self.inputs())
        values[1][2]["conclusion"] = "skipped"
        document = verifier.build_document(*values)
        self.assertFalse(document["accepted"])
        self.assertIn("STAGE-EXECUTION", document["failed_checks"])

    def test_rejects_missing_phase_and_residual_open_order(self) -> None:
        values = list(self.inputs())
        values[4]["open_order_count"] = 1
        values[5]["phase_events"] = values[5]["phase_events"][:-1]
        document = verifier.build_document(*values)
        self.assertIn("EXECUTION-PHASES", document["failed_checks"])
        self.assertIn("EXECUTION-OPEN-ORDERS", document["failed_checks"])

    def test_rejects_duplicate_phase_and_unproven_no_fill(self) -> None:
        values = list(self.inputs())
        values[3]["receipts"][1].pop("code")
        values[5]["phase_events"].append({"phase": "execute", "exit_code": 0})
        document = verifier.build_document(*values)
        self.assertIn("EXECUTION-RECEIPT", document["failed_checks"])
        self.assertIn("EXECUTION-PHASES", document["failed_checks"])

    def test_rejects_missing_or_duplicate_terminal_disposition(self) -> None:
        values = list(self.inputs())
        values[3]["receipts"].pop()
        document = verifier.build_document(*values)
        self.assertIn("EXECUTION-DISPOSITIONS", document["failed_checks"])
        self.assertIn("EXECUTION-OPEN-ORDERS", document["failed_checks"])

        values = list(self.inputs())
        values[3]["receipts"][1]["intent_index"] = 1
        document = verifier.build_document(*values)
        self.assertIn("EXECUTION-DISPOSITIONS", document["failed_checks"])

    def test_rejects_execution_identity_or_price_protection_mismatch(self) -> None:
        values = list(self.inputs())
        values[4]["execution_id"] = "another-execution"
        values[3]["receipts"][0]["actual_vwap"] = "0.47"
        document = verifier.build_document(*values)
        self.assertIn("EXECUTION-IDENTITY", document["failed_checks"])
        self.assertIn("EXECUTION-PROTECTION", document["failed_checks"])

    def test_manual_research_only_allows_only_execution_skip(self) -> None:
        run, jobs, manifest, receipt, verification, diagnostic = self.inputs()
        run["event"] = "workflow_dispatch"
        jobs[2]["conclusion"] = "skipped"
        manifest["schedule"]["event_name"] = "workflow_dispatch"
        manifest["execution"]["requested"] = False
        manifest["execution"]["configuration_ready"] = False
        manifest["execution"]["workflow_result"] = "skipped"
        document = verifier.build_document(
            run, jobs, manifest, None, None, None
        )
        self.assertTrue(document["accepted"])
        statuses = {item["id"]: item["status"] for item in document["checks"]}
        self.assertEqual(statuses["STAGE-EXECUTION"], "NOT_APPLICABLE")
        self.assertEqual(statuses["SCHEDULE-FULL-ITERATION"], "NOT_APPLICABLE")
