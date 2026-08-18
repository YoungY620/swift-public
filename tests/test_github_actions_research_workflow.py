from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "forecast-edge-research.yml"


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

    def test_each_release_restores_and_republishes_cumulative_history(self) -> None:
        restore = self.step("Restore cumulative research history from the latest Release")
        package = self.step("Build cumulative research Release")
        publish = self.step("Publish cumulative research Release")

        self.assertIn("gh release download", restore)
        self.assertIn("forecast-edge-research-history.tar.gz", restore)
        self.assertIn("history_archive_sha256", restore)
        self.assertIn('tar -C "$REPORTS_DIR" -czf "$HISTORY_ARCHIVE_PATH" reports', package)
        self.assertIn("previous_release", package)
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

    def test_release_permission_and_public_disclosure_gate_are_explicit(self) -> None:
        self.assertIn("contents: write", self.workflow)
        self.assertIn("ALLOW_PUBLIC_RESEARCH_RELEASES", self.workflow)
        self.assertIn("GitHub Releases inherit repository visibility", self.workflow)


if __name__ == "__main__":
    unittest.main()
