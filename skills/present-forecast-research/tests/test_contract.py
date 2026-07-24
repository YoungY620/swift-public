"""Independent contract checks for one forecast-edge v3 report."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_contract  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "report.json"
UPSTREAM_SCHEMA = ROOT.parent / "forecast-edge" / "references" / "report.schema.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ReportContractTests(unittest.TestCase):
    def test_valid_fixture(self) -> None:
        report = report_contract.validate_report(load_fixture())
        self.assertEqual(report["schema_version"], "3.0")

    def test_only_schema_three_is_accepted(self) -> None:
        report = load_fixture()
        report["schema_version"] = "2.0"
        with self.assertRaisesRegex(report_contract.PresentationContractError, "only report schema 3.0"):
            report_contract.validate_report(report)

    def test_probability_tree_is_recomputed(self) -> None:
        report = load_fixture()
        report["candidates"][0]["model"]["tree"]["root"]["probability"] = 0.71
        with self.assertRaisesRegex(report_contract.PresentationContractError, "yes_probability"):
            report_contract.validate_report(report)

    def test_probability_tree_hash_is_verified(self) -> None:
        report = load_fixture()
        report["candidates"][0]["model"]["calculation"]["tree_sha256"] = "0" * 64
        with self.assertRaisesRegex(report_contract.PresentationContractError, "tree_sha256"):
            report_contract.validate_report(report)

    def test_resolution_review_is_bound_to_market_criteria(self) -> None:
        report = load_fixture()
        report["candidates"][0]["resolution_review"]["criteria_text"] = "Different criteria"
        with self.assertRaisesRegex(report_contract.PresentationContractError, "differs"):
            report_contract.validate_report(report)

    def test_resolution_review_reason_must_be_market_specific(self) -> None:
        report = load_fixture()
        report["candidates"][0]["resolution_review"]["reason"] = report[
            "current_positions"
        ][0]["resolution_review"]["reason"]
        with self.assertRaisesRegex(report_contract.PresentationContractError, "market-specific"):
            report_contract.validate_report(report)

    def test_unknown_and_unreferenced_evidence_are_rejected(self) -> None:
        unknown = load_fixture()
        unknown["candidates"][0]["model"]["tree"]["root"]["evidence_ids"] = ["missing"]
        with self.assertRaisesRegex(report_contract.PresentationContractError, "unknown evidence"):
            report_contract.validate_report(unknown)

        unreferenced = load_fixture()
        extra = copy.deepcopy(unreferenced["candidates"][0]["evidence"][0])
        extra["id"] = "unused-probability"
        extra["roles"] = ["PROBABILITY"]
        unreferenced["candidates"][0]["evidence"].append(extra)
        with self.assertRaisesRegex(report_contract.PresentationContractError, "unreferenced probability"):
            report_contract.validate_report(unreferenced)

    def test_execution_vwap_is_recomputed(self) -> None:
        report = load_fixture()
        report["actions"][1]["execution"]["vwap"] = 0.51
        with self.assertRaisesRegex(report_contract.PresentationContractError, "vwap"):
            report_contract.validate_report(report)

    def test_presenter_schema_copy_matches_upstream(self) -> None:
        self.assertEqual(
            (ROOT / "references" / "report.schema.json").read_bytes(),
            UPSTREAM_SCHEMA.read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
