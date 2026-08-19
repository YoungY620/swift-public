from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "forecast-edge-research.yml"
SCHEDULED_PROMPT_PATH = ROOT / "automation" / "prompts" / "scheduled-scan.zh-CN.md"
REPAIR_PROMPT_PATH = ROOT / "automation" / "prompts" / "repair-trade-intents.zh-CN.md"


class GitHubActionsResearchWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def step(self, name: str) -> str:
        marker = f"      - name: {name}\n"
        start = self.workflow.index(marker)
        end = self.workflow.find("\n      - name: ", start + len(marker))
        return self.workflow[start:] if end == -1 else self.workflow[start:end]

    def test_research_data_stays_outside_the_checkout_and_git_branches(self) -> None:
        self.assertIn('runtime_dir="$RUNNER_TEMP/forecast-edge-', self.workflow)
        self.assertIn('echo "RUNTIME_DIR=$runtime_dir"', self.workflow)
        self.assertNotIn("runtime/github-actions", self.workflow)
        self.assertNotIn("forecast-edge-private-report", self.workflow)
        self.assertNotIn("git push", self.workflow)
        self.assertNotIn("git checkout -b", self.workflow)

    def test_research_job_uses_github_hosted_linux(self) -> None:
        self.assertEqual(self.workflow.count("runs-on: ubuntu-latest"), 2)
        self.assertNotIn("self-hosted", self.workflow)
        self.assertNotIn("local-mac-forecast-edge", self.workflow)
        validate = self.step("Validate workflow configuration")
        self.assertIn("BASH_VERSINFO[0] < 4", validate)
        self.assertIn("curl gh jq npm python3 sha256sum tar timeout", validate)

    def test_each_release_contains_one_report_per_adapter_and_restores_a_rolling_window(self) -> None:
        restore = self.step("Restore recent research Releases")
        package = self.step("Build current-iteration research Release")
        publish = self.step("Publish current-iteration research Release")

        self.assertIn("gh release download", restore)
        self.assertIn("FORECAST_EDGE_HISTORY_RELEASE_COUNT", self.workflow)
        self.assertIn('.[0:$limit]', restore)
        self.assertIn("forecast-edge-research-report.tar.gz", restore)
        self.assertIn("report_archive_sha256", restore)
        self.assertIn('"${current_report_directories[@]}"', package)
        self.assertIn('--arg schema_version "2.0"', package)
        self.assertIn('reports: $reports[0]', package)
        self.assertIn('--argjson report_count "$report_count"', package)
        self.assertIn('report-$adapter.json', package)
        self.assertIn('manifest_schema" == "1.0"', restore)
        self.assertIn('manifest_schema" != "2.0"', restore)
        self.assertIn("history_releases_used", package)
        self.assertNotIn("history_archive", package)
        self.assertIn('release_tag="forecast-edge-research/run-', package)
        self.assertIn('gh release create "$RELEASE_TAG"', publish)
        self.assertIn('--target "$GITHUB_SHA"', publish)
        self.assertIn('--latest=false', publish)

    def test_trade_intent_artifact_contains_only_jsonl(self) -> None:
        upload = self.step("Upload validated trade-intent artifact")

        self.assertEqual(self.workflow.count("uses: actions/upload-artifact@v4"), 1)
        self.assertIn("path: ${{ env.TRADE_INTENTS_PATH }}", upload)
        self.assertNotIn("VALIDATION_SUMMARY_PATH", upload)
        self.assertNotIn("MANIFEST", upload)
        self.assertNotIn("path: |", upload)

    def test_research_job_does_not_run_a_venue_trade_probe(self) -> None:
        self.assertNotIn("Probe Polymarket runner trade availability", self.workflow)
        self.assertNotIn("https://polymarket.com/api/geoblock", self.workflow)
        self.assertNotIn("Polymarket runner trade-availability", self.workflow)

    def test_kimi_provider_is_probed_before_agent_execution(self) -> None:
        probe = self.step("Probe Kimi model availability")

        self.assertLess(
            self.workflow.index("      - name: Probe Kimi model availability\n"),
            self.workflow.index("      - name: Run Kimi Code — initial attempt\n"),
        )
        self.assertIn("secrets.KIMI_CODE_API_KEY", probe)
        self.assertIn('"$KIMI_MODEL_BASE_URL/models"', probe)
        self.assertIn('.id == $model', probe)
        self.assertNotIn('echo "$KIMI_MODEL_API_KEY"', probe)

    def test_python_dependencies_are_isolated_in_a_runner_temp_venv(self) -> None:
        install = self.step("Install JSON Schema validator")

        self.assertIn('echo "PYTHON_VENV_DIR=$runtime_dir/python-venv"', self.workflow)
        self.assertIn('python3 -m venv "$PYTHON_VENV_DIR"', install)
        self.assertIn('"$PYTHON_VENV_DIR/bin/python" -m pip install', install)
        self.assertIn('echo "$PYTHON_VENV_DIR/bin" >> "$GITHUB_PATH"', install)
        self.assertNotIn("--break-system-packages", install)

    def test_release_permission_and_public_disclosure_gate_are_explicit(self) -> None:
        self.assertIn("contents: write", self.workflow)
        self.assertIn("ALLOW_PUBLIC_RESEARCH_RELEASES", self.workflow)
        self.assertIn("GitHub Releases inherit repository visibility", self.workflow)

    def test_only_required_api_and_notification_secrets_are_configured(self) -> None:
        configured_secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.workflow))
        self.assertEqual(
            configured_secrets,
            {
                "KIMI_CODE_API_KEY",
                "FEISHU_APP_ID",
                "FEISHU_APP_SECRET",
                "FEISHU_CHAT_ID",
            },
        )

    def test_published_market_urls_are_venue_neutral_and_verified(self) -> None:
        verify = self.step("Resolve and verify published market URLs")
        format_issue = self.step("Format Issue notification")

        self.assertLess(
            self.workflow.index("      - name: Resolve and verify published market URLs\n"),
            self.workflow.index("      - name: Build current-iteration research Release\n"),
        )
        self.assertIn('parsed.scheme != "https"', verify)
        self.assertIn("--location --fail-with-body", verify)
        self.assertIn("%{http_code}", verify)
        self.assertIn('[[ "$http_status" != "200" ]]', verify)
        self.assertIn('echo "URL_VERIFICATION_PATH=$runtime_dir/url_verification.json"', self.workflow)
        self.assertIn('all(.[]; .http_status == 200)', verify)
        self.assertIn('market_data.py', verify)
        self.assertIn('--adapter "$venue" canonical-url "$market_identifier"', verify)
        self.assertIn('.value.venue == $venue', verify)
        self.assertIn('.value.multi_market_identifier == $market_identifier', verify)
        self.assertNotIn("polymarket_data.py", verify)
        self.assertNotIn("gamma", verify.lower())
        self.assertNotIn("polymarket", verify.lower())
        self.assertIn("NOTIFICATION_ITEMS_PATH", format_issue)
        self.assertIn("预测市场：\\(.venue)", format_issue)
        self.assertIn("[打开已验证的市场页面]", format_issue)

    def test_feishu_receives_the_exact_issue_body_as_a_card_after_issue_creation(self) -> None:
        send = self.step("Send Issue notification to Feishu")

        self.assertLess(
            self.workflow.index("      - name: Create Issue notification\n"),
            self.workflow.index("      - name: Send Issue notification to Feishu\n"),
        )
        self.assertIn('--rawfile issue "$RUNTIME_DIR/issue.md"', send)
        self.assertIn('msg_type: "interactive"', send)
        self.assertIn('schema: "2.0"', send)
        self.assertIn('tag: "markdown", content: $issue', send)
        self.assertIn('tag: "plain_text", content: $title', send)
        self.assertIn('template: "blue"', send)
        self.assertNotIn('msg_type: "text"', send)
        self.assertNotIn("lark_md", send)
        self.assertIn('uuid: $uuid', send)
        self.assertIn("receive_id_type=chat_id", send)
        self.assertNotIn("FEISHU_RECEIVE_ID_TYPE", send)
        self.assertNotIn("FEISHU_EXPECTED_BOT_NAME", send)

    def test_stable_public_options_are_workflow_constants(self) -> None:
        self.assertIn(
            "ENABLED_PREDICTION_MARKETS: ${{ vars.FORECAST_EDGE_MARKET_ADAPTERS || 'polymarket' }}",
            self.workflow,
        )
        self.assertIn('KIMI_CODE_VERSION: "0.37.0"', self.workflow)
        self.assertIn("KIMI_MODEL_NAME: kimi-for-coding", self.workflow)
        self.assertIn(
            "KIMI_MODEL_BASE_URL: https://api.kimi.com/coding/v1", self.workflow
        )
        self.assertNotIn("vars.KIMI_CODE_VERSION", self.workflow)
        self.assertNotIn("vars.KIMI_MODEL_NAME", self.workflow)
        self.assertNotIn("vars.KIMI_MODEL_BASE_URL", self.workflow)
        self.assertNotIn("vars.ENABLED_PREDICTION_MARKETS", self.workflow)

    def test_all_validation_attempts_use_the_multi_venue_iteration_validator(self) -> None:
        self.assertEqual(self.workflow.count("scripts/validate_research_iteration.py"), 4)
        self.assertEqual(self.workflow.count('--current-reports-manifest "$CURRENT_REPORTS_MANIFEST"'), 4)
        self.assertEqual(self.workflow.count('--account-snapshots-manifest "$ACCOUNT_SNAPSHOTS_MANIFEST"'), 4)
        self.assertEqual(
            self.workflow.count(
                '--expected-account-snapshots-sha256 "${{ steps.account-snapshots.outputs.sha256 }}"'
            ),
            4,
        )
        self.assertEqual(self.workflow.count('--repository-root "$GITHUB_WORKSPACE"'), 4)
        self.assertNotIn("CURRENT_REPORT_PATH_FILE", self.workflow)
        self.assertNotIn("--polymarket-account-snapshot", self.workflow)

    def test_trade_intent_contract_requires_explicit_venue_and_routing_tuple(self) -> None:
        prompt = (ROOT / "automation" / "prompts" / "trade-intents.zh-CN.md").read_text(
            encoding="utf-8"
        )
        schema = (ROOT / "schemas" / "trade-intent.schema.json").read_text(encoding="utf-8")

        self.assertIn('"venue"', schema)
        self.assertIn('"multi_market_identifier"', schema)
        self.assertIn('"instrument_identifier"', schema)
        self.assertIn("report.run.account.adapter", prompt)
        self.assertIn("market.multi_market_identifier", prompt)
        self.assertIn("market.instrument_identifier", prompt)

    def test_kimi_failures_emit_bounded_diagnostics(self) -> None:
        for attempt in range(1, 5):
            self.assertIn(
                f"sed -n '1,200p' \"$RUNTIME_DIR/kimi-attempt-{attempt}.stderr\" >&2",
                self.workflow,
            )

    def test_prompt_requires_current_holdings_and_novel_event_research(self) -> None:
        prompt = SCHEDULED_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("重新研究它的全部当前持仓", prompt)
        self.assertIn("至少 10 个", prompt)
        self.assertIn("不得直接沿用历史概率", prompt)
        self.assertIn("新增且只新增一个报告", prompt)

    def test_repair_prompt_distinguishes_history_from_the_current_report(self) -> None:
        prompt = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
        render = self.step("Render initial and repair prompts")

        self.assertIn("{{BASELINE_REPORTS_PATH}}", prompt)
        self.assertIn("路径不在历史报告清单", prompt)
        self.assertIn("每个已启用 adapter 必须恰好有一个本轮报告", prompt)
        self.assertIn("不得把历史报告误当成", prompt)
        self.assertIn("{{BASELINE_REPORTS_PATH}}", render)


if __name__ == "__main__":
    unittest.main()
