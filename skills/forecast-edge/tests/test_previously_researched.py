"""Executable examples for the previously-researched history script."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "previously_researched.py"


def write_report(reports_dir: Path, run: str, document: dict) -> None:
    run_dir = reports_dir / run
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps(document), encoding="utf-8")


def entry(market_id: str, url: str = "") -> dict:
    return {"market": {"id": market_id, "url": url}}


class PreviouslyResearchedTests(unittest.TestCase):
    def run_script(self, reports_dir: Path) -> tuple[int, dict, str]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), str(reports_dir)],
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, payload, proc.stderr

    def test_collects_market_ids_and_event_slugs_from_positions_and_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            write_report(reports, "run-a", {
                "current_positions": [entry("111", "https://polymarket.com/event/fed-july?tid=111")],
                "candidates": [entry("222", "https://polymarket.com/event/venezuela-leader?tid=222")],
            })
            write_report(reports, "run-b", {
                "current_positions": [entry("111", "https://polymarket.com/event/fed-july?tid=111")],
                "candidates": [entry("333")],
            })
            code, payload, _ = self.run_script(reports)
            self.assertEqual(code, 0)
            self.assertEqual(payload["reports_scanned"], 2)
            self.assertEqual(payload["researched_market_ids"], ["111", "222", "333"])
            self.assertEqual(
                payload["researched_event_slugs"],
                ["fed-july", "venezuela-leader"],
            )

    def test_empty_archive_returns_empty_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, payload, _ = self.run_script(Path(tmp))
            self.assertEqual(code, 0)
            self.assertEqual(payload["reports_scanned"], 0)
            self.assertEqual(payload["researched_market_count"], 0)
            self.assertEqual(payload["researched_event_count"], 0)

    def test_missing_directory_is_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_script(Path(tmp) / "nope")
            self.assertEqual(code, 2)
            self.assertIn("previously_researched", stderr)

    def test_malformed_report_is_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            run_dir = reports / "run-bad"
            run_dir.mkdir()
            (run_dir / "report.json").write_text("not json", encoding="utf-8")
            code, _, stderr = self.run_script(reports)
            self.assertEqual(code, 2)
            self.assertIn("unreadable report", stderr)

    def test_novel_quota_config_present(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        scan = config["scan"]
        self.assertEqual(scan["min_novel_deep_research_events"], 10)
        self.assertGreaterEqual(
            scan["candidate_research_attempt_threshold_per_tier"],
            scan["min_novel_deep_research_events"],
        )


if __name__ == "__main__":
    unittest.main()
