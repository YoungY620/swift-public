"""Executable examples for the v3 immutable report contract."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probability_tree  # noqa: E402
import report_tool  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "report.json"


def load_report() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ReportToolTests(unittest.TestCase):
    def test_valid_v3_report_renders_actions_and_evidence(self) -> None:
        report = report_tool.validate_report(load_report())
        markdown = report_tool.render_report(report)
        self.assertIn("买入（BUY）", markdown)
        self.assertIn("维持现状（MAINTAIN）", markdown)
        self.assertIn("本轮份额", markdown)
        self.assertIn("市场 YES 概率", markdown)
        self.assertIn("结算审查：PASS", markdown)
        self.assertIn("Example Statistics Office", markdown)

    def test_v3_report_rejects_non_chinese_narrative(self) -> None:
        report = load_report()
        report["summary"] = "Maintain one position and buy one candidate."
        with self.assertRaisesRegex(report_tool.ReportError, "Simplified-Chinese"):
            report_tool.validate_report(report)

    def test_adapter_report_requires_venue_native_routing_identifiers(self) -> None:
        report = load_report()
        report["run"]["account"] = {
            "input": "fixture-account",
            "adapter": "kalshi",
            "identifier": "fixture-account",
        }
        for index, analysis in enumerate(
            report["current_positions"] + report["candidates"], start=1
        ):
            analysis["market"]["event_identifier"] = f"KX-EVENT-{index}"
            analysis["market"]["multi_market_identifier"] = f"KX-MARKET-{index}"
            analysis["market"]["instrument_identifier"] = f"KX-CONTRACT-{index}"
            analysis["market"]["url"] = f"https://example.com/markets/{index}"

        self.assertEqual(report_tool.validate_report(report)["run"]["account"]["adapter"], "kalshi")

        del report["candidates"][0]["market"]["instrument_identifier"]
        with self.assertRaisesRegex(report_tool.ReportError, "instrument_identifier"):
            report_tool.validate_report(report)

    def test_current_position_requires_previous_comparison_field(self) -> None:
        report = load_report()
        del report["current_positions"][0]["comparison"]
        with self.assertRaisesRegex(report_tool.ReportError, "comparison"):
            report_tool.validate_report(report)

    def test_embedded_tree_is_recomputed(self) -> None:
        report = load_report()
        report["candidates"][0]["model"]["tree"]["root"]["probability"] = 0.71
        with self.assertRaisesRegex(report_tool.ReportError, "yes_probability"):
            report_tool.validate_report(report)

    def test_tree_hash_binds_calculation_to_recorded_tree(self) -> None:
        report = load_report()
        report["candidates"][0]["model"]["calculation"]["tree_sha256"] = "0" * 64
        with self.assertRaisesRegex(report_tool.ReportError, "tree_sha256"):
            report_tool.validate_report(report)

    def test_unknown_tree_evidence_is_rejected(self) -> None:
        report = load_report()
        report["candidates"][0]["model"]["tree"]["root"]["evidence_ids"] = ["missing"]
        with self.assertRaisesRegex(report_tool.ReportError, "unknown evidence"):
            report_tool.validate_report(report)

    def test_unreferenced_probability_evidence_is_rejected(self) -> None:
        report = load_report()
        extra = copy.deepcopy(report["candidates"][0]["evidence"][0])
        extra["id"] = "unused-probability"
        extra["roles"] = ["PROBABILITY"]
        report["candidates"][0]["evidence"].append(extra)
        with self.assertRaisesRegex(report_tool.ReportError, "not referenced by the tree"):
            report_tool.validate_report(report)

    def test_resolution_review_must_pass(self) -> None:
        report = load_report()
        report["candidates"][0]["resolution_review"]["status"] = "FAIL"
        with self.assertRaisesRegex(report_tool.ReportError, "must be PASS"):
            report_tool.validate_report(report)

    def test_resolution_review_requires_specific_complete_reasoning(self) -> None:
        report = load_report()
        report["candidates"][0]["resolution_review"]["resolution_source"] = ""
        with self.assertRaisesRegex(report_tool.ReportError, "resolution_source"):
            report_tool.validate_report(report)

        report = load_report()
        report["candidates"][0]["resolution_review"]["reason"] = report[
            "current_positions"
        ][0]["resolution_review"]["reason"]
        with self.assertRaisesRegex(report_tool.ReportError, "market-specific"):
            report_tool.validate_report(report)

    def test_resolution_review_is_bound_to_the_market_criteria(self) -> None:
        report = load_report()
        report["candidates"][0]["resolution_review"]["criteria_text"] = "Different criteria"
        with self.assertRaisesRegex(report_tool.ReportError, "differs"):
            report_tool.validate_report(report)

    def test_execution_fill_values_are_recomputed(self) -> None:
        report = load_report()
        report["actions"][1]["execution"]["vwap"] = 0.51
        with self.assertRaisesRegex(report_tool.ReportError, "vwap"):
            report_tool.validate_report(report)

    def test_action_delta_must_match_execution(self) -> None:
        report = load_report()
        report["actions"][1]["recommended_cash"] = 24
        with self.assertRaisesRegex(report_tool.ReportError, "recommended_cash"):
            report_tool.validate_report(report)

    def test_hard_ten_point_entry_floor_has_no_override(self) -> None:
        report = load_report()
        report["candidates"][0]["opportunity"]["raw_edge"] = 0.1
        with self.assertRaisesRegex(report_tool.ReportError, "above 10pp"):
            report_tool.validate_report(report)

    def test_rejects_allocation_that_does_not_continue_shared_cash_chain(self) -> None:
        report = load_report()
        report["candidates"][0]["allocation"]["cash_before"] = 70
        report["candidates"][0]["allocation"]["cash_after"] = 45
        with self.assertRaisesRegex(report_tool.ReportError, "shared cash chain"):
            report_tool.validate_report(report)

    def test_rejects_nominal_funded_action(self) -> None:
        report = load_report()
        report["actions"][1]["rationale"] = "建议使用理论金额执行本轮买入。"
        with self.assertRaisesRegex(report_tool.ReportError, "nominal or theoretical"):
            report_tool.validate_report(report)

    def test_requires_final_action_for_every_analysis(self) -> None:
        report = load_report()
        report["actions"] = report["actions"][1:]
        with self.assertRaisesRegex(report_tool.ReportError, "missing final actions"):
            report_tool.validate_report(report)

    def test_model_value_is_yes_probability_complement_for_no(self) -> None:
        report = load_report()
        analysis = report["candidates"][0]
        analysis["market"]["outcome"] = "NO"
        analysis["model"]["model_value"] = 0.3
        analysis["opportunity"]["model_value"] = 0.3
        report["actions"][1]["outcome"] = "NO"
        execution = report["actions"][1]["execution"]
        execution["net_edge"] = -0.2
        analysis["opportunity"]["net_edge"] = -0.2
        with self.assertRaisesRegex(report_tool.ReportError, "net_edge"):
            report_tool.validate_report(report)

    def test_archive_is_immutable_and_previous_finds_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            first = report_tool.archive_report(FIXTURE, output_root, [])
            self.assertTrue((first / "report.json").is_file())
            self.assertTrue((first / "report.md").is_file())
            with self.assertRaisesRegex(report_tool.ReportError, "refusing to modify"):
                report_tool.archive_report(FIXTURE, output_root, [])
            previous = report_tool.previous_reports(output_root, 2)
            self.assertEqual(previous[0]["run_id"], "sample-run")


if __name__ == "__main__":
    unittest.main()
