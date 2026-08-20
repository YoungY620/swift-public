"""Executable examples for fees, VWAP crossings, fractional Kelly, reserve, and actions."""

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
    def test_uses_the_venue_book_minimum_and_rejects_a_conflicting_override(self) -> None:
        document = sample()
        document["min_order_shares"] = 1
        with self.assertRaisesRegex(
            trade_math.TradeMathError, "differs from the venue order book"
        ):
            trade_math.evaluate_trade(document)

    def test_requires_a_positive_venue_book_minimum(self) -> None:
        document = sample()
        del document["book"]["min_order_size"]
        with self.assertRaisesRegex(trade_math.TradeMathError, "min_order_size"):
            trade_math.evaluate_trade(document)

    def test_requires_adapter_normalized_fee_per_unit(self) -> None:
        document = sample()
        del document["book"]["asks"][0]["fee_per_unit"]
        with self.assertRaisesRegex(trade_math.TradeMathError, "fee_per_unit"):
            trade_math.evaluate_trade(document)

    def test_new_position_uses_fractional_kelly_and_preserves_edge(self) -> None:
        result = trade_math.evaluate_trade(sample())
        self.assertEqual(result["signal"], "BUY")
        self.assertAlmostEqual(result["kelly"]["configured_fraction"], 0.25)
        self.assertGreaterEqual(result["buy"]["edge_retention"], 0.7)
        self.assertLessEqual(result["buy"]["suggested_cash"], result["orderbook_capacity"]["cash"] + 1e-8)
        self.assertEqual(result["cash"]["reserve"], 100.0)  # median(30,40,50) * 2.5

    def test_kelly_fraction_defaults_to_one_quarter(self) -> None:
        document = sample()
        del document["kelly_fraction"]
        result = trade_math.evaluate_trade(document)
        self.assertAlmostEqual(result["kelly"]["configured_fraction"], 0.25)

    def test_position_cap_limits_new_entry_cash(self) -> None:
        result = trade_math.evaluate_trade(sample())
        self.assertEqual(result["position_cap"]["max_fraction"], 0.05)
        self.assertEqual(result["position_cap"]["total_cap_cash"], 50.0)
        self.assertEqual(result["position_cap"]["remaining_cap_cash"], 50.0)
        # The 5% position cap binds below the fractional-Kelly target of ~71.43.
        self.assertAlmostEqual(result["buy"]["suggested_cash"], 50.0)
        self.assertAlmostEqual(result["buy"]["suggested_shares"], 50.0 / 0.51)

    def test_position_cap_yields_to_kelly_when_relaxed(self) -> None:
        document = sample()
        document["max_position_fraction"] = 1.0
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["position_cap"]["remaining_cap_cash"], 1000.0)
        self.assertAlmostEqual(result["buy"]["suggested_cash"], 1000 * 0.25 * (0.14 / 0.49))
        self.assertEqual(result["signal"], "BUY")

    def test_position_cap_already_exhausted_blocks_entry(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 100, "cost": 50}
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["position_cap"]["remaining_cap_cash"], 0.0)
        self.assertEqual(result["buy"]["suggested_cash"], 0.0)
        self.assertEqual(result["signal"], "MAINTAIN")

    def test_trim_fires_when_position_share_drifts_above_threshold(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 400, "cost": 200}
        document["book"]["bids"] = [
            {"price": 0.60, "size": 1000, "fee_per_unit": 0.0096}
        ]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "TRIM")
        self.assertEqual(result["recommended_target_cash"], 200)
        trim = result["trim"]
        self.assertTrue(trim["trigger"])
        self.assertAlmostEqual(trim["share"], 0.23616)
        self.assertAlmostEqual(trim["target_fraction"], 0.036376953125, places=12)
        self.assertAlmostEqual(trim["threshold"], 0.15)
        self.assertAlmostEqual(trim["trim_cash"], 86.16)
        self.assertAlmostEqual(trim["trim_shares"], 86.16 / 0.5904)
        self.assertTrue(trim["fill"]["fully_filled"])

    def test_trim_stays_off_below_threshold(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 20, "cost": 10}
        document["book"]["bids"] = [
            {"price": 0.60, "size": 1000, "fee_per_unit": 0.0096}
        ]
        result = trade_math.evaluate_trade(document)
        self.assertFalse(result["trim"]["trigger"])
        self.assertEqual(result["trim"]["trim_shares"], 0.0)
        self.assertAlmostEqual(result["trim"]["share"], 0.011808)
        self.assertEqual(result["signal"], "ADD")

    def test_trim_skips_dust_below_minimum_order_size(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 255.76, "cost": 130}
        document["book"]["bids"] = [
            {"price": 0.60, "size": 1000, "fee_per_unit": 0.0096}
        ]
        result = trade_math.evaluate_trade(document)
        trim = result["trim"]
        self.assertGreater(trim["share"], trim["threshold"])
        self.assertFalse(trim["trigger"])
        self.assertEqual(trim["trim_shares"], 0.0)
        self.assertEqual(trim["trim_cash"], 0.0)
        self.assertEqual(result["signal"], "MAINTAIN")

    def test_sell_crossing_takes_priority_over_trim(self) -> None:
        document = sample()
        document["model_probability"] = 0.50
        document["current_position"] = {"shares": 400, "cost": 200}
        document["book"]["bids"] = [
            {"price": 0.60, "size": 1000, "fee_per_unit": 0.0096}
        ]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "SELL")
        self.assertFalse(result["trim"]["trigger"])

    def test_zero_cash_preserves_market_opportunity_but_not_account_buy(self) -> None:
        document = sample()
        document["bankroll"] = 0
        document["available_cash"] = 0
        document["reserve"] = {"reference_position_costs": [], "target_positions": 2.5}
        result = trade_math.evaluate_trade(document)
        self.assertTrue(result["opportunity"]["entry_eligible"])
        self.assertEqual(result["signal"], "MAINTAIN")
        self.assertEqual(result["buy"]["suggested_cash"], 0)

    def test_existing_position_adds_only_above_current_fractional_kelly_target(self) -> None:
        document = sample()
        document["current_position"] = {"shares": 20, "cost": 10}
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "ADD")

    def test_complete_bid_vwap_cross_causes_sell_even_at_an_accounting_loss(self) -> None:
        document = sample()
        document["model_probability"] = 0.60
        document["current_position"] = {"shares": 10, "cost": 9}
        document["book"]["bids"] = [
            {"price": 0.70, "size": 10, "fee_per_unit": 0.0084}
        ]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "SELL")
        self.assertTrue(result["sell"]["fill"]["fully_filled"])

    def test_existing_position_maintains_shares_without_a_sell_cross(self) -> None:
        document = sample()
        document["model_probability"] = 0.55
        document["current_position"] = {"shares": 300, "cost": 200}
        document["book"]["bids"] = [
            {"price": 0.40, "size": 500, "fee_per_unit": 0.0096}
        ]
        result = trade_math.evaluate_trade(document)
        self.assertEqual(result["signal"], "MAINTAIN")
        self.assertEqual(result["buy"]["suggested_shares"], 0.0)

    def test_no_side_is_valued_as_one_minus_yes_probability(self) -> None:
        document = sample()
        document["side"] = "NO"
        document["model_probability"] = 0.35
        result = trade_math.evaluate_trade(document)
        self.assertAlmostEqual(result["model_value"], 0.65)

    def test_raw_edge_must_normally_be_strictly_greater_than_ten_points(self) -> None:
        document = sample()
        document["model_probability"] = 0.60
        result = trade_math.evaluate_trade(document)
        self.assertAlmostEqual(result["soft_edge"]["raw_edge"], 0.10)
        self.assertFalse(result["soft_edge"]["passes"])
        self.assertFalse(result["opportunity"]["entry_eligible"])
        self.assertEqual(result["buy"]["suggested_cash"], 0.0)
        self.assertEqual(result["signal"], "MAINTAIN")

    def test_documented_soft_edge_override_can_allow_a_smaller_entry_edge(self) -> None:
        document = sample()
        document["model_probability"] = 0.59
        document["soft_edge_override"] = True
        document["soft_edge_override_reason"] = "短期限、高质量一手证据且净优势仍满足执行要求。"
        result = trade_math.evaluate_trade(document)
        self.assertFalse(result["soft_edge"]["passes"])
        self.assertTrue(result["soft_edge"]["override_applied"])
        self.assertTrue(result["opportunity"]["entry_eligible"])
        self.assertEqual(result["signal"], "BUY")

    def test_soft_edge_override_requires_an_auditable_reason(self) -> None:
        document = sample()
        document["model_probability"] = 0.59
        document["soft_edge_override"] = True
        with self.assertRaisesRegex(trade_math.TradeMathError, "override_reason is required"):
            trade_math.evaluate_trade(document)


if __name__ == "__main__":
    unittest.main()
