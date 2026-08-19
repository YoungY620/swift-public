from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from execution.polymarket.artifact import ArtifactError, load_and_validate_jsonl
from execution.polymarket.models import (
    BookLevel,
    FeeSchedule,
    MarketSnapshot,
    RiskLimits,
    TradeIntent,
)
from execution.polymarket.planner import PlanningError, build_execution_plan


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


if __name__ == "__main__":
    unittest.main()
