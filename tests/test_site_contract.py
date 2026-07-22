from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SiteContractTests(unittest.TestCase):
    def test_frontend_uses_safe_dom_rendering_and_external_link_protection(self) -> None:
        javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", javascript)
        self.assertIn("textContent", javascript)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_probability_rail_is_present(self) -> None:
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("probability-rail", html)
        self.assertIn("prefers-reduced-motion", css)


if __name__ == "__main__":
    unittest.main()
