from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SiteContractTests(unittest.TestCase):
    def test_frontend_uses_safe_dom_rendering(self) -> None:
        javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", javascript)
        self.assertIn("textContent", javascript)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_support_panel_has_two_accessible_tabs(self) -> None:
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('role="tablist"', html)
        self.assertIn('data-support-tab="evidence"', html)
        self.assertIn('data-support-tab="tree"', html)
        self.assertEqual(html.count('role="tabpanel"'), 2)

    def test_probability_tree_renderer_covers_all_node_types(self) -> None:
        javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        for node_type in ("estimate", "partition", "bayes_update", "complement"):
            self.assertIn(node_type, javascript)
        self.assertIn("makeProbabilityNode", javascript)
        self.assertIn("makeEvidenceRefs", javascript)
        self.assertIn("ArrowRight", javascript)

    def test_probability_tree_assigns_hierarchical_paths(self) -> None:
        javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('path = "1"', javascript)
        self.assertIn("branchPath", javascript)
        self.assertIn("branchIndex + 1", javascript)
        self.assertIn("updateIndex + 2", javascript)
        self.assertIn('0, "1"', javascript)
        self.assertIn("tree-node-path", css)
        self.assertIn("tree-branch-path", css)

    def test_responsive_and_reduced_motion_rules_remain(self) -> None:
        css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("tree-view", css)
        self.assertIn("@media (max-width: 520px)", css)
        self.assertIn("prefers-reduced-motion", css)


if __name__ == "__main__":
    unittest.main()
