"""Executable checks for the canonical research-policy documentation."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class PolicyConfigTests(unittest.TestCase):
    def test_prioritizes_politics_and_economics_and_excludes_sports(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Prioritize political and economic candidates", skill)
        self.assertIn("Treat culture and markets as secondary domains", skill)
        self.assertIn("exclude sports and esports", skill)

    def test_enumerates_the_active_universe_before_building_a_hundred_candidate_prescreen(self) -> None:
        selection = (ROOT / "references" / "market-selection.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("active-event traversal until exhaustion", selection)
        self.assertIn("about 100 events", selection)
        self.assertIn("at least ten eligible events", selection)

    def test_final_report_language_is_simplified_chinese(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Simplified Chinese", skill)

    def test_ten_point_raw_edge_floor_is_hard(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("strictly greater than 10 percentage points", skill)
        self.assertIn("hard floor with no exceptions", skill)

    def test_shared_research_files_do_not_name_a_venue_or_collateral(self) -> None:
        paths = [
            ROOT / "SKILL.md",
            ROOT / "agents" / "openai.yaml",
            ROOT / "references" / "market-adapters.md",
            ROOT / "scripts" / "trade_math.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
        for venue_specific_name in ("polymarket", "kalshi", "pusd"):
            with self.subTest(venue_specific_name):
                self.assertNotIn(venue_specific_name, combined)


if __name__ == "__main__":
    unittest.main()
