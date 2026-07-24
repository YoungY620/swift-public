"""Executable examples of the recursive weighted probability-tree contract."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probability_tree  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "probability-tree.json"


class ProbabilityTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_partition_computes_total_probability(self) -> None:
        result = probability_tree.evaluate_tree(self.document)
        self.assertEqual(result.probability, Decimal("0.560000000000"))
        self.assertEqual(result.node_count, 3)
        self.assertEqual(result.deepest_level, 2)
        self.assertEqual(len(result.tree_sha256), 64)

    def test_internal_probability_must_match_children(self) -> None:
        self.document["root"]["probability"] = 0.57
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "child calculation"):
            probability_tree.evaluate_tree(self.document)

    def test_partition_rejects_non_exhaustive_weights(self) -> None:
        self.document["root"]["children"][1]["weight"] = 0.3
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "expected 1"):
            probability_tree.evaluate_tree(self.document)

    def test_leaf_requires_evidence(self) -> None:
        self.document["root"]["children"][0]["node"]["evidence_ids"] = []
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "leaf must reference"):
            probability_tree.evaluate_tree(self.document)

    def test_duplicate_node_or_branch_id_is_rejected(self) -> None:
        children = self.document["root"]["children"]
        children[1]["id"] = children[0]["id"]
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "duplicate tree ID"):
            probability_tree.evaluate_tree(self.document)

    def test_depth_limit_is_a_guardrail(self) -> None:
        nested = copy.deepcopy(self.document["root"])
        nested["id"] = "nested-root"
        nested["children"][0]["id"] = "nested-branch-a"
        nested["children"][1]["id"] = "nested-branch-b"
        nested["children"][0]["node"] = copy.deepcopy(self.document["root"])
        document = {"root": nested}
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "maximum depth"):
            probability_tree.evaluate_tree(document, max_depth=2)


if __name__ == "__main__":
    unittest.main()
