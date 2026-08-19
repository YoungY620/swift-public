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
    def run_script(self, reports_dir: Path, adapter: str | None = None) -> tuple[int, dict, str]:
        command = [sys.executable, str(SCRIPT), str(reports_dir)]
        if adapter is not None:
            command.extend(["--adapter", adapter])
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, payload, proc.stderr

    def test_collects_market_ids_without_interpreting_venue_urls(self) -> None:
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
            self.assertEqual(payload["researched_event_identifiers"], [])
            self.assertNotIn("researched_event_slugs", payload)

    def test_adapter_filter_keeps_identifiers_venue_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            write_report(
                reports,
                "run-poly",
                {
                    "run": {"account": {"adapter": "polymarket"}},
                    "current_positions": [],
                    "candidates": [
                        {
                            "market": {
                                "id": "local-id",
                                "multi_market_identifier": "poly-market",
                                "event_identifier": "poly-event",
                            }
                        }
                    ],
                },
            )
            write_report(
                reports,
                "run-kalshi",
                {
                    "run": {"account": {"adapter": "kalshi"}},
                    "current_positions": [],
                    "candidates": [
                        {
                            "market": {
                                "id": "local-id",
                                "multi_market_identifier": "kalshi-market",
                                "event_identifier": "kalshi-event",
                            }
                        }
                    ],
                },
            )

            code, payload, _ = self.run_script(reports, "kalshi")

            self.assertEqual(code, 0)
            self.assertEqual(payload["reports_scanned"], 1)
            self.assertEqual(payload["researched_market_identifiers"], ["kalshi-market"])
            self.assertEqual(payload["researched_event_identifiers"], ["kalshi-event"])

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

if __name__ == "__main__":
    unittest.main()
