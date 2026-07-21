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

    def test_enumerates_the_active_universe_before_building_a_hundred_candidate_prescreen(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        scan = config["scan"]
        self.assertTrue(scan["enumerate_active_event_universe"])
        self.assertEqual(scan["active_event_page_size"], 100)
        self.assertEqual(scan["active_event_safety_limit"], 100000)
        self.assertEqual(scan["prescreen_event_target"], 100)
        self.assertEqual(scan["candidate_research_soft_limit"], 10)

    def test_final_report_language_is_simplified_chinese(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        self.assertEqual(config["report"]["language"], "zh-CN")

    def test_ten_point_raw_edge_preference_is_soft_and_auditable(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        execution = config["execution"]
        self.assertEqual(execution["soft_minimum_raw_edge"], 0.1)
        self.assertEqual(execution["strong_minimum_raw_edge"], 0.3)
        self.assertFalse(execution["soft_minimum_raw_edge_is_hard"])
        self.assertTrue(execution["require_soft_edge_override_reason"])

    def test_discovers_current_pusd_instead_of_using_legacy_collateral(self) -> None:
        config = json.loads((ROOT / "config" / "defaults.json").read_text())
        self.assertEqual(config["data"]["collateral_symbol"], "pUSD")
        self.assertTrue(config["data"]["discover_collateral_each_scan"])
        self.assertFalse(config["data"]["allow_fixed_legacy_collateral"])


if __name__ == "__main__":
    unittest.main()
