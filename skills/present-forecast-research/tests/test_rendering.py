"""Rendering and collaborative-document checks."""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import present_report  # noqa: E402
import report_contract  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "report.json"


class RenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report, self.source, self.source_hash = report_contract.load_report(FIXTURE)

    def test_generate_five_immutable_artifacts_with_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = present_report.generate(
                self.report, self.source, self.source_hash, root, mode="automatic"
            )
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {
                    "manifest.json",
                    "private-brief.md",
                    "research-report.md",
                    "public-signals.json",
                    "public-posts.md",
                },
            )
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            for name in ("manifest.json", "private-brief.md", "research-report.md"):
                self.assertEqual(stat.S_IMODE((destination / name).stat().st_mode), 0o600)
            for name in ("public-signals.json", "public-posts.md"):
                self.assertEqual(stat.S_IMODE((destination / name).stat().st_mode), 0o644)
            second = present_report.generate(
                self.report, self.source, self.source_hash, root, mode="automatic"
            )
            self.assertEqual(second, destination)

    def test_report_contains_mermaid_and_plain_text_numbered_trees(self) -> None:
        tree = json.loads(
            (ROOT.parent / "forecast-edge" / "tests" / "fixtures" / "probability-tree.json")
            .read_text(encoding="utf-8")
        )
        markdown = present_report.render_tree_visuals(tree)
        self.assertIn("```mermaid", markdown)
        self.assertIn("<summary>纯文本概率树</summary>", markdown)
        self.assertIn("1.1", markdown)
        self.assertIn("权重", markdown)

    def test_public_posts_use_direction_markers_and_fit_limit(self) -> None:
        public = present_report.build_public_signals(
            self.report, self.source_hash, "2026-07-19T01:00:00Z"
        )
        posts = present_report.render_public_posts(public).strip().split("\n\n---\n\n")
        self.assertTrue(posts[0].startswith("➕✅ 买 YES"))
        self.assertTrue(all(len(post) <= 400 for post in posts))

    def test_collaborative_prose_can_change_but_generated_blocks_cannot(self) -> None:
        original = present_report.render_research_report(self.report)
        revised = original.replace("# 预测市场完整研究报告", "# 预测市场完整研究报告\n\n编辑补充说明。", 1)
        present_report.verify_collaborative_draft(self.report, revised)
        modified = revised.replace("本轮分配：25.00", "本轮分配：26.00", 1)
        with self.assertRaisesRegex(present_report.PresentationError, "was modified"):
            present_report.verify_collaborative_draft(self.report, modified)

    def test_prepare_and_finalize_collaborative_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            draft = present_report.prepare_draft(
                self.report, self.source_hash, root / "workspace"
            )
            revised = draft.read_text(encoding="utf-8").replace(
                "# 预测市场完整研究报告", "# 预测市场完整研究报告\n\n人工编辑说明。", 1
            )
            draft.write_text(revised, encoding="utf-8")
            present_report.verify_collaborative_draft(self.report, revised)
            destination = present_report.generate(
                self.report,
                self.source,
                self.source_hash,
                root / "output",
                mode="collaborative",
                research_override=revised,
            )
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "collaborative")
            self.assertIn("人工编辑说明", (destination / "research-report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
