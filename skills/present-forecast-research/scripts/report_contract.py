#!/usr/bin/env python3
"""Independent v3 report and v2 public-signal validation for the presenter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any


SCALE = Decimal("0.000000000001")
ONE = Decimal("1.000000000000")
MAX_DEPTH = 64
SIGNALS = {"BUY", "ADD", "MAINTAIN", "SELL"}
PUBLIC_SIGNALS = {"BUY", "ADD", "SELL"}
CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
LOCAL_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class PresentationContractError(ValueError):
    """Raised when source or public presentation data violates its contract."""


@dataclass(frozen=True)
class TreeEvaluation:
    probability: Decimal
    node_count: int
    deepest_level: int
    tree_sha256: str
    evidence_ids: frozenset[str]


def q(value: Decimal) -> Decimal:
    return value.quantize(SCALE, rounding=ROUND_HALF_EVEN)


def number(value: Any, path: str, *, minimum: Decimal = Decimal(0)) -> Decimal:
    if isinstance(value, bool):
        raise PresentationContractError(f"{path} must be numeric")
    try:
        result = q(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise PresentationContractError(f"{path} must be numeric") from exc
    if result < minimum:
        raise PresentationContractError(f"{path} must be at least {minimum}")
    return result


def probability(value: Any, path: str) -> Decimal:
    result = number(value, path)
    if result > ONE:
        raise PresentationContractError(f"{path} must be between 0 and 1")
    return result


def exact(left: Any, right: Any, path: str) -> None:
    if number(left, path, minimum=Decimal("-999999999999")) != number(
        right, path, minimum=Decimal("-999999999999")
    ):
        raise PresentationContractError(f"{path} values do not match at 12-decimal precision")


def obj(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PresentationContractError(f"{path} must be an object")
    return value


def array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise PresentationContractError(f"{path} must be an array")
    return value


def fields(value: dict[str, Any], required: set[str], path: str, *, exact_only: bool = False) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise PresentationContractError(f"{path} is missing fields: {', '.join(missing)}")
    if exact_only:
        extra = sorted(set(value) - required)
        if extra:
            raise PresentationContractError(f"{path} contains forbidden fields: {', '.join(extra)}")


def text(value: Any, path: str, *, chinese: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PresentationContractError(f"{path} must be a non-empty string")
    if chinese and not CHINESE_RE.search(value):
        raise PresentationContractError(f"{path} must contain Chinese narrative text")
    return value.strip()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate_tree(tree: Any) -> TreeEvaluation:
    document = obj(tree, "tree")
    fields(document, {"root"}, "tree", exact_only=True)
    identifiers: set[str] = set()
    references: set[str] = set()

    def unique(value: Any, path: str) -> str:
        identifier = text(value, path)
        if identifier in identifiers:
            raise PresentationContractError(f"{path} duplicates tree ID {identifier}")
        identifiers.add(identifier)
        return identifier

    def evidence_ids(value: Any, path: str, *, required: bool) -> set[str]:
        rows = array(value, path)
        result = {text(item, f"{path}[]") for item in rows}
        if len(result) != len(rows):
            raise PresentationContractError(f"{path} contains duplicate evidence IDs")
        if required and not result:
            raise PresentationContractError(f"{path} must not be empty")
        references.update(result)
        return result

    def visit(value: Any, path: str, depth: int) -> tuple[Decimal, int, int]:
        if depth > MAX_DEPTH:
            raise PresentationContractError(f"{path} exceeds maximum depth {MAX_DEPTH}")
        node = obj(value, path)
        fields(
            node,
            {"id", "label", "reason", "probability", "evidence_ids", "children"},
            path,
            exact_only=True,
        )
        unique(node["id"], f"{path}.id")
        text(node["label"], f"{path}.label")
        text(node["reason"], f"{path}.reason", chinese=True)
        declared = probability(node["probability"], f"{path}.probability")
        children = array(node["children"], f"{path}.children")
        evidence_ids(node["evidence_ids"], f"{path}.evidence_ids", required=not children)
        if not children:
            return declared, 1, depth
        if len(children) < 2:
            raise PresentationContractError(f"{path}.children must contain at least two branches")
        total_weight = Decimal(0)
        total = Decimal(0)
        count = 1
        deepest = depth
        for index, raw_branch in enumerate(children):
            branch_path = f"{path}.children[{index}]"
            branch = obj(raw_branch, branch_path)
            fields(
                branch,
                {"id", "label", "weight", "weight_reason", "evidence_ids", "node"},
                branch_path,
                exact_only=True,
            )
            unique(branch["id"], f"{branch_path}.id")
            text(branch["label"], f"{branch_path}.label")
            text(branch["weight_reason"], f"{branch_path}.weight_reason", chinese=True)
            evidence_ids(branch["evidence_ids"], f"{branch_path}.evidence_ids", required=True)
            weight = probability(branch["weight"], f"{branch_path}.weight")
            child_probability, child_count, child_depth = visit(
                branch["node"], f"{branch_path}.node", depth + 1
            )
            total_weight += weight
            total += weight * child_probability
            count += child_count
            deepest = max(deepest, child_depth)
        if q(total_weight) != ONE:
            raise PresentationContractError(f"{path}.children weights must sum to 1")
        computed = q(total)
        if declared != computed:
            raise PresentationContractError(f"{path}.probability does not match child calculation")
        return computed, count, deepest

    root_probability, node_count, deepest = visit(document["root"], "tree.root", 1)
    return TreeEvaluation(
        root_probability,
        node_count,
        deepest,
        canonical_sha256(document),
        frozenset(references),
    )


def validate_execution(action: dict[str, Any], analysis: dict[str, Any], path: str) -> None:
    name = action["action"]
    expected_cash = number(action["recommended_cash"], f"{path}.recommended_cash")
    expected_shares = number(action["recommended_shares"], f"{path}.recommended_shares")
    execution = action["execution"]
    if name == "MAINTAIN":
        if execution is not None or expected_cash != 0 or expected_shares != 0:
            raise PresentationContractError(f"{path} MAINTAIN must have zero deltas and null execution")
        return
    execution = obj(execution, f"{path}.execution")
    required = {
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
    }
    fields(execution, required, f"{path}.execution", exact_only=True)
    direction = "PAY" if name in {"BUY", "ADD"} else "RECEIVE"
    if execution["cash_direction"] != direction:
        raise PresentationContractError(f"{path}.execution.cash_direction must be {direction}")
    if not re.fullmatch(r"[a-f0-9]{64}", str(execution["source_snapshot_sha256"])):
        raise PresentationContractError(f"{path}.execution has an invalid snapshot hash")
    fills = array(execution["fills"], f"{path}.execution.fills")
    if not fills:
        raise PresentationContractError(f"{path}.execution.fills must not be empty")
    shares = Decimal(0)
    gross = Decimal(0)
    fee = Decimal(0)
    for index, raw_fill in enumerate(fills):
        fill_path = f"{path}.execution.fills[{index}]"
        fill = obj(raw_fill, fill_path)
        fields(fill, {"price", "shares", "fee_cash"}, fill_path, exact_only=True)
        fill_price = probability(fill["price"], f"{fill_path}.price")
        fill_shares = number(fill["shares"], f"{fill_path}.shares")
        if fill_shares <= 0:
            raise PresentationContractError(f"{fill_path}.shares must be positive")
        shares += fill_shares
        gross += fill_price * fill_shares
        fee += number(fill["fee_cash"], f"{fill_path}.fee_cash")
    shares, gross, fee = q(shares), q(gross), q(fee)
    vwap = q(gross / shares)
    cash = q(gross + fee if direction == "PAY" else gross - fee)
    effective = q(cash / shares)
    best = probability(execution["best_price"], f"{path}.execution.best_price")
    slippage = q(vwap - best if direction == "PAY" else best - vwap)
    model_value = probability(analysis["model"]["model_value"], f"{path}.model_value")
    edge = q(model_value - effective if direction == "PAY" else effective - model_value)
    if slippage < 0 or edge < 0:
        raise PresentationContractError(f"{path}.execution has negative slippage or net edge")
    for field, expected in {
        "shares": shares,
        "cash_amount": cash,
        "gross_cash": gross,
        "fee_cash": fee,
        "vwap": vwap,
        "effective_unit_price": effective,
        "slippage": slippage,
        "net_edge": edge,
    }.items():
        exact(execution[field], expected, f"{path}.execution.{field}")
    exact(expected_cash, cash, f"{path}.recommended_cash")
    exact(expected_shares, shares, f"{path}.recommended_shares")
    exact(analysis["opportunity"]["executable_price"], effective, f"{path}.executable_price")
    exact(analysis["opportunity"]["net_edge"], edge, f"{path}.net_edge")


def validate_report(report: Any) -> dict[str, Any]:
    root = obj(report, "report")
    top_fields = {
        "schema_version",
        "run",
        "portfolio",
        "discovery_audit",
        "current_positions",
        "candidates",
        "actions",
        "summary",
    }
    fields(root, top_fields, "report", exact_only=True)
    if root["schema_version"] != "3.0":
        raise PresentationContractError("presenter accepts only report schema 3.0")
    run = obj(root["run"], "report.run")
    fields(
        run,
        {"id", "started_at", "completed_at", "data_cutoff", "account", "previous_report_ids"},
        "report.run",
        exact_only=True,
    )
    text(run["id"], "report.run.id")
    if len(array(run["previous_report_ids"], "report.run.previous_report_ids")) > 2:
        raise PresentationContractError("report.run.previous_report_ids exceeds two")
    portfolio = obj(root["portfolio"], "report.portfolio")
    portfolio_fields = {
        "available_collateral",
        "holdings_liquidation_value",
        "estimated_bankroll",
        "reserve_cash",
        "deployable_cash",
        "allocated_cash",
        "remaining_cash",
        "cold_start",
        "new_position_limit",
        "diversification_summary",
    }
    fields(portfolio, portfolio_fields, "report.portfolio", exact_only=True)
    available = number(portfolio["available_collateral"], "portfolio.available")
    holdings = number(portfolio["holdings_liquidation_value"], "portfolio.holdings")
    bankroll = number(portfolio["estimated_bankroll"], "portfolio.bankroll")
    reserve = number(portfolio["reserve_cash"], "portfolio.reserve")
    deployable = number(portfolio["deployable_cash"], "portfolio.deployable")
    allocated = number(portfolio["allocated_cash"], "portfolio.allocated")
    remaining = number(portfolio["remaining_cash"], "portfolio.remaining")
    if q(available + holdings) != bankroll or q(reserve + deployable) != available:
        raise PresentationContractError("portfolio bankroll or reserve identity is inconsistent")
    if q(allocated + remaining) != deployable:
        raise PresentationContractError("portfolio allocation identity is inconsistent")
    text(portfolio["diversification_summary"], "portfolio.diversification_summary", chinese=True)
    text(root["summary"], "report.summary", chinese=True)

    analyses: dict[str, dict[str, Any]] = {}
    resolution_reasons: list[str] = []
    for collection_name, current in (("current_positions", True), ("candidates", False)):
        for index, raw_analysis in enumerate(array(root[collection_name], f"report.{collection_name}")):
            path = f"report.{collection_name}[{index}]"
            analysis = obj(raw_analysis, path)
            required = {
                "market",
                "resolution_review",
                "model",
                "evidence",
                "position",
                "signal",
                "opportunity",
                "allocation",
            }
            if current:
                required.add("comparison")
            fields(analysis, required, path, exact_only=True)
            market = obj(analysis["market"], f"{path}.market")
            market_id = text(market.get("id"), f"{path}.market.id")
            if market_id in analyses:
                raise PresentationContractError(f"duplicate market ID {market_id}")
            if not re.match(r"^https://(?:www\.)?polymarket\.com/event/", str(market.get("url"))):
                raise PresentationContractError(f"{path}.market.url must be an event URL")
            if market.get("outcome") not in {"YES", "NO"}:
                raise PresentationContractError(f"{path}.market.outcome is invalid")
            evidence_rows = array(analysis["evidence"], f"{path}.evidence")
            if not evidence_rows:
                raise PresentationContractError(f"{path}.evidence must not be empty")
            evidence_by_id: dict[str, dict[str, Any]] = {}
            probability_ids: set[str] = set()
            resolution_ids: set[str] = set()
            for evidence_index, raw_evidence in enumerate(evidence_rows):
                evidence_path = f"{path}.evidence[{evidence_index}]"
                evidence = obj(raw_evidence, evidence_path)
                evidence_id = text(evidence.get("id"), f"{evidence_path}.id")
                if evidence_id in evidence_by_id:
                    raise PresentationContractError(f"duplicate evidence ID {evidence_id}")
                evidence_by_id[evidence_id] = evidence
                roles = set(array(evidence.get("roles"), f"{evidence_path}.roles"))
                if not roles or not roles <= {"PROBABILITY", "RESOLUTION"}:
                    raise PresentationContractError(f"{evidence_path}.roles is invalid")
                probability_ids.update({evidence_id} if "PROBABILITY" in roles else set())
                resolution_ids.update({evidence_id} if "RESOLUTION" in roles else set())
            review = obj(analysis["resolution_review"], f"{path}.resolution_review")
            review_path = f"{path}.resolution_review"
            review_fields = {
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
            }
            fields(review, review_fields, review_path, exact_only=True)
            if review.get("status") != "PASS":
                raise PresentationContractError(f"{path}.resolution_review.status must be PASS")
            if review["criteria_text"] != market.get("resolution_criteria"):
                raise PresentationContractError(
                    f"{path}.resolution_review.criteria_text differs from the market criteria"
                )
            for name in ("yes_condition", "no_condition", "reason"):
                text(review[name], f"{review_path}.{name}", chinese=True)
            text(review["resolution_source"], f"{review_path}.resolution_source")
            for edge_index, edge_case in enumerate(array(review["edge_cases"], f"{review_path}.edge_cases")):
                text(edge_case, f"{review_path}.edge_cases[{edge_index}]", chinese=True)
            resolution_reasons.append(review["reason"].strip())
            review_ids = set(array(review.get("evidence_ids"), f"{path}.resolution_review.evidence_ids"))
            if resolution_ids - review_ids or review_ids - set(evidence_by_id):
                raise PresentationContractError(f"{path}.resolution_review evidence links are incomplete")
            model = obj(analysis["model"], f"{path}.model")
            tree_evaluation = evaluate_tree(model.get("tree"))
            if tree_evaluation.evidence_ids - set(evidence_by_id):
                raise PresentationContractError(f"{path}.model.tree refers to unknown evidence")
            if probability_ids - set(tree_evaluation.evidence_ids):
                raise PresentationContractError(f"{path} has unreferenced probability evidence")
            exact(model.get("yes_probability"), tree_evaluation.probability, f"{path}.yes_probability")
            expected_value = (
                tree_evaluation.probability
                if market["outcome"] == "YES"
                else q(ONE - tree_evaluation.probability)
            )
            exact(model.get("model_value"), expected_value, f"{path}.model_value")
            calculation = obj(model.get("calculation"), f"{path}.model.calculation")
            if calculation.get("tree_sha256") != tree_evaluation.tree_sha256:
                raise PresentationContractError(f"{path}.model.calculation.tree_sha256 mismatch")
            exact(
                calculation.get("root_probability"),
                tree_evaluation.probability,
                f"{path}.model.calculation.root_probability",
            )
            if calculation.get("node_count") != tree_evaluation.node_count:
                raise PresentationContractError(f"{path}.model.calculation.node_count mismatch")
            if calculation.get("deepest_level") != tree_evaluation.deepest_level:
                raise PresentationContractError(f"{path}.model.calculation.deepest_level mismatch")
            position = obj(analysis["position"], f"{path}.position")
            held = position.get("held")
            shares = number(position.get("shares"), f"{path}.position.shares")
            if current and (held is not True or shares <= 0):
                raise PresentationContractError(f"{path}.position is not a current holding")
            if not current and (held is not False or shares != 0):
                raise PresentationContractError(f"{path}.position is not empty")
            signal = obj(analysis["signal"], f"{path}.signal")
            if signal.get("action") not in SIGNALS:
                raise PresentationContractError(f"{path}.signal.action is invalid")
            text(signal.get("rationale"), f"{path}.signal.rationale", chinese=True)
            exact(
                analysis["opportunity"]["model_value"],
                model["model_value"],
                f"{path}.opportunity.model_value",
            )
            analyses[market_id] = analysis

    if len(resolution_reasons) != len(set(resolution_reasons)):
        raise PresentationContractError(
            "resolution review reasons must be market-specific, not repeated boilerplate"
        )

    action_markets: set[str] = set()
    funded_total = Decimal(0)
    for index, raw_action in enumerate(array(root["actions"], "report.actions")):
        path = f"report.actions[{index}]"
        action = obj(raw_action, path)
        required = {
            "action",
            "market_id",
            "market_question",
            "outcome",
            "recommended_cash",
            "recommended_shares",
            "rationale",
            "public_reason",
            "execution",
        }
        fields(action, required, path, exact_only=True)
        market_id = text(action["market_id"], f"{path}.market_id")
        if market_id in action_markets or market_id not in analyses:
            raise PresentationContractError(f"{path}.market_id is duplicate or unknown")
        action_markets.add(market_id)
        analysis = analyses[market_id]
        if action["action"] != analysis["signal"]["action"]:
            raise PresentationContractError(f"{path}.action differs from analysis")
        if action["outcome"] != analysis["market"]["outcome"]:
            raise PresentationContractError(f"{path}.outcome differs from analysis")
        text(action["rationale"], f"{path}.rationale", chinese=True)
        text(action["public_reason"], f"{path}.public_reason", chinese=True)
        validate_execution(action, analysis, path)
        if action["action"] in {"BUY", "ADD"}:
            raw_edge = number(
                analysis["opportunity"]["raw_edge"], f"{path}.raw_edge", minimum=Decimal("-1")
            )
            if raw_edge <= Decimal("0.100000000000"):
                raise PresentationContractError(f"{path} funded entry does not clear 10pp")
            funded_total += number(action["recommended_cash"], f"{path}.recommended_cash")
        if action["action"] == "SELL":
            exact(action["recommended_shares"], analysis["position"]["shares"], f"{path}.shares")
    if action_markets != set(analyses):
        raise PresentationContractError("every analyzed market must have one final action")
    if q(funded_total) != allocated:
        raise PresentationContractError("funded action total differs from portfolio allocation")
    return root


PUBLIC_ACTION_FIELDS = {
    "market_id",
    "market_name",
    "action",
    "outcome",
    "polymarket_url",
    "yes_probability",
    "fair_price",
    "current_price",
    "raw_edge",
    "net_edge",
    "reason",
    "resolution",
    "evidence",
    "probability_tree",
}


def validate_public_signals(value: Any) -> dict[str, Any]:
    root = obj(value, "public")
    root_fields = {
        "schema_version",
        "brief_id",
        "source_report_id",
        "source_report_sha256",
        "generated_at",
        "as_of",
        "title",
        "actions",
    }
    fields(root, root_fields, "public", exact_only=True)
    if root["schema_version"] != "2.0" or root["title"] != "预测市场研究信号":
        raise PresentationContractError("public protocol version or title is invalid")
    for name in ("brief_id", "source_report_id", "generated_at", "as_of", "title"):
        text(root[name], f"public.{name}")
    if not re.fullmatch(r"[a-f0-9]{64}", str(root["source_report_sha256"])):
        raise PresentationContractError("public source report hash is invalid")
    for index, raw_action in enumerate(array(root["actions"], "public.actions")):
        path = f"public.actions[{index}]"
        action = obj(raw_action, path)
        fields(action, PUBLIC_ACTION_FIELDS, path, exact_only=True)
        if action["action"] not in PUBLIC_SIGNALS:
            raise PresentationContractError(f"{path}.action is not public")
        if action["outcome"] not in {"YES", "NO"}:
            raise PresentationContractError(f"{path}.outcome is invalid")
        if not re.match(r"^https://(?:www\.)?polymarket\.com/event/", action["polymarket_url"]):
            raise PresentationContractError(f"{path}.polymarket_url is not an event URL")
        for name in ("market_id", "market_name", "reason"):
            text(action[name], f"{path}.{name}", chinese=name == "reason")
        yes_probability = probability(action["yes_probability"], f"{path}.yes_probability")
        fair_price = yes_probability if action["outcome"] == "YES" else q(ONE - yes_probability)
        exact(action["fair_price"], fair_price, f"{path}.fair_price")
        probability(action["current_price"], f"{path}.current_price")
        raw_edge = number(action["raw_edge"], f"{path}.raw_edge", minimum=Decimal("-1"))
        if raw_edge > ONE:
            raise PresentationContractError(f"{path}.raw_edge must be between -1 and 1")
        probability(action["net_edge"], f"{path}.net_edge")
        resolution_path = f"{path}.resolution"
        resolution = obj(action["resolution"], resolution_path)
        resolution_fields = {
            "criteria_text",
            "yes_condition",
            "no_condition",
            "deadline",
            "resolution_source",
        }
        fields(resolution, resolution_fields, resolution_path, exact_only=True)
        for name in ("criteria_text", "yes_condition", "no_condition", "resolution_source"):
            text(resolution[name], f"{resolution_path}.{name}")
        if resolution["deadline"] is not None:
            text(resolution["deadline"], f"{resolution_path}.deadline")
        evidence_rows = array(action["evidence"], f"{path}.evidence")
        if not evidence_rows:
            raise PresentationContractError(f"{path}.evidence must not be empty")
        evidence_ids: set[str] = set()
        for evidence_index, raw_evidence in enumerate(evidence_rows):
            evidence_path = f"{path}.evidence[{evidence_index}]"
            evidence = obj(raw_evidence, evidence_path)
            allowed = {"id", "source_url", "source_title", "source_content", "probability_impact"}
            fields(evidence, allowed, evidence_path, exact_only=True)
            identifier = text(evidence["id"], f"{evidence_path}.id")
            if identifier in evidence_ids:
                raise PresentationContractError(f"{evidence_path}.id is duplicated")
            evidence_ids.add(identifier)
            if not str(evidence["source_url"]).startswith("https://"):
                raise PresentationContractError(f"{evidence_path}.source_url must be HTTPS")
            for name in ("source_title", "source_content", "probability_impact"):
                text(evidence[name], f"{evidence_path}.{name}")
        tree = evaluate_tree(action["probability_tree"])
        if tree.evidence_ids - evidence_ids:
            raise PresentationContractError(f"{path}.probability_tree has unknown public evidence")
        exact(tree.probability, yes_probability, f"{path}.probability_tree")
    serialized = json.dumps(root, ensure_ascii=False)
    if ADDRESS_RE.search(serialized) or EMAIL_RE.search(serialized) or LOCAL_PATH_RE.search(serialized):
        raise PresentationContractError("public output contains sensitive data")
    if "polymarket.com/@" in serialized:
        raise PresentationContractError("public output contains a profile URL")
    return root


def resolve_report_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_dir():
        candidate = path / "report.json"
        if not candidate.is_file():
            raise PresentationContractError(f"input directory does not contain report.json: {path}")
        return candidate
    if not path.is_file() or path.name != "report.json":
        raise PresentationContractError("input must be an explicit report.json or one run directory")
    return path


def load_report(value: str | Path) -> tuple[dict[str, Any], Path, str]:
    path = resolve_report_path(value)
    raw = path.read_bytes()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PresentationContractError(f"invalid report JSON: {exc}") from exc
    return validate_report(document), path, hashlib.sha256(raw).hexdigest()
