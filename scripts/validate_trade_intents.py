#!/usr/bin/env python3
"""Validate strict multi-market trade-intent JSONL and public sell holdings."""

from __future__ import annotations

import argparse
import json
import re
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
        raise TradeIntentError("normalized account snapshot positions must contain a list")
    return [position for position in container if isinstance(position, dict)]


def snapshot_currency(snapshot: dict[str, Any]) -> str:
    portfolio = snapshot.get("portfolio", {})
    if not isinstance(portfolio, dict) or not str(portfolio.get("currency", "")).strip():
        raise TradeIntentError("normalized account snapshot has no portfolio currency")
    return str(portfolio["currency"])


def held_quantity(snapshot: dict[str, Any], item: dict[str, Any]) -> Decimal:
    total = Decimal(0)
    for position in snapshot_positions(snapshot):
        if str(position.get("market_identifier", "")).casefold() != str(
            item["multi_market_identifier"]
        ).casefold():
            continue
        if str(position.get("instrument_identifier", "")) != str(item["instrument_identifier"]):
            continue
        if str(position.get("outcome", "")).casefold() != str(item["outcome"]).casefold():
            continue
        try:
            size = Decimal(str(position.get("quantity", "")))
        except InvalidOperation as exc:
            raise TradeIntentError("matched normalized position has an invalid quantity") from exc
        if not size.is_finite() or size < 0:
            raise TradeIntentError("matched normalized position has an invalid quantity")
        total += size
    return total


def load_adapter_registry(path: Path) -> dict[str, dict[str, Any]]:
    registry = load_json_object(path, "market adapter registry")
    if registry.get("schema_version") != "1.0":
        raise TradeIntentError("market adapter registry must use schema_version 1.0")
    adapters = registry.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        raise TradeIntentError("market adapter registry must contain adapters")
    result: dict[str, dict[str, Any]] = {}
    for adapter, raw in adapters.items():
        if not isinstance(adapter, str) or not isinstance(raw, dict):
            raise TradeIntentError("market adapter registry contains an invalid entry")
        for field in ("market_identifier_pattern", "instrument_identifier_pattern"):
            pattern = raw.get(field)
            if not isinstance(pattern, str) or not pattern:
                raise TradeIntentError(f"market adapter {adapter} has no {field}")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise TradeIntentError(
                    f"market adapter {adapter} has an invalid {field}: {exc}"
                ) from exc
        result[adapter] = raw
    return result


def load_snapshot_manifest(path: Path) -> dict[str, dict[str, Any]]:
    manifest = load_json_object(path, "account snapshot manifest")
    if manifest.get("schema_version") != "1.0":
        raise TradeIntentError("account snapshot manifest must use schema_version 1.0")
    raw_snapshots = manifest.get("snapshots")
    if not isinstance(raw_snapshots, dict):
        raise TradeIntentError("account snapshot manifest must contain a snapshots object")
    snapshots: dict[str, dict[str, Any]] = {}
    for adapter, raw_path in raw_snapshots.items():
        if not isinstance(adapter, str) or not isinstance(raw_path, str) or not raw_path:
            raise TradeIntentError("account snapshot manifest contains an invalid entry")
        snapshot = load_json_object(Path(raw_path), f"{adapter} account snapshot")
        if snapshot.get("schema_version") != "1.0" or snapshot.get("adapter") != adapter:
            raise TradeIntentError(
                f"{adapter} account snapshot does not match the normalized adapter contract"
            )
        snapshot_currency(snapshot)
        snapshot_positions(snapshot)
        snapshots[adapter] = snapshot
    return snapshots


def report_decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise TradeIntentError(f"{label} is not a valid decimal") from exc
    if not result.is_finite():
        raise TradeIntentError(f"{label} must be finite")
    return result


def report_adapter(report: dict[str, Any]) -> str:
    run = report.get("run")
    account = run.get("account") if isinstance(run, dict) else None
    adapter = account.get("adapter") if isinstance(account, dict) else None
    if not isinstance(adapter, str) or not adapter:
        raise TradeIntentError("private report run.account.adapter must identify one venue")
    return adapter


def validate_report_alignment(items: list[dict[str, Any]], report: dict[str, Any]) -> None:
    if report.get("schema_version") != "3.0":
        raise TradeIntentError("private report must use schema_version 3.0")
    actions = report.get("actions")
    if not isinstance(actions, list):
        raise TradeIntentError("private report actions must be a list")

    adapter = report_adapter(report)
    venue_items = [
        (line_number, item)
        for line_number, item in enumerate(items, start=1)
        if item["venue"] == adapter
    ]

    current_positions = report.get("current_positions", [])
    candidates = report.get("candidates", [])
    if not isinstance(current_positions, list) or not isinstance(candidates, list):
        raise TradeIntentError("private report analyses must be lists")
    analyses = current_positions + candidates
    if not all(isinstance(analysis, dict) for analysis in analyses):
        raise TradeIntentError("private report analyses must be objects")
    analysis_by_market: dict[str, dict[str, Any]] = {}
    for analysis in analyses:
        market = analysis.get("market")
        if not isinstance(market, dict) or not str(market.get("id", "")):
            raise TradeIntentError("private report analysis has no market id")
        market_id = str(market["id"])
        if market_id in analysis_by_market:
            raise TradeIntentError(f"private report duplicates market id {market_id}")
        analysis_by_market[market_id] = analysis

    remaining = list(venue_items)
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
        market_id = str(raw_action.get("market_id", ""))
        analysis = analysis_by_market.get(market_id)
        if analysis is None:
            raise TradeIntentError(
                f"private report action {action_index} has no matching market analysis"
            )
        market = analysis.get("market")
        if not isinstance(market, dict):
            raise TradeIntentError(
                f"private report action {action_index} has an invalid market analysis"
            )
        market_identifier = str(market.get("multi_market_identifier", ""))
        instrument_identifier = str(market.get("instrument_identifier", ""))
        if not market_identifier or not instrument_identifier:
            raise TradeIntentError(
                f"private report action {action_index} lacks routing identifiers"
            )
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
            if str(item["multi_market_identifier"]) != market_identifier:
                continue
            if str(item["instrument_identifier"]) != instrument_identifier:
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
    if actionable != len(venue_items):
        raise TradeIntentError(
            f"private report for {adapter} has {actionable} actionable entries but JSONL has "
            f"{len(venue_items)} items"
        )


def validate_items(
    items: list[dict[str, Any]],
    schema: dict[str, Any],
    enabled_venues: set[str],
    snapshots: dict[str, dict[str, Any]],
    adapters: dict[str, dict[str, Any]],
    reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if Draft202012Validator is None:
        raise TradeIntentError("the jsonschema package is required")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    seen_targets: set[tuple[str, str, str]] = set()
    counts: dict[str, int] = {}
    if set(snapshots) != enabled_venues:
        raise TradeIntentError(
            "normalized account snapshots must exactly match the enabled venues"
        )
    unregistered = enabled_venues - set(adapters)
    if unregistered:
        raise TradeIntentError(
            f"enabled venues have no registered adapter: {', '.join(sorted(unregistered))}"
        )

    for line_number, item in enumerate(items, start=1):
        schema_errors = sorted(validator.iter_errors(item), key=lambda error: list(error.path))
        if schema_errors:
            error = schema_errors[0]
            field = ".".join(str(part) for part in error.path) or "<item>"
            raise TradeIntentError(f"line {line_number} field {field}: {error.message}")

        venue = str(item["venue"])
        if venue not in enabled_venues:
            raise TradeIntentError(f"line {line_number} venue {venue!r} is not enabled")
        adapter = adapters[venue]
        if re.fullmatch(
            str(adapter["market_identifier_pattern"]),
            str(item["multi_market_identifier"]),
        ) is None:
            raise TradeIntentError(
                f"line {line_number} market identifier violates the {venue} adapter contract"
            )
        if re.fullmatch(
            str(adapter["instrument_identifier_pattern"]),
            str(item["instrument_identifier"]),
        ) is None:
            raise TradeIntentError(
                f"line {line_number} instrument identifier violates the {venue} adapter contract"
            )
        routing_target = (
            venue,
            str(item["multi_market_identifier"]),
            str(item["instrument_identifier"]),
        )
        if routing_target in seen_targets:
            raise TradeIntentError(
                f"line {line_number} duplicates or conflicts with an earlier instruction "
                f"for {routing_target}"
            )
        seen_targets.add(routing_target)
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

        snapshot = snapshots[venue]
        expected_currency = snapshot_currency(snapshot)
        if item["currency"] != expected_currency:
            raise TradeIntentError(
                f"line {line_number} currency {item['currency']!r} does not match "
                f"the {venue} snapshot currency {expected_currency!r}"
            )
        if item["side"] == "sell":
            available = held_quantity(snapshot, item)
            if available < quantity:
                raise TradeIntentError(
                    f"line {line_number} sell quantity {quantity} exceeds held "
                    f"quantity {available} on {venue}"
                )

    if reports is not None:
        report_venues: set[str] = set()
        for report in reports:
            adapter = report_adapter(report)
            if adapter in report_venues:
                raise TradeIntentError(f"multiple private reports declare venue {adapter}")
            report_venues.add(adapter)
            validate_report_alignment(items, report)
        if report_venues != enabled_venues:
            raise TradeIntentError(
                "private report venues must exactly match the enabled venues"
            )
        item_venues = {str(item["venue"]) for item in items}
        if item_venues - report_venues:
            raise TradeIntentError("JSONL contains a venue with no matching private report")
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
    parser.add_argument("--account-snapshots-manifest", type=Path, required=True)
    parser.add_argument(
        "--adapter-registry",
        type=Path,
        default=root / "skills" / "forecast-edge" / "config" / "adapters.json",
    )
    parser.add_argument("--report", type=Path, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    args = build_parser(root).parse_args(argv)
    enabled_venues = {venue.strip() for venue in args.enabled_venues.split(",") if venue.strip()}
    if not enabled_venues:
        raise TradeIntentError("at least one enabled venue is required")
    schema = load_json_object(args.schema, "trade-intent schema")
    snapshots = load_snapshot_manifest(args.account_snapshots_manifest)
    adapters = load_adapter_registry(args.adapter_registry)
    reports = [load_json_object(path, "private report") for path in args.report]
    summary = validate_items(
        load_jsonl(args.jsonl),
        schema,
        enabled_venues,
        snapshots,
        adapters,
        reports if reports else None,
    )
    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TradeIntentError) as exc:
        print(f"trade-intent validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
