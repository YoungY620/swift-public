"""Executable report-contract and immutable-archive examples."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_tool  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


class ReportToolTests(unittest.TestCase):
    def test_valid_report_renders_actions_and_evidence(self) -> None:
        report = report_tool.read_report(FIXTURES / "report.json")
        markdown = report_tool.render_report(report)
        self.assertIn("## 行动", markdown)
        self.assertIn("Example Statistics Office", markdown)
        self.assertIn("新增证据影响", markdown)

    def test_current_position_requires_previous_comparison_field(self) -> None:
        report = json.loads((FIXTURES / "report.json").read_text())
        del report["current_positions"][0]["comparison"]
        with self.assertRaisesRegex(report_tool.ReportError, "comparison"):
            report_tool.validate_report(report)

    def test_archive_is_immutable_and_previous_finds_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = report_tool.archive_report(FIXTURES / "report.json", root, [FIXTURES / "book.json"])
            self.assertTrue((destination / "report.json").is_file())
            self.assertTrue((destination / "report.md").is_file())
            self.assertTrue((destination / "snapshots" / "book.json").is_file())
            with self.assertRaisesRegex(report_tool.ReportError, "refusing to modify"):
                report_tool.archive_report(FIXTURES / "report.json", root, [])
            recent = report_tool.previous_reports(root, 2)
            self.assertEqual(recent[0]["run_id"], "sample-run")


if __name__ == "__main__":
    unittest.main()
