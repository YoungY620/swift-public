"""Pure, deterministic execution planning against a fresh order book."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Mapping, Sequence

from .models import (
    BPS,
    ONE,
    SHARE_QUANTUM,
    ZERO,
    BookLevel,
    MarketSnapshot,
    PlannedFill,
    PlannedOrder,
    RiskLimits,
    SkippedIntent,
    TradeIntent,
)


class PlanningError(ValueError):
    """A hard risk or integrity violation that invalidates the whole batch."""


@dataclass(frozen=True)
class ExecutionPlan:
    orders: tuple[PlannedOrder, ...]
    skipped: tuple[SkippedIntent, ...]
    total_buy_cash: Decimal
    total_sell_notional: Decimal

    def to_dict(self) -> dict[str, object]:
        from .models import decimal_string

        return {
            "orders": [order.to_dict() for order in self.orders],
            "skipped": [item.to_dict() for item in self.skipped],
            "total_buy_cash": decimal_string(self.total_buy_cash),
            "total_sell_notional": decimal_string(self.total_sell_notional),
        }


def _round_down_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    return (value / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum


def _round_price(value: Decimal, tick: Decimal, *, side: str) -> Decimal:
    rounding = ROUND_DOWN if side == "buy" else ROUND_UP
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _assert_market(intent: TradeIntent, market: MarketSnapshot) -> None:
    if intent.venue != "polymarket":
        raise PlanningError(f"unsupported venue {intent.venue!r}")
    if market.condition_id.casefold() != intent.multi_market_identifier.casefold():
        raise PlanningError("live market condition does not match the trade intent")
    if market.token_id != intent.instrument_identifier:
        raise PlanningError("live outcome token does not match the trade intent")
    if market.outcome != intent.outcome:
        raise PlanningError("live token outcome does not match the trade intent")
    if not market.active or market.closed or not market.accepting_orders:
        raise PlanningError("market is not active and accepting orders")
    if not market.enable_order_book:
        raise PlanningError("market order book is disabled")
    if market.tick_size <= ZERO or market.tick_size >= ONE:
        raise PlanningError("market tick size is invalid")


def _walk_levels(
    *,
    side: str,
    levels: Sequence[BookLevel],
    quantity_cap: Decimal,
    cash_cap: Decimal | None,
    price_limit: Decimal,
    market: MarketSnapshot,
) -> tuple[tuple[PlannedFill, ...], Decimal]:
    fills: list[PlannedFill] = []
    remaining = quantity_cap
    cash_remaining = cash_cap
    ordered = sorted(levels, key=lambda level: level.price, reverse=side == "sell")
    for level in ordered:
        if remaining < SHARE_QUANTUM:
            break
        if side == "buy" and level.price > price_limit:
            break
        if side == "sell" and level.price < price_limit:
            break
        quantity = min(level.size, remaining)
        fee_per_share = market.fee_schedule.fee_per_share(level.price)
        if side == "buy" and cash_remaining is not None:
            all_in_unit = level.price + fee_per_share
            if all_in_unit <= ZERO:
                raise PlanningError("calculated all-in unit cost is invalid")
            quantity = min(quantity, cash_remaining / all_in_unit)
        quantity = _round_down_quantum(quantity, SHARE_QUANTUM)
        if quantity < SHARE_QUANTUM:
            break
        gross = quantity * level.price
        fee = quantity * fee_per_share
        fills.append(
            PlannedFill(price=level.price, quantity=quantity, gross_notional=gross, fee=fee)
        )
        remaining -= quantity
        if cash_remaining is not None:
            cash_remaining -= gross + fee
    return tuple(fills), remaining


def _plan_one(
    *,
    index: int,
    intent: TradeIntent,
    market: MarketSnapshot,
    held_quantity: Decimal,
    available_cash: Decimal,
    limits: RiskLimits,
) -> PlannedOrder | SkippedIntent:
    _assert_market(intent, market)
    suggested_price = intent.notional / intent.quantity
    if suggested_price > ONE:
        raise PlanningError(
            f"intent {index} suggested notional divided by quantity exceeds 1"
        )
    if intent.notional > limits.max_order_notional:
        raise PlanningError(
            f"intent {index} notional {intent.notional} exceeds per-order limit "
            f"{limits.max_order_notional}"
        )
    if intent.quantity > limits.max_order_quantity:
        raise PlanningError(
            f"intent {index} quantity {intent.quantity} exceeds per-order limit "
            f"{limits.max_order_quantity}"
        )

    maximum_fee_per_share = market.fee_schedule.maximum_fee_per_share()
    if maximum_fee_per_share > limits.max_fee_per_share:
        raise PlanningError(
            f"intent {index} live fee bound {maximum_fee_per_share} exceeds "
            f"{limits.max_fee_per_share}"
        )

    deviation = Decimal(limits.max_price_deviation_bps) / BPS
    if intent.side == "buy":
        raw_limit = min(ONE - market.tick_size, suggested_price * (ONE + deviation))
    else:
        raw_limit = max(market.tick_size, suggested_price * (ONE - deviation))
    limit_price = _round_price(raw_limit, market.tick_size, side=intent.side)

    quantity_cap = intent.quantity
    if intent.side == "sell":
        quantity_cap = min(quantity_cap, held_quantity)
        if held_quantity <= ZERO:
            return SkippedIntent(
                index,
                "no_position",
                "no matching position is available to sell",
                intent,
            )
    cash_cap: Decimal | None = None
    if intent.side == "buy":
        spendable = max(ZERO, available_cash - limits.minimum_cash_reserve)
        if spendable <= ZERO:
            return SkippedIntent(
                index,
                "insufficient_cash",
                "available collateral does not preserve the configured cash reserve",
                intent,
            )
        worst_case_unit_cost = limit_price + maximum_fee_per_share
        quantity_cap = min(
            quantity_cap,
            intent.notional / limit_price,
            spendable / worst_case_unit_cost,
        )
        cash_cap = spendable

    quantity_cap = _round_down_quantum(quantity_cap, SHARE_QUANTUM)
    if quantity_cap < market.min_order_size:
        return SkippedIntent(
            index,
            "below_minimum",
            "safe quantity is below the live market minimum order size",
            intent,
        )

    fills, remaining = _walk_levels(
        side=intent.side,
        levels=market.asks if intent.side == "buy" else market.bids,
        quantity_cap=quantity_cap,
        cash_cap=cash_cap,
        price_limit=limit_price,
        market=market,
    )
    filled_quantity = sum((fill.quantity for fill in fills), ZERO)
    if filled_quantity < market.min_order_size:
        return SkippedIntent(
            index,
            "insufficient_liquidity",
            "price-protected live liquidity is below the market minimum",
            intent,
        )
    reduction = max(ZERO, intent.quantity - filled_quantity)
    reduction_bps = int((reduction / intent.quantity * BPS).to_integral_value(rounding=ROUND_UP))
    if reduction_bps > limits.max_quantity_reduction_bps:
        return SkippedIntent(
            index,
            "quantity_reduction_too_large",
            f"safe quantity reduction {reduction_bps} bps exceeds the configured limit",
            intent,
        )

    gross = sum((fill.gross_notional for fill in fills), ZERO)
    fee = sum((fill.fee for fill in fills), ZERO)
    vwap = gross / filled_quantity
    live_deviation_bps = abs(vwap - suggested_price) / suggested_price * BPS
    if live_deviation_bps > Decimal(limits.max_price_deviation_bps):
        raise PlanningError(
            f"intent {index} simulated VWAP differs from suggested notional/quantity price "
            "beyond the configured limit"
        )
    cash_effect = gross + fee if intent.side == "buy" else gross - fee
    limit_gross_notional = filled_quantity * limit_price
    worst_case_fee = filled_quantity * maximum_fee_per_share
    worst_case_cash_effect = (
        limit_gross_notional + worst_case_fee
        if intent.side == "buy"
        else limit_gross_notional - worst_case_fee
    )
    if worst_case_cash_effect <= ZERO:
        return SkippedIntent(
            index,
            "fees_exceed_proceeds",
            "the protected sell proceeds are non-positive after the maximum live fee",
            intent,
        )
    if intent.side == "buy" and cash_cap is not None and worst_case_cash_effect > cash_cap:
        raise PlanningError(f"intent {index} exceeds its protected all-in cash cap")
    if intent.side == "sell" and held_quantity < filled_quantity:
        raise PlanningError(f"intent {index} exceeds the authenticated live position")

    return PlannedOrder(
        intent_index=index,
        intent=intent,
        market_id=market.market_id,
        token_id=market.token_id,
        side=intent.side,
        limit_price=limit_price,
        quantity=filled_quantity,
        gross_notional=gross,
        fee=fee,
        cash_effect=cash_effect,
        limit_gross_notional=limit_gross_notional,
        worst_case_fee=worst_case_fee,
        worst_case_cash_effect=worst_case_cash_effect,
        vwap=vwap,
        quantity_reduction_bps=reduction_bps,
        book_timestamp=market.book_timestamp,
        fills=fills,
    )


def build_execution_plan(
    *,
    intents: Sequence[TradeIntent],
    markets: Mapping[str, MarketSnapshot],
    positions: Mapping[str, Decimal],
    available_cash: Decimal,
    limits: RiskLimits,
) -> ExecutionPlan:
    """Build one bounded batch without relying on future sell proceeds.

    Buys share the collateral balance that existed at planning time.  Sells are
    deliberately not credited to later buys: a delayed or failed sell can never
    cause the batch to overspend.
    """

    if len(intents) > limits.max_intents:
        raise PlanningError(
            f"batch contains {len(intents)} intents; maximum is {limits.max_intents}"
        )
    if available_cash < ZERO:
        raise PlanningError("available cash cannot be negative")
    reference_batch_notional = sum((intent.notional for intent in intents), ZERO)
    if reference_batch_notional > limits.max_batch_notional:
        raise PlanningError(
            f"batch reference notional {reference_batch_notional} exceeds "
            f"{limits.max_batch_notional}"
        )

    orders: list[PlannedOrder] = []
    skipped: list[SkippedIntent] = []
    remaining_cash = available_cash
    for index, intent in enumerate(intents, start=1):
        market = markets.get(intent.instrument_identifier)
        if market is None:
            raise PlanningError(f"intent {index} has no trusted live market snapshot")
        planned = _plan_one(
            index=index,
            intent=intent,
            market=market,
            held_quantity=positions.get(intent.instrument_identifier, ZERO),
            available_cash=remaining_cash,
            limits=limits,
        )
        if isinstance(planned, SkippedIntent):
            skipped.append(planned)
            continue
        orders.append(planned)
        if planned.side == "buy":
            remaining_cash -= planned.worst_case_cash_effect

    # Sells first reduce exposure.  Buy allocations remain based on the original
    # collateral balance and therefore do not depend on those proceeds arriving.
    orders.sort(key=lambda order: (order.side != "sell", order.intent_index))
    total_buy_cash = sum(
        (order.worst_case_cash_effect for order in orders if order.side == "buy"), ZERO
    )
    total_sell_notional = sum(
        (order.gross_notional for order in orders if order.side == "sell"), ZERO
    )
    if total_buy_cash > max(ZERO, available_cash - limits.minimum_cash_reserve):
        raise PlanningError("aggregate buys would breach the configured cash reserve")
    return ExecutionPlan(
        orders=tuple(orders),
        skipped=tuple(skipped),
        total_buy_cash=total_buy_cash,
        total_sell_notional=total_sell_notional,
    )
