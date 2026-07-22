#!/usr/bin/env python3
"""Validate privacy-safe public Forecast Edge briefs without dependencies."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_FIELDS = {"schema_version", "brief_id", "generated_at", "as_of", "title", "actions"}
ACTION_FIELDS = {
    "market_id",
    "market_name",
    "action",
    "outcome",
    "polymarket_url",
    "link_verified_at",
    "fair_price",
    "current_price",
    "price_gap",
    "confidence",
    "reason",
    "evidence",
    "probability_tree",
}
EVIDENCE_FIELDS = {"id", "source_url", "source_title", "source_content", "probability_impact"}
INDEX_FIELDS = {"schema_version", "briefs"}
INDEX_ITEM_FIELDS = {"brief_id", "generated_at", "title", "file"}
BRIEF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
TREE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PUBLIC_TITLE = "预测市场研究信号"
PROBABILITY_TOLERANCE = 1e-6
WALLET_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
LOCAL_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)")


class PublicBriefError(ValueError):
    """Raised when public data violates the protocol."""


def require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicBriefError(f"{path} must be an object")
    return value


def require_exact_fields(value: dict[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise PublicBriefError(f"{path} is missing fields: {', '.join(missing)}")
    if extra:
        raise PublicBriefError(f"{path} contains forbidden fields: {', '.join(extra)}")


def require_string(value: Any, path: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicBriefError(f"{path} must be a non-empty string")
    if len(value) > maximum:
        raise PublicBriefError(f"{path} exceeds {maximum} characters")
    return value


def require_timestamp(value: Any, path: str) -> str:
    text = require_string(value, path, maximum=64)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PublicBriefError(f"{path} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PublicBriefError(f"{path} must include a timezone")
    return text


def require_number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PublicBriefError(f"{path} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise PublicBriefError(f"{path} must be between {minimum} and {maximum}")
    return number


def require_https_url(value: Any, path: str) -> str:
    url = require_string(value, path, maximum=2048)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise PublicBriefError(f"{path} must be an absolute HTTPS URL")
    return url


def scan_sensitive_strings(value: Any, path: str = "brief") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            scan_sensitive_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_sensitive_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if WALLET_RE.search(value):
            raise PublicBriefError(f"{path} contains a wallet address")
        if EMAIL_RE.search(value):
            raise PublicBriefError(f"{path} contains an email address")
        if LOCAL_PATH_RE.search(value):
            raise PublicBriefError(f"{path} contains a local user path")


def validate_evidence(value: Any, path: str) -> dict[str, Any]:
    item = require_object(value, path)
    require_exact_fields(item, EVIDENCE_FIELDS, path)
    identifier = require_string(item["id"], f"{path}.id", maximum=100)
    if not TREE_ID_RE.fullmatch(identifier):
        raise PublicBriefError(f"{path}.id contains unsupported characters")
    require_https_url(item["source_url"], f"{path}.source_url")
    require_string(item["source_title"], f"{path}.source_title", maximum=240)
    require_string(item["source_content"], f"{path}.source_content", maximum=1200)
    require_string(item["probability_impact"], f"{path}.probability_impact", maximum=500)
    return item


def require_unique_id(value: Any, path: str, identifiers: set[str]) -> str:
    identifier = require_string(value, path, maximum=100)
    if not TREE_ID_RE.fullmatch(identifier):
        raise PublicBriefError(f"{path} contains unsupported characters")
    if identifier in identifiers:
        raise PublicBriefError(f"{path} duplicates ID {identifier}")
    identifiers.add(identifier)
    return identifier


def validate_evidence_refs(
    value: Any,
    path: str,
    evidence_ids: set[str],
    referenced_evidence: set[str],
) -> None:
    if not isinstance(value, list):
        raise PublicBriefError(f"{path} must be an array")
    seen: set[str] = set()
    for index, raw in enumerate(value):
        reference = require_string(raw, f"{path}[{index}]", maximum=100)
        if reference in seen:
            raise PublicBriefError(f"{path} must not contain duplicates")
        seen.add(reference)
        if reference not in evidence_ids:
            raise PublicBriefError(f"{path}[{index}] refers to unknown evidence {reference}")
        referenced_evidence.add(reference)


def validate_probability_node(
    value: Any,
    path: str,
    evidence_ids: set[str],
    referenced_evidence: set[str],
    identifiers: set[str],
    depth: int = 1,
) -> float:
    if depth > 64:
        raise PublicBriefError(f"{path} exceeds maximum depth 64")
    node = require_object(value, path)
    node_type = node.get("type")
    common = {"id", "type", "label", "description", "probability", "evidence_refs"}
    expected = {
        "estimate": common,
        "partition": common | {"branches"},
        "bayes_update": common | {"prior", "updates"},
        "complement": common | {"child"},
    }.get(node_type)
    if expected is None:
        raise PublicBriefError(f"{path}.type is unsupported: {node_type}")
    require_exact_fields(node, expected, path)
    require_unique_id(node["id"], f"{path}.id", identifiers)
    require_string(node["label"], f"{path}.label", maximum=160)
    require_string(node["description"], f"{path}.description", maximum=600)
    declared = require_number(node["probability"], f"{path}.probability", 0.0, 1.0)
    validate_evidence_refs(
        node["evidence_refs"],
        f"{path}.evidence_refs",
        evidence_ids,
        referenced_evidence,
    )

    if node_type == "estimate":
        computed = declared
    elif node_type == "partition":
        branches = node["branches"]
        if not isinstance(branches, list) or len(branches) < 2:
            raise PublicBriefError(f"{path}.branches must contain at least two branches")
        total_weight = 0.0
        computed = 0.0
        for index, raw in enumerate(branches):
            branch_path = f"{path}.branches[{index}]"
            branch = require_object(raw, branch_path)
            require_exact_fields(
                branch,
                {"id", "label", "weight", "evidence_refs", "child"},
                branch_path,
            )
            require_unique_id(branch["id"], f"{branch_path}.id", identifiers)
            require_string(branch["label"], f"{branch_path}.label", maximum=160)
            weight = require_number(branch["weight"], f"{branch_path}.weight", 0.0, 1.0)
            validate_evidence_refs(
                branch["evidence_refs"],
                f"{branch_path}.evidence_refs",
                evidence_ids,
                referenced_evidence,
            )
            child_probability = validate_probability_node(
                branch["child"],
                f"{branch_path}.child",
                evidence_ids,
                referenced_evidence,
                identifiers,
                depth + 1,
            )
            total_weight += weight
            computed += weight * child_probability
        if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE):
            raise PublicBriefError(f"{path}.branches weights must sum to 1")
    elif node_type == "complement":
        child_probability = validate_probability_node(
            node["child"],
            f"{path}.child",
            evidence_ids,
            referenced_evidence,
            identifiers,
            depth + 1,
        )
        computed = 1.0 - child_probability
    else:
        computed = validate_probability_node(
            node["prior"],
            f"{path}.prior",
            evidence_ids,
            referenced_evidence,
            identifiers,
            depth + 1,
        )
        updates = node["updates"]
        if not isinstance(updates, list) or not updates:
            raise PublicBriefError(f"{path}.updates must contain at least one item")
        for index, raw in enumerate(updates):
            update_path = f"{path}.updates[{index}]"
            update = require_object(raw, update_path)
            require_exact_fields(
                update,
                {
                    "id",
                    "label",
                    "likelihood_if_true",
                    "likelihood_if_false",
                    "posterior_probability",
                    "evidence_refs",
                },
                update_path,
            )
            require_unique_id(update["id"], f"{update_path}.id", identifiers)
            require_string(update["label"], f"{update_path}.label", maximum=160)
            likelihood_true = require_number(
                update["likelihood_if_true"], f"{update_path}.likelihood_if_true", 0.0, 1.0
            )
            likelihood_false = require_number(
                update["likelihood_if_false"], f"{update_path}.likelihood_if_false", 0.0, 1.0
            )
            denominator = computed * likelihood_true + (1.0 - computed) * likelihood_false
            if denominator == 0.0:
                raise PublicBriefError(f"{update_path} has zero evidence probability")
            computed = computed * likelihood_true / denominator
            posterior = require_number(
                update["posterior_probability"],
                f"{update_path}.posterior_probability",
                0.0,
                1.0,
            )
            if not math.isclose(
                posterior,
                computed,
                rel_tol=0.0,
                abs_tol=PROBABILITY_TOLERANCE,
            ):
                raise PublicBriefError(
                    f"{update_path}.posterior_probability does not match Bayes calculation"
                )
            validate_evidence_refs(
                update["evidence_refs"],
                f"{update_path}.evidence_refs",
                evidence_ids,
                referenced_evidence,
            )

    if not math.isclose(
        declared,
        computed,
        rel_tol=0.0,
        abs_tol=PROBABILITY_TOLERANCE,
    ):
        raise PublicBriefError(f"{path}.probability does not match its child calculation")
    return declared


def validate_probability_tree(
    value: Any,
    path: str,
    evidence_ids: set[str],
    fair_price: float,
) -> None:
    tree = require_object(value, path)
    require_exact_fields(tree, {"root"}, path)
    referenced_evidence: set[str] = set()
    root_probability = validate_probability_node(
        tree["root"],
        f"{path}.root",
        evidence_ids,
        referenced_evidence,
        set(),
    )
    if not math.isclose(
        root_probability,
        fair_price,
        rel_tol=0.0,
        abs_tol=PROBABILITY_TOLERANCE,
    ):
        raise PublicBriefError(f"{path}.root.probability must equal fair_price")
    unreferenced = sorted(evidence_ids - referenced_evidence)
    if unreferenced:
        raise PublicBriefError(f"{path} does not reference evidence: {', '.join(unreferenced)}")


def validate_action(value: Any, path: str) -> dict[str, Any]:
    action = require_object(value, path)
    require_exact_fields(action, ACTION_FIELDS, path)
    require_string(action["market_id"], f"{path}.market_id", maximum=160)
    require_string(action["market_name"], f"{path}.market_name", maximum=240)
    if action["action"] not in {"BUY", "ADD", "SELL"}:
        raise PublicBriefError(f"{path}.action must be BUY, ADD, or SELL")
    if action["outcome"] not in {"YES", "NO"}:
        raise PublicBriefError(f"{path}.outcome must be YES or NO")
    market_url = require_https_url(action["polymarket_url"], f"{path}.polymarket_url")
    parsed = urlparse(market_url)
    if parsed.hostname not in {"polymarket.com", "www.polymarket.com"} or not parsed.path.startswith(
        "/event/"
    ):
        raise PublicBriefError(f"{path}.polymarket_url must be a Polymarket event URL")
    require_timestamp(action["link_verified_at"], f"{path}.link_verified_at")
    fair = require_number(action["fair_price"], f"{path}.fair_price", 0.0, 1.0)
    current = require_number(action["current_price"], f"{path}.current_price", 0.0, 1.0)
    gap = require_number(action["price_gap"], f"{path}.price_gap", -1.0, 1.0)
    if not math.isclose(gap, fair - current, rel_tol=0.0, abs_tol=1e-9):
        raise PublicBriefError(f"{path}.price_gap must equal fair_price - current_price")
    if action["confidence"] not in {"HIGH", "MEDIUM", "LOW"}:
        raise PublicBriefError(f"{path}.confidence must be HIGH, MEDIUM, or LOW")
    require_string(action["reason"], f"{path}.reason", maximum=400)
    evidence = action["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise PublicBriefError(f"{path}.evidence must contain at least one item")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        validated = validate_evidence(item, f"{path}.evidence[{index}]")
        if validated["id"] in evidence_ids:
            raise PublicBriefError(f"{path}.evidence contains duplicate IDs")
        evidence_ids.add(validated["id"])
    validate_probability_tree(
        action["probability_tree"],
        f"{path}.probability_tree",
        evidence_ids,
        fair,
    )
    return action


def validate_brief(value: Any) -> dict[str, Any]:
    brief = require_object(value, "brief")
    require_exact_fields(brief, ROOT_FIELDS, "brief")
    if brief["schema_version"] != "1.1":
        raise PublicBriefError("brief.schema_version must equal '1.1'")
    brief_id = require_string(brief["brief_id"], "brief.brief_id", maximum=80)
    if not BRIEF_ID_RE.fullmatch(brief_id):
        raise PublicBriefError("brief.brief_id contains unsupported characters")
    require_timestamp(brief["generated_at"], "brief.generated_at")
    require_timestamp(brief["as_of"], "brief.as_of")
    require_string(brief["title"], "brief.title", maximum=120)
    if brief["title"] != PUBLIC_TITLE:
        raise PublicBriefError(f"brief.title must equal {PUBLIC_TITLE}")
    actions = brief["actions"]
    if not isinstance(actions, list):
        raise PublicBriefError("brief.actions must be an array")
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(actions):
        action = validate_action(item, f"brief.actions[{index}]")
        key = (action["market_id"], action["outcome"])
        if key in seen:
            raise PublicBriefError("brief.actions contains a duplicate market outcome")
        seen.add(key)
    scan_sensitive_strings(brief)
    return brief


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicBriefError(f"cannot read JSON from {path}: {exc}") from exc


def validate_index(value: Any, directory: Path) -> dict[str, Any]:
    index = require_object(value, "index")
    require_exact_fields(index, INDEX_FIELDS, "index")
    if index["schema_version"] != "1.0" or not isinstance(index["briefs"], list):
        raise PublicBriefError("index must use schema 1.0 and contain a briefs array")
    referenced: set[str] = set()
    previous_time: str | None = None
    for position, raw in enumerate(index["briefs"]):
        path = f"index.briefs[{position}]"
        item = require_object(raw, path)
        require_exact_fields(item, INDEX_ITEM_FIELDS, path)
        require_string(item["brief_id"], f"{path}.brief_id", maximum=80)
        generated = require_timestamp(item["generated_at"], f"{path}.generated_at")
        require_string(item["title"], f"{path}.title", maximum=120)
        filename = require_string(item["file"], f"{path}.file", maximum=100)
        if filename != f"{item['brief_id']}.json" or "/" in filename or "\\" in filename:
            raise PublicBriefError(f"{path}.file must be the brief ID plus .json")
        if filename in referenced:
            raise PublicBriefError(f"{path}.file is duplicated")
        referenced.add(filename)
        brief = validate_brief(load_json(directory / filename))
        if any(brief[key] != item[key] for key in ("brief_id", "generated_at", "title")):
            raise PublicBriefError(f"{path} does not match {filename}")
        if previous_time is not None and generated > previous_time:
            raise PublicBriefError("index.briefs must be newest first")
        previous_time = generated

    actual = {path.name for path in directory.glob("*.json")} - {"index.json", "latest.json"}
    if actual != referenced:
        raise PublicBriefError("index.json and daily brief files do not match")
    latest = validate_brief(load_json(directory / "latest.json"))
    if index["briefs"] and latest["brief_id"] != index["briefs"][0]["brief_id"]:
        raise PublicBriefError("latest.json must match the first index entry")
    if not index["briefs"] and latest["actions"]:
        raise PublicBriefError("latest.json cannot contain actions when the index is empty")
    return index


def validate_path(path: Path) -> None:
    if path.is_dir():
        validate_index(load_json(path / "index.json"), path)
    else:
        validate_brief(load_json(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="public brief JSON file or site data directory")
    args = parser.parse_args(argv)
    try:
        validate_path(args.path)
    except PublicBriefError as exc:
        print(f"invalid public brief: {exc}", file=sys.stderr)
        return 2
    print(f"valid public brief: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
