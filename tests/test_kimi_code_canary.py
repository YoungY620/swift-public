from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/kimi-code-canary.yml"
MODULE_PATH = ROOT / "scripts/summarize_kimi_canary.py"
SPEC = importlib.util.spec_from_file_location("summarize_kimi_canary", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class KimiCodeCanaryWorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_single_agent_and_never_trades(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertEqual(workflow.count("-- kimi --agent-file"), 1)
        self.assertNotIn("execute-polymarket", workflow)
        self.assertNotIn("POLYMARKET", workflow)
        self.assertNotIn("--retry", workflow)
        self.assertIn("--output-format stream-json", workflow)
        self.assertIn("permissions:\n      contents: read", workflow)
        self.assertIn("sanitize_research_diagnostics.py", workflow)
        self.assertIn("--secret-env KIMI_MODEL_API_KEY", workflow)

    def test_summary_proves_one_completed_read_and_matching_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = {
                "nonce": "123-1",
                "evidence_name": "read-tool-contract",
                "evidence_value": "verified",
            }
            (root / "evidence.json").write_text(json.dumps(evidence))
            (root / "probe.json").write_text(
                json.dumps(
                    {
                        "http_status": 200,
                        "base_url_host": "api.kimi.com",
                        "model_ids": ["kimi-for-coding"],
                        "request_id": "request-1",
                        "rate_limit_headers": {},
                    }
                )
            )
            events = [
                {
                    "role": "assistant",
                    "model": "kimi-for-coding",
                    "tool_calls": [
                        {
                            "id": "Read_0",
                            "function": {"name": "Read", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "Read_0", "content": "evidence"},
                {
                    "role": "assistant",
                    "content": json.dumps({"status": "ok", **evidence}),
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            ]
            (root / "stream.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n"
            )
            (root / "stderr").write_text("")
            sessions = root / "sessions"
            sessions.mkdir()
            (sessions / "wire.jsonl").write_text(
                json.dumps(
                    {
                        "response": {
                            "request_id": "agent-request-1",
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        }
                    }
                )
                + "\n"
            )
            summary = MODULE.summarize(
                stream_path=root / "stream.jsonl",
                stderr_path=root / "stderr",
                provider_probe_path=root / "probe.json",
                evidence_path=root / "evidence.json",
                sessions_path=sessions,
                requested_model="kimi-for-coding",
                cli_version="0.37.2",
                exit_status=0,
            )
            self.assertTrue(summary["passed"])
            self.assertEqual(summary["kimi_code"]["tool_call_counts"], {"Read": 1})
            self.assertEqual(
                summary["kimi_code"]["usage_samples"],
                [{"input_tokens": 10, "output_tokens": 5}],
            )
            self.assertEqual(
                summary["kimi_code"]["agent_request_ids"], ["agent-request-1"]
            )
            self.assertEqual(summary["kimi_code"]["failure_category"], "NONE")


if __name__ == "__main__":
    unittest.main()
