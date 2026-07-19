"""Executable examples for domain-priority and report-language policy."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class PolicyConfigTests(unittest.TestCase):
    def test_prioritizes_politics_and_economics_and_excludes_sports(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        scan = config["scan"]
        self.assertEqual(scan["primary_themes"], ["politics", "economics"])
        self.assertEqual(scan["secondary_themes"], ["culture", "markets"])
        self.assertEqual(scan["excluded_themes"], ["sports", "esports"])

    def test_final_report_language_is_simplified_chinese(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        self.assertEqual(config["report"]["language"], "zh-CN")


if __name__ == "__main__":
    unittest.main()
