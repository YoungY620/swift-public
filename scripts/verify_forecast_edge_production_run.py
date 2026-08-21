#!/usr/bin/env python3
"""Deterministically verify one completed Forecast Edge parent iteration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class AcceptanceInputError(ValueError):
    pass


def load_json(path: Path, label: str, *, optional: bool = False) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if optional:
            return None
        raise AcceptanceInputError(f"could not read {label}: {exc}") from exc
    return None if optional and value is None else value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def as_object(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def as_nonnegative_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def normalize_jobs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("jobs")
    if not isinstance(value, list):
        raise AcceptanceInputError("jobs must be an array or an object containing jobs")
    return [item for item in value if isinstance(item, dict)]


def stage_jobs(jobs: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    pattern = re.compile(rf"^{re.escape(stage)}(?: /|$)")
    return [item for item in jobs if pattern.search(str(item.get("name") or ""))]


def build_document(
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    manifest: dict[str, Any] | None,
    receipt: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    diagnostic_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(identifier: str, passed: bool, evidence: list[str], reason: str) -> None:
        checks.append(
            {
                "id": identifier,
                "status": "PASS" if passed else "FAIL",
                "evidence": evidence,
                "reason": reason,
            }
        )

    def not_applicable(identifier: str, reason: str) -> None:
        checks.append(
            {
                "id": identifier,
                "status": "NOT_APPLICABLE",
                "evidence": [],
                "reason": reason,
            }
        )

    run_id = str(run.get("id") or "")
    event = str(run.get("event") or "")
    run_attempt = str(run.get("run_attempt") or "")
    head_sha = str(run.get("head_sha") or "")
    parent_success = run.get("status") == "completed" and run.get("conclusion") == "success"
    check(
        "PARENT-CONCLUSION",
        parent_success,
        ["run"],
        f"parent status={run.get('status')!r}, conclusion={run.get('conclusion')!r}",
    )

    stage_conclusions: dict[str, str] = {}
    for stage in ("gate", "research", "execution", "release"):
        matches = stage_jobs(jobs, stage)
        conclusion = str(matches[0].get("conclusion") or "missing") if len(matches) == 1 else "missing"
        stage_conclusions[stage] = conclusion
        check(
            f"STAGE-{stage.upper()}",
            len(matches) == 1 and conclusion == "success",
            ["jobs"],
            f"matched={len(matches)}, conclusion={conclusion}",
        )

    manifest_execution = as_object(manifest.get("execution")) if manifest else None
    execution_requested = (
        manifest_execution.get("requested") is True
        if manifest_execution
        else event == "schedule" or stage_conclusions["execution"] != "skipped"
    )
    research_only = event == "workflow_dispatch" and not execution_requested
    if research_only:
        execution_matches = stage_jobs(jobs, "execution")
        execution_conclusion = (
            str(execution_matches[0].get("conclusion") or "missing")
            if len(execution_matches) == 1
            else "missing"
        )
        execution_check_index = next(
            index for index, item in enumerate(checks) if item["id"] == "STAGE-EXECUTION"
        )
        checks[execution_check_index] = {
            "id": "STAGE-EXECUTION",
            "status": "NOT_APPLICABLE" if execution_conclusion == "skipped" else "FAIL",
            "evidence": ["jobs"],
            "reason": (
                "explicit research-only manual iteration"
                if execution_conclusion == "skipped"
                else f"research-only execution conclusion={execution_conclusion}"
            ),
        }

    if manifest is None:
        check("RELEASE-LINEAGE", False, ["release.manifest"], "final manifest unavailable")
    else:
        lineage_ok = (
            str(manifest.get("workflow_run_id") or "") == run_id
            and str(manifest.get("workflow_run_attempt") or "") == run_attempt
            and str(manifest.get("commit_sha") or "") == head_sha
        )
        check(
            "RELEASE-LINEAGE",
            lineage_ok,
            ["run", "release.manifest"],
            "manifest run id, attempt and commit must match the completed parent run",
        )

    if event == "schedule":
        schedule = as_object(manifest.get("schedule")) if manifest else None
        execution = manifest_execution
        schedule_ok = bool(
            schedule
            and execution
            and schedule.get("event_name") == "schedule"
            and schedule.get("interval_hours") == 8
            and schedule.get("anchor_utc_hour") == 2
            and schedule.get("gate_should_run") is True
            and schedule.get("automation_paused") is False
            and execution.get("requested") is True
            and execution.get("configuration_ready") is True
            and execution.get("workflow_result") == "success"
        )
        check(
            "SCHEDULE-FULL-ITERATION",
            schedule_ok,
            ["run", "release.manifest"],
            "schedule must record the fixed eight-hour gate and requested successful execution",
        )
    else:
        not_applicable("SCHEDULE-FULL-ITERATION", f"parent event is {event!r}, not schedule")

    if not execution_requested:
        for identifier in (
            "EXECUTION-IDENTITY",
            "EXECUTION-RECEIPT",
            "EXECUTION-DISPOSITIONS",
            "EXECUTION-PROTECTION",
            "EXECUTION-PHASES",
            "EXECUTION-OPEN-ORDERS",
        ):
            not_applicable(identifier, "explicit research-only manual iteration")
    else:
        receipt_values = (
            receipt.get("receipts", [])
            if receipt and isinstance(receipt.get("receipts"), list)
            else []
        )
        receipt_items = [item for item in receipt_values if isinstance(item, dict)]
        receipt_statuses = [str(item.get("status") or "") for item in receipt_items]
        cancellation = as_object(receipt.get("pre_execution_cancellation")) if receipt else None
        execution_ids = {
            str(value.get("execution_id") or "")
            for value in (receipt, verification, diagnostic_summary)
            if isinstance(value, dict)
        }
        execution_identity_ok = bool(
            len(execution_ids) == 1
            and "" not in execution_ids
            and diagnostic_summary
            and str(diagnostic_summary.get("workflow_run_id") or "") == run_id
            and str(diagnostic_summary.get("workflow_run_attempt") or "") == run_attempt
            and str(diagnostic_summary.get("commit_sha") or "") == head_sha
        )
        check(
            "EXECUTION-IDENTITY",
            execution_identity_ok,
            ["run", "release.execution_receipt", "release.execution_verification", "diagnostics.summary"],
            "receipt, verification and diagnostics must share one execution id and parent lineage",
        )
        receipt_ok = bool(
            receipt
            and len(receipt_items) == len(receipt_values)
            and receipt.get("status")
            in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "NO_ACTIONS"}
            and cancellation
            and cancellation.get("remaining_open_order_count") == 0
            and all(status in {"FULL", "NO_FILL"} for status in receipt_statuses)
            and all(
                item.get("status") != "NO_FILL" or item.get("code") == "FOK_NOT_FILLED"
                for item in receipt_items
            )
        )
        check(
            "EXECUTION-RECEIPT",
            receipt_ok,
            ["release.execution_receipt"],
            f"receipt status={receipt.get('status') if receipt else None!r}, terminal statuses={receipt_statuses}",
        )

        source_count = (
            diagnostic_summary.get("source_intent_count")
            if diagnostic_summary
            else None
        )
        planned_count = (
            diagnostic_summary.get("planned_order_count")
            if diagnostic_summary
            else None
        )
        skipped_count = (
            diagnostic_summary.get("skipped_intent_count")
            if diagnostic_summary
            else None
        )
        receipt_count = (
            diagnostic_summary.get("receipt_count")
            if diagnostic_summary
            else None
        )
        intent_trace = (
            diagnostic_summary.get("intent_trace")
            if diagnostic_summary and isinstance(diagnostic_summary.get("intent_trace"), list)
            else []
        )
        trace_items = [item for item in intent_trace if isinstance(item, dict)]
        trace_indices = [item.get("intent_index") for item in trace_items]
        planned_indices = [
            item.get("intent_index")
            for item in trace_items
            if item.get("planning_disposition") == "planned"
        ]
        skipped_trace = [
            item
            for item in trace_items
            if item.get("planning_disposition") == "skipped"
        ]
        receipt_indices = [item.get("intent_index") for item in receipt_items]
        count_values = (source_count, planned_count, skipped_count, receipt_count)
        counts_valid = all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in count_values
        )
        indices_valid = all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (*trace_indices, *planned_indices, *receipt_indices)
        )
        receipt_status_value = receipt.get("status") if receipt else None
        dispositions_ok = bool(
            diagnostic_summary
            and counts_valid
            and indices_valid
            and source_count == planned_count + skipped_count
            and receipt_count == planned_count == len(receipt_items)
            and len(trace_items) == len(intent_trace) == source_count
            and trace_indices == list(range(1, source_count + 1))
            and len(planned_indices) == planned_count
            and len(skipped_trace) == skipped_count
            and sorted(receipt_indices) == sorted(planned_indices)
            and len(set(receipt_indices)) == len(receipt_indices)
            and all(
                isinstance(item.get("receipt"), dict)
                and item.get("execution_disposition")
                == str(item["receipt"].get("status", "")).lower()
                and item.get("execution_disposition") in {"full", "no_fill"}
                for item in trace_items
                if item.get("planning_disposition") == "planned"
            )
            and all(
                isinstance(item.get("skip_code"), str)
                and bool(item.get("skip_code"))
                and isinstance(item.get("skip_message"), str)
                and bool(item.get("skip_message"))
                and item.get("execution_disposition") == "not_submitted"
                for item in skipped_trace
            )
            and (
                (planned_count == 0 and receipt_status_value == "NO_ACTIONS")
                or (
                    planned_count > 0
                    and receipt_status_value in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
                )
            )
        )
        check(
            "EXECUTION-DISPOSITIONS",
            dispositions_ok,
            ["release.execution_receipt", "diagnostics.summary"],
            (
                "every source intent must be exactly one planned terminal receipt or one "
                f"explained skip; source={source_count!r}, planned={planned_count!r}, "
                f"skipped={skipped_count!r}, receipts={receipt_count!r}"
            ),
        )

        protection_ok = len(receipt_items) == len(receipt_values)
        for item in receipt_items:
            side = item.get("side")
            status = item.get("status")
            planned_quantity = as_nonnegative_decimal(item.get("planned_quantity"))
            filled_quantity = as_nonnegative_decimal(item.get("filled_quantity"))
            limit_price = as_nonnegative_decimal(item.get("planned_limit_price"))
            actual_vwap = as_nonnegative_decimal(item.get("actual_vwap"))
            planned_cash = as_nonnegative_decimal(item.get("planned_cash_limit"))
            actual_cash = as_nonnegative_decimal(item.get("collateral_effect"))
            item_ok = bool(
                item.get("order_type") == "FOK"
                and item.get("execution_style") == "protected_market"
                and side in {"buy", "sell"}
                and status in {"FULL", "NO_FILL"}
                and None
                not in {
                    planned_quantity,
                    filled_quantity,
                    limit_price,
                    actual_vwap,
                    planned_cash,
                    actual_cash,
                }
                and filled_quantity <= planned_quantity
                and (
                    status != "NO_FILL"
                    or (
                        filled_quantity == 0
                        and actual_vwap == 0
                        and actual_cash == 0
                        and item.get("code") == "FOK_NOT_FILLED"
                    )
                )
                and (
                    status != "FULL"
                    or (
                        filled_quantity > 0
                        and actual_vwap > 0
                        and (
                            (
                                side == "buy"
                                and actual_vwap <= limit_price
                                and actual_cash <= planned_cash
                            )
                            or (
                                side == "sell"
                                and actual_vwap >= limit_price
                                and actual_cash >= planned_cash
                            )
                        )
                    )
                )
            )
            protection_ok = protection_ok and item_ok
        check(
            "EXECUTION-PROTECTION",
            protection_ok,
            ["release.execution_receipt"],
            "every terminal order must be protected FOK and satisfy quantity, price and cash bounds",
        )

        phase_events = (
            diagnostic_summary.get("phase_events")
            if diagnostic_summary and isinstance(diagnostic_summary.get("phase_events"), list)
            else []
        )
        phase_map = {
            phase: [
                item.get("exit_code")
                for item in phase_events
                if isinstance(item, dict) and item.get("phase") == phase
            ]
            for phase in (
                "install",
                "validate",
                "cancel",
                "preflight",
                "plan",
                "execute",
                "verify",
            )
        }
        required_phases = tuple(phase_map)
        phases_ok = bool(
            diagnostic_summary
            and diagnostic_summary.get("secret_scan_passed") is True
            and diagnostic_summary.get("failed_phase") is None
            and all(phase_map[phase] == [0] for phase in required_phases)
        )
        phase_detail = {
            phase: values[0] if len(values) == 1 else values
            for phase, values in phase_map.items()
        }
        check(
            "EXECUTION-PHASES",
            phases_ok,
            ["diagnostics.summary"],
            f"required phase exit codes={phase_detail!r}",
        )

        order_values = (
            verification.get("orders")
            if verification and isinstance(verification.get("orders"), list)
            else []
        )
        orders = [item for item in order_values if isinstance(item, dict)]
        open_orders_ok = bool(
            verification
            and len(orders) == len(order_values)
            and len(orders) == len(receipt_items)
            and verification.get("open_order_count") == 0
            and all(item.get("open") is False for item in orders)
        )
        check(
            "EXECUTION-OPEN-ORDERS",
            open_orders_ok,
            ["release.execution_verification"],
            f"open_order_count={verification.get('open_order_count') if verification else None!r}",
        )

    failed = [item["id"] for item in checks if item["status"] == "FAIL"]
    return {
        "schema_version": "1.0",
        "parent_run_id": run_id,
        "event": event,
        "production_schedule": event == "schedule",
        "execution_requested": execution_requested,
        "stage_conclusions": stage_conclusions,
        "accepted": not failed,
        "failed_checks": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--diagnostic-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    try:
        run = load_json(args.run, "run")
        if not isinstance(run, dict):
            raise AcceptanceInputError("run must be an object")
        document = build_document(
            run,
            normalize_jobs(load_json(args.jobs, "jobs")),
            as_object(load_json(args.manifest, "manifest", optional=True)),
            as_object(load_json(args.receipt, "receipt", optional=True)),
            as_object(load_json(args.verification, "verification", optional=True)),
            as_object(
                load_json(args.diagnostic_summary, "diagnostic summary", optional=True)
            ),
        )
        write_json(args.output, document)
    except AcceptanceInputError as exc:
        print(f"verify_forecast_edge_production_run: {exc}", file=sys.stderr)
        return 2
    return 1 if args.require_pass and not document["accepted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
