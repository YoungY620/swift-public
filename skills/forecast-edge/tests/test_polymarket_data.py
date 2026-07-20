"""Examples and edge cases for the public Polymarket data normalizer."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import polymarket_data  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


class StubClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requested_url: str | None = None

    def get_json(self, url: str) -> object:
        self.requested_url = url
        return self.response


class PolymarketDataTests(unittest.TestCase):
    def test_discovers_current_pusd_collateral_from_public_assets(self) -> None:
        client = StubClient(
            [
                {
                    "asset": "pUSD",
                    "address": "0x4444444444444444444444444444444444444444",
                    "decimals": 6,
                    "collateral_ratio": "1.00",
                    "withdrawal_fee": "0.0",
                }
            ]
        )
        asset = polymarket_data.fetch_collateral_asset(client, "https://assets.test", "pUSD")
        self.assertEqual(asset["asset"], "pUSD")
        self.assertEqual(asset["token_address"], "0x4444444444444444444444444444444444444444")
        self.assertEqual(asset["decimals"], 6)
        self.assertEqual(asset["source"], "public_collateral_assets_api")
        self.assertEqual(client.requested_url, "https://assets.test/v1/info/assets")

    def test_collateral_discovery_fails_closed_when_pusd_is_missing(self) -> None:
        client = StubClient([])
        with self.assertRaisesRegex(polymarket_data.DataError, "exactly one active pUSD"):
            polymarket_data.fetch_collateral_asset(client, "https://assets.test", "pUSD")

    def test_collateral_discovery_rejects_invalid_contract_metadata(self) -> None:
        client = StubClient([{"asset": "pUSD", "address": "not-an-address", "decimals": 6}])
        with self.assertRaisesRegex(polymarket_data.DataError, "address was invalid"):
            polymarket_data.fetch_collateral_asset(client, "https://assets.test", "pUSD")

    def test_decimals_override_requires_an_explicit_token(self) -> None:
        args = argparse.Namespace(
            token_address=None,
            decimals=18,
            collateral_symbol="pUSD",
            assets_base="https://assets.test",
        )
        with self.assertRaisesRegex(polymarket_data.DataError, "requires --token-address"):
            polymarket_data.resolve_collateral_asset(args, StubClient([]))

    def test_profile_urls_with_embedded_addresses_need_no_network_resolution(self) -> None:
        value = "https://polymarket.com/@0x3333333333333333333333333333333333333333-1700000000000"
        self.assertEqual(
            polymarket_data.extract_embedded_address(value),
            "0x3333333333333333333333333333333333333333",
        )

    def test_username_page_uses_the_repeated_public_proxy_wallet(self) -> None:
        page = (FIXTURES / "profile.html").read_bytes()
        self.assertEqual(
            polymarket_data.extract_profile_wallet(page, "fixture-user"),
            "0x2222222222222222222222222222222222222222",
        )

    def test_market_string_arrays_are_normalized_into_outcome_rows(self) -> None:
        raw = json.loads((FIXTURES / "market.json").read_text())
        market = polymarket_data.normalize_market(raw)
        self.assertEqual(market["id"], "540817")
        self.assertEqual(market["outcomes"][0], {"name": "Yes", "price": 0.51, "token_id": "yes-token"})
        self.assertEqual(market["liquidity"], 987.65)

    def test_book_is_sorted_in_executable_walk_order(self) -> None:
        raw = json.loads((FIXTURES / "book.json").read_text())
        book = polymarket_data.normalize_book(raw)
        self.assertEqual([level["price"] for level in book["asks"]], [0.51, 0.60])
        self.assertEqual([level["price"] for level in book["bids"]], [0.49, 0.40])

    def test_fee_basis_points_are_normalized_for_trade_math(self) -> None:
        fee = polymarket_data.normalize_fee_rate({"base_fee": 400})
        self.assertEqual(fee["fee_rate_bps"], 400)
        self.assertAlmostEqual(fee["fee_rate"], 0.04)


if __name__ == "__main__":
    unittest.main()
