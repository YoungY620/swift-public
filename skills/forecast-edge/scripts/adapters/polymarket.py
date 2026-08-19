#!/usr/bin/env python3
"""Normalize the existing read-only Polymarket helper to the adapter contract."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


ADAPTER = "polymarket"
NATIVE = Path(__file__).resolve().parents[1] / "polymarket_data.py"


class NormalizationError(RuntimeError):
    """Raised when native Polymarket data cannot satisfy the adapter contract."""


def native(command: str, arguments: list[str]) -> Any:
    completed = subprocess.run(
        [sys.executable, str(NATIVE), command, *arguments],
        text=True,
        capture_output=True,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise NormalizationError(f"native Polymarket command {command} failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NormalizationError("native Polymarket helper returned invalid JSON") from exc


def text(value: Any) -> str:
    return str(value or "").strip()


def normalize_position(raw: dict[str, Any]) -> dict[str, Any]:
    market_identifier = text(raw.get("conditionId") or raw.get("condition_id"))
    instrument_identifier = text(raw.get("asset") or raw.get("tokenId") or raw.get("token_id"))
    outcome = text(raw.get("outcome")).lower()
    if not market_identifier or not instrument_identifier or outcome not in {"yes", "no"}:
        raise NormalizationError("position is missing market, instrument, or binary outcome")
    event_slug = text(raw.get("eventSlug") or raw.get("event_slug"))
    market_slug = text(raw.get("slug") or raw.get("marketSlug") or raw.get("market_slug"))
    slug = event_slug or market_slug
    return {
        "market_id": text(raw.get("marketId") or raw.get("market_id")) or market_identifier,
        "market_identifier": market_identifier,
        "condition_id": market_identifier,
        "instrument_identifier": instrument_identifier,
        "token_id": instrument_identifier,
        "market_slug": market_slug or None,
        "event_identifier": raw.get("eventId") or raw.get("event_id"),
        "event_slug": event_slug or None,
        "outcome": outcome,
        "question": raw.get("title") or raw.get("question"),
        "quantity": raw.get("size"),
        "average_price": raw.get("avgPrice"),
        "displayed_value": raw.get("currentValue"),
        "url": f"https://polymarket.com/event/{slug}" if slug else None,
    }


def normalize_account(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise NormalizationError("account response must be an object")
    account = raw.get("account")
    collateral = raw.get("collateral")
    positions_container = raw.get("positions")
    if not isinstance(account, dict) or not isinstance(collateral, dict):
        raise NormalizationError("account response is missing identity or collateral")
    if isinstance(positions_container, dict):
        position_rows = positions_container.get("positions")
    else:
        position_rows = positions_container
    if not isinstance(position_rows, list):
        raise NormalizationError("account positions must be a list")
    positions = [normalize_position(item) for item in position_rows if isinstance(item, dict)]
    currency = text(collateral.get("asset"))
    if not currency:
        raise NormalizationError("account collateral has no currency")
    return {
        "schema_version": "1.0",
        "adapter": ADAPTER,
        "account": {
            "input": account.get("input"),
            "identifier": account.get("address"),
            "public_url": account.get("profile_url"),
            "source": account.get("source"),
        },
        "portfolio": {
            "currency": currency,
            "available_cash": collateral.get("balance"),
            "balance_source": collateral.get("collateral_source"),
            "holdings_liquidation_value": None,
            "note": collateral.get("note"),
        },
        "positions": {"count": len(positions), "positions": positions},
    }


def normalize_market(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise NormalizationError("market response must be an object")
    condition_id = text(raw.get("condition_id") or raw.get("conditionId"))
    outcomes = raw.get("outcomes")
    if not condition_id or not isinstance(outcomes, list):
        raise NormalizationError("market response lacks identifier or outcomes")
    normalized_outcomes: list[dict[str, Any]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        instrument = text(outcome.get("instrument_identifier") or outcome.get("token_id"))
        if not instrument:
            raise NormalizationError("market outcome lacks an instrument identifier")
        normalized_outcomes.append(
            {
                "name": text(outcome.get("name")).lower(),
                "price": outcome.get("price"),
                "instrument_identifier": instrument,
            }
        )
    result = dict(raw)
    result.update(
        {
            "adapter": ADAPTER,
            "market_identifier": condition_id,
            "event_identifier": text(
                raw.get("event_identifier") or raw.get("event_id") or raw.get("event_slug")
            )
            or None,
            "outcomes": normalized_outcomes,
        }
    )
    return result


def normalize_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise NormalizationError("event response must be an object")
    result = dict(raw)
    result["adapter"] = ADAPTER
    if isinstance(result.get("markets"), list):
        normalized_markets = []
        for item in result["markets"]:
            if not isinstance(item, dict):
                continue
            market = normalize_market(item)
            if not market.get("event_identifier"):
                market["event_identifier"] = text(result.get("id") or result.get("slug")) or None
            normalized_markets.append(market)
        result["markets"] = normalized_markets
    return result


def normalize_candidate_events(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise NormalizationError("candidate-events response must be an object")
    result = dict(raw)
    result["adapter"] = ADAPTER
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        result["candidates"] = [normalize_event(item) for item in candidates if isinstance(item, dict)]
    return result


def normalize_book_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise NormalizationError("book response must be an object")
    book = raw.get("book")
    fee = raw.get("fee")
    if not isinstance(book, dict) or not isinstance(fee, dict):
        raise NormalizationError("book response is missing book or fee metadata")
    try:
        fee_rate = float(fee["fee_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NormalizationError("book response has no valid fee rate") from exc
    if not math.isfinite(fee_rate) or fee_rate < 0:
        raise NormalizationError("book response has no valid fee rate")

    normalized_book = dict(book)
    for side in ("bids", "asks"):
        levels = book.get(side)
        if not isinstance(levels, list):
            raise NormalizationError(f"book {side} must be a list")
        normalized_levels = []
        for index, level in enumerate(levels):
            if not isinstance(level, dict):
                raise NormalizationError(f"book {side}[{index}] must be an object")
            try:
                price = float(level["price"])
                size = float(level["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise NormalizationError(
                    f"book {side}[{index}] has invalid price or size"
                ) from exc
            if not math.isfinite(price) or not 0 <= price <= 1:
                raise NormalizationError(f"book {side}[{index}] has invalid price")
            if not math.isfinite(size) or size < 0:
                raise NormalizationError(f"book {side}[{index}] has invalid size")
            normalized_level = dict(level)
            normalized_level["fee_per_unit"] = fee_rate * price * (1.0 - price)
            normalized_levels.append(normalized_level)
        normalized_book[side] = normalized_levels

    result = dict(raw)
    result["book"] = normalized_book
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "command", choices=["account", "book", "candidate-events", "canonical-url", "event", "market"]
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "account":
            result = normalize_account(native("account", args.arguments))
        elif args.command == "market":
            result = normalize_market(native("market", args.arguments))
        elif args.command == "event":
            result = normalize_event(native("event", args.arguments))
        elif args.command == "candidate-events":
            result = normalize_candidate_events(native("candidate-events", args.arguments))
        elif args.command == "book":
            result = {
                "schema_version": "1.0",
                "adapter": ADAPTER,
                **normalize_book_response(native("book", args.arguments)),
            }
        else:
            if len(args.arguments) != 1:
                raise NormalizationError("canonical-url requires exactly one market identifier or id")
            market = normalize_market(native("market", args.arguments))
            url = market.get("url")
            if not isinstance(url, str) or not url:
                raise NormalizationError("market has no canonical public URL")
            result = {
                "schema_version": "1.0",
                "adapter": ADAPTER,
                "market_id": market.get("id"),
                "market_identifier": market.get("market_identifier"),
                "url": url,
            }
    except (NormalizationError, OSError) as exc:
        print(f"polymarket_adapter: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
