#!/usr/bin/env python3
"""Build a secret-free briefing context when a research report is unavailable."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ACTIONABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}
ALLOWED_ACTIONS = ACTIONABLE_ACTIONS | {"MAINTAIN"}
ALLOWED_NOVELTY = {"FIRST_RESEARCH", "EXISTING_EVENT_NEW_MARKET"}
POLYMARKET_URL = re.compile(r"https://(?:www\.)?polymarket\.com/event/[^\s]+")


class ContextError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def signed_decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def probability_text(value: Any) -> str | None:
    rendered = decimal_text(value)
    if rendered is None:
        return None
    number = Decimal(rendered)
    return rendered if number <= 1 else None


def clean_text(value: Any, *, limit: int = 520) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = " ".join(value.split())
    if not rendered:
        return None
    return rendered[:limit]


def safe_market_url(value: Any) -> str | None:
    if not isinstance(value, str) or not POLYMARKET_URL.fullmatch(value):
        return None
    return value


def position_summaries(position_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(position_rows, list):
        return []
    summaries: list[dict[str, Any]] = []
    for raw in position_rows:
        if not isinstance(raw, dict):
            continue
        outcome = str(raw.get("outcome") or "").upper()
        if outcome not in {"YES", "NO"}:
            continue
        question = clean_text(raw.get("question"), limit=240)
        if question is None:
            continue
        summaries.append(
            {
                "question": question,
                "outcome": outcome,
                "quantity": decimal_text(raw.get("quantity")),
                "displayed_value": decimal_text(raw.get("displayed_value")),
                "url": safe_market_url(raw.get("url")),
            }
        )
    return summaries


def nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def parse_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ContextError("research date is invalid") from exc


def account_snapshots(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None or not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        snapshot = load_object(path)
        if snapshot is None or snapshot.get("schema_version") != "1.0":
            continue
        adapter = snapshot.get("adapter")
        portfolio = snapshot.get("portfolio")
        positions = snapshot.get("positions")
        if not isinstance(adapter, str) or not adapter or not isinstance(portfolio, dict):
            continue
        if not isinstance(positions, dict):
            continue
        count = nonnegative_int(positions.get("count"))
        position_rows = positions.get("positions")
        if count is None and isinstance(position_rows, list):
            count = len(position_rows)
        currency = portfolio.get("currency")
        rows.append(
            {
                "venue": adapter,
                "currency": currency if isinstance(currency, str) and currency else None,
                "available_cash": decimal_text(portfolio.get("available_cash")),
                "current_position_count": count,
                "positions": position_summaries(position_rows),
            }
        )
    return rows


def read_baseline_paths(path: Path | None) -> list[Path]:
    if path is None or not path.is_file():
        return []
    try:
        return [
            Path(line).resolve()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return []


def report_market_summaries(report: dict[str, Any]) -> list[dict[str, Any]]:
    action_by_market: dict[str, dict[str, Any]] = {}
    actions = report.get("actions")
    if isinstance(actions, list):
        for raw_action in actions:
            if not isinstance(raw_action, dict):
                continue
            market_id = raw_action.get("market_id")
            if isinstance(market_id, str) and market_id and market_id not in action_by_market:
                action_by_market[market_id] = raw_action

    summaries: list[dict[str, Any]] = []
    for kind, collection_name in (
        ("CURRENT_POSITION", "current_positions"),
        ("CANDIDATE", "candidates"),
    ):
        collection = report.get(collection_name)
        if not isinstance(collection, list):
            continue
        for analysis in collection:
            if not isinstance(analysis, dict):
                continue
            market = analysis.get("market")
            model = analysis.get("model")
            signal = analysis.get("signal")
            opportunity = analysis.get("opportunity")
            allocation = analysis.get("allocation")
            if not all(
                isinstance(value, dict)
                for value in (market, model, signal, opportunity)
            ):
                continue
            question = clean_text(market.get("question"), limit=240)
            outcome = str(market.get("outcome") or "").upper()
            action = str(signal.get("action") or "").upper()
            if question is None or outcome not in {"YES", "NO"} or action not in ALLOWED_ACTIONS:
                continue
            market_id = market.get("id")
            raw_action = action_by_market.get(market_id) if isinstance(market_id, str) else None
            novelty = analysis.get("research_novelty")
            novelty_classification = (
                novelty.get("classification") if isinstance(novelty, dict) else None
            )
            if novelty_classification not in ALLOWED_NOVELTY:
                novelty_classification = None
            summaries.append(
                {
                    "kind": kind,
                    "question": question,
                    "url": safe_market_url(market.get("url")),
                    "outcome": outcome,
                    "action": action,
                    "research_novelty": novelty_classification,
                    "yes_probability": probability_text(model.get("yes_probability")),
                    "executable_price": signed_decimal_text(
                        opportunity.get("executable_price")
                    ),
                    "raw_edge": signed_decimal_text(opportunity.get("raw_edge")),
                    "net_edge": signed_decimal_text(opportunity.get("net_edge")),
                    "allocated_cash": decimal_text(
                        allocation.get("allocated_cash")
                        if isinstance(allocation, dict)
                        else None
                    ),
                    "recommended_cash": decimal_text(
                        raw_action.get("recommended_cash")
                        if isinstance(raw_action, dict)
                        else None
                    ),
                    "recommended_shares": decimal_text(
                        raw_action.get("recommended_shares")
                        if isinstance(raw_action, dict)
                        else None
                    ),
                    "research_conclusion": clean_text(model.get("conclusion")),
                    "execution_reason": clean_text(opportunity.get("reason")),
                }
            )
    return summaries


def report_summary(path: Path) -> dict[str, Any] | None:
    report = load_object(path)
    if report is None:
        return None
    run = report.get("run")
    account = run.get("account") if isinstance(run, dict) else None
    completed_at = run.get("completed_at") if isinstance(run, dict) else None
    adapter = account.get("adapter") if isinstance(account, dict) else None
    current = report.get("current_positions")
    candidates = report.get("candidates")
    actions = report.get("actions")
    portfolio = report.get("portfolio")
    if (
        not isinstance(adapter, str)
        or not isinstance(completed_at, str)
        or not isinstance(current, list)
        or not isinstance(candidates, list)
        or not isinstance(actions, list)
    ):
        return None
    try:
        normalized_time = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    portfolio = portfolio if isinstance(portfolio, dict) else {}
    return {
        "venue": adapter,
        "completed_at": completed_at,
        "completed_timestamp": normalized_time.timestamp(),
        "current_position_count": len(current),
        "candidate_count": len(candidates),
        "researched_market_count": len(current) + len(candidates),
        "actionable_recommendation_count": sum(
            1
            for action in actions
            if isinstance(action, dict) and action.get("action") in ACTIONABLE_ACTIONS
        ),
        "currency": (
            portfolio.get("currency")
            if isinstance(portfolio.get("currency"), str)
            else None
        ),
        "available_collateral": decimal_text(portfolio.get("available_collateral")),
        "estimated_bankroll": decimal_text(portfolio.get("estimated_bankroll")),
        "market_summaries": report_market_summaries(report),
    }


def latest_baselines(paths: list[Path]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for path in paths:
        summary = report_summary(path)
        if summary is None:
            continue
        venue = summary["venue"]
        if venue not in latest or summary["completed_timestamp"] > latest[venue]["completed_timestamp"]:
            latest[venue] = summary
    rows: list[dict[str, Any]] = []
    for venue in sorted(latest):
        row = dict(latest[venue])
        row.pop("completed_timestamp", None)
        rows.append(row)
    return rows


def current_report_summaries(
    manifest_path: Path | None,
) -> list[dict[str, Any]]:
    if manifest_path is None:
        return []
    manifest = load_object(manifest_path)
    entries = manifest.get("reports") if manifest is not None else None
    if not isinstance(entries, list):
        return []
    rows: list[dict[str, Any]] = []
    for entry in entries:
        raw_path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(raw_path, str):
            return []
        summary = report_summary(Path(raw_path))
        if summary is None:
            return []
        summary.pop("completed_timestamp", None)
        rows.append(summary)
    return rows


def validated_intent_count(path: Path | None) -> int | None:
    summary = load_object(path) if path is not None else None
    return nonnegative_int(summary.get("items")) if summary is not None else None


def glob_count(directory: Path | None, pattern: str) -> int:
    if directory is None or not directory.is_dir():
        return 0
    return len(list(directory.glob(pattern)))


def build_context(args: argparse.Namespace) -> dict[str, Any]:
    research_date = parse_date(args.research_date)
    configured_venues = sorted(
        {item.strip() for item in args.enabled_venues.split(",") if item.strip()}
    )
    snapshots = account_snapshots(args.account_snapshots_dir)
    baseline_paths = read_baseline_paths(args.baseline_reports_list)
    baselines = latest_baselines(baseline_paths)
    current_reports = current_report_summaries(args.current_reports_manifest)
    intent_count = validated_intent_count(args.validation_summary)
    agent_attempts = glob_count(args.runtime_dir, "kimi-attempt-*.stream.jsonl")
    validation_attempts = glob_count(args.runtime_dir, "validation-attempt-*.stdout")
    generated_reports = 0
    if args.runtime_dir is not None:
        reports_root = args.runtime_dir / "reports" / "reports"
        baseline_set = set(baseline_paths)
        generated_reports = sum(
            1
            for path in reports_root.glob("*/report.json")
            if path.resolve() not in baseline_set
        )

    if current_reports:
        last_stage = "REPORT_VALIDATED"
    elif generated_reports:
        last_stage = "REPORT_GENERATED_UNVALIDATED"
    elif validation_attempts:
        last_stage = "REPORT_VALIDATION_ATTEMPTED"
    elif agent_attempts:
        last_stage = "RESEARCH_AGENT_RAN"
    elif snapshots:
        last_stage = "ACCOUNT_SNAPSHOT_CAPTURED"
    elif baseline_paths:
        last_stage = "HISTORY_RESTORED"
    elif args.runtime_dir is not None and args.runtime_dir.is_dir():
        last_stage = "WORKFLOW_CONFIGURED"
    else:
        last_stage = "SETUP_NOT_REACHED"

    venues = configured_venues or sorted(
        {row["venue"] for row in snapshots + baselines + current_reports}
    )
    return {
        "schema_version": "1.0",
        "research_date": research_date,
        "venues": venues,
        "account_snapshots": snapshots,
        "latest_validated_baselines": baselines,
        "current_validated_reports": current_reports,
        "progress": {
            "last_reached_stage": last_stage,
            "restored_historical_report_count": len(baseline_paths),
            "account_snapshot_count": len(snapshots),
            "research_agent_attempt_count": agent_attempts,
            "report_validation_attempt_count": validation_attempts,
            "generated_report_count": generated_reports,
            "validated_report_count": len(current_reports),
            "validated_trade_intent_count": intent_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-date", required=True)
    parser.add_argument("--enabled-venues", default="")
    parser.add_argument("--account-snapshots-dir", type=Path)
    parser.add_argument("--baseline-reports-list", type=Path)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--current-reports-manifest", type=Path)
    parser.add_argument("--validation-summary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        context = build_context(args)
    except ContextError as exc:
        print(f"forecast-edge fallback context: {exc}")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
