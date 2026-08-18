from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "forecast-edge-research.yml"
SCHEDULED_PROMPT_PATH = ROOT / "automation" / "prompts" / "scheduled-scan.zh-CN.md"


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

    def test_each_release_contains_one_report_and_restores_a_rolling_window(self) -> None:
        restore = self.step("Restore recent research Releases")
        package = self.step("Build current-iteration research Release")
        publish = self.step("Publish current-iteration research Release")

        self.assertIn("gh release download", restore)
        self.assertIn("FORECAST_EDGE_HISTORY_RELEASE_COUNT", self.workflow)
        self.assertIn('.[0:$limit]', restore)
        self.assertIn("forecast-edge-research-report.tar.gz", restore)
        self.assertIn("report_archive_sha256", restore)
        self.assertIn(
            'tar -C "$REPORTS_DIR" -czf "$REPORT_ARCHIVE_PATH" "$current_report_dir_relative"',
            package,
        )
        self.assertIn('--argjson report_count "1"', package)
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

    def test_polymarket_trade_probe_is_read_only(self) -> None:
        probe = self.step("Probe Polymarket runner trade availability")
        self.assertIn("https://polymarket.com/api/geoblock", probe)
        self.assertIn("https://clob.polymarket.com/", probe)
        self.assertIn("read-only network probe", probe)
        self.assertNotIn("KIMI_CODE_API_KEY", probe)
        self.assertNotIn("private key", probe.lower())
        self.assertNotIn("post-order", probe.lower())

    def test_release_permission_and_public_disclosure_gate_are_explicit(self) -> None:
        self.assertIn("contents: write", self.workflow)
        self.assertIn("ALLOW_PUBLIC_RESEARCH_RELEASES", self.workflow)
        self.assertIn("GitHub Releases inherit repository visibility", self.workflow)

    def test_only_kimi_api_key_is_a_configured_secret(self) -> None:
        configured_secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.workflow))
        self.assertEqual(configured_secrets, {"KIMI_CODE_API_KEY"})

    def test_stable_public_options_are_workflow_constants(self) -> None:
        self.assertIn("ENABLED_PREDICTION_MARKETS: polymarket", self.workflow)
        self.assertIn('KIMI_CODE_VERSION: "0.37.0"', self.workflow)
        self.assertIn("KIMI_MODEL_NAME: kimi-for-coding", self.workflow)
        self.assertNotIn("vars.KIMI_CODE_VERSION", self.workflow)
        self.assertNotIn("vars.KIMI_MODEL_NAME", self.workflow)
        self.assertNotIn("vars.ENABLED_PREDICTION_MARKETS", self.workflow)

    def test_prompt_requires_current_holdings_and_novel_event_research(self) -> None:
        prompt = SCHEDULED_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("重新研究全部当前持仓", prompt)
        self.assertIn("至少 10 个", prompt)
        self.assertIn("不得直接沿用历史概率", prompt)
        self.assertIn("只能新增一个", prompt)


if __name__ == "__main__":
    unittest.main()
