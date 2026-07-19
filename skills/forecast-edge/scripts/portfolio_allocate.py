#!/usr/bin/env python3
"""Allocate real portfolio cash across ranked BUY and ADD opportunities."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


class AllocationError(ValueError):
    """Raised when portfolio allocation inputs are invalid or inconsistent."""


def number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise AllocationError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AllocationError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise AllocationError(f"{name} must be finite and at least {minimum}")
    return result


def close(left: float, right: float, tolerance: float = 1e-7) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AllocationError(f"{name} must be an integer")
    if value < minimum:
        raise AllocationError(f"{name} must be at least {minimum}")
    return value


def opportunity_cap(item: dict[str, Any], index: int) -> float:
    values = [
        number(item.get("standalone_kelly_cash"), f"opportunities[{index}].standalone_kelly_cash"),
        number(item.get("orderbook_capacity_cash"), f"opportunities[{index}].orderbook_capacity_cash"),
    ]
    if item.get("portfolio_cash_cap") is not None:
        values.append(number(item["portfolio_cash_cap"], f"opportunities[{index}].portfolio_cash_cap"))
    return min(values)


def calculate_reserve(
    available_cash: float,
    cold_start: bool,
    new_position_limit: int,
    opportunities: list[dict[str, Any]],
    reserve: dict[str, Any],
) -> dict[str, float]:
    target_positions = number(reserve.get("target_positions", 2.5), "reserve.target_positions")
    references = reserve.get("reference_position_costs", [])
    if not isinstance(references, list):
        raise AllocationError("reserve.reference_position_costs must be a list")
    parsed_references = [number(value, "reserve.reference_position_cost") for value in references]

    reference_position = 0.0
    if parsed_references:
        reference_position = statistics.median(parsed_references)
    elif cold_start and available_cash > 0:
        provisional = []
        for index, item in enumerate(opportunities):
            if not bool(item.get("eligible", True)) or str(item.get("action", "BUY")).upper() != "BUY":
                continue
            cap = opportunity_cap(item, index)
            if cap > 0:
                provisional.append((int(item.get("priority", index + 1)), cap))
        provisional.sort(key=lambda pair: pair[0])
        provisional = provisional[:new_position_limit]
        if provisional:
            slot_count = len(provisional)
            cash_slot = available_cash / (target_positions + slot_count) if target_positions + slot_count else 0.0
            reference_position = min(statistics.median(cap for _, cap in provisional), cash_slot)

    target_reserve = min(available_cash, reference_position * target_positions)
    release_cash = number(reserve.get("release_cash", 0.0), "reserve.release_cash")
    if release_cash > target_reserve + 1e-9:
        raise AllocationError("reserve.release_cash cannot exceed the calculated reserve")
    if release_cash > 0 and not str(reserve.get("release_reason", "")).strip():
        raise AllocationError("reserve.release_reason is required when reserve cash is released")
    protected_reserve = target_reserve - release_cash
    return {
        "reference_position_cash": reference_position,
        "target_positions": target_positions,
        "target_reserve_cash": target_reserve,
        "released_cash": release_cash,
        "protected_reserve_cash": protected_reserve,
    }


def allocate_portfolio(document: dict[str, Any]) -> dict[str, Any]:
    portfolio = document.get("portfolio")
    if not isinstance(portfolio, dict):
        raise AllocationError("portfolio must be an object")
    available_cash = number(portfolio.get("available_cash"), "portfolio.available_cash")
    holdings_value = number(
        portfolio.get("holdings_liquidation_value", 0.0), "portfolio.holdings_liquidation_value"
    )
    conservative_bankroll = available_cash + holdings_value
    if portfolio.get("reported_bankroll") is not None:
        reported = number(portfolio["reported_bankroll"], "portfolio.reported_bankroll")
        if not close(reported, conservative_bankroll):
            raise AllocationError(
                f"reported bankroll {reported} does not equal cash plus holdings {conservative_bankroll}"
            )
    cold_start = portfolio.get("cold_start", False)
    if not isinstance(cold_start, bool):
        raise AllocationError("portfolio.cold_start must be boolean")
    new_position_limit = integer(
        portfolio.get("new_position_limit", 2), "portfolio.new_position_limit"
    )

    raw_opportunities = document.get("opportunities")
    if not isinstance(raw_opportunities, list):
        raise AllocationError("opportunities must be a list")
    opportunities: list[tuple[int, int, dict[str, Any]]] = []
    priorities: set[int] = set()
    market_ids: set[str] = set()
    for index, raw in enumerate(raw_opportunities):
        if not isinstance(raw, dict):
            raise AllocationError(f"opportunities[{index}] must be an object")
        market_id = str(raw.get("market_id", "")).strip()
        if not market_id or market_id in market_ids:
            raise AllocationError(f"opportunities[{index}].market_id must be non-empty and unique")
        market_ids.add(market_id)
        action = str(raw.get("action", "BUY")).upper()
        if action not in {"BUY", "ADD"}:
            raise AllocationError(f"opportunities[{index}].action must be BUY or ADD")
        priority = integer(raw.get("priority", index + 1), f"opportunities[{index}].priority")
        if priority in priorities:
            raise AllocationError("eligible opportunity priorities must be non-negative and unique")
        priorities.add(priority)
        opportunities.append((priority, index, raw))
    opportunities.sort(key=lambda row: row[0])

    reserve_input = document.get("reserve", {})
    if not isinstance(reserve_input, dict):
        raise AllocationError("reserve must be an object")
    reserve = calculate_reserve(
        available_cash,
        cold_start,
        new_position_limit,
        [raw for _, _, raw in opportunities],
        reserve_input,
    )
    deployable_cash = max(0.0, available_cash - reserve["protected_reserve_cash"])
    remaining_cash = deployable_cash
    allocated_cash = 0.0
    new_positions = 0
    allocations = []

    for priority, original_index, item in opportunities:
        action = str(item.get("action", "BUY")).upper()
        eligible = item.get("eligible", True)
        if not isinstance(eligible, bool):
            raise AllocationError(f"opportunities[{original_index}].eligible must be boolean")
        cap = opportunity_cap(item, original_index)
        minimum_order_cash = number(
            item.get("minimum_order_cash", 0.0), f"opportunities[{original_index}].minimum_order_cash"
        )
        cash_before = remaining_cash
        reason = "allocated"
        allocation = 0.0
        if not eligible:
            reason = "not eligible after portfolio review"
        elif action == "BUY" and cold_start and new_positions >= new_position_limit:
            reason = "cold-start new-position limit reached"
        elif cap <= 0:
            reason = "no standalone executable capacity"
        elif remaining_cash <= 0:
            reason = "no deployable cash remaining"
        else:
            allocation = min(cap, remaining_cash)
            if allocation + 1e-9 < minimum_order_cash:
                allocation = 0.0
                reason = "below minimum executable order"
        if allocation > 0:
            remaining_cash -= allocation
            allocated_cash += allocation
            if action == "BUY":
                new_positions += 1
        allocations.append(
            {
                "market_id": str(item["market_id"]),
                "action": action,
                "priority": priority,
                "eligible": eligible,
                "standalone_kelly_cash": number(
                    item.get("standalone_kelly_cash"),
                    f"opportunities[{original_index}].standalone_kelly_cash",
                ),
                "orderbook_capacity_cash": number(
                    item.get("orderbook_capacity_cash"),
                    f"opportunities[{original_index}].orderbook_capacity_cash",
                ),
                "portfolio_cash_cap": (
                    number(item["portfolio_cash_cap"], f"opportunities[{original_index}].portfolio_cash_cap")
                    if item.get("portfolio_cash_cap") is not None
                    else None
                ),
                "minimum_order_cash": minimum_order_cash,
                "cash_before": cash_before,
                "allocated_cash": allocation,
                "cash_after": remaining_cash,
                "reason": reason,
            }
        )

    if allocated_cash > deployable_cash + 1e-7:
        raise AllocationError("allocated cash exceeds deployable cash")
    return {
        "portfolio": {
            "available_cash": available_cash,
            "holdings_liquidation_value": holdings_value,
            "conservative_bankroll": conservative_bankroll,
            "cold_start": cold_start,
            "new_position_limit": new_position_limit,
            "new_positions_allocated": new_positions,
            "deployable_cash": deployable_cash,
            "allocated_cash": allocated_cash,
            "remaining_cash": remaining_cash,
        },
        "reserve": reserve,
        "allocations": allocations,
    }


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Portfolio allocation JSON file, or - for stdin")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = read_json(args.input)
        if not isinstance(document, dict):
            raise AllocationError("input must be a JSON object")
        result = allocate_portfolio(document)
    except (OSError, json.JSONDecodeError, AllocationError) as exc:
        print(f"portfolio_allocate: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
