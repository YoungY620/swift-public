from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from execution.polymarket.artifact import ArtifactError, load_and_validate_jsonl
from execution.polymarket.cli import _planning_summary
from execution.polymarket.engine import _cap_to_signed_market_quantity
from execution.polymarket.ledger import LedgerError, SQLiteLedger
from execution.polymarket.models import (
    BookLevel,
    FeeSchedule,
    MarketSnapshot,
    RiskLimits,
    TradeIntent,
)
from execution.polymarket.planner import PlanningError, build_execution_plan
from execution.polymarket.sdk_adapter import (
    AdapterError,
    LivePolymarketAdapter,
)


ROOT = Path(__file__).resolve().parents[1]
CONDITION = "0x" + "a" * 64
TOKEN = "123456789"


def intent(
    *,
    side: str = "buy",
    price: str = "0.50",
    notional: str = "5.00",
    quantity: str = "10",
    condition: str = CONDITION,
    token: str = TOKEN,
) -> TradeIntent:
    return TradeIntent.from_dict(
        {
            "schema_version": "1.0",
            "venue": "polymarket",
            "multi_market_identifier": condition,
            "instrument_identifier": token,
            "outcome": "yes",
            "side": side,
            "reference_price": price,
            "notional": notional,
            "quantity": quantity,
            "currency": "pUSD",
        }
    )


def market(
    *,
    bids: tuple[tuple[str, str], ...] = (("0.49", "100"),),
    asks: tuple[tuple[str, str], ...] = (("0.51", "100"),),
    minimum: str = "1",
    condition: str = CONDITION,
    token: str = TOKEN,
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id="42",
        condition_id=condition,
        token_id=token,
        outcome="yes",
        active=True,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        neg_risk=False,
        min_order_size=Decimal(minimum),
        tick_size=Decimal("0.01"),
        fee_schedule=FeeSchedule(rate=Decimal("0.04"), exponent=Decimal("1")),
        bids=tuple(BookLevel(Decimal(price), Decimal(size)) for price, size in bids),
        asks=tuple(BookLevel(Decimal(price), Decimal(size)) for price, size in asks),
        book_timestamp="2026-08-18T00:00:00Z",
    )


class PlannerTests(unittest.TestCase):
    def test_buy_uses_live_book_and_never_exceeds_intent_quantity(self) -> None:
        plan = build_execution_plan(
            intents=[intent()],
            markets={TOKEN: market()},
            positions={},
            available_cash=Decimal("100"),
            limits=RiskLimits(minimum_cash_reserve=Decimal("5")),
        )
        self.assertEqual(len(plan.orders), 1)
        order = plan.orders[0]
        self.assertLessEqual(order.quantity, Decimal("10"))
        self.assertEqual(order.vwap, Decimal("0.51"))
        self.assertLessEqual(order.limit_price, Decimal("0.52"))
        self.assertLessEqual(order.cash_effect, Decimal("5.25"))

    def test_buy_shrinks_for_cash_but_preserves_reserve(self) -> None:
        plan = build_execution_plan(
            intents=[intent()],
            markets={TOKEN: market()},
            positions={},
            available_cash=Decimal("8"),
            limits=RiskLimits(
                minimum_cash_reserve=Decimal("5"),
                max_quantity_reduction_bps=5000,
            ),
        )
        self.assertEqual(len(plan.orders), 1)
        self.assertLess(plan.orders[0].quantity, Decimal("10"))
        self.assertLessEqual(plan.orders[0].cash_effect, Decimal("3"))

    def test_buy_skips_when_cash_reduction_is_too_large(self) -> None:
        plan = build_execution_plan(
            intents=[intent()],
            markets={TOKEN: market()},
            positions={},
            available_cash=Decimal("6"),
            limits=RiskLimits(minimum_cash_reserve=Decimal("5")),
        )
        self.assertEqual(plan.orders, ())
        self.assertEqual(plan.skipped[0].code, "quantity_reduction_too_large")
        self.assertEqual(plan.skipped[0].intent, intent())

    def test_sell_uses_minimum_of_intent_holding_and_bid_liquidity(self) -> None:
        plan = build_execution_plan(
            intents=[intent(side="sell")],
            markets={TOKEN: market(bids=(("0.49", "7"),))},
            positions={TOKEN: Decimal("8")},
            available_cash=Decimal("0"),
            limits=RiskLimits(max_quantity_reduction_bps=5000),
        )
        self.assertEqual(plan.orders[0].quantity, Decimal("7"))
        self.assertEqual(plan.orders[0].side, "sell")

    def test_price_guard_rejects_book_outside_threshold(self) -> None:
        plan = build_execution_plan(
            intents=[intent()],
            markets={TOKEN: market(asks=(("0.60", "100"),))},
            positions={},
            available_cash=Decimal("100"),
            limits=RiskLimits(),
        )
        self.assertEqual(plan.orders, ())
        self.assertEqual(plan.skipped[0].code, "insufficient_liquidity")

    def test_buy_quantity_is_capped_by_both_suggested_shares_and_amount(self) -> None:
        limits = RiskLimits(
            max_price_deviation_bps=1000,
            max_quantity_reduction_bps=2000,
        )
        plan = build_execution_plan(
            intents=[intent(price="0.50", notional="5", quantity="10")],
            markets={TOKEN: market(asks=(("0.52", "100"),))},
            positions={},
            available_cash=Decimal("100"),
            limits=limits,
        )
        order = plan.orders[0]
        self.assertEqual(order.limit_price, Decimal("0.55"))
        self.assertEqual(order.quantity, Decimal("9.09"))
        self.assertLessEqual(order.limit_gross_notional, Decimal("5"))

    def test_simulated_vwap_is_checked_against_notional_per_suggested_share(self) -> None:
        limits = RiskLimits(
            max_price_deviation_bps=500,
            max_quantity_reduction_bps=2000,
        )
        plan = build_execution_plan(
            intents=[intent(price="0.49", notional="5", quantity="10")],
            markets={TOKEN: market(asks=(("0.52", "100"),))},
            positions={},
            available_cash=Decimal("100"),
            limits=limits,
        )
        self.assertEqual(plan.orders[0].limit_price, Decimal("0.52"))
        self.assertEqual(plan.orders[0].vwap, Decimal("0.52"))

        with self.assertRaisesRegex(PlanningError, "simulated VWAP"):
            build_execution_plan(
                intents=[intent(price="0.49", notional="5", quantity="10")],
                markets={TOKEN: market(asks=(("0.47", "100"),))},
                positions={},
                available_cash=Decimal("100"),
                limits=limits,
            )

    def test_abnormal_order_notional_fails_the_whole_batch(self) -> None:
        with self.assertRaisesRegex(PlanningError, "exceeds"):
            build_execution_plan(
                intents=[intent(notional="500", quantity="1000")],
                markets={TOKEN: market()},
                positions={},
                available_cash=Decimal("1000"),
                limits=RiskLimits(),
            )

    def test_sell_proceeds_are_not_reused_by_buy_planning(self) -> None:
        other_condition = "0x" + "b" * 64
        other_token = "987654321"
        plan = build_execution_plan(
            intents=[
                intent(side="sell"),
                intent(condition=other_condition, token=other_token),
            ],
            markets={
                TOKEN: market(),
                other_token: market(condition=other_condition, token=other_token),
            },
            positions={TOKEN: Decimal("10")},
            available_cash=Decimal("5"),
            limits=RiskLimits(minimum_cash_reserve=Decimal("5")),
        )
        self.assertEqual(len(plan.orders), 1)
        self.assertEqual(plan.orders[0].side, "sell")
        self.assertEqual(plan.skipped[0].code, "insufficient_cash")

    def test_planning_summary_accounts_for_every_original_intent(self) -> None:
        other_condition = "0x" + "b" * 64
        other_token = "987654321"
        plan = build_execution_plan(
            intents=[
                intent(side="sell"),
                intent(condition=other_condition, token=other_token),
            ],
            markets={
                TOKEN: market(),
                other_token: market(condition=other_condition, token=other_token),
            },
            positions={TOKEN: Decimal("10")},
            available_cash=Decimal("5"),
            limits=RiskLimits(minimum_cash_reserve=Decimal("5")),
        )
        summary = _planning_summary(
            {
                "created_at": "2026-08-20T00:00:00Z",
                "expires_at": "2026-08-20T00:02:00Z",
                **plan.to_dict(),
            }
        )
        self.assertEqual(summary["source_intent_count"], 2)
        self.assertEqual(summary["planned_order_count"], 1)
        self.assertEqual(summary["skipped_intent_count"], 1)
        self.assertEqual(
            [item["intent_index"] for item in summary["intent_outcomes"]],
            [1, 2],
        )
        self.assertEqual(summary["intent_outcomes"][1]["skip"]["code"], "insufficient_cash")


class ArtifactTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "trade-intents.jsonl"
        path.write_text(text, encoding="utf-8")
        return path

    def valid_line(self) -> str:
        return json.dumps(intent().to_dict(), separators=(",", ":")) + "\n"

    def test_valid_artifact(self) -> None:
        values = load_and_validate_jsonl(
            self.write(self.valid_line()),
            schema_path=ROOT / "schemas" / "trade-intent.schema.json",
        )
        self.assertEqual(len(values), 1)

    def test_zero_byte_artifact_is_no_action(self) -> None:
        values = load_and_validate_jsonl(
            self.write(""),
            schema_path=ROOT / "schemas" / "trade-intent.schema.json",
        )
        self.assertEqual(values, [])

    def test_blank_line_is_rejected(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "blank"):
            load_and_validate_jsonl(
                self.write(self.valid_line() + "\n"),
                schema_path=ROOT / "schemas" / "trade-intent.schema.json",
            )

    def test_unknown_field_is_rejected(self) -> None:
        raw = intent().to_dict()
        raw["unexpected"] = True
        with self.assertRaises(ArtifactError):
            load_and_validate_jsonl(
                self.write(json.dumps(raw) + "\n"),
                schema_path=ROOT / "schemas" / "trade-intent.schema.json",
            )


@dataclass
class SignedOrderFixture:
    salt: int
    signature: str


class SQLiteLedgerTests(unittest.TestCase):
    def ledger(self, path: Path, execution_id: str, owner: str = "runner-a") -> SQLiteLedger:
        return SQLiteLedger(path=path, execution_id=execution_id, lease_owner=owner)

    def test_local_ledger_persists_order_and_completed_replay(self) -> None:
        path = Path(tempfile.mkdtemp()) / "ledger.sqlite3"
        ledger = self.ledger(path, "execution-1")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            ledger.claim_run(
                artifact_sha256="a" * 64,
                planned_notional=Decimal("5"),
                daily_notional_limit=Decimal("10"),
            ),
            "new",
        )
        payload = ledger.save_signed_order(
            0,
            SignedOrderFixture(salt=7, signature="0xsigned"),
            {"intent_index": 0},
            before_token_balance=Decimal("0"),
            before_collateral_balance=Decimal("20"),
        )
        self.assertEqual(payload["salt"], 7)
        ledger.mark_submitted(0, {"order_id": "order-1"})
        ledger.mark_order_terminal(0, {"status": "filled"})
        ledger.complete({"status": "COMPLETED"})

        replay = self.ledger(path, "execution-1", owner="runner-b")
        self.assertEqual(
            replay.claim_run(
                artifact_sha256="a" * 64,
                planned_notional=Decimal("5"),
                daily_notional_limit=Decimal("10"),
            ),
            "completed",
        )
        self.assertEqual(replay.get(replay.order_key(0))["state"], "VERIFIED")

    def test_local_ledger_enforces_shared_daily_limit(self) -> None:
        path = Path(tempfile.mkdtemp()) / "ledger.sqlite3"
        first = self.ledger(path, "execution-1")
        first.claim_run(
            artifact_sha256="a" * 64,
            planned_notional=Decimal("6"),
            daily_notional_limit=Decimal("10"),
        )
        second = self.ledger(path, "execution-2")
        with self.assertRaisesRegex(LedgerError, "daily notional"):
            second.claim_run(
                artifact_sha256="b" * 64,
                planned_notional=Decimal("5"),
                daily_notional_limit=Decimal("10"),
            )

    def test_local_ledger_rejects_another_unexpired_lease_owner(self) -> None:
        path = Path(tempfile.mkdtemp()) / "ledger.sqlite3"
        first = self.ledger(path, "execution-1")
        first.claim_run(
            artifact_sha256="a" * 64,
            planned_notional=Decimal("1"),
            daily_notional_limit=Decimal("10"),
        )
        second = self.ledger(path, "execution-1", owner="runner-b")
        with self.assertRaisesRegex(LedgerError, "another runner"):
            second.claim_run(
                artifact_sha256="a" * 64,
                planned_notional=Decimal("1"),
                daily_notional_limit=Decimal("10"),
            )


class MarketOrderClientFixture:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"signed": True}


class ProtectedMarketOrderTests(unittest.TestCase):
    def adapter(self) -> tuple[LivePolymarketAdapter, MarketOrderClientFixture]:
        client = MarketOrderClientFixture()
        adapter = LivePolymarketAdapter.__new__(LivePolymarketAdapter)
        adapter.client = client
        return adapter, client

    def test_buy_is_fok_with_exact_protected_share_request_and_spend_cap(self) -> None:
        adapter, client = self.adapter()
        signed = adapter.create_market_order(
            token_id=TOKEN,
            price_limit=Decimal("0.52"),
            quantity=Decimal("9.09"),
            side="buy",
            max_buy_spend=Decimal("4.90"),
        )
        self.assertEqual(signed, {"signed": True})
        self.assertEqual(
            client.calls,
            [
                {
                    "token_id": TOKEN,
                    "side": "BUY",
                    "amount": Decimal("4.7268"),
                    "max_spend": Decimal("4.90"),
                    "max_price": Decimal("0.52"),
                    "order_type": "FOK",
                }
            ],
        )

    def test_sell_is_fok_with_share_quantity_and_price_floor(self) -> None:
        adapter, client = self.adapter()
        adapter.create_market_order(
            token_id=TOKEN,
            price_limit=Decimal("0.48"),
            quantity=Decimal("7"),
            side="sell",
        )
        self.assertEqual(
            client.calls,
            [
                {
                    "token_id": TOKEN,
                    "side": "SELL",
                    "shares": Decimal("7"),
                    "min_price": Decimal("0.48"),
                    "order_type": "FOK",
                }
            ],
        )

    def test_buy_requires_an_all_in_spend_cap(self) -> None:
        adapter, _ = self.adapter()
        with self.assertRaisesRegex(AdapterError, "max_buy_spend"):
            adapter.create_market_order(
                token_id=TOKEN,
                price_limit=Decimal("0.52"),
                quantity=Decimal("9.09"),
                side="buy",
            )

    def test_reads_sdk_rounded_share_quantity_from_signed_amounts(self) -> None:
        @dataclass
        class SignedFixture:
            maker_amount: int
            taker_amount: int

        signed = SignedFixture(maker_amount=4_720_000, taker_amount=9_077_000)
        self.assertEqual(
            LivePolymarketAdapter.signed_market_order_quantity(signed, side="buy"),
            Decimal("9.077"),
        )
        self.assertEqual(
            LivePolymarketAdapter.signed_market_order_quantity(signed, side="sell"),
            Decimal("4.72"),
        )

    def test_sdk_rounding_caps_and_recomputes_the_durable_plan(self) -> None:
        limits = RiskLimits(
            max_price_deviation_bps=1000,
            max_quantity_reduction_bps=2000,
        )
        planned = build_execution_plan(
            intents=[intent(price="0.50", notional="5", quantity="10")],
            markets={TOKEN: market(asks=(("0.52", "100"),))},
            positions={},
            available_cash=Decimal("100"),
            limits=limits,
        ).orders[0]
        capped = _cap_to_signed_market_quantity(planned, Decimal("9.077"), limits)
        self.assertEqual(capped.quantity, Decimal("9.077"))
        self.assertEqual(sum(fill.quantity for fill in capped.fills), capped.quantity)
        self.assertEqual(capped.gross_notional, capped.vwap * capped.quantity)
        self.assertLessEqual(capped.worst_case_cash_effect, planned.worst_case_cash_effect)


class CancellationAdapterFixture:
    def __init__(self, snapshots: list[tuple[dict[str, object], ...]]) -> None:
        self.snapshots = iter(snapshots)
        self.canceled: list[str] = []

    def open_orders(self):
        return next(self.snapshots)

    def cancel_order(self, order_id: str):
        self.canceled.append(order_id)
        return {"not_canceled": {}}

    def order_if_open(self, order_id: str):
        return None


class CancelAllOpenOrdersTests(unittest.TestCase):
    def test_cancels_every_order_and_verifies_account_is_empty(self) -> None:
        fixture = CancellationAdapterFixture(
            [
                ({"id": "old-1"}, {"id": "old-2"}),
                (),
            ]
        )
        summary = LivePolymarketAdapter.cancel_all_open_orders(fixture)  # type: ignore[arg-type]
        self.assertEqual(fixture.canceled, ["old-1", "old-2"])
        self.assertEqual(summary["canceled_order_count"], 2)
        self.assertEqual(summary["remaining_open_order_count"], 0)

    def test_cancels_order_that_appears_during_the_sweep(self) -> None:
        fixture = CancellationAdapterFixture(
            [
                ({"id": "old-1"},),
                ({"id": "racing-2"},),
                (),
            ]
        )
        summary = LivePolymarketAdapter.cancel_all_open_orders(fixture)  # type: ignore[arg-type]
        self.assertEqual(fixture.canceled, ["old-1", "racing-2"])
        self.assertEqual(summary["canceled_order_count"], 2)


@dataclass
class OrderFixture:
    id: str
    status: str
    associate_trades: tuple[str, ...] = ()


class PageFixture:
    def __init__(self, *items: OrderFixture) -> None:
        self.items = items

    def iter_items(self):
        return iter(self.items)


class ClientFixture:
    def __init__(self, *items: OrderFixture) -> None:
        self.items = items

    def list_open_orders(self, *, id: str) -> PageFixture:
        return PageFixture(*(item for item in self.items if item.id == id))


class OpenOrderStatusTests(unittest.TestCase):
    def adapter(self, *items: OrderFixture) -> LivePolymarketAdapter:
        adapter = LivePolymarketAdapter.__new__(LivePolymarketAdapter)
        adapter.client = ClientFixture(*items)
        return adapter

    def test_live_order_is_open(self) -> None:
        result = self.adapter(OrderFixture("order-1", "LIVE")).order_if_open("order-1")
        self.assertEqual(
            result,
            {"id": "order-1", "status": "LIVE", "associate_trades": ()},
        )

    def test_matched_order_is_not_open(self) -> None:
        result = self.adapter(OrderFixture("order-1", "MATCHED")).order_if_open("order-1")
        self.assertIsNone(result)

    def test_terminal_order_record_remains_available_for_trade_reconciliation(self) -> None:
        result = self.adapter(
            OrderFixture("order-1", "MATCHED", ("trade-1",))
        ).order("order-1")
        self.assertEqual(
            result,
            {
                "id": "order-1",
                "status": "MATCHED",
                "associate_trades": ("trade-1",),
            },
        )

    def test_terminal_and_non_resting_statuses_are_not_open(self) -> None:
        for status in ("CANCELED", "CANCELLED", "DELAYED", "UNMATCHED"):
            with self.subTest(status=status):
                result = self.adapter(OrderFixture("order-1", status)).order_if_open("order-1")
                self.assertIsNone(result)

    def test_unknown_status_fails_closed(self) -> None:
        with self.assertRaisesRegex(AdapterError, "unknown order status"):
            self.adapter(OrderFixture("order-1", "PENDING")).order_if_open("order-1")

if __name__ == "__main__":
    unittest.main()
