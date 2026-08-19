from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_PATH = ROOT / ".github" / "workflows" / "forecast-edge.yml"
RESEARCH_PATH = ROOT / ".github" / "workflows" / "forecast-edge-research.yml"
EXECUTION_PATH = ROOT / ".github" / "workflows" / "execute-polymarket.yml"
SCHEDULED_PROMPT_PATH = ROOT / "automation" / "prompts" / "scheduled-scan.zh-CN.md"
REPAIR_PROMPT_PATH = ROOT / "automation" / "prompts" / "repair-trade-intents.zh-CN.md"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parent = PARENT_PATH.read_text(encoding="utf-8")
        cls.research = RESEARCH_PATH.read_text(encoding="utf-8")
        cls.execution = EXECUTION_PATH.read_text(encoding="utf-8")

    @staticmethod
    def step(workflow: str, name: str) -> str:
        marker = f"      - name: {name}\n"
        start = workflow.index(marker)
        end = workflow.find("\n      - name: ", start + len(marker))
        return workflow[start:] if end == -1 else workflow[start:end]

    def test_parent_owns_schedule_and_calls_two_reusable_subworkflows(self) -> None:
        self.assertIn('cron: "23 * * * *"', self.parent)
        self.assertIn("uses: ./.github/workflows/forecast-edge-research.yml", self.parent)
        self.assertIn("uses: ./.github/workflows/execute-polymarket.yml", self.parent)
        self.assertIn("needs: [gate, research]", self.parent)
        self.assertIn("needs.research.outputs.has-intents == 'true'", self.parent)
        self.assertIn("needs.gate.outputs.execution-ready == 'true'", self.parent)
        self.assertIn("inputs['execute-live-trades']", self.parent)
        self.assertNotIn("workflow_run:", self.parent)

    def test_children_are_reusable_and_do_not_publish(self) -> None:
        for child in (self.research, self.execution):
            self.assertIn("workflow_call:", child)
            self.assertNotIn("workflow_run:", child)
            self.assertNotIn("actions/upload-artifact", child)
            self.assertNotIn("actions/download-artifact", child)
            self.assertNotIn("gh release create", child)
            self.assertNotIn("gh issue create", child)
        self.assertNotIn("cron:", self.research)

    def test_private_trade_intents_are_staged_on_mode_0700_ec2_storage(self) -> None:
        configure = self.step(self.research, "Configure authenticated EC2 handoff channel")
        stage = self.step(self.research, "Stage private research handoff on EC2")
        self.assertIn('install -d -m 700 "$REMOTE_HANDOFF_DIR"', configure)
        self.assertIn('"$TRADE_INTENTS_PATH"', stage)
        self.assertIn("trade-intents.jsonl", stage)
        self.assertIn("chmod 600", stage)
        self.assertNotIn("trade-intents.jsonl", self.step(
            self.research, "Build current-iteration research Release"
        ))

    def test_private_research_details_are_not_written_to_actions_logs(self) -> None:
        self.assertNotIn('cat "$VALIDATION_ERRORS_PATH"', self.research)
        self.assertNotIn('cat "$VALIDATION_SUMMARY_PATH"', self.research)
        self.assertNotRegex(self.research, r"sed -n .*kimi-attempt-[1-4]\.stderr")
        self.assertNotIn("HTTP 200: $canonical_url", self.research)

    def test_parent_publishes_only_after_optional_execution_resolves(self) -> None:
        self.assertIn("needs: [gate, research, execution]", self.parent)
        release = self.step(self.parent, "Publish one final iteration Release")
        self.assertIn('gh release create "$RELEASE_TAG"', release)
        self.assertIn('"$RELEASE_RUNTIME_DIR/trade-intents.jsonl"', release)
        self.assertIn("execution-receipt.json", release)
        release_index = self.parent.index("      - name: Publish one final iteration Release\n")
        issue_index = self.parent.index("      - name: Create post-release Issue notification\n")
        feishu_index = self.parent.index("      - name: Send post-release notification to Feishu\n")
        self.assertLess(release_index, issue_index)
        self.assertLess(issue_index, feishu_index)

    def test_cleanup_only_runs_for_an_iteration_that_started(self) -> None:
        cleanup = self.parent[self.parent.index("  cleanup:\n") :]
        self.assertIn("needs.gate.outputs.should-run == 'true'", cleanup)
        self.assertIn('if [[ -d "$handoff_dir" ]]', cleanup)

    def test_no_actions_artifact_is_used_anywhere_in_the_parent_graph(self) -> None:
        combined = self.parent + self.research + self.execution
        self.assertNotIn("actions/upload-artifact", combined)
        self.assertNotIn("actions/download-artifact", combined)
        self.assertNotIn("forecast-edge-trade-intents", combined)

    def test_execution_gate_checks_toggle_without_loading_trading_credentials(self) -> None:
        gate = self.step(self.parent, "Validate schedule and private workflow configuration")
        self.assertIn("POLYMARKET_LIVE_TRADING_ENABLED", gate)
        self.assertIn("execution_ready=$execution_requested", gate)
        for name in (
            "POLYMARKET_SIGNER_PRIVATE_KEY",
            "POLYMARKET_CLOB_API_KEY",
            "POLYMARKET_CLOB_API_SECRET",
            "POLYMARKET_CLOB_API_PASSPHRASE",
        ):
            self.assertNotIn(name, gate)

    def test_execution_uses_persistent_ec2_and_sqlite_ledger(self) -> None:
        for label in ("self-hosted", "Linux", "X64", "polymarket-execution", "eu-north-1"):
            self.assertIn(f"      - {label}", self.execution)
        self.assertIn(
            "LEDGER_PATH: /home/ubuntu/poly-edge-live/runtime/execution-ledger.sqlite3",
            self.execution,
        )
        self.assertIn("cancel remainders", self.execution)
        self.assertNotIn("ssh ", self.execution)
        self.assertNotIn("scp ", self.execution)
        self.assertNotIn("aws ec2 run-instances", self.execution)
        self.assertNotIn("Dynamo", self.execution)

    def test_execution_details_stay_out_of_actions_logs_until_release(self) -> None:
        execute = self.step(
            self.execution,
            "Validate, price, execute, cancel remainders, and verify locally",
        )
        for command in ("validate", "preflight", "plan", "execute", "verify"):
            self.assertIn(f'> "$RUNTIME_DIR/{command}-command.log" 2>&1', execute)

    def test_polymarket_credentials_exist_only_in_server_local_file(self) -> None:
        self.assertIn(
            "SERVER_SECRET_PATH: /home/ubuntu/poly-edge-live/runtime/secrets.json",
            self.execution,
        )
        self.assertIn('"$SERVER_SECRET_PATH"', self.execution)
        self.assertNotIn("secrets.POLYMARKET_", self.execution)
        execution_call = self.parent[
            self.parent.index("  execution:\n") : self.parent.index("\n  release:\n")
        ]
        self.assertNotIn("secrets:", execution_call)
        self.assertNotIn("POLYMARKET_SIGNER_PRIVATE_KEY", self.parent)
        self.assertNotIn("POLYMARKET_CLOB_API_", self.parent)

    def test_execution_price_threshold_is_configurable_and_notional_bounded(self) -> None:
        self.assertIn("POLYMARKET_MAX_PRICE_DEVIATION_BPS", self.execution)
        self.assertIn("max_price_deviation_bps", self.execution)

    def test_research_uses_only_model_and_ec2_handoff_secrets(self) -> None:
        configured = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.research))
        self.assertEqual(
            configured,
            {"KIMI_CODE_API_KEY", "POLYMARKET_EC2_SSH_PRIVATE_KEY"},
        )
        research_call = self.parent[
            self.parent.index("  research:\n") : self.parent.index("\n  execution:\n")
        ]
        self.assertNotIn("secrets: inherit", research_call)
        self.assertNotIn("POLYMARKET_SIGNER_PRIVATE_KEY", research_call)
        self.assertNotIn("POLYMARKET_CLOB_API_", research_call)

    def test_public_release_requires_post_execution_disclosure_opt_ins(self) -> None:
        validate = self.step(self.research, "Validate workflow configuration")
        self.assertIn("ALLOW_PUBLIC_RESEARCH_RELEASES", validate)
        self.assertIn("ALLOW_PUBLIC_TRADE_INTENTS", validate)

    def test_prompt_requires_current_holdings_and_novel_event_research(self) -> None:
        prompt = SCHEDULED_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("重新研究全部当前持仓", prompt)
        self.assertIn("至少 10 个", prompt)
        self.assertIn("不得直接沿用历史概率", prompt)
        self.assertIn("只能新增一个", prompt)

    def test_repair_prompt_distinguishes_history_from_current_report(self) -> None:
        prompt = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
        render = self.step(self.research, "Render initial and repair prompts")
        self.assertIn("{{BASELINE_REPORTS_PATH}}", prompt)
        self.assertIn("路径不在历史报告清单", prompt)
        self.assertIn("本轮报告数量为 0", prompt)
        self.assertIn("{{BASELINE_REPORTS_PATH}}", render)


if __name__ == "__main__":
    unittest.main()
