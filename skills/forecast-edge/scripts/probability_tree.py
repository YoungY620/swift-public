#!/usr/bin/env python3
"""Validate and evaluate a recursive weighted conditional-probability tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any


SCALE = Decimal("0.000000000001")
ONE = Decimal("1.000000000000")
MAX_DEPTH = 64


class ProbabilityTreeError(ValueError):
    """Raised when a probability tree violates the v3 contract."""


@dataclass
class Evaluation:
    probability: Decimal
    node_count: int
    deepest_level: int
    audit: list[dict[str, Any]]
    tree_sha256: str


def quantize(value: Decimal) -> Decimal:
    return value.quantize(SCALE, rounding=ROUND_HALF_EVEN)


def decimal_probability(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise ProbabilityTreeError(f"{path} must be numeric")
    try:
        number = quantize(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ProbabilityTreeError(f"{path} must be numeric") from exc
    if number < 0 or number > ONE:
        raise ProbabilityTreeError(f"{path} must be between 0 and 1")
    return number


def canonical_decimal(value: Decimal) -> str:
    return format(quantize(value), ".12f")


def canonical_tree_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProbabilityTreeError(f"{path} must be a non-empty string")
    return value.strip()


def _require_evidence_ids(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ProbabilityTreeError(f"{path} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        identifier = _require_string(raw, f"{path}[{index}]")
        if identifier in seen:
            raise ProbabilityTreeError(f"{path} contains duplicate evidence ID {identifier}")
        seen.add(identifier)
        result.append(identifier)
    return result


def evaluate_tree(document: dict[str, Any], max_depth: int = MAX_DEPTH) -> Evaluation:
    root = document.get("root", document)
    if not isinstance(root, dict):
        raise ProbabilityTreeError("root must be an object")
    audit: list[dict[str, Any]] = []
    identifiers: set[str] = set()

    def unique_identifier(value: Any, path: str) -> str:
        identifier = _require_string(value, path)
        if identifier in identifiers:
            raise ProbabilityTreeError(f"duplicate tree ID: {identifier}")
        identifiers.add(identifier)
        return identifier

    def evaluate(node: Any, path: str, depth: int) -> tuple[Decimal, int, int]:
        if depth > max_depth:
            raise ProbabilityTreeError(f"{path} exceeds maximum depth {max_depth}")
        if not isinstance(node, dict):
            raise ProbabilityTreeError(f"{path} must be an object")
        node_id = unique_identifier(node.get("id"), f"{path}.id")
        label = _require_string(node.get("label"), f"{path}.label")
        _require_string(node.get("reason"), f"{path}.reason")
        evidence_ids = _require_evidence_ids(node.get("evidence_ids"), f"{path}.evidence_ids")
        declared = decimal_probability(node.get("probability"), f"{path}.probability")
        children = node.get("children")
        if not isinstance(children, list):
            raise ProbabilityTreeError(f"{path}.children must be an array")

        count = 1
        deepest = depth
        if not children:
            if not evidence_ids:
                raise ProbabilityTreeError(f"{path} leaf must reference at least one evidence ID")
            computed = declared
        else:
            if len(children) < 2:
                raise ProbabilityTreeError(f"{path}.children must contain at least two branches")
            total_weight = Decimal(0)
            total = Decimal(0)
            for index, raw_branch in enumerate(children):
                branch_path = f"{path}.children[{index}]"
                if not isinstance(raw_branch, dict):
                    raise ProbabilityTreeError(f"{branch_path} must be an object")
                branch_id = unique_identifier(raw_branch.get("id"), f"{branch_path}.id")
                branch_label = _require_string(raw_branch.get("label"), f"{branch_path}.label")
                _require_string(raw_branch.get("weight_reason"), f"{branch_path}.weight_reason")
                branch_evidence = _require_evidence_ids(
                    raw_branch.get("evidence_ids"), f"{branch_path}.evidence_ids"
                )
                if not branch_evidence:
                    raise ProbabilityTreeError(
                        f"{branch_path} weight must reference at least one evidence ID"
                    )
                weight = decimal_probability(raw_branch.get("weight"), f"{branch_path}.weight")
                child_probability, child_count, child_depth = evaluate(
                    raw_branch.get("node"), f"{branch_path}.node", depth + 1
                )
                total_weight += weight
                total += weight * child_probability
                count += child_count
                deepest = max(deepest, child_depth)
                audit.append(
                    {
                        "path": branch_path,
                        "id": branch_id,
                        "label": branch_label,
                        "kind": "branch",
                        "weight": canonical_decimal(weight),
                        "conditional_probability": canonical_decimal(child_probability),
                    }
                )
            if quantize(total_weight) != ONE:
                raise ProbabilityTreeError(
                    f"{path}.children weights sum to {canonical_decimal(total_weight)}, expected 1.000000000000"
                )
            computed = quantize(total)
            if declared != computed:
                raise ProbabilityTreeError(
                    f"{path}.probability {canonical_decimal(declared)} does not match child calculation "
                    f"{canonical_decimal(computed)}"
                )

        audit.append(
            {
                "path": path,
                "id": node_id,
                "label": label,
                "kind": "leaf" if not children else "partition",
                "probability": canonical_decimal(computed),
            }
        )
        return computed, count, deepest

    result, count, deepest = evaluate(root, "root", 1)
    tree_document = document if "root" in document else {"root": document}
    return Evaluation(result, count, deepest, audit, canonical_tree_sha256(tree_document))


def evaluation_document(evaluation: Evaluation) -> dict[str, Any]:
    return {
        "engine_version": "3.0",
        "tree_sha256": evaluation.tree_sha256,
        "root_probability": canonical_decimal(evaluation.probability),
        "node_count": evaluation.node_count,
        "deepest_level": evaluation.deepest_level,
        "audit": evaluation.audit,
    }


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Tree JSON file, or - for stdin")
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = read_json(args.input)
        if not isinstance(document, dict):
            raise ProbabilityTreeError("input must be a JSON object")
        output = evaluation_document(evaluate_tree(document, args.max_depth))
    except (OSError, json.JSONDecodeError, ProbabilityTreeError) as exc:
        print(f"probability_tree: {exc}", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
