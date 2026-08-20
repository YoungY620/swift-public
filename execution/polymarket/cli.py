"""Command-line stages used by the Polymarket execution workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .artifact import (
    ArtifactError,
    load_and_validate_jsonl,
    load_json_object,
    sha256_file,
    write_filtered_jsonl,
)
from .engine import ExecutionError, UncertainExecutionError, execute_orders, verify_receipts
from .ledger import DynamoLedger, LedgerError, SQLiteLedger
from .models import (
    ZERO,
    MarketSnapshot,
    PlannedOrder,
    RiskLimits,
    decimal_string,
    decimal_value,
)
from .planner import PlanningError, build_execution_plan
from .sdk_adapter import (
    AdapterError,
    Credentials,
    LivePolymarketAdapter,
    required_exchange_allowance,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_intents(path: Path, schema: Path) -> list[Any]:
    return load_and_validate_jsonl(path, schema_path=schema, venue="polymarket")


def command_validate(args: argparse.Namespace) -> int:
    intents = _load_intents(args.input, args.schema)
    write_filtered_jsonl(args.output, intents)
    summary = {
        "schema_version": "1.0",
        "venue": "polymarket",
        "items": len(intents),
        "source_sha256": sha256_file(args.input),
        "filtered_sha256": sha256_file(args.output),
    }
    _write_json(args.summary, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    intents = _load_intents(args.intents, args.schema)
    with LivePolymarketAdapter(Credentials.from_env()) as adapter:
        snapshot = adapter.preflight(intents)
    snapshot["trade_intents_sha256"] = sha256_file(args.intents)
    _write_json(args.output, snapshot)
    print(
        json.dumps(
            {
                "wallet": snapshot["account"]["wallet"],
                "country": snapshot["health"]["geoblock"]["country"],
                "markets": len(snapshot["markets"]),
            },
            sort_keys=True,
        )
    )
    return 0


def command_cancel_open_orders(args: argparse.Namespace) -> int:
    with LivePolymarketAdapter(Credentials.from_env()) as adapter:
        summary = adapter.cancel_all_open_orders()
    summary.update(
        {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    )
    _write_json(args.output, summary)
    print(
        json.dumps(
            {
                "canceled_order_count": summary["canceled_order_count"],
                "remaining_open_order_count": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def command_plan(args: argparse.Namespace) -> int:
    intents = _load_intents(args.intents, args.schema)
    preflight = load_json_object(args.preflight, "preflight snapshot")
    limits = RiskLimits.from_dict(load_json_object(args.limits, "risk limits"))
    if preflight.get("trade_intents_sha256") != sha256_file(args.intents):
        raise PlanningError("preflight snapshot is bound to another intent file")
    if preflight.get("health", {}).get("geoblock", {}).get("blocked") is not False:
        raise PlanningError("preflight did not prove blocked=false")
    if preflight.get("open_order_count") != 0:
        raise PlanningError("preflight account still has open orders")
    collateral_asset = preflight.get("collateral_asset", {})
    if collateral_asset.get("asset") != "pUSD" or collateral_asset.get("decimals") != 6:
        raise PlanningError("preflight did not resolve the active six-decimal pUSD collateral")
    for intent in intents:
        if intent.currency != "pUSD":
            raise PlanningError("trade intent currency is not the active pUSD collateral")

    positions = {
        str(item.get("token_id", "")): decimal_value(item.get("size", "0"), "position size")
        for item in preflight.get("positions", [])
        if isinstance(item, dict)
    }
    markets = {
        str(token): MarketSnapshot.from_dict(raw)
        for token, raw in preflight.get("markets", {}).items()
        if isinstance(raw, dict)
    }
    available_cash = decimal_value(
        preflight.get("collateral", {}).get("effective_available_balance", "0"),
        "effective available collateral",
    )
    plan = build_execution_plan(
        intents=intents,
        markets=markets,
        positions=positions,
        available_cash=available_cash,
        limits=limits,
    )
    for order in plan.orders:
        market = markets[order.token_id]
        allowance = (
            preflight["collateral"]
            if order.side == "buy"
            else preflight.get("conditional", {}).get(order.token_id, {})
        )
        balance = decimal_value(allowance.get("balance", "0"), "asset balance")
        required = order.cash_effect if order.side == "buy" else order.quantity
        if balance < required:
            raise PlanningError(
                f"intent {order.intent_index} required balance {required} exceeds {balance}"
            )
        if required_exchange_allowance(market, allowance) < required:
            raise PlanningError(
                f"intent {order.intent_index} lacks preexisting exchange allowance"
            )

    now = datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "execution_id": args.execution_id,
        "artifact_sha256": args.artifact_sha256,
        "intents_sha256": sha256_file(args.intents),
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(seconds=limits.plan_ttl_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
        "account": preflight["account"],
        "limits": limits.to_dict(),
        **plan.to_dict(),
    }
    _write_json(args.output, document)
    print(
        json.dumps(
            {
                "orders": len(plan.orders),
                "skipped": len(plan.skipped),
                "total_buy_cash": decimal_string(plan.total_buy_cash),
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ExecutionError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _planning_summary(document: dict[str, Any]) -> dict[str, Any]:
    raw_orders = document.get("orders", [])
    raw_skipped = document.get("skipped", [])
    if not isinstance(raw_orders, list) or not isinstance(raw_skipped, list):
        raise ExecutionError("execution plan orders and skipped entries must be arrays")
    outcomes: list[dict[str, Any]] = []
    for raw in raw_orders:
        if not isinstance(raw, dict):
            raise ExecutionError("execution plan contains a non-object order")
        outcomes.append(
            {
                "intent_index": int(raw.get("intent_index", 0)),
                "disposition": "planned",
                "intent": raw.get("intent"),
                "planned_order": raw,
            }
        )
    for raw in raw_skipped:
        if not isinstance(raw, dict):
            raise ExecutionError("execution plan contains a non-object skipped intent")
        outcomes.append(
            {
                "intent_index": int(raw.get("intent_index", 0)),
                "disposition": "skipped",
                "intent": raw.get("intent"),
                "skip": {
                    "code": raw.get("code"),
                    "message": raw.get("message"),
                },
            }
        )
    outcomes.sort(key=lambda item: int(item["intent_index"]))
    expected_indices = list(range(1, len(outcomes) + 1))
    if [item["intent_index"] for item in outcomes] != expected_indices:
        raise ExecutionError("execution plan does not account for every original intent exactly once")
    if any(not isinstance(item.get("intent"), dict) for item in outcomes):
        raise ExecutionError("execution plan intent trace is incomplete")
    return {
        "schema_version": "1.0",
        "plan_created_at": document.get("created_at"),
        "plan_expires_at": document.get("expires_at"),
        "source_intent_count": len(outcomes),
        "planned_order_count": len(raw_orders),
        "skipped_intent_count": len(raw_skipped),
        "intent_outcomes": outcomes,
    }


def command_execute(args: argparse.Namespace) -> int:
    document = load_json_object(args.plan, "execution plan")
    cancellations = load_json_object(args.cancellations, "pre-execution cancellation summary")
    if cancellations.get("remaining_open_order_count") != 0:
        raise ExecutionError("pre-execution cancellation did not prove an empty order book")
    if document.get("execution_id") != args.execution_id:
        raise ExecutionError("execution plan id does not match the workflow")
    if document.get("artifact_sha256") != args.artifact_sha256:
        raise ExecutionError("execution plan is bound to another upstream artifact")
    if datetime.now(timezone.utc) > _parse_utc(str(document.get("expires_at", "")), "expires_at"):
        raise ExecutionError("execution plan expired before order submission")
    limits = RiskLimits.from_dict(document.get("limits", {}))
    orders = tuple(PlannedOrder.from_dict(item) for item in document.get("orders", []))
    planning = _planning_summary(document)
    if not orders:
        summary = {
            "schema_version": "1.0",
            "status": "NO_ACTIONS",
            "execution_id": args.execution_id,
            "claim": "not_required",
            "planning": planning,
            "pre_execution_cancellation": cancellations,
            "receipts": [],
        }
        _write_json(args.output, summary)
        return 0

    if args.ledger_path is not None:
        ledger = SQLiteLedger(
            path=args.ledger_path,
            execution_id=args.execution_id,
            lease_owner=args.lease_owner,
        )
    else:
        ledger = DynamoLedger(
            table_name=args.ledger_table,
            execution_id=args.execution_id,
            lease_owner=args.lease_owner,
        )
    planned_notional = sum((order.gross_notional for order in orders), ZERO)
    claim = ledger.claim_run(
        artifact_sha256=args.artifact_sha256,
        planned_notional=planned_notional,
        daily_notional_limit=decimal_value(
            args.daily_notional_limit, "daily notional limit", positive=True
        ),
    )
    if claim == "completed":
        existing = ledger.get(ledger.run_key) or {}
        summary = json.loads(str(existing.get("summary", "{}")))
        summary.setdefault("execution_id", args.execution_id)
        summary.setdefault("planning", planning)
        summary["idempotent_replay"] = True
        _write_json(args.output, summary)
        return 0
    try:
        with LivePolymarketAdapter(Credentials.from_env()) as adapter:
            summary = execute_orders(
                adapter=adapter,
                orders=orders,
                limits=limits,
                ledger=ledger,
                settlement_timeout_seconds=args.settlement_timeout_seconds,
                pre_execution_cancellation=cancellations,
                execution_id=args.execution_id,
                execution_claim=claim,
                planning=planning,
            )
        _write_json(args.output, summary)
        return 0
    except Exception:
        existing = ledger.get(ledger.run_key) or {}
        if existing.get("summary"):
            summary = json.loads(str(existing["summary"]))
            summary["execution_id"] = args.execution_id
            _write_json(args.output, summary)
        raise


def command_verify(args: argparse.Namespace) -> int:
    summary = load_json_object(args.receipt, "execution receipt")
    if summary.get("execution_id") != args.execution_id:
        raise ExecutionError("execution receipt id does not match the workflow")
    with LivePolymarketAdapter(Credentials.from_env()) as adapter:
        verification = verify_receipts(adapter=adapter, summary=summary)
    verification["execution_id"] = args.execution_id
    _write_json(args.output, verification)
    print(json.dumps({"orders": len(verification["orders"]), "verified": True}))
    return 0


def build_parser(root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    schema_default = root / "schemas" / "trade-intent.schema.json"

    validate = subparsers.add_parser("validate-artifact")
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--output", required=True, type=Path)
    validate.add_argument("--summary", required=True, type=Path)
    validate.add_argument("--schema", type=Path, default=schema_default)
    validate.set_defaults(function=command_validate)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--intents", required=True, type=Path)
    preflight.add_argument("--output", required=True, type=Path)
    preflight.add_argument("--schema", type=Path, default=schema_default)
    preflight.set_defaults(function=command_preflight)

    cancel = subparsers.add_parser("cancel-open-orders")
    cancel.add_argument("--output", required=True, type=Path)
    cancel.set_defaults(function=command_cancel_open_orders)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--intents", required=True, type=Path)
    plan.add_argument("--preflight", required=True, type=Path)
    plan.add_argument("--limits", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--execution-id", required=True)
    plan.add_argument("--artifact-sha256", required=True)
    plan.add_argument("--schema", type=Path, default=schema_default)
    plan.set_defaults(function=command_plan)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--plan", required=True, type=Path)
    execute.add_argument("--cancellations", required=True, type=Path)
    execute.add_argument("--output", required=True, type=Path)
    execute.add_argument("--execution-id", required=True)
    execute.add_argument("--artifact-sha256", required=True)
    ledger = execute.add_mutually_exclusive_group(required=True)
    ledger.add_argument("--ledger-table")
    ledger.add_argument("--ledger-path", type=Path)
    execute.add_argument("--lease-owner", required=True)
    execute.add_argument("--daily-notional-limit", required=True)
    execute.add_argument("--settlement-timeout-seconds", type=int, default=90)
    execute.set_defaults(function=command_execute)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--execution-id", required=True)
    verify.set_defaults(function=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    args = build_parser(root).parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AdapterError,
        ArtifactError,
        ExecutionError,
        LedgerError,
        PlanningError,
        UncertainExecutionError,
        OSError,
        ValueError,
    ) as exc:
        print(f"polymarket execution failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
