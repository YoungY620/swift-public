"""Executable examples for real-cash portfolio allocation and cold-start limits."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portfolio_allocate  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


def sample() -> dict:
    return json.loads((FIXTURES / "portfolio-allocation.json").read_text())


class PortfolioAllocateTests(unittest.TestCase):
    def test_allocates_once_across_ranked_opportunities(self) -> None:
        result = portfolio_allocate.allocate_portfolio(sample())
        self.assertAlmostEqual(result["portfolio"]["conservative_bankroll"], 120)
        self.assertAlmostEqual(result["reserve"]["protected_reserve_cash"], 27.5)
        self.assertAlmostEqual(result["portfolio"]["deployable_cash"], 72.5)
        by_id = {item["market_id"]: item for item in result["allocations"]}
        self.assertAlmostEqual(by_id["buy-1"]["allocated_cash"], 40)
        self.assertAlmostEqual(by_id["buy-2"]["allocated_cash"], 20)
        self.assertEqual(by_id["buy-3"]["allocated_cash"], 0)
        self.assertIn("cold-start", by_id["buy-3"]["reason"])
        self.assertAlmostEqual(by_id["add-1"]["allocated_cash"], 12.5)
        self.assertAlmostEqual(result["portfolio"]["remaining_cash"], 0)

    def test_zero_cash_never_produces_a_funded_entry(self) -> None:
        document = sample()
        document["portfolio"].update(
            {"available_cash": 0, "holdings_liquidation_value": 15, "reported_bankroll": 15}
        )
        result = portfolio_allocate.allocate_portfolio(document)
        self.assertEqual(result["portfolio"]["allocated_cash"], 0)
        self.assertTrue(all(item["allocated_cash"] == 0 for item in result["allocations"]))

    def test_rejects_nominal_bankroll_that_differs_from_real_assets(self) -> None:
        document = sample()
        document["portfolio"]["reported_bankroll"] = 1000
        with self.assertRaisesRegex(portfolio_allocate.AllocationError, "does not equal"):
            portfolio_allocate.allocate_portfolio(document)

    def test_portfolio_cap_can_reduce_a_semantically_redundant_candidate(self) -> None:
        document = sample()
        document["portfolio"]["cold_start"] = False
        document["opportunities"][0]["portfolio_cash_cap"] = 7
        result = portfolio_allocate.allocate_portfolio(document)
        self.assertEqual(result["allocations"][0]["allocated_cash"], 7)

    def test_reserve_release_requires_a_reason(self) -> None:
        document = copy.deepcopy(sample())
        document["reserve"]["release_cash"] = 5
        with self.assertRaisesRegex(portfolio_allocate.AllocationError, "release_reason"):
            portfolio_allocate.allocate_portfolio(document)

    def test_rejects_string_booleans_and_fractional_priorities(self) -> None:
        document = sample()
        document["portfolio"]["cold_start"] = "false"
        with self.assertRaisesRegex(portfolio_allocate.AllocationError, "must be boolean"):
            portfolio_allocate.allocate_portfolio(document)

        document = sample()
        document["opportunities"][0]["priority"] = 1.5
        with self.assertRaisesRegex(portfolio_allocate.AllocationError, "must be an integer"):
            portfolio_allocate.allocate_portfolio(document)


if __name__ == "__main__":
    unittest.main()
