#!/usr/bin/env python3
"""Validate, render, archive, and locate immutable forecast-edge reports."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import probability_tree


LEGACY_SIGNALS = {"BUY", "ADD", "HOLD", "SELL"}
CURRENT_SIGNALS = {"BUY", "ADD", "MAINTAIN", "TRIM", "SELL"}
SIGNAL_LABELS = {
    "BUY": "买入",
    "ADD": "加仓",
    "HOLD": "持有",
    "MAINTAIN": "维持现状",
    "TRIM": "减仓",
    "SELL": "卖出",
}
MODERN_SCHEMA_VERSIONS = {"2.0", "2.1", "2.2", "3.0"}
SOFT_EDGE_SCHEMA_VERSIONS = {"2.1", "2.2"}
OUTCOME_LABELS = {"YES": "是", "NO": "否"}
REVIEW_STATUSES = {"PASS", "FAIL"}
REVIEW_LABELS = {"PASS": "通过", "FAIL": "未通过"}
CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
EVIDENCE_FIELDS = {
    "source_name",
    "source_type",
    "title",
    "url",
    "published_at",
    "retrieved_at",
    "summary",
    "conclusion",
    "factor_id",
    "credibility",
}
V3_EVIDENCE_FIELDS = EVIDENCE_FIELDS | {"id", "roles"}


class ReportError(ValueError):
    """Raised when a report violates the forecast-edge report contract."""


def signals_for_schema_version(schema_version: str) -> set[str]:
    return CURRENT_SIGNALS if schema_version in {"2.2", "3.0"} else LEGACY_SIGNALS


def require_chinese_text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not CHINESE_RE.search(value):
        raise ReportError(f"{path} must contain Simplified-Chinese narrative text")


def finite_number(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise ReportError(f"{path} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReportError(f"{path} must be numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise ReportError(f"{path} must be finite and at least {minimum}")
    return result


def approximately_equal(left: float, right: float, tolerance: float = 1e-7) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def decimal_value(value: Any, path: str, *, minimum: Decimal = Decimal(0)) -> Decimal:
    if isinstance(value, bool):
        raise ReportError(f"{path} must be numeric")
    try:
        result = probability_tree.quantize(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ReportError(f"{path} must be numeric") from exc
    if result < minimum:
        raise ReportError(f"{path} must be at least {minimum}")
    return result


def require_decimal_equal(left: Any, right: Any, path: str) -> None:
    if decimal_value(left, path, minimum=Decimal("-999999999999")) != decimal_value(
        right, path, minimum=Decimal("-999999999999")
    ):
        raise ReportError(f"{path} values do not match at 12-decimal precision")


def require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(f"{path} must be an object")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReportError(f"{path} must be a list")
    return value


def require_fields(value: dict[str, Any], fields: set[str], path: str) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        raise ReportError(f"{path} is missing fields: {', '.join(missing)}")


def require_safe_market_url(value: Any, path: str) -> None:
    if not isinstance(value, str) or value != value.strip() or any(ch.isspace() for ch in value):
        raise ReportError(f"{path} must be a trimmed HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ReportError(f"{path} must be an absolute credential-free HTTPS URL")


def validate_evidence(evidence: Any, path: str, *, current_position: bool) -> None:
    rows = require_list(evidence, path)
    if not rows:
        raise ReportError(f"{path} must contain at least one evidence item")
    for index, raw in enumerate(rows):
        item_path = f"{path}[{index}]"
        item = require_object(raw, item_path)
        require_fields(item, EVIDENCE_FIELDS, item_path)
        if current_position:
            require_fields(item, {"new_since_previous"}, item_path)
            if not isinstance(item["new_since_previous"], bool):
                raise ReportError(f"{item_path}.new_since_previous must be boolean")
            if item["new_since_previous"]:
                impact = require_object(item.get("impact"), f"{item_path}.impact")
                require_fields(
                    impact,
                    {"affected_factor", "factor_probability_change", "final_probability_change"},
                    f"{item_path}.impact",
                )


def validate_legacy_resolution_review(
    raw: Any, path: str, *, current_position: bool, action: str
) -> None:
    review = require_object(raw, path)
    require_fields(review, {"clarity", "verification", "note"}, path)
    for field in ("clarity", "verification"):
        if review[field] not in REVIEW_STATUSES:
            raise ReportError(f"{path}.{field} must be PASS or FAIL")
    require_chinese_text(review["note"], f"{path}.note")
    if not current_position and review["clarity"] != "PASS":
        raise ReportError(f"{path}.clarity must be PASS for candidates")
    if action in {"BUY", "ADD"} and (
        review["clarity"] != "PASS" or review["verification"] != "PASS"
    ):
        raise ReportError(f"{path} must pass clarity and verification before BUY or ADD")


def collect_tree_evidence_ids(node: dict[str, Any]) -> set[str]:
    references = set(str(value) for value in node.get("evidence_ids", []))
    for branch in node.get("children", []):
        references.update(str(value) for value in branch.get("evidence_ids", []))
        child = branch.get("node")
        if isinstance(child, dict):
            references.update(collect_tree_evidence_ids(child))
    return references


def validate_v3_evidence(
    evidence: Any,
    path: str,
    *,
    current_position: bool,
) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
    rows = require_list(evidence, path)
    if not rows:
        raise ReportError(f"{path} must contain at least one evidence item")
    by_id: dict[str, dict[str, Any]] = {}
    probability_ids: set[str] = set()
    resolution_ids: set[str] = set()
    for index, raw in enumerate(rows):
        item_path = f"{path}[{index}]"
        item = require_object(raw, item_path)
        require_fields(item, V3_EVIDENCE_FIELDS, item_path)
        identifier = str(item["id"]).strip()
        if not identifier:
            raise ReportError(f"{item_path}.id must be non-empty")
        if identifier in by_id:
            raise ReportError(f"{item_path}.id duplicates {identifier}")
        by_id[identifier] = item
        roles = require_list(item["roles"], f"{item_path}.roles")
        if not roles or any(role not in {"PROBABILITY", "RESOLUTION"} for role in roles):
            raise ReportError(f"{item_path}.roles contains an unsupported role")
        if len(set(roles)) != len(roles):
            raise ReportError(f"{item_path}.roles must not contain duplicates")
        if "PROBABILITY" in roles:
            probability_ids.add(identifier)
        if "RESOLUTION" in roles:
            resolution_ids.add(identifier)
        if not isinstance(item["url"], str) or not item["url"].startswith("https://"):
            raise ReportError(f"{item_path}.url must be an HTTPS URL")
        for field in ("summary", "conclusion", "credibility"):
            require_chinese_text(item[field], f"{item_path}.{field}")
        if current_position:
            require_fields(item, {"new_since_previous"}, item_path)
            if not isinstance(item["new_since_previous"], bool):
                raise ReportError(f"{item_path}.new_since_previous must be boolean")
            if item["new_since_previous"]:
                impact = require_object(item.get("impact"), f"{item_path}.impact")
                require_fields(
                    impact,
                    {"affected_factor", "factor_probability_change", "final_probability_change"},
                    f"{item_path}.impact",
                )
    return by_id, probability_ids, resolution_ids


def validate_v3_resolution_review(
    value: Any,
    path: str,
    evidence_ids: set[str],
    required_resolution_ids: set[str],
) -> set[str]:
    review = require_object(value, path)
    require_fields(
        review,
        {
            "status",
            "criteria_text",
            "yes_condition",
            "no_condition",
            "deadline",
            "timezone",
            "resolution_source",
            "edge_cases",
            "reason",
            "evidence_ids",
        },
        path,
    )
    if review["status"] != "PASS":
        raise ReportError(f"{path}.status must be PASS")
    for field in ("yes_condition", "no_condition", "reason"):
        require_chinese_text(review[field], f"{path}.{field}")
    if not isinstance(review["criteria_text"], str) or not review["criteria_text"].strip():
        raise ReportError(f"{path}.criteria_text must be non-empty")
    if not isinstance(review["resolution_source"], str) or not review["resolution_source"].strip():
        raise ReportError(f"{path}.resolution_source must be non-empty")
    edge_cases = require_list(review["edge_cases"], f"{path}.edge_cases")
    for index, edge_case in enumerate(edge_cases):
        require_chinese_text(edge_case, f"{path}.edge_cases[{index}]")
    references = set(str(item) for item in require_list(review["evidence_ids"], f"{path}.evidence_ids"))
    if not references:
        raise ReportError(f"{path}.evidence_ids must not be empty")
    unknown = references - evidence_ids
    if unknown:
        raise ReportError(f"{path}.evidence_ids refers to unknown evidence: {', '.join(sorted(unknown))}")
    missing = required_resolution_ids - references
    if missing:
        raise ReportError(f"resolution evidence is not referenced: {', '.join(sorted(missing))}")
    return references


def validate_v3_analysis(raw: Any, path: str, *, current_position: bool) -> None:
    analysis = require_object(raw, path)
    require_fields(
        analysis,
        {
            "market",
            "resolution_review",
            "model",
            "evidence",
            "position",
            "signal",
            "opportunity",
            "allocation",
        },
        path,
    )
    market = require_object(analysis["market"], f"{path}.market")
    require_fields(
        market,
        {"id", "question", "url", "outcome", "resolution_criteria", "end_date"},
        f"{path}.market",
    )
    if market["outcome"] not in {"YES", "NO"}:
        raise ReportError(f"{path}.market.outcome must be YES or NO")
    require_safe_market_url(market["url"], f"{path}.market.url")

    evidence_by_id, probability_ids, resolution_ids = validate_v3_evidence(
        analysis["evidence"], f"{path}.evidence", current_position=current_position
    )
    all_evidence_ids = set(evidence_by_id)
    validate_v3_resolution_review(
        analysis["resolution_review"],
        f"{path}.resolution_review",
        all_evidence_ids,
        resolution_ids,
    )
    if analysis["resolution_review"]["criteria_text"] != market["resolution_criteria"]:
        raise ReportError(
            f"{path}.resolution_review.criteria_text differs from the market resolution criteria"
        )

    model = require_object(analysis["model"], f"{path}.model")
    require_fields(
        model,
        {"yes_probability", "model_value", "conclusion", "tree", "calculation"},
        f"{path}.model",
    )
    require_chinese_text(model["conclusion"], f"{path}.model.conclusion")
    tree = require_object(model["tree"], f"{path}.model.tree")
    try:
        evaluation = probability_tree.evaluate_tree(tree)
    except probability_tree.ProbabilityTreeError as exc:
        raise ReportError(f"{path}.model.tree: {exc}") from exc
    tree_references = collect_tree_evidence_ids(tree["root"])
    unknown_tree_references = tree_references - all_evidence_ids
    if unknown_tree_references:
        raise ReportError(
            f"{path}.model.tree refers to unknown evidence: "
            + ", ".join(sorted(unknown_tree_references))
        )
    missing_probability = probability_ids - tree_references
    if missing_probability:
        raise ReportError(
            f"probability evidence is not referenced by the tree: {', '.join(sorted(missing_probability))}"
        )
    require_decimal_equal(
        model["yes_probability"], evaluation.probability, f"{path}.model.yes_probability"
    )
    expected_model_value = (
        evaluation.probability if market["outcome"] == "YES" else probability_tree.ONE - evaluation.probability
    )
    require_decimal_equal(model["model_value"], expected_model_value, f"{path}.model.model_value")
    calculation = require_object(model["calculation"], f"{path}.model.calculation")
    require_fields(
        calculation,
        {"engine_version", "tree_sha256", "root_probability", "node_count", "deepest_level"},
        f"{path}.model.calculation",
    )
    if calculation["engine_version"] != "3.0":
        raise ReportError(f"{path}.model.calculation.engine_version must be 3.0")
    if calculation["tree_sha256"] != evaluation.tree_sha256:
        raise ReportError(f"{path}.model.calculation.tree_sha256 does not match the embedded tree")
    require_decimal_equal(
        calculation["root_probability"], evaluation.probability, f"{path}.model.calculation"
    )
    if calculation["node_count"] != evaluation.node_count:
        raise ReportError(f"{path}.model.calculation.node_count does not match the embedded tree")
    if calculation["deepest_level"] != evaluation.deepest_level:
        raise ReportError(f"{path}.model.calculation.deepest_level does not match the embedded tree")

    position = require_object(analysis["position"], f"{path}.position")
    require_fields(position, {"held", "shares", "cost_cash"}, f"{path}.position")
    shares = decimal_value(position["shares"], f"{path}.position.shares")
    cost_cash = decimal_value(position["cost_cash"], f"{path}.position.cost_cash")
    if current_position and (position["held"] is not True or shares <= 0):
        raise ReportError(f"{path}.position must describe a positive current holding")
    if not current_position and (position["held"] is not False or shares != 0 or cost_cash != 0):
        raise ReportError(f"{path}.position must be an empty candidate position")

    signal = require_object(analysis["signal"], f"{path}.signal")
    require_fields(signal, {"action", "rationale"}, f"{path}.signal")
    if signal["action"] not in CURRENT_SIGNALS:
        raise ReportError(f"{path}.signal.action must be one of {sorted(CURRENT_SIGNALS)}")
    require_chinese_text(signal["rationale"], f"{path}.signal.rationale")

    opportunity = require_object(analysis["opportunity"], f"{path}.opportunity")
    require_fields(
        opportunity,
        {
            "entry_eligible",
            "exit_eligible",
            "model_value",
            "executable_price",
            "net_edge",
            "raw_edge",
            "minimum_raw_edge",
            "fee_rate",
            "book_timestamp",
            "reason",
        },
        f"{path}.opportunity",
    )
    require_decimal_equal(
        opportunity["model_value"], model["model_value"], f"{path}.opportunity.model_value"
    )
    require_decimal_equal(opportunity["minimum_raw_edge"], 0.1, f"{path}.opportunity.minimum_raw_edge")
    require_chinese_text(opportunity["reason"], f"{path}.opportunity.reason")

    allocation = require_object(analysis["allocation"], f"{path}.allocation")
    require_fields(
        allocation,
        {
            "priority",
            "standalone_kelly_cash",
            "orderbook_capacity_cash",
            "portfolio_cash_cap",
            "minimum_order_cash",
            "cash_before",
            "allocated_cash",
            "cash_after",
            "reason",
        },
        f"{path}.allocation",
    )
    before = decimal_value(allocation["cash_before"], f"{path}.allocation.cash_before")
    allocated = decimal_value(allocation["allocated_cash"], f"{path}.allocation.allocated_cash")
    after = decimal_value(allocation["cash_after"], f"{path}.allocation.cash_after")
    if probability_tree.quantize(before - allocated) != after:
        raise ReportError(f"{path}.allocation cash_before - allocated_cash must equal cash_after")
    require_chinese_text(allocation["reason"], f"{path}.allocation.reason")
    if current_position and "comparison" not in analysis:
        raise ReportError(f"{path}.comparison is required for current positions")


def validate_v3_execution(
    action: dict[str, Any],
    analysis: dict[str, Any],
    path: str,
) -> None:
    action_name = action["action"]
    recommended_cash = decimal_value(action["recommended_cash"], f"{path}.recommended_cash")
    recommended_shares = decimal_value(action["recommended_shares"], f"{path}.recommended_shares")
    execution = action.get("execution")
    if action_name == "MAINTAIN":
        if execution is not None or recommended_cash != 0 or recommended_shares != 0:
            raise ReportError(f"{path} MAINTAIN must have zero deltas and null execution")
        return
    if not isinstance(execution, dict):
        raise ReportError(f"{path}.execution is required for {action_name}")
    require_fields(
        execution,
        {
            "shares",
            "cash_amount",
            "cash_direction",
            "best_price",
            "vwap",
            "effective_unit_price",
            "gross_cash",
            "fee_cash",
            "slippage",
            "net_edge",
            "book_timestamp",
            "fills",
            "source_snapshot_sha256",
        },
        f"{path}.execution",
    )
    expected_direction = "PAY" if action_name in {"BUY", "ADD"} else "RECEIVE"
    if execution["cash_direction"] != expected_direction:
        raise ReportError(f"{path}.execution.cash_direction must be {expected_direction}")
    if not re.fullmatch(r"[a-f0-9]{64}", str(execution["source_snapshot_sha256"])):
        raise ReportError(f"{path}.execution.source_snapshot_sha256 must be a lowercase SHA-256")
    fills = require_list(execution["fills"], f"{path}.execution.fills")
    if not fills:
        raise ReportError(f"{path}.execution.fills must not be empty")
    fill_shares = Decimal(0)
    gross_cash = Decimal(0)
    fee_cash = Decimal(0)
    for index, raw_fill in enumerate(fills):
        fill_path = f"{path}.execution.fills[{index}]"
        fill = require_object(raw_fill, fill_path)
        require_fields(fill, {"price", "shares", "fee_cash"}, fill_path)
        price = decimal_value(fill["price"], f"{fill_path}.price")
        shares = decimal_value(fill["shares"], f"{fill_path}.shares")
        fee = decimal_value(fill["fee_cash"], f"{fill_path}.fee_cash")
        if shares <= 0 or price > probability_tree.ONE:
            raise ReportError(f"{fill_path} contains an invalid price or share count")
        fill_shares += shares
        gross_cash += price * shares
        fee_cash += fee
    fill_shares = probability_tree.quantize(fill_shares)
    gross_cash = probability_tree.quantize(gross_cash)
    fee_cash = probability_tree.quantize(fee_cash)
    if fill_shares <= 0:
        raise ReportError(f"{path}.execution has no filled shares")
    vwap = probability_tree.quantize(gross_cash / fill_shares)
    cash_amount = probability_tree.quantize(
        gross_cash + fee_cash if expected_direction == "PAY" else gross_cash - fee_cash
    )
    effective = probability_tree.quantize(cash_amount / fill_shares)
    best_price = decimal_value(execution["best_price"], f"{path}.execution.best_price")
    slippage = probability_tree.quantize(
        vwap - best_price if expected_direction == "PAY" else best_price - vwap
    )
    if slippage < 0:
        raise ReportError(f"{path}.execution.slippage cannot be negative")
    model_value = decimal_value(analysis["model"]["model_value"], f"{path}.model_value")
    net_edge = probability_tree.quantize(
        model_value - effective if expected_direction == "PAY" else effective - model_value
    )
    if net_edge < 0 and action_name != "TRIM":
        raise ReportError(f"{path}.execution.net_edge must be positive for an action")
    comparisons = {
        "shares": fill_shares,
        "gross_cash": gross_cash,
        "fee_cash": fee_cash,
        "cash_amount": cash_amount,
        "vwap": vwap,
        "effective_unit_price": effective,
        "slippage": slippage,
        "net_edge": net_edge,
    }
    for field, expected in comparisons.items():
        require_decimal_equal(execution[field], expected, f"{path}.execution.{field}")
    require_decimal_equal(recommended_cash, cash_amount, f"{path}.recommended_cash")
    require_decimal_equal(recommended_shares, fill_shares, f"{path}.recommended_shares")
    require_decimal_equal(
        analysis["opportunity"]["executable_price"],
        effective,
        f"{path}.opportunity.executable_price",
    )
    require_decimal_equal(
        analysis["opportunity"]["net_edge"], net_edge, f"{path}.opportunity.net_edge"
    )
    if execution["book_timestamp"] != analysis["opportunity"]["book_timestamp"]:
        raise ReportError(f"{path}.execution.book_timestamp differs from the opportunity")


def validate_analysis(raw: Any, path: str, *, current_position: bool, schema_version: str) -> None:
    if schema_version == "3.0":
        validate_v3_analysis(raw, path, current_position=current_position)
        return
    analysis = require_object(raw, path)
    require_fields(analysis, {"market", "model", "evidence", "signal"}, path)
    market = require_object(analysis["market"], f"{path}.market")
    require_fields(market, {"id", "question", "url", "outcome", "resolution_criteria"}, f"{path}.market")
    model = require_object(analysis["model"], f"{path}.model")
    require_fields(model, {"probability", "model_value", "conclusion"}, f"{path}.model")
    signal = require_object(analysis["signal"], f"{path}.signal")
    require_fields(signal, {"action", "rationale"}, f"{path}.signal")
    allowed_signals = signals_for_schema_version(schema_version)
    if signal["action"] not in allowed_signals:
        raise ReportError(f"{path}.signal.action must be one of {sorted(allowed_signals)}")
    if schema_version == "2.1":
        require_fields(analysis, {"resolution_review"}, path)
        validate_legacy_resolution_review(
            analysis["resolution_review"],
            f"{path}.resolution_review",
            current_position=current_position,
            action=signal["action"],
        )
    validate_evidence(analysis["evidence"], f"{path}.evidence", current_position=current_position)
    if current_position and "comparison" not in analysis:
        raise ReportError(f"{path}.comparison is required for current positions; use null on the first run")
    if schema_version in MODERN_SCHEMA_VERSIONS:
        require_chinese_text(model["conclusion"], f"{path}.model.conclusion")
        require_chinese_text(signal["rationale"], f"{path}.signal.rationale")
        for index, evidence in enumerate(analysis["evidence"]):
            for field in ("summary", "conclusion", "credibility"):
                require_chinese_text(evidence[field], f"{path}.evidence[{index}].{field}")
        require_fields(analysis, {"opportunity", "allocation"}, path)
        opportunity = require_object(analysis["opportunity"], f"{path}.opportunity")
        require_fields(
            opportunity,
            {"entry_eligible", "exit_eligible", "model_value", "executable_price", "net_edge", "reason"},
            f"{path}.opportunity",
        )
        if not isinstance(opportunity["entry_eligible"], bool) or not isinstance(
            opportunity["exit_eligible"], bool
        ):
            raise ReportError(f"{path}.opportunity eligibility fields must be boolean")
        finite_number(opportunity["model_value"], f"{path}.opportunity.model_value")
        finite_number(opportunity["executable_price"], f"{path}.opportunity.executable_price")
        finite_number(opportunity["net_edge"], f"{path}.opportunity.net_edge")
        if schema_version in SOFT_EDGE_SCHEMA_VERSIONS:
            require_fields(
                opportunity,
                {
                    "raw_edge",
                    "soft_minimum_raw_edge",
                    "soft_edge_pass",
                    "soft_edge_override_reason",
                },
                f"{path}.opportunity",
            )
            raw_edge = finite_number(
                opportunity["raw_edge"], f"{path}.opportunity.raw_edge", minimum=-1.0
            )
            soft_minimum = finite_number(
                opportunity["soft_minimum_raw_edge"],
                f"{path}.opportunity.soft_minimum_raw_edge",
            )
            if not approximately_equal(soft_minimum, 0.10):
                raise ReportError(f"{path}.opportunity.soft_minimum_raw_edge must equal 0.10")
            soft_edge_pass = opportunity["soft_edge_pass"]
            if not isinstance(soft_edge_pass, bool):
                raise ReportError(f"{path}.opportunity.soft_edge_pass must be boolean")
            expected_soft_pass = raw_edge > soft_minimum + 1e-12
            if soft_edge_pass != expected_soft_pass:
                raise ReportError(
                    f"{path}.opportunity.soft_edge_pass must match raw_edge > soft_minimum_raw_edge"
                )
            override_reason = opportunity["soft_edge_override_reason"]
            if override_reason is not None:
                require_chinese_text(
                    override_reason, f"{path}.opportunity.soft_edge_override_reason"
                )
            if opportunity["entry_eligible"] and not soft_edge_pass and override_reason is None:
                raise ReportError(
                    f"{path}.opportunity requires a soft-edge override reason below or at 10pp"
                )
        allocation = require_object(analysis["allocation"], f"{path}.allocation")
        require_fields(
            allocation,
            {
                "priority",
                "standalone_kelly_cash",
                "orderbook_capacity_cash",
                "portfolio_cash_cap",
                "minimum_order_cash",
                "cash_before",
                "allocated_cash",
                "cash_after",
                "reason",
            },
            f"{path}.allocation",
        )
        priority = allocation["priority"]
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int) or priority < 0
        ):
            raise ReportError(f"{path}.allocation.priority must be a non-negative integer or null")
        for field in (
            "standalone_kelly_cash",
            "orderbook_capacity_cash",
            "minimum_order_cash",
            "cash_before",
            "allocated_cash",
            "cash_after",
        ):
            finite_number(allocation[field], f"{path}.allocation.{field}")
        if allocation["portfolio_cash_cap"] is not None:
            finite_number(allocation["portfolio_cash_cap"], f"{path}.allocation.portfolio_cash_cap")
        before = float(allocation["cash_before"])
        allocated = float(allocation["allocated_cash"])
        after = float(allocation["cash_after"])
        if not approximately_equal(before - allocated, after):
            raise ReportError(f"{path}.allocation cash_before - allocated_cash must equal cash_after")


def validate_report(report: Any) -> dict[str, Any]:
    root = require_object(report, "report")
    require_fields(
        root,
        {
            "schema_version",
            "run",
            "portfolio",
            "current_positions",
            "candidates",
            "actions",
            "summary",
        },
        "report",
    )
    schema_version = str(root["schema_version"])
    if schema_version not in {"1.0", "2.0", "2.1", "2.2", "3.0"}:
        raise ReportError("report.schema_version is unsupported")
    run = require_object(root["run"], "report.run")
    require_fields(
        run,
        {"id", "started_at", "completed_at", "data_cutoff", "account", "previous_report_ids"},
        "report.run",
    )
    account = require_object(run["account"], "report.run.account")
    require_fields(account, {"input"}, "report.run.account")
    adapter = account.get("adapter")
    if adapter is None:
        if not str(account.get("address") or "").strip():
            raise ReportError("legacy report.run.account must contain address")
    else:
        if not isinstance(adapter, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", adapter) is None:
            raise ReportError("report.run.account.adapter is invalid")
        if not str(account.get("identifier") or "").strip():
            raise ReportError("adapter report.run.account must contain identifier")
    require_list(run["previous_report_ids"], "report.run.previous_report_ids")
    if len(run["previous_report_ids"]) > 2:
        raise ReportError("report.run.previous_report_ids cannot contain more than two reports")
    portfolio = require_object(root["portfolio"], "report.portfolio")
    portfolio_fields = {"available_collateral", "estimated_bankroll", "reserve_cash"}
    if schema_version in MODERN_SCHEMA_VERSIONS:
        portfolio_fields.update(
            {
                "holdings_liquidation_value",
                "deployable_cash",
                "allocated_cash",
                "remaining_cash",
                "cold_start",
                "new_position_limit",
                "diversification_summary",
            }
        )
    require_fields(portfolio, portfolio_fields, "report.portfolio")
    if schema_version == "3.0":
        require_fields(root, {"discovery_audit"}, "report")
        discovery = require_object(root["discovery_audit"], "report.discovery_audit")
        require_fields(
            discovery,
            {
                "pagination_completed",
                "fetched_events",
                "excluded_counts",
                "semantically_qualified_events",
                "selected_events",
                "tier_attempts",
                "operation_needs_satisfied",
                "summary",
            },
            "report.discovery_audit",
        )
        require_chinese_text(discovery["summary"], "report.discovery_audit.summary")
    current = require_list(root["current_positions"], "report.current_positions")
    candidates = require_list(root["candidates"], "report.candidates")
    for index, item in enumerate(current):
        validate_analysis(
            item,
            f"report.current_positions[{index}]",
            current_position=True,
            schema_version=schema_version,
        )
    for index, item in enumerate(candidates):
        validate_analysis(
            item,
            f"report.candidates[{index}]",
            current_position=False,
            schema_version=schema_version,
        )
    if adapter is not None:
        identifier_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
        for index, analysis in enumerate(current + candidates):
            market = require_object(analysis.get("market"), f"report.analysis[{index}].market")
            require_fields(
                market,
                {"event_identifier", "multi_market_identifier", "instrument_identifier"},
                f"report.analysis[{index}].market",
            )
            for field in ("event_identifier", "multi_market_identifier", "instrument_identifier"):
                value = market[field]
                if not isinstance(value, str) or identifier_pattern.fullmatch(value) is None:
                    raise ReportError(f"report.analysis[{index}].market.{field} is invalid")
    if schema_version == "2.1":
        notes = [item["resolution_review"]["note"].strip() for item in current + candidates]
        if len(notes) != len(set(notes)):
            raise ReportError("resolution review notes must be market-specific, not repeated boilerplate")
    if schema_version == "3.0":
        reasons = [item["resolution_review"]["reason"].strip() for item in current + candidates]
        if len(reasons) != len(set(reasons)):
            raise ReportError("resolution review reasons must be market-specific, not repeated boilerplate")
    actions = require_list(root["actions"], "report.actions")
    for index, raw in enumerate(actions):
        action = require_object(raw, f"report.actions[{index}]")
        action_fields = {
            "action",
            "market_id",
            "market_question",
            "outcome",
            "recommended_cash",
            "rationale",
        }
        if schema_version == "3.0":
            action_fields.update({"recommended_shares", "public_reason", "execution"})
        require_fields(action, action_fields, f"report.actions[{index}]")
        allowed_signals = signals_for_schema_version(schema_version)
        if action["action"] not in allowed_signals:
            raise ReportError(
                f"report.actions[{index}].action must be one of {sorted(allowed_signals)}"
            )
    if not isinstance(root["summary"], str) or not root["summary"].strip():
        raise ReportError("report.summary must be a non-empty string")
    if schema_version in MODERN_SCHEMA_VERSIONS:
        require_chinese_text(root["summary"], "report.summary")
        for index, action in enumerate(actions):
            require_chinese_text(action["rationale"], f"report.actions[{index}].rationale")
            if schema_version == "3.0":
                require_chinese_text(action["public_reason"], f"report.actions[{index}].public_reason")
        validate_portfolio_consistency(root)
    return root


def validate_portfolio_consistency(root: dict[str, Any]) -> None:
    schema_version = str(root["schema_version"])
    portfolio = root["portfolio"]
    available = finite_number(portfolio["available_collateral"], "report.portfolio.available_collateral")
    holdings = finite_number(
        portfolio["holdings_liquidation_value"], "report.portfolio.holdings_liquidation_value"
    )
    bankroll = finite_number(portfolio["estimated_bankroll"], "report.portfolio.estimated_bankroll")
    reserve = finite_number(portfolio["reserve_cash"], "report.portfolio.reserve_cash")
    deployable = finite_number(portfolio["deployable_cash"], "report.portfolio.deployable_cash")
    allocated = finite_number(portfolio["allocated_cash"], "report.portfolio.allocated_cash")
    remaining = finite_number(portfolio["remaining_cash"], "report.portfolio.remaining_cash")
    if not approximately_equal(bankroll, available + holdings):
        raise ReportError("portfolio bankroll must equal available collateral plus holdings liquidation value")
    if not approximately_equal(available, reserve + deployable):
        raise ReportError("available collateral must equal reserve cash plus deployable cash")
    if not approximately_equal(deployable, allocated + remaining):
        raise ReportError("deployable cash must equal allocated cash plus remaining cash")
    if not isinstance(portfolio["cold_start"], bool):
        raise ReportError("report.portfolio.cold_start must be boolean")
    new_position_limit = portfolio["new_position_limit"]
    if isinstance(new_position_limit, bool) or not isinstance(new_position_limit, int):
        raise ReportError("report.portfolio.new_position_limit must be an integer")
    if new_position_limit < 0:
        raise ReportError("report.portfolio.new_position_limit cannot be negative")
    if not isinstance(portfolio["diversification_summary"], str) or not portfolio[
        "diversification_summary"
    ].strip():
        raise ReportError("report.portfolio.diversification_summary must be non-empty")
    require_chinese_text(
        portfolio["diversification_summary"], "report.portfolio.diversification_summary"
    )

    current_market_ids = {str(analysis["market"]["id"]) for analysis in root["current_positions"]}
    candidate_market_ids = {str(analysis["market"]["id"]) for analysis in root["candidates"]}
    analyses = root["current_positions"] + root["candidates"]
    analysis_by_market: dict[str, dict[str, Any]] = {}
    prioritized_allocations: list[tuple[int, str, dict[str, Any]]] = []
    allocation_priorities: set[int] = set()
    for index, analysis in enumerate(analyses):
        market_id = str(analysis["market"]["id"])
        if market_id in analysis_by_market:
            raise ReportError(f"duplicate analyzed market id: {market_id}")
        analysis_by_market[market_id] = analysis
        sizing = analysis.get("sizing")
        if isinstance(sizing, dict) and isinstance(sizing.get("cash"), dict):
            sizing_bankroll = finite_number(
                sizing["cash"].get("bankroll"), f"analysis[{index}].sizing.cash.bankroll"
            )
            sizing_available = finite_number(
                sizing["cash"].get("available"), f"analysis[{index}].sizing.cash.available"
            )
            if not approximately_equal(sizing_bankroll, bankroll):
                raise ReportError(f"market {market_id} sizing bankroll differs from portfolio bankroll")
            if not approximately_equal(sizing_available, available):
                raise ReportError(f"market {market_id} sizing available cash differs from portfolio cash")
        allocation = analysis["allocation"]
        priority = allocation["priority"]
        allocated_for_market = finite_number(
            allocation["allocated_cash"], f"market {market_id} allocation"
        )
        if priority is None:
            if allocated_for_market > 1e-9:
                raise ReportError(f"funded allocation for market {market_id} requires a priority")
        else:
            if priority in allocation_priorities:
                raise ReportError(f"duplicate allocation priority: {priority}")
            allocation_priorities.add(priority)
            prioritized_allocations.append((priority, market_id, allocation))

    cash_cursor = deployable
    for _, market_id, allocation in sorted(prioritized_allocations):
        before = finite_number(allocation["cash_before"], f"market {market_id} cash_before")
        after = finite_number(allocation["cash_after"], f"market {market_id} cash_after")
        if not approximately_equal(before, cash_cursor):
            raise ReportError(f"market {market_id} allocation does not continue the shared cash chain")
        cash_cursor = after
    if not approximately_equal(cash_cursor, remaining):
        raise ReportError("portfolio remaining cash does not match the allocation cash chain")

    funded_total = 0.0
    funded_buys = 0
    action_markets: set[str] = set()
    forbidden_nominal = re.compile(r"theoretical|nominal|reference bankroll|参考资金|理论金额", re.I)
    for index, action in enumerate(root["actions"]):
        market_id = str(action["market_id"])
        if market_id in action_markets:
            raise ReportError(f"duplicate action market id: {market_id}")
        action_markets.add(market_id)
        if market_id not in analysis_by_market:
            raise ReportError(f"action market {market_id} has no matching analysis")
        analysis = analysis_by_market[market_id]
        if action["action"] != analysis["signal"]["action"]:
            raise ReportError(f"action for market {market_id} differs from analysis signal")
        if action["outcome"] != analysis["market"]["outcome"]:
            raise ReportError(f"action outcome for market {market_id} differs from the analysis")
        if action["market_question"] != analysis["market"]["question"]:
            raise ReportError(f"action question for market {market_id} differs from the analysis")
        if market_id in current_market_ids and action["action"] == "BUY":
            raise ReportError(f"current position {market_id} cannot use BUY; use ADD")
        if market_id in candidate_market_ids and action["action"] in {"ADD", "SELL", "TRIM"}:
            raise ReportError(f"new candidate {market_id} cannot use {action['action']}")
        if schema_version == "3.0":
            validate_v3_execution(action, analysis, f"report.actions[{index}]")
            if action["action"] == "SELL":
                require_decimal_equal(
                    action["recommended_shares"],
                    analysis["position"]["shares"],
                    f"report.actions[{index}].recommended_shares",
                )
        amount = finite_number(action["recommended_cash"], f"report.actions[{index}].recommended_cash")
        allocated_for_market = finite_number(
            analysis["allocation"]["allocated_cash"], f"market {market_id} allocation"
        )
        if action["action"] in {"BUY", "ADD"}:
            if amount <= 0:
                raise ReportError(f"funded {action['action']} for market {market_id} must be positive")
            if not analysis["opportunity"]["entry_eligible"]:
                raise ReportError(f"funded entry for market {market_id} is not entry eligible")
            if schema_version == "3.0" and decimal_value(
                analysis["opportunity"]["raw_edge"], f"market {market_id} raw_edge", minimum=Decimal("-1")
            ) <= Decimal("0.100000000000"):
                raise ReportError(f"funded entry for market {market_id} must have raw edge above 10pp")
            if not approximately_equal(amount, allocated_for_market):
                raise ReportError(f"action amount for market {market_id} differs from portfolio allocation")
            if forbidden_nominal.search(str(action.get("rationale", ""))):
                raise ReportError(f"funded action for market {market_id} uses nominal or theoretical cash")
            funded_total += amount
            if action["action"] == "BUY":
                funded_buys += 1
        elif action["action"] == "MAINTAIN" or schema_version != "3.0":
            if amount > 1e-9 or allocated_for_market > 1e-9:
                raise ReportError(f"non-entry action for market {market_id} must allocate zero cash")
        elif action["action"] in {"SELL", "TRIM"}:
            if amount <= 0 or allocated_for_market > 1e-9:
                raise ReportError(
                    f"{action['action']} action for market {market_id} must return cash without allocation"
                )
    if not approximately_equal(funded_total, allocated):
        raise ReportError("sum of funded BUY and ADD actions must equal portfolio allocated cash")
    if available <= 1e-9 and funded_total > 1e-9:
        raise ReportError("zero available collateral cannot fund BUY or ADD actions")
    if portfolio["cold_start"] and funded_buys > new_position_limit:
        raise ReportError("cold-start funded BUY count exceeds the configured limit")
    missing_actions = sorted(set(analysis_by_market) - action_markets)
    if missing_actions:
        raise ReportError(f"analyzed markets missing final actions: {', '.join(missing_actions)}")


def markdown_escape(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def localized_enum(value: Any, labels: dict[str, str]) -> str:
    machine_value = str(value)
    label = labels.get(machine_value)
    return f"{label}（{machine_value}）" if label else machine_value


def render_comparison(comparison: Any) -> str:
    if comparison is None:
        return "首次研究，无上一轮报告"
    if not isinstance(comparison, dict):
        return markdown_escape(comparison)
    labels = {
        "previous_report_id": "上一轮报告",
        "probability_change": "概率变化",
        "price_change": "价格变化",
        "action_change": "操作变化",
        "reason": "变化原因",
    }
    parts = [
        f"{labels.get(key, key)}：{markdown_escape(value)}"
        for key, value in comparison.items()
    ]
    return "；".join(parts) if parts else "无可比字段"


def render_evidence(items: list[dict[str, Any]], *, current_position: bool) -> list[str]:
    lines = [
        "| 来源 | 发布时间 | 摘要 | 结论 | 概率因子 | 可信度 |" + (" 新证据 |" if current_position else ""),
        "|---|---|---|---|---|---|" + ("---|" if current_position else ""),
    ]
    for item in items:
        source = f"[{markdown_escape(item['source_name'])}]({item['url']})"
        row = [
            source,
            markdown_escape(item["published_at"]),
            markdown_escape(item["summary"]),
            markdown_escape(item["conclusion"]),
            markdown_escape(item["factor_id"]),
            markdown_escape(item["credibility"]),
        ]
        if current_position:
            row.append("是" if item.get("new_since_previous") else "否")
        lines.append("| " + " | ".join(row) + " |")
        if current_position and item.get("new_since_previous") and item.get("impact"):
            impact = item["impact"]
            lines.append(
                f"\n新增证据影响：`{markdown_escape(impact['affected_factor'])}`；"
                f"子概率变化 {markdown_escape(impact['factor_probability_change'])}；"
                f"最终概率变化 {markdown_escape(impact['final_probability_change'])}。"
            )
    return lines


def render_analysis(item: dict[str, Any], *, current_position: bool) -> list[str]:
    market = item["market"]
    model = item["model"]
    signal = item["signal"]
    yes_probability = model.get("yes_probability", model.get("probability"))
    lines = [
        f"### [{market['question']}]({market['url']}) — {localized_enum(signal['action'], SIGNAL_LABELS)}",
        "",
        f"- 市场：`{market['id']}`；研究方向：{localized_enum(market['outcome'], OUTCOME_LABELS)}",
        f"- 市场 YES 概率：{yes_probability}；操作方向模型价值：{model['model_value']}",
        f"- 结论：{model['conclusion']}",
        f"- 行动理由：{signal['rationale']}",
        f"- 结算标准（原文）：{market['resolution_criteria']}",
    ]
    if current_position:
        lines.append(f"- 上轮对比：{render_comparison(item.get('comparison'))}")
    if "resolution_review" in item:
        review = item["resolution_review"]
        if "status" in review:
            lines.extend(
                [
                    f"- 结算审查：{review['status']}；{review['reason']}",
                    f"- YES 条件：{review['yes_condition']}",
                    f"- NO 条件：{review['no_condition']}",
                ]
            )
        else:
            lines.append(
                f"- 规则审查：清晰度 {REVIEW_LABELS.get(review.get('clarity'), review.get('clarity'))}；"
                f"理解验证 {REVIEW_LABELS.get(review.get('verification'), review.get('verification'))}；"
                f"{review.get('note', '')}"
            )
    opportunity = item.get("opportunity")
    if isinstance(opportunity, dict) and "soft_edge_pass" in opportunity:
        soft_status = "超过" if opportunity["soft_edge_pass"] else "未超过"
        soft_line = (
            f"- 入场软门槛：原始优势 {float(opportunity['raw_edge']) * 100:.2f}pp；"
            f"{soft_status} 10pp。"
        )
        if opportunity.get("soft_edge_override_reason"):
            soft_line += f"例外理由：{opportunity['soft_edge_override_reason']}"
        lines.append(soft_line)
    lines.extend(["", "#### 关键证据", ""])
    lines.extend(render_evidence(item["evidence"], current_position=current_position))
    return lines


def render_report(report: dict[str, Any]) -> str:
    run = report["run"]
    portfolio = report["portfolio"]
    lines = [
        "# Forecast Edge 扫描报告",
        "",
        f"- 运行 ID：`{run['id']}`",
        f"- 数据截止：{run['data_cutoff']}",
        f"- 预测市场：`{run['account'].get('adapter', 'legacy-unknown')}`",
        f"- 账户：`{run['account'].get('identifier', run['account'].get('address'))}`",
        f"- 可用抵押资金：{portfolio['available_collateral']}",
        f"- 保守总资金：{portfolio['estimated_bankroll']}",
        f"- 机会储备：{portfolio['reserve_cash']}",
    ]
    if report.get("schema_version") in MODERN_SCHEMA_VERSIONS:
        lines.extend(
            [
                f"- 持仓可变现价值：{portfolio['holdings_liquidation_value']}",
                f"- 可部署现金：{portfolio['deployable_cash']}",
                f"- 本轮分配：{portfolio['allocated_cash']}",
                f"- 分配后剩余：{portfolio['remaining_cash']}",
                f"- 组合分散判断：{portfolio['diversification_summary']}",
            ]
        )
    lines.extend(
        [
            "",
            "## 摘要",
            "",
            report["summary"],
            "",
            "## 行动",
            "",
            "| 信号 | 市场 | 方向 | 本轮份额 | 本轮金额 | 理由 |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for action in report["actions"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(localized_enum(action["action"], SIGNAL_LABELS)),
                    markdown_escape(action["market_question"]),
                    markdown_escape(localized_enum(action["outcome"], OUTCOME_LABELS)),
                    markdown_escape(action.get("recommended_shares", 0)),
                    markdown_escape(action["recommended_cash"]),
                    markdown_escape(action["rationale"]),
                ]
            )
            + " |"
        )
    if not report["actions"]:
        lines.append("| — | 本轮没有交易信号 | — | 0 | 0 | 保持资金闲置或继续持有 |")

    lines.extend(["", "## 当前持仓", ""])
    if report["current_positions"]:
        for item in report["current_positions"]:
            lines.extend(render_analysis(item, current_position=True))
            lines.append("")
    else:
        lines.append("当前没有持仓。")

    lines.extend(["", "## 新增候选", ""])
    if report["candidates"]:
        for item in report["candidates"]:
            lines.extend(render_analysis(item, current_position=False))
            lines.append("")
    else:
        lines.append("本轮没有完成研究的新增候选。")
    return "\n".join(lines).rstrip() + "\n"


def read_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return validate_report(json.load(handle))


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise ReportError("run id cannot be converted to a safe directory name")
    return cleaned


def archive_report(report_path: Path, output_root: Path, snapshots: list[Path]) -> Path:
    report = read_report(report_path)
    timestamp = safe_component(str(report["run"]["completed_at"]).replace(":", "-"))
    run_id = safe_component(str(report["run"]["id"]))
    adapter = report["run"]["account"].get("adapter")
    adapter_component = f"_{safe_component(str(adapter))}" if adapter else ""
    destination = output_root / "reports" / f"{timestamp}{adapter_component}_{run_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise ReportError(f"refusing to modify existing report directory: {destination}") from exc
    try:
        shutil.copy2(report_path, destination / "report.json")
        (destination / "report.md").write_text(render_report(report), encoding="utf-8")
        if snapshots:
            snapshot_dir = destination / "snapshots"
            snapshot_dir.mkdir()
            names: set[str] = set()
            for source in snapshots:
                if source.name in names:
                    raise ReportError(f"duplicate snapshot filename: {source.name}")
                names.add(source.name)
                shutil.copy2(source, snapshot_dir / source.name)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def previous_reports(
    output_root: Path, limit: int, adapter: str | None = None
) -> list[dict[str, Any]]:
    reports_root = output_root / "reports"
    if not reports_root.exists():
        return []
    results = []
    for directory in sorted((item for item in reports_root.iterdir() if item.is_dir()), reverse=True):
        report_path = directory / "report.json"
        if not report_path.is_file():
            continue
        try:
            report = read_report(report_path)
        except (OSError, json.JSONDecodeError, ReportError):
            continue
        account = report["run"]["account"]
        report_adapter = account.get("adapter")
        if adapter is not None and report_adapter != adapter:
            continue
        results.append(
            {
                "directory": str(directory),
                "report_path": str(report_path),
                "run_id": report["run"]["id"],
                "completed_at": report["run"]["completed_at"],
                "adapter": report_adapter,
            }
        )
        if len(results) >= limit:
            break
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate report JSON")
    validate.add_argument("report")
    render = subparsers.add_parser("render", help="Render report JSON as Markdown")
    render.add_argument("report")
    render.add_argument("output", help="Output Markdown path, or - for stdout")
    archive = subparsers.add_parser("archive", help="Create a new immutable report directory")
    archive.add_argument("report")
    archive.add_argument("--output-root", required=True)
    archive.add_argument("--snapshot", action="append", default=[])
    previous = subparsers.add_parser("previous", help="Locate recent immutable reports")
    previous.add_argument("--output-root", required=True)
    previous.add_argument("--limit", type=int, default=2)
    previous.add_argument("--adapter")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            report = read_report(Path(args.report))
            result: Any = {"valid": True, "run_id": report["run"]["id"]}
        elif args.command == "render":
            report = read_report(Path(args.report))
            markdown = render_report(report)
            if args.output == "-":
                sys.stdout.write(markdown)
                return 0
            Path(args.output).write_text(markdown, encoding="utf-8")
            result = {"rendered": str(Path(args.output))}
        elif args.command == "archive":
            destination = archive_report(
                Path(args.report), Path(args.output_root), [Path(path) for path in args.snapshot]
            )
            result = {"archived": str(destination)}
        elif args.command == "previous":
            if args.limit < 1:
                raise ReportError("--limit must be positive")
            result = previous_reports(Path(args.output_root), args.limit, args.adapter)
        else:  # pragma: no cover - argparse enforces a command.
            raise ReportError(f"unsupported command: {args.command}")
    except (OSError, json.JSONDecodeError, ReportError) as exc:
        print(f"report_tool: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
