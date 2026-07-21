"""Examples and edge cases for the public Polymarket data normalizer."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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


class FailingStubClient:
    def get_json(self, url: str) -> object:
        raise polymarket_data.DataError(f"request failed for {url}: not found")


class PagingStubClient:
    def __init__(self, rows: list[dict[str, object]], *, repeat_first_page: bool = False) -> None:
        self.rows = rows
        self.repeat_first_page = repeat_first_page
        self.requested_urls: list[str] = []

    def get_json(self, url: str) -> object:
        self.requested_urls.append(url)
        query = parse_qs(urlparse(url).query)
        limit = int(query["limit"][0])
        offset = int(query.get("after_cursor", [0])[0])
        requested_offset = offset
        if self.repeat_first_page and requested_offset > 0:
            offset = 0
        page = [
            {
                "id": f"event-{row['id']}",
                "slug": f"event-{row['id']}",
                "markets": [row],
            }
            for row in self.rows[offset : offset + limit]
        ]
        next_offset = requested_offset + limit
        next_cursor = str(next_offset) if self.repeat_first_page or next_offset < len(self.rows) else ""
        return {"events": page, "next_cursor": next_cursor}


def raw_market(identifier: int, question: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": str(identifier),
        "question": question,
        "slug": f"market-{identifier}",
        "conditionId": f"condition-{identifier}",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "clobTokenIds": f'["yes-{identifier}", "no-{identifier}"]',
    }
    value.update(overrides)
    return value


def raw_event(identifier: int, title: str, market: dict[str, object], **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": str(identifier),
        "title": title,
        "slug": f"event-{identifier}",
        "active": True,
        "closed": False,
        "markets": [market],
    }
    value.update(overrides)
    return value


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

    def test_missing_optional_public_profile_does_not_invalidate_account_data(self) -> None:
        profile = polymarket_data.fetch_public_profile(
            "0x2222222222222222222222222222222222222222",
            FailingStubClient(),
            "https://gamma.test",
        )
        self.assertFalse(profile["available"])
        self.assertEqual(profile["source"], "gamma_public_profile")

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

    def test_active_event_universe_pages_until_exhaustion(self) -> None:
        rows = [raw_market(index, f"Policy market {index}") for index in range(5)]
        client = PagingStubClient(rows)
        events, scan = polymarket_data.fetch_active_event_universe(
            client,
            "https://gamma.test",
            page_size=2,
            safety_limit=10,
        )
        self.assertEqual(len(events), 5)
        self.assertEqual(scan["fetched_events"], 5)
        self.assertEqual(scan["expanded_markets"], 5)
        self.assertEqual(scan["pages_requested"], 3)
        self.assertTrue(scan["complete"])
        self.assertIn("after_cursor=4", client.requested_urls[-1])

    def test_active_event_universe_rejects_repeated_pages(self) -> None:
        client = PagingStubClient(
            [raw_market(1, "Policy one"), raw_market(2, "Policy two")],
            repeat_first_page=True,
        )
        with self.assertRaisesRegex(polymarket_data.DataError, "repeated a page"):
            polymarket_data.fetch_active_event_universe(
                client,
                "https://gamma.test",
                page_size=2,
                safety_limit=10,
            )

    def test_candidate_events_exclude_holdings_sports_duplicates_and_unusable_events(self) -> None:
        held = raw_event(1, "Held policy event", raw_market(1, "Held policy market", conditionId="held-condition"))
        sports = raw_event(2, "NBA Finals", raw_market(2, "Will Team A win?"), category="Sports")
        politics = raw_event(3, "Senate budget", raw_market(3, "Will the Senate pass the budget?"))
        duplicate = dict(politics)
        unusable = raw_event(4, "Unusable event", raw_market(4, "Market without orders", acceptingOrders=False))
        snapshot = {
            "positions": {
                "positions": [
                    {"conditionId": "held-condition", "asset": "yes-1"},
                ]
            }
        }

        result = polymarket_data.build_candidate_events(
            [held, sports, politics, duplicate, unusable],
            snapshot,
        )

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["id"], "3")
        self.assertEqual(
            result["excluded_by_reason"],
            {
                "current_holding_event": 1,
                "duplicate": 1,
                "no_tradeable_markets": 1,
                "sports_or_esports": 1,
            },
        )

    def test_sports_filter_does_not_reject_the_nuclear_football_phrase(self) -> None:
        market = raw_market(5, "Will the President transfer the nuclear football?")
        self.assertFalse(polymarket_data.is_sports_or_esports_market(market))

    def test_market_url_uses_event_slug_for_multi_market_events(self) -> None:
        market = raw_market(7, "Will the event happen?")
        market.update({"slug": "child-market", "eventSlug": "parent-event"})
        self.assertEqual(
            polymarket_data.normalize_market(market)["url"],
            "https://polymarket.com/event/parent-event?tid=7",
        )


if __name__ == "__main__":
    unittest.main()
