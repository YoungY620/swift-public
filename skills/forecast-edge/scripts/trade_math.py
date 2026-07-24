#!/usr/bin/env python3
"""Compute deterministic Polymarket edge, order-book capacity, and half-Kelly sizing."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


class TradeMathError(ValueError):
    """Raised when sizing inputs are invalid."""


def number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TradeMathError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise TradeMathError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise TradeMathError(f"{name} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise TradeMathError(f"{name} must be at most {maximum}")
    return result


def fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)


def normalize_levels(raw: Any, *, bids: bool) -> list[dict[str, float]]:
    if not isinstance(raw, list):
        raise TradeMathError("order-book levels must be a list")
    levels = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TradeMathError(f"order-book level {index} must be an object")
        price = number(item.get("price"), f"level[{index}].price", minimum=0.0, maximum=1.0)
        size = number(item.get("size"), f"level[{index}].size", minimum=0.0)
        if size > 0:
            levels.append({"price": price, "size": size})
    return sorted(levels, key=lambda level: level["price"], reverse=bids)


def walk_levels(levels: list[dict[str, float]], shares: float, fee_rate: float, *, sell: bool) -> dict[str, Any]:
    remaining = shares
    gross = 0.0
    fee = 0.0
    fills = []
    for level in levels:
        if remaining <= 1e-12:
            break
        take = min(remaining, level["size"])
        price = level["price"]
        level_fee = take * fee_per_share(price, fee_rate)
        gross += take * price
        fee += level_fee
        fills.append({"price": price, "shares": take, "fee": level_fee})
        remaining -= take
    filled = shares - remaining
    return {
        "requested_shares": shares,
        "filled_shares": filled,
        "fully_filled": remaining <= 1e-9,
        "gross": gross,
        "fee": fee,
        "cash": gross - fee if sell else gross + fee,
        "vwap": gross / filled if filled else None,
        "all_in_unit": (gross + fee) / filled if filled and not sell else None,
        "net_unit": (gross - fee) / filled if filled and sell else None,
        "fills": fills,
    }


def retention_capacity(
    asks: list[dict[str, float]], model_value: float, fee_rate: float, minimum_retention: float
) -> dict[str, Any]:
    if not asks:
        return {"shares": 0.0, "cash": 0.0, "raw_edge": None, "actual_edge": None, "retention": None}
    raw_edge = model_value - asks[0]["price"]
    if raw_edge <= 0:
        return {"shares": 0.0, "cash": 0.0, "raw_edge": raw_edge, "actual_edge": None, "retention": None}

    required_edge = minimum_retention * raw_edge
    cumulative_surplus = 0.0
    shares = 0.0
    for level in asks:
        contribution = model_value - level["price"] - fee_per_share(level["price"], fee_rate)
        surplus_per_share = contribution - required_edge
        if surplus_per_share >= 0:
            shares += level["size"]
            cumulative_surplus += surplus_per_share * level["size"]
            continue
        if cumulative_surplus <= 0:
            break
        partial = min(level["size"], cumulative_surplus / -surplus_per_share)
        shares += partial
        cumulative_surplus += surplus_per_share * partial
        break

    fill = walk_levels(asks, shares, fee_rate, sell=False)
    actual_edge = model_value - fill["all_in_unit"] if shares else None
    retention = actual_edge / raw_edge if actual_edge is not None else None
    return {
        "shares": shares,
        "cash": fill["cash"],
        "raw_edge": raw_edge,
        "actual_edge": actual_edge,
        "retention": retention,
        "fill": fill,
    }


def shares_for_cash(asks: list[dict[str, float]], cash: float, fee_rate: float) -> float:
    remaining = cash
    shares = 0.0
    for level in asks:
        unit_cost = level["price"] + fee_per_share(level["price"], fee_rate)
        if unit_cost <= 0:
            continue
        take = min(level["size"], remaining / unit_cost)
        shares += take
        remaining -= take * unit_cost
        if take < level["size"] or remaining <= 1e-9:
            break
    return shares


def reserve_amount(document: dict[str, Any]) -> float:
    if "reserve_cash" in document:
        return number(document["reserve_cash"], "reserve_cash", minimum=0.0)
    reserve = document.get("reserve", {})
    if not isinstance(reserve, dict):
        raise TradeMathError("reserve must be an object")
    costs = reserve.get("reference_position_costs", [])
    if not costs:
        return 0.0
    parsed = [number(value, "reserve.reference_position_cost", minimum=0.0) for value in costs]
    count = number(reserve.get("target_positions", 2.5), "reserve.target_positions", minimum=0.0)
    return statistics.median(parsed) * count


def evaluate_trade(document: dict[str, Any]) -> dict[str, Any]:
    side = str(document.get("side", "YES")).upper()
    if side not in {"YES", "NO"}:
        raise TradeMathError("side must be YES or NO")
    model_probability = number(document.get("model_probability"), "model_probability", minimum=0.0, maximum=1.0)
    model_value = model_probability if side == "YES" else 1.0 - model_probability
    bankroll = number(document.get("bankroll"), "bankroll", minimum=0.0)
    available_cash = number(document.get("available_cash"), "available_cash", minimum=0.0)
    fee_rate = number(document.get("fee_rate", 0.0), "fee_rate", minimum=0.0)
    kelly_fraction = number(document.get("kelly_fraction", 0.5), "kelly_fraction", minimum=0.0, maximum=1.0)
    minimum_retention = number(
        document.get("minimum_edge_retention", 0.7),
        "minimum_edge_retention",
        minimum=0.0,
        maximum=1.0,
    )
    soft_minimum_raw_edge = number(
        document.get("soft_minimum_raw_edge", 0.10),
        "soft_minimum_raw_edge",
        minimum=0.0,
        maximum=1.0,
    )
    soft_edge_override = document.get("soft_edge_override", False)
    if not isinstance(soft_edge_override, bool):
        raise TradeMathError("soft_edge_override must be a boolean")
    soft_edge_override_reason = str(document.get("soft_edge_override_reason") or "").strip()
    if soft_edge_override and not soft_edge_override_reason:
        raise TradeMathError("soft_edge_override_reason is required when soft_edge_override is true")
    min_order_shares = number(document.get("min_order_shares", 0.0), "min_order_shares", minimum=0.0)
    current = document.get("current_position") or {}
    if not isinstance(current, dict):
        raise TradeMathError("current_position must be an object")
    current_shares = number(current.get("shares", 0.0), "current_position.shares", minimum=0.0)
    current_cost = number(current.get("cost", 0.0), "current_position.cost", minimum=0.0)
    book = document.get("book")
    if not isinstance(book, dict):
        raise TradeMathError("book must be an object")
    asks = normalize_levels(book.get("asks", []), bids=False)
    bids = normalize_levels(book.get("bids", []), bids=True)

    reserve_cash = reserve_amount(document)
    use_reserve = bool(document.get("use_reserve", False))
    normal_cash_budget = available_cash if use_reserve else max(0.0, available_cash - reserve_cash)

    capacity = retention_capacity(asks, model_value, fee_rate, minimum_retention)
    soft_edge_pass = bool(
        capacity["raw_edge"] is not None
        and capacity["raw_edge"] > soft_minimum_raw_edge + 1e-12
    )
    soft_edge_override_applied = bool(soft_edge_override and not soft_edge_pass)
    soft_entry_allowed = soft_edge_pass or soft_edge_override
    probe_shares = min_order_shares if min_order_shares > 0 else 1.0
    probe_fill = walk_levels(asks, probe_shares, fee_rate, sell=False)
    probe_actual_edge = (
        model_value - probe_fill["all_in_unit"]
        if probe_fill["fully_filled"] and probe_fill["all_in_unit"] is not None
        else None
    )
    probe_retention = (
        probe_actual_edge / capacity["raw_edge"]
        if probe_actual_edge is not None and capacity["raw_edge"] not in (None, 0)
        else None
    )
    entry_eligible = bool(
        soft_entry_allowed
        and probe_actual_edge is not None
        and probe_actual_edge > 0
        and probe_retention is not None
        and probe_retention + 1e-9 >= minimum_retention
    )
    if asks:
        best_all_in_cost = asks[0]["price"] + fee_per_share(asks[0]["price"], fee_rate)
        if best_all_in_cost < 1.0:
            full_kelly = max(0.0, (model_value - best_all_in_cost) / (1.0 - best_all_in_cost))
        else:
            full_kelly = 0.0
    else:
        best_all_in_cost = None
        full_kelly = 0.0
    fractional_kelly = full_kelly * kelly_fraction
    kelly_target_cash = bankroll * fractional_kelly
    incremental_kelly_cash = max(0.0, kelly_target_cash - current_cost)
    suggested_cash_cap = (
        min(incremental_kelly_cash, capacity["cash"], normal_cash_budget)
        if soft_entry_allowed
        else 0.0
    )
    suggested_shares = shares_for_cash(asks, suggested_cash_cap, fee_rate) if suggested_cash_cap > 0 else 0.0
    buy_fill = walk_levels(asks, suggested_shares, fee_rate, sell=False)
    buy_actual_edge = model_value - buy_fill["all_in_unit"] if buy_fill["all_in_unit"] is not None else None
    buy_retention = (
        buy_actual_edge / capacity["raw_edge"]
        if buy_actual_edge is not None and capacity["raw_edge"] not in (None, 0)
        else None
    )
    buy_cross = bool(
        soft_entry_allowed
        and suggested_shares >= min_order_shares
        and buy_actual_edge is not None
        and buy_actual_edge > 0
        and buy_retention is not None
        and buy_retention + 1e-9 >= minimum_retention
    )

    sell_fill = walk_levels(bids, current_shares, fee_rate, sell=True) if current_shares > 0 else None
    sell_cross = bool(
        sell_fill
        and sell_fill["fully_filled"]
        and sell_fill["net_unit"] is not None
        and sell_fill["net_unit"] > model_value
    )

    if sell_cross:
        signal = "SELL"
        recommended_target_cash = 0.0
    elif buy_cross:
        signal = "ADD" if current_shares > 0 else "BUY"
        recommended_target_cash = current_cost + buy_fill["cash"]
    else:
        signal = "MAINTAIN"
        recommended_target_cash = current_cost

    days = document.get("days_to_resolution")
    expected_profit = suggested_shares * model_value - buy_fill["cash"] if suggested_shares else 0.0
    daily_expected_return = None
    if days is not None and buy_fill["cash"] > 0:
        parsed_days = number(days, "days_to_resolution", minimum=1e-12)
        daily_expected_return = expected_profit / buy_fill["cash"] / parsed_days

    return {
        "signal": signal,
        "side": side,
        "model_probability": model_probability,
        "model_value": model_value,
        "opportunity": {
            "entry_eligible": entry_eligible,
            "probe_shares": probe_shares,
            "executable_all_in_price": probe_fill["all_in_unit"],
            "actual_edge": probe_actual_edge,
            "edge_retention": probe_retention,
            "fill": probe_fill,
        },
        "soft_edge": {
            "minimum_raw_edge": soft_minimum_raw_edge,
            "requires_strictly_greater": True,
            "raw_edge": capacity["raw_edge"],
            "passes": soft_edge_pass,
            "override_requested": soft_edge_override,
            "override_applied": soft_edge_override_applied,
            "override_reason": soft_edge_override_reason or None,
        },
        "buy": {
            "cross": buy_cross,
            "best_all_in_cost": best_all_in_cost,
            "raw_edge": capacity["raw_edge"],
            "suggested_shares": suggested_shares,
            "suggested_cash": buy_fill["cash"],
            "actual_edge": buy_actual_edge,
            "edge_retention": buy_retention,
            "expected_profit_at_resolution": expected_profit,
            "daily_expected_return": daily_expected_return,
            "fill": buy_fill,
        },
        "sell": {
            "cross": sell_cross,
            "shares": current_shares if sell_cross else 0.0,
            "fill": sell_fill,
        },
        "kelly": {
            "full_fraction": full_kelly,
            "configured_fraction": kelly_fraction,
            "fractional_fraction": fractional_kelly,
            "target_cash": kelly_target_cash,
            "incremental_cash": incremental_kelly_cash,
        },
        "orderbook_capacity": capacity,
        "cash": {
            "bankroll": bankroll,
            "available": available_cash,
            "reserve": reserve_cash,
            "reserve_used": use_reserve,
            "buying_budget": normal_cash_budget,
        },
        "current_position": {"shares": current_shares, "cost": current_cost},
        "recommended_target_cash": recommended_target_cash,
    }


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Sizing JSON file, or - for stdin")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = read_json(args.input)
        if not isinstance(document, dict):
            raise TradeMathError("input must be a JSON object")
        result = evaluate_trade(document)
    except (OSError, json.JSONDecodeError, TradeMathError) as exc:
        print(f"trade_math: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
