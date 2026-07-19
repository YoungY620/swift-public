#!/usr/bin/env python3
"""Validate and evaluate a recursive point-estimate probability tree."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProbabilityTreeError(ValueError):
    """Raised when a probability tree is invalid."""


@dataclass
class Evaluation:
    probability: float
    node_count: int
    deepest_level: int
    audit: list[dict[str, Any]]


def probability(value: Any, path: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProbabilityTreeError(f"{path} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ProbabilityTreeError(f"{path} must be between 0 and 1")
    return number


def evaluate_tree(document: dict[str, Any], max_depth: int = 64, tolerance: float = 1e-9) -> Evaluation:
    root = document.get("root", document)
    if not isinstance(root, dict):
        raise ProbabilityTreeError("root must be an object")
    audit: list[dict[str, Any]] = []
    identifiers: set[str] = set()

    def evaluate(node: dict[str, Any], path: str, depth: int) -> tuple[float, int, int]:
        if depth > max_depth:
            raise ProbabilityTreeError(f"{path} exceeds maximum depth {max_depth}")
        node_type = node.get("type")
        node_id = str(node.get("id", path))
        if "id" in node:
            if node_id in identifiers:
                raise ProbabilityTreeError(f"duplicate node id: {node_id}")
            identifiers.add(node_id)

        count = 1
        deepest = depth
        if node_type == "estimate":
            result = probability(node.get("probability"), f"{path}.probability")
        elif node_type == "complement":
            child = node.get("child")
            if not isinstance(child, dict):
                raise ProbabilityTreeError(f"{path}.child must be an object")
            child_result, child_count, child_depth = evaluate(child, f"{path}.child", depth + 1)
            result = 1.0 - child_result
            count += child_count
            deepest = max(deepest, child_depth)
        elif node_type == "partition":
            branches = node.get("branches")
            if not isinstance(branches, list) or not branches:
                raise ProbabilityTreeError(f"{path}.branches must be a non-empty list")
            total_weight = 0.0
            result = 0.0
            for index, branch in enumerate(branches):
                branch_path = f"{path}.branches[{index}]"
                if not isinstance(branch, dict):
                    raise ProbabilityTreeError(f"{branch_path} must be an object")
                weight = probability(branch.get("weight"), f"{branch_path}.weight")
                child = branch.get("child")
                if child is None and "probability" in branch:
                    child = {"type": "estimate", "probability": branch["probability"]}
                if not isinstance(child, dict):
                    raise ProbabilityTreeError(f"{branch_path}.child must be an object")
                child_result, child_count, child_depth = evaluate(child, f"{branch_path}.child", depth + 1)
                total_weight += weight
                result += weight * child_result
                count += child_count
                deepest = max(deepest, child_depth)
            if abs(total_weight - 1.0) > tolerance:
                raise ProbabilityTreeError(
                    f"{path} branch weights sum to {total_weight:.12g}, expected 1"
                )
        elif node_type == "bayes_update":
            prior_value = node.get("prior")
            if isinstance(prior_value, dict):
                prior, prior_count, prior_depth = evaluate(prior_value, f"{path}.prior", depth + 1)
                count += prior_count
                deepest = max(deepest, prior_depth)
            else:
                prior = probability(prior_value, f"{path}.prior")
            evidence = node.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ProbabilityTreeError(f"{path}.evidence must be a non-empty list")
            result = prior
            for index, item in enumerate(evidence):
                item_path = f"{path}.evidence[{index}]"
                if not isinstance(item, dict):
                    raise ProbabilityTreeError(f"{item_path} must be an object")
                likelihood_true = probability(
                    item.get("likelihood_if_true"), f"{item_path}.likelihood_if_true"
                )
                likelihood_false = probability(
                    item.get("likelihood_if_false"), f"{item_path}.likelihood_if_false"
                )
                denominator = result * likelihood_true + (1.0 - result) * likelihood_false
                if denominator == 0.0:
                    raise ProbabilityTreeError(f"{item_path} has zero evidence probability")
                result = result * likelihood_true / denominator
                count += 1
                deepest = max(deepest, depth + 1)
                audit.append(
                    {
                        "path": item_path,
                        "id": item.get("id", item_path),
                        "type": "evidence_update",
                        "probability": result,
                    }
                )
        else:
            raise ProbabilityTreeError(f"{path}.type is unsupported: {node_type!r}")

        audit.append(
            {
                "path": path,
                "id": node_id,
                "label": node.get("label"),
                "type": node_type,
                "probability": result,
            }
        )
        return result, count, deepest

    result, count, deepest = evaluate(root, "root", 1)
    return Evaluation(result, count, deepest, audit)


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Tree JSON file, or - for stdin")
    parser.add_argument("--max-depth", type=int, default=64)
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = read_json(args.input)
        if not isinstance(document, dict):
            raise ProbabilityTreeError("input must be a JSON object")
        evaluation = evaluate_tree(document, args.max_depth, args.tolerance)
    except (OSError, json.JSONDecodeError, ProbabilityTreeError) as exc:
        print(f"probability_tree: {exc}", file=sys.stderr)
        return 2
    output = {
        "probability": evaluation.probability,
        "node_count": evaluation.node_count,
        "deepest_level": evaluation.deepest_level,
        "audit": evaluation.audit,
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
