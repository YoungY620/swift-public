"""Executable examples for fees, VWAP crossings, half Kelly, reserve, and actions."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import trade_math  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


def sample() -> dict:
    return json.loads((FIXTURES / "trade-input.json").read_text())


class TradeMathTests(unittest.TestCase):
    def test_fee_formula_is_applied_per_filled_price(self) -> None:
        self.assertAlmostEqual(trade_math.fee_per_share(0.5, 0.04), 0.01)

    def test_new_position_uses_half_kelly_and_preserves_edge(self) -> None:
        result = trade_math.evaluate_trade(sample())
        self.assertEqual(result["signal"], "BUY")
        self.assertAlmostEqual(result["kelly"]["configured_fraction"], 0.5)
        self.assertGreaterEqual(result["buy"]["edge_retention"], 0.7)
        self.assertLessEqual(result["buy"]["suggested_cash"], result["orderbook_capacity"]["cash"] + 1e-8)
        self.assertEqual(result["cash"]["reserve"], 100.0)  # median(30,40,50) * 2.5

    def test_zero_cash_preserves_market_opportunity_but_not_account_buy(self) -> None:
        document = sample()
        document["bankroll"] = 0
        document["available_cash"] = 0
        document["reserve"] = {"reference_position_costs": [], "target_positions": 2.5}
        result = trade_math.evaluate_trade(document)
        self.assertTrue(result["opportunity"]["entry_eligible"])
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["buy"]["suggested_cash"], 0)

    def test_existing_position_adds_only_above_current_half_kelly_target(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 20, "cost": 10}
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "ADD")

    def test_complete_bid_vwap_cross_causes_sell_even_at_an_accounting_loss(self) -> None:
        document = sample()
        document["model_probability"] = 0.60
        document["current_position"] = {"shares": 10, "cost": 9}
        document["book"]["bids"] = [{"price": 0.70, "size": 10}]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "SELL")
        self.assertTrue(result["sell"]["fill"]["fully_filled"])

    def test_smaller_target_without_sell_cross_holds_instead_of_rebalancing(self) -> None:
        document = sample()
        document["model_probability"] = 0.55
        document["current_position"] = {"shares": 300, "cost": 200}
        document["book"]["bids"] = [{"price": 0.40, "size": 500}]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["buy"]["suggested_shares"], 0.0)

    def test_no_side_is_valued_as_one_minus_yes_probability(self) -> None:
        document = sample()
        document["side"] = "NO"
        document["model_probability"] = 0.35
        result = trade_math.evaluate_trade(document)
        self.assertAlmostEqual(result["model_value"], 0.65)


if __name__ == "__main__":
    unittest.main()
