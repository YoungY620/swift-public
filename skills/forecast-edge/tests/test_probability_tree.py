"""Executable examples of recursive total-probability and Bayesian calculations."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probability_tree  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


class ProbabilityTreeTests(unittest.TestCase):
    def test_partition_computes_total_probability(self) -> None:
        document = json.loads((FIXTURES / "probability-tree.json").read_text())
        result = probability_tree.evaluate_tree(document)
        self.assertAlmostEqual(result.probability, 0.56)
        self.assertEqual(result.node_count, 3)

    def test_bayes_update_applies_likelihoods_sequentially(self) -> None:
        document = {
            "type": "bayes_update",
            "prior": 0.2,
            "evidence": [{"likelihood_if_true": 0.9, "likelihood_if_false": 0.1}],
        }
        result = probability_tree.evaluate_tree(document)
        self.assertAlmostEqual(result.probability, 0.6923076923)

    def test_partition_rejects_non_exhaustive_weights(self) -> None:
        document = {
            "type": "partition",
            "branches": [
                {"weight": 0.6, "probability": 0.8},
                {"weight": 0.3, "probability": 0.2},
            ],
        }
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "expected 1"):
            probability_tree.evaluate_tree(document)

    def test_depth_limit_is_a_guardrail(self) -> None:
        document = {
            "type": "complement",
            "child": {"type": "complement", "child": {"type": "estimate", "probability": 0.5}},
        }
        with self.assertRaisesRegex(probability_tree.ProbabilityTreeError, "maximum depth"):
            probability_tree.evaluate_tree(document, max_depth=2)


if __name__ == "__main__":
    unittest.main()
