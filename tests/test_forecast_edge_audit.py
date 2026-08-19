from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = ROOT / "automation" / "audit" / "forecast-edge-audit-rubric.json"
SCHEMA_PATH = ROOT / "schemas" / "forecast-edge-audit.schema.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "forecast-edge-audit.yml"
AGENT_PROFILE_PATH = ROOT / "automation" / "agents" / "forecast-edge-auditor.md"
MODULE_PATH = ROOT / "scripts" / "forecast_edge_audit.py"

spec = importlib.util.spec_from_file_location("forecast_edge_audit", MODULE_PATH)
assert spec is not None and spec.loader is not None
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class ForecastEdgeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.evidence = {
            "run": {"id": "123"},
            "jobs": [],
            "configuration": {},
            "source_contract": {},
            "release": {},
            "evidence_safety": {"secret_value_scan_passed": True},
            "agent_response": {"available": True},
        }

    def response(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "rubric_id": "forecast-edge-production-audit",
            "rubric_version": "1.0",
            "overall_status": "PASS",
            "confidence": "HIGH",
            "parent_run_id": "123",
            "checks": [
                {
                    "id": item["id"],
                    "status": "PASS",
                    "severity": item["severity"],
                    "evidence": ["run"],
                    "reason": "证据满足检查要求。",
                }
                for item in self.rubric["checks"]
            ],
            "anomalies": [],
            "missing_evidence": [],
            "agent_notes": "",
        }

    def test_rubric_ids_are_unique_and_schema_is_valid(self) -> None:
        Draft202012Validator.check_schema(self.schema)
        ids = [item["id"] for item in self.rubric["checks"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 15)

    def test_workflow_runs_after_parent_and_gates_off_empty_hourly_wakeups(self) -> None:
        self.assertIn("workflow_run:", self.workflow)
        self.assertIn("Forecast Edge research and optional execution", self.workflow)
        self.assertIn("Select completed iterations that need agent review", self.workflow)
        self.assertIn("needs.select.outputs.audit-applicable == 'true'", self.workflow)
        self.assertIn("Run read-only Kimi audit agent", self.workflow)
        self.assertIn('--agent-file "$AUDIT_AGENT_PROFILE_PATH"', self.workflow)
        profile = AGENT_PROFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("tools: []", profile)
        self.assertIn("subagents: []", profile)
        self.assertNotIn("actions/upload-artifact", self.workflow)
        self.assertNotIn("actions/download-artifact", self.workflow)

    def test_audit_agent_receives_only_the_model_secret(self) -> None:
        import re

        configured = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.workflow))
        self.assertEqual(configured, {"KIMI_CODE_API_KEY"})
        for forbidden in (
            "POLYMARKET_EC2_SSH_PRIVATE_KEY",
            "POLYMARKET_SIGNER_PRIVATE_KEY",
            "POLYMARKET_CLOB_API_SECRET",
            "AWS_SECRET_ACCESS_KEY",
        ):
            self.assertNotIn(forbidden, self.workflow)

    def test_static_contract_is_derived_from_source(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "forecast_edge_workflow_contract.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        contract = json.loads(completed.stdout)
        boolean_items = [value for value in contract.values() if isinstance(value, bool)]
        self.assertTrue(boolean_items)
        self.assertTrue(all(boolean_items))
        self.assertEqual(contract["audit_agent_credential_count"], 1)

    def test_valid_complete_response_is_accepted(self) -> None:
        audit_module.validate_response(self.response(), self.rubric, self.evidence, self.schema)

    def test_missing_evidence_path_is_rejected(self) -> None:
        response = self.response()
        response["checks"][0]["evidence"] = ["release.missing"]
        with self.assertRaisesRegex(audit_module.AuditValidationError, "missing evidence path"):
            audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_critical_failure_cannot_report_overall_pass(self) -> None:
        response = self.response()
        response["checks"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(audit_module.AuditValidationError, "critical check failure"):
            audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_secret_like_output_is_rejected(self) -> None:
        response = self.response()
        response["agent_notes"] = "POLYMARKET_CLOB_API_SECRET"
        with self.assertRaisesRegex(audit_module.AuditValidationError, "secret-like"):
            audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_extracts_last_json_object_from_cli_chatter(self) -> None:
        text = 'progress\n• {"old":true}\nNeed final JSON.\n' + json.dumps(self.response(), ensure_ascii=False)
        self.assertEqual(audit_module.extract_last_json_object(text), self.response())

    def test_extract_rejects_secret_like_chatter(self) -> None:
        with self.assertRaisesRegex(audit_module.AuditValidationError, "secret-like"):
            audit_module.extract_last_json_object("progress POLYMARKET_CLOB_API_SECRET")

    def test_fallback_is_schema_valid_and_fail_closed(self) -> None:
        response = audit_module.fallback_response(self.rubric, "123", "invalid response")
        errors = list(Draft202012Validator(self.schema).iter_errors(response))
        self.assertEqual(errors, [])
        self.assertEqual(response["overall_status"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(all(item["status"] == "INSUFFICIENT_EVIDENCE" for item in response["checks"]))


if __name__ == "__main__":
    unittest.main()
