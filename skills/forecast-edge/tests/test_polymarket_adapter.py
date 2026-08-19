from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "adapters" / "polymarket.py"
SPEC = importlib.util.spec_from_file_location("polymarket_adapter", MODULE_PATH)
assert SPEC and SPEC.loader
polymarket_adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(polymarket_adapter)


class PolymarketAdapterTests(unittest.TestCase):
    def test_normalizes_native_fee_formula_per_order_book_level(self) -> None:
        normalized = polymarket_adapter.normalize_book_response(
            {
                "book": {
                    "bids": [{"price": 0.4, "size": 3}],
                    "asks": [{"price": 0.5, "size": 2}],
                },
                "fee": {"fee_rate": 0.04},
            }
        )

        self.assertAlmostEqual(
            normalized["book"]["bids"][0]["fee_per_unit"], 0.0096
        )
        self.assertAlmostEqual(
            normalized["book"]["asks"][0]["fee_per_unit"], 0.01
        )

    def test_rejects_book_without_native_fee_metadata(self) -> None:
        with self.assertRaisesRegex(
            polymarket_adapter.NormalizationError, "fee metadata"
        ):
            polymarket_adapter.normalize_book_response(
                {"book": {"bids": [], "asks": []}}
            )


if __name__ == "__main__":
    unittest.main()
