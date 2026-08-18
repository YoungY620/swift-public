#!/usr/bin/env python3
"""Validate strict multi-market trade-intent JSONL and public sell holdings."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised by CI configuration failures.
    Draft202012Validator = None  # type: ignore[assignment]


NOTIONAL_TOLERANCE = Decimal("0.01")


class TradeIntentError(ValueError):
    """Raised when a trade-intent artifact violates its contract."""


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TradeIntentError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TradeIntentError(f"{label} must be a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TradeIntentError(f"trade-intent JSONL is unavailable: {exc}") from exc
    if text == "":
        return []

    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise TradeIntentError(
                f"line {line_number} is blank; use a zero-byte file for no actions"
            )
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TradeIntentError(f"line {line_number} is not valid JSON: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise TradeIntentError(f"line {line_number} must contain one JSON object")
        items.append(item)
    return items


def decimal_field(item: dict[str, Any], name: str, line_number: int) -> Decimal:
    try:
        value = Decimal(item[name])
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise TradeIntentError(f"line {line_number} field {name} is not a decimal string") from exc
    if not value.is_finite() or value <= 0:
        raise TradeIntentError(f"line {line_number} field {name} must be positive and finite")
    return value


def snapshot_positions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    container = snapshot.get("positions", {})
    if isinstance(container, dict):
        container = container.get("positions", [])
    if not isinstance(container, list):
        raise TradeIntentError("Polymarket account snapshot positions must contain a list")
    return [position for position in container if isinstance(position, dict)]


def snapshot_currency(snapshot: dict[str, Any]) -> str:
    collateral = snapshot.get("collateral", {})
    if not isinstance(collateral, dict) or not str(collateral.get("asset", "")).strip():
        raise TradeIntentError("Polymarket account snapshot has no active collateral asset")
    return str(collateral["asset"])


def held_quantity(snapshot: dict[str, Any], item: dict[str, Any]) -> Decimal:
    total = Decimal(0)
    for position in snapshot_positions(snapshot):
        if str(position.get("conditionId", "")).casefold() != str(
            item["multi_market_identifier"]
        ).casefold():
            continue
        if str(position.get("asset", "")) != str(item["instrument_identifier"]):
            continue
        if str(position.get("outcome", "")).casefold() != str(item["outcome"]).casefold():
            continue
        try:
            size = Decimal(str(position.get("size", "")))
        except InvalidOperation as exc:
            raise TradeIntentError("matched Polymarket position has an invalid size") from exc
        if not size.is_finite() or size < 0:
            raise TradeIntentError("matched Polymarket position has an invalid size")
        total += size
    return total


def report_decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise TradeIntentError(f"{label} is not a valid decimal") from exc
    if not result.is_finite():
        raise TradeIntentError(f"{label} must be finite")
    return result


def validate_report_alignment(items: list[dict[str, Any]], report: dict[str, Any]) -> None:
    if report.get("schema_version") != "3.0":
        raise TradeIntentError("private report must use schema_version 3.0")
    actions = report.get("actions")
    if not isinstance(actions, list):
        raise TradeIntentError("private report actions must be a list")

    remaining = list(enumerate(items, start=1))
    actionable = 0
    side_by_action = {"BUY": "buy", "ADD": "buy", "SELL": "sell", "TRIM": "sell"}
    for action_index, raw_action in enumerate(actions):
        if not isinstance(raw_action, dict):
            raise TradeIntentError(f"private report action {action_index} must be an object")
        action_name = raw_action.get("action")
        if action_name == "MAINTAIN":
            continue
        if action_name not in side_by_action:
            raise TradeIntentError(
                f"private report action {action_index} has an unsupported action"
            )
        execution = raw_action.get("execution")
        if not isinstance(execution, dict):
            raise TradeIntentError(f"private report action {action_index} has no execution")
        actionable += 1
        expected_side = side_by_action[str(action_name)]
        expected_outcome = str(raw_action.get("outcome", "")).casefold()
        expected_price = report_decimal(execution.get("vwap"), f"action {action_index} vwap")
        expected_notional = report_decimal(
            execution.get("gross_cash"), f"action {action_index} gross_cash"
        )
        expected_quantity = report_decimal(
            execution.get("shares"), f"action {action_index} shares"
        )

        match_at: int | None = None
        for remaining_index, (line_number, item) in enumerate(remaining):
            if item["side"] != expected_side or item["outcome"] != expected_outcome:
                continue
            price = Decimal(item["reference_price"])
            notional = Decimal(item["notional"])
            quantity = Decimal(item["quantity"])
            if price != expected_price or quantity != expected_quantity:
                continue
            if abs(notional - expected_notional) > NOTIONAL_TOLERANCE:
                continue
            match_at = remaining_index
            break
        if match_at is None:
            raise TradeIntentError(
                f"private report action {action_index} has no matching JSONL item "
                f"({expected_side} {expected_outcome}, price={expected_price}, "
                f"notional={expected_notional}, quantity={expected_quantity})"
            )
        remaining.pop(match_at)

    if remaining:
        lines = ", ".join(str(line_number) for line_number, _ in remaining)
        raise TradeIntentError(
            f"JSONL lines {lines} have no matching actionable private report entry"
        )
    if actionable != len(items):
        raise TradeIntentError(
            f"private report has {actionable} actionable entries but JSONL has {len(items)} items"
        )


def validate_items(
    items: list[dict[str, Any]],
    schema: dict[str, Any],
    enabled_venues: set[str],
    polymarket_snapshot: dict[str, Any] | None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if Draft202012Validator is None:
        raise TradeIntentError("the jsonschema package is required")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    seen_instruments: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}

    for line_number, item in enumerate(items, start=1):
        schema_errors = sorted(validator.iter_errors(item), key=lambda error: list(error.path))
        if schema_errors:
            error = schema_errors[0]
            field = ".".join(str(part) for part in error.path) or "<item>"
            raise TradeIntentError(f"line {line_number} field {field}: {error.message}")

        venue = str(item["venue"])
        if venue not in enabled_venues:
            raise TradeIntentError(f"line {line_number} venue {venue!r} is not enabled")
        instrument_key = (venue, str(item["instrument_identifier"]))
        if instrument_key in seen_instruments:
            raise TradeIntentError(
                f"line {line_number} duplicates or conflicts with an earlier instruction "
                f"for {instrument_key}"
            )
        seen_instruments.add(instrument_key)
        counts[venue] = counts.get(venue, 0) + 1

        price = decimal_field(item, "reference_price", line_number)
        notional = decimal_field(item, "notional", line_number)
        quantity = decimal_field(item, "quantity", line_number)
        if price > 1:
            raise TradeIntentError(f"line {line_number} reference_price must not exceed 1")
        expected_notional = price * quantity
        if abs(notional - expected_notional) > NOTIONAL_TOLERANCE:
            raise TradeIntentError(
                f"line {line_number} notional differs from reference_price * quantity by more than "
                f"{NOTIONAL_TOLERANCE}"
            )

        if venue == "polymarket":
            if polymarket_snapshot is None:
                raise TradeIntentError("Polymarket intents require a trusted account snapshot")
            expected_currency = snapshot_currency(polymarket_snapshot)
            if item["currency"] != expected_currency:
                raise TradeIntentError(
                    f"line {line_number} currency {item['currency']!r} does not match "
                    f"active collateral {expected_currency!r}"
                )
            if item["side"] == "sell":
                available = held_quantity(polymarket_snapshot, item)
                if available < quantity:
                    raise TradeIntentError(
                        f"line {line_number} sell quantity {quantity} exceeds held "
                        f"quantity {available}"
                    )

    if report is not None:
        validate_report_alignment(items, report)
    return {"items": len(items), "venues": dict(sorted(counts.items()))}


def build_parser(root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=root / "schemas" / "trade-intent.schema.json",
    )
    parser.add_argument("--enabled-venues", default="polymarket")
    parser.add_argument("--polymarket-account-snapshot", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    args = build_parser(root).parse_args(argv)
    enabled_venues = {venue.strip() for venue in args.enabled_venues.split(",") if venue.strip()}
    if not enabled_venues:
        raise TradeIntentError("at least one enabled venue is required")
    schema = load_json_object(args.schema, "trade-intent schema")
    snapshot = (
        load_json_object(args.polymarket_account_snapshot, "Polymarket account snapshot")
        if args.polymarket_account_snapshot
        else None
    )
    report = load_json_object(args.report, "private report") if args.report else None
    summary = validate_items(load_jsonl(args.jsonl), schema, enabled_venues, snapshot, report)
    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TradeIntentError) as exc:
        print(f"trade-intent validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
