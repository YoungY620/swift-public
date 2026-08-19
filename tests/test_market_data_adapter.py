from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "forecast-edge" / "scripts" / "market_data.py"
SPEC = importlib.util.spec_from_file_location("market_data", MODULE_PATH)
assert SPEC and SPEC.loader
market_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(market_data)


class MarketDataAdapterTests(unittest.TestCase):
    def test_rejects_unknown_adapter(self) -> None:
        registry = {"schema_version": "1.0", "adapters": {}}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(market_data.AdapterError, "unknown market adapter"):
                market_data.adapter_script(
                    registry,
                    "kalshi",
                    Path(temporary),
                    "market",
                )

    def test_rejects_adapter_script_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            skill_root = parent / "skill"
            skill_root.mkdir()
            outside = parent / "outside.py"
            outside.write_text("print('unsafe')\n", encoding="utf-8")
            registry = {
                "schema_version": "1.0",
                "adapters": {
                    "unsafe": {
                        "script": "../outside.py",
                        "commands": ["market"],
                    }
                },
            }

            with self.assertRaisesRegex(market_data.AdapterError, "escapes"):
                market_data.adapter_script(registry, "unsafe", skill_root, "market")

    def test_canonical_url_must_be_safe_https(self) -> None:
        self.assertEqual(
            market_data.require_safe_https_url("https://example.com/market/one"),
            "https://example.com/market/one",
        )
        for value in (
            "http://example.com/market/one",
            "https://user:secret@example.com/market/one",
            "https://example.com/market one",
        ):
            with self.subTest(value=value):
                with self.assertRaises(market_data.AdapterError):
                    market_data.require_safe_https_url(value)


if __name__ == "__main__":
    unittest.main()
