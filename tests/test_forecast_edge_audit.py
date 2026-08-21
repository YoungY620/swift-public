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
FEISHU_AGENT_PROFILE_PATH = (
    ROOT / "automation" / "agents" / "forecast-edge-feishu-reporter.md"
)
FEISHU_PROMPT_PATH = (
    ROOT / "automation" / "prompts" / "forecast-edge-feishu-brief.zh-CN.md"
)
FEISHU_SCHEMA_PATH = ROOT / "schemas" / "forecast-edge-feishu-brief.schema.json"
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
            "rubric_version": self.rubric["rubric_version"],
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
        self.assertIn("Run read-only Kimi Code Judge agent", self.workflow)
        self.assertIn('--agent-file "$AUDIT_AGENT_PROFILE_PATH"', self.workflow)
        self.assertIn("kimi --agent-file", self.workflow)
        self.assertNotIn("--yolo", self.workflow)
        self.assertIn("--output-format stream-json", self.workflow)
        self.assertIn('| stdbuf -oL tee "$AUDIT_AGENT_RAW_RESPONSE_PATH"', self.workflow)
        self.assertIn('tee "$AUDIT_RUNTIME_DIR/agent.stderr" >&2', self.workflow)
        self.assertIn('--trace-output "$AUDIT_AGENT_TRACE_PATH"', self.workflow)
        profile = AGENT_PROFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("- Read", profile)
        self.assertIn("- Agent", profile)
        self.assertIn("- explore", profile)
        self.assertIn('gh run download "$PARENT_RUN_ID"', self.workflow)
        self.assertIn("Collect and verify research diagnostic artifact", self.workflow)
        self.assertIn("forecast-edge-research-diagnostics-$PARENT_RUN_ID-$run_attempt", self.workflow)
        self.assertIn("research_diagnostics", self.workflow)
        self.assertIn("verify_polymarket_execution_diagnostics.py", self.workflow)
        self.assertIn("Send final production iteration status to Feishu", self.workflow)
        self.assertIn("github.event_name == 'workflow_run'", self.workflow)

    def test_final_feishu_card_is_agentic_and_uses_breaker_color_semantics(self) -> None:
        self.assertIn("Run read-only Kimi Code Feishu briefing agent", self.workflow)
        self.assertIn('--agent-file "$FEISHU_BRIEF_AGENT_PROFILE_PATH"', self.workflow)
        self.assertIn("forecast-edge-feishu-brief-", self.workflow)
        self.assertIn('gh run download "$GITHUB_RUN_ID"', self.workflow)
        self.assertIn("本轮 Agent 简报 artifact 不可用", self.workflow)
        self.assertIn("template=green", self.workflow)
        self.assertIn("template=red", self.workflow)
        self.assertNotIn("template=orange", self.workflow)
        self.assertIn('${TRIP_REQUIRED:-false}', self.workflow)
        color_start = self.workflow.index("          template=green\n")
        color_end = self.workflow.index(
            "          message_path=\"$RUNNER_TEMP/forecast-edge-final-status.md\"\n",
            color_start,
        )
        color_logic = self.workflow[color_start:color_end]
        self.assertIn('${BREAKER_SUCCEEDED:-false}', color_logic)
        self.assertNotIn("overall", color_logic)
        self.assertNotIn("parent_conclusion", color_logic)
        self.assertIn("**执行回执**", self.workflow)
        profile = FEISHU_AGENT_PROFILE_PATH.read_text(encoding="utf-8")
        prompt = FEISHU_PROMPT_PATH.read_text(encoding="utf-8")
        schema = json.loads(FEISHU_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.assertIn("- Read", profile)
        self.assertIn("- Agent", profile)
        self.assertIn("- explore", profile)
        self.assertIn("一、私人操作建议", prompt)
        self.assertIn("二、简报", prompt)
        self.assertIn("无可执行交易意图", prompt)
        self.assertIn("source-inventory.json", prompt)
        self.assertIn("source-inventory.json", profile)
        self.assertIn("forecast_edge_feishu_brief.py\" inventory", self.workflow)

    def test_automatic_audits_trip_a_tiered_production_breaker(self) -> None:
        self.assertIn("Trip production circuit breaker on serious findings", self.workflow)
        self.assertIn("actions/workflows/forecast-edge.yml/disable", self.workflow)
        self.assertIn("POLYMARKET_LIVE_TRADING_ENABLED", self.workflow)
        self.assertIn("actions: write", self.workflow)
        self.assertIn("github.event_name == 'workflow_run'", self.workflow)
        self.assertIn("inputs['trip-circuit-breaker'] == true", self.workflow)
        self.assertIn("--agent-judgment-valid", self.workflow)
        self.assertIn("EXEC-UNCERTAIN", MODULE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn('and .status == "INSUFFICIENT_EVIDENCE"', self.workflow)
        self.assertIn("FORECAST_EDGE_AUTOMATION_PAUSED", self.workflow)

    def test_audit_agent_receives_only_the_model_secret(self) -> None:
        import re

        configured = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.workflow))
        self.assertEqual(
            configured,
            {
                "KIMI_CODE_API_KEY",
                "FEISHU_APP_ID",
                "FEISHU_APP_SECRET",
                "FEISHU_CHAT_ID",
                "FORECAST_EDGE_BREAKER_TOKEN",
            },
        )
        agent_start = self.workflow.index("      - name: Run read-only Kimi Code Judge agent\n")
        agent_end = self.workflow.index(
            "\n      - name: Validate agent judgment or produce warning fallback",
            agent_start,
        )
        agent_step = self.workflow[agent_start:agent_end]
        self.assertEqual(
            set(re.findall(r"secrets\.([A-Z0-9_]+)", agent_step)),
            {"KIMI_CODE_API_KEY"},
        )
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
        self.assertEqual(contract["audit_workflow_credential_count"], 5)

    def test_valid_complete_response_is_accepted(self) -> None:
        audit_module.validate_response(self.response(), self.rubric, self.evidence, self.schema)

    def test_missing_evidence_path_is_rejected(self) -> None:
        response = self.response()
        response["checks"][0]["evidence"] = ["release.missing"]
        with self.assertRaisesRegex(audit_module.AuditValidationError, "missing evidence path"):
            audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_insufficient_evidence_may_cite_the_missing_path(self) -> None:
        response = self.response()
        response["overall_status"] = "INSUFFICIENT_EVIDENCE"
        response["checks"][0]["status"] = "INSUFFICIENT_EVIDENCE"
        response["checks"][0]["evidence"] = ["release.missing"]
        response["missing_evidence"] = ["release.missing"]
        audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_not_applicable_may_cite_the_missing_path(self) -> None:
        response = self.response()
        response["checks"][0]["status"] = "NOT_APPLICABLE"
        response["checks"][0]["evidence"] = ["release.missing"]
        audit_module.validate_response(response, self.rubric, self.evidence, self.schema)

    def test_warning_may_explicitly_report_a_missing_expected_path(self) -> None:
        response = self.response()
        response["overall_status"] = "WARN"
        response["checks"][0]["status"] = "WARN"
        response["checks"][0]["evidence"] = ["release.missing"]
        response["missing_evidence"] = ["release.missing"]
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

    def test_extracts_last_json_object_from_kimi_code_chatter(self) -> None:
        text = 'progress\n• {"old":true}\nNeed final JSON.\n' + json.dumps(self.response(), ensure_ascii=False)
        self.assertEqual(audit_module.extract_last_json_object(text), self.response())

    def test_extract_rejects_secret_like_chatter(self) -> None:
        with self.assertRaisesRegex(audit_module.AuditValidationError, "secret-like"):
            audit_module.extract_last_json_object("progress POLYMARKET_CLOB_API_SECRET")

    def test_extract_requires_real_read_and_subagent_tool_trace(self) -> None:
        response_text = json.dumps(self.response(), ensure_ascii=False)
        stream = "\n".join(
            [
                json.dumps({"role": "meta", "type": "system.version", "version": "0.37.0"}),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "id": "Read_0",
                                "function": {"name": "Read", "arguments": "{}"},
                            }
                        ],
                    }
                ),
                json.dumps({"role": "tool", "tool_call_id": "Read_0", "content": "evidence"}),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "id": "Agent_0",
                                "function": {"name": "Agent", "arguments": "{}"},
                            }
                        ],
                    }
                ),
                json.dumps({"role": "tool", "tool_call_id": "Agent_0", "content": "independent"}),
                json.dumps({"role": "assistant", "content": response_text}, ensure_ascii=False),
            ]
        )
        response, trace = audit_module.extract_agent_stream(stream)
        self.assertEqual(response, self.response())
        self.assertTrue(trace["agentic_trace_verified"])
        self.assertEqual(trace["tool_call_count"], 2)

    def test_extract_rejects_single_plain_model_response(self) -> None:
        stream = json.dumps({"role": "assistant", "content": json.dumps(self.response())})
        with self.assertRaisesRegex(audit_module.AuditValidationError, "required tools"):
            audit_module.extract_agent_stream(stream)

    def test_extract_rejects_raw_signature_and_auth_material(self) -> None:
        for protected in (
            "0x" + "ab" * 65,
            "Authorization: Bearer protected-token-value",
            'result={"mnemonic":"one two three four five six seven eight nine ten eleven twelve"}',
        ):
            with self.subTest(protected=protected[:24]):
                with self.assertRaisesRegex(audit_module.AuditValidationError, "secret-like"):
                    audit_module.extract_last_json_object("progress " + protected)

    def test_normalizer_downgrades_an_unsupported_pass_without_upgrading(self) -> None:
        response = self.response()
        response["checks"][0]["evidence"] = ["release.missing"]
        normalized = audit_module.normalize_unsupported_judgments(
            response, self.rubric, self.evidence
        )
        self.assertEqual(normalized["checks"][0]["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(normalized["overall_status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("release.missing", normalized["missing_evidence"])

    def test_normalizer_preserves_a_fail(self) -> None:
        response = self.response()
        response["overall_status"] = "FAIL"
        response["checks"][0]["status"] = "FAIL"
        response["checks"][1]["evidence"] = ["release.missing"]
        normalized = audit_module.normalize_unsupported_judgments(
            response, self.rubric, self.evidence
        )
        self.assertEqual(normalized["checks"][0]["status"], "FAIL")
        self.assertEqual(normalized["overall_status"], "FAIL")

    def test_normalizer_registers_missing_anomaly_evidence(self) -> None:
        response = self.response()
        response["overall_status"] = "WARN"
        response["checks"][0]["status"] = "WARN"
        response["anomalies"] = [
            {
                "check_id": response["checks"][0]["id"],
                "severity": "critical",
                "summary": "缺少预期证据。",
                "evidence": ["release.missing"],
            }
        ]
        normalized = audit_module.normalize_unsupported_judgments(
            response, self.rubric, self.evidence
        )
        self.assertIn("release.missing", normalized["missing_evidence"])
        audit_module.validate_response(normalized, self.rubric, self.evidence, self.schema)

    def test_fallback_is_schema_valid_and_warns_without_fabricating_judgments(self) -> None:
        response = audit_module.fallback_response(self.rubric, "123", "invalid response")
        errors = list(Draft202012Validator(self.schema).iter_errors(response))
        self.assertEqual(errors, [])
        self.assertEqual(response["overall_status"], "WARN")
        self.assertTrue(all(item["status"] == "WARN" for item in response["checks"]))
        self.assertIn("agent_response.validated_judgment", response["missing_evidence"])

    def test_invalid_agent_output_does_not_trip_an_otherwise_safe_run(self) -> None:
        response = audit_module.fallback_response(self.rubric, "123", "timeout")
        self.assertEqual(
            audit_module.classify_serious_findings(
                response, self.evidence, agent_judgment_valid=False
            ),
            [],
        )

    def test_only_valid_explicit_critical_agent_failures_trip(self) -> None:
        response = self.response()
        response["overall_status"] = "FAIL"
        response["checks"][0]["status"] = "FAIL"
        self.assertEqual(
            audit_module.classify_serious_findings(
                response, self.evidence, agent_judgment_valid=False
            ),
            [],
        )
        self.assertIn(
            response["checks"][0]["id"],
            audit_module.classify_serious_findings(
                response, self.evidence, agent_judgment_valid=True
            ),
        )

    def test_definitive_fok_no_fill_is_not_serious(self) -> None:
        evidence = dict(self.evidence)
        evidence["diagnostics"] = {
            "receipt": {
                "status": "COMPLETED_WITH_WARNINGS",
                "pre_execution_cancellation": {"remaining_open_order_count": 0},
                "receipts": [
                    {
                        "status": "NO_FILL",
                        "code": "FOK_NOT_FILLED",
                        "order_id": None,
                        "order_type": "FOK",
                        "execution_style": "protected_market",
                        "side": "buy",
                        "planned_quantity": "5",
                        "filled_quantity": "0",
                        "planned_limit_price": "0.50",
                        "actual_vwap": "0",
                        "collateral_effect": "0",
                        "before_token_balance": "10",
                        "after_token_balance": "10",
                        "before_collateral_balance": "20",
                        "after_collateral_balance": "20",
                        "trades": [],
                        "transaction_hashes": [],
                    }
                ],
            },
            "preflight": {"open_order_count": 0},
            "verification": {"open_order_count": 0, "orders": []},
        }
        self.assertEqual(
            audit_module.classify_serious_findings(
                self.response(), evidence, agent_judgment_valid=True
            ),
            [],
        )

    def test_uncertain_partial_open_and_protection_violations_trip(self) -> None:
        evidence = dict(self.evidence)
        evidence["diagnostics"] = {
            "summary": {"execution_status": "UNCERTAIN"},
            "receipt": {
                "status": "COMPLETED_WITH_WARNINGS",
                "pre_execution_cancellation": {"remaining_open_order_count": 1},
                "receipts": [
                    {"status": "PARTIAL"},
                    {
                        "status": "FULL",
                        "side": "buy",
                        "planned_quantity": "5",
                        "filled_quantity": "6",
                        "planned_limit_price": "0.50",
                        "planned_cash_limit": "2.50",
                        "actual_vwap": "0.60",
                        "collateral_effect": "3.60",
                    },
                ],
            },
            "preflight": {"open_order_count": 0},
            "verification": {"open_order_count": 1, "orders": [{"open": True}]},
        }
        findings = audit_module.classify_serious_findings(
            self.response(), evidence, agent_judgment_valid=True
        )
        for expected in (
            "EXEC-UNCERTAIN",
            "EXEC-PARTIAL",
            "EXEC-OPEN-ORDERS",
            "EXEC-PROTECTION",
            "EXEC-RECEIPT-INTEGRITY",
        ):
            self.assertIn(expected, findings)

    def test_missing_diagnostics_only_trip_after_transaction_step_started(self) -> None:
        evidence = dict(self.evidence)
        evidence["diagnostics"] = {"metadata": {"found": False}}
        evidence["jobs"] = [
            {
                "name": "execution / execute",
                "steps": [
                    {"name": "Validate EC2 runner and private execution configuration", "conclusion": "failure"}
                ],
            }
        ]
        self.assertNotIn(
            "EXEC-DIAGNOSTICS",
            audit_module.classify_serious_findings(
                self.response(), evidence, agent_judgment_valid=True
            ),
        )
        evidence["jobs"][0]["steps"].append(
            {
                "name": "Validate, cancel open orders, price, execute FOK market orders, and verify locally",
                "conclusion": "failure",
            }
        )
        self.assertIn(
            "EXEC-DIAGNOSTICS",
            audit_module.classify_serious_findings(
                self.response(), evidence, agent_judgment_valid=True
            ),
        )


if __name__ == "__main__":
    unittest.main()
