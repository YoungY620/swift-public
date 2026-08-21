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


def adapter_report() -> dict:
    report = load_report()
    report["run"]["account"] = {
        "input": "fixture-account",
        "adapter": "polymarket",
        "identifier": "fixture-account",
    }
    for index, analysis in enumerate(
        report["current_positions"] + report["candidates"], start=1
    ):
        analysis["market"]["event_identifier"] = f"event-{index}"
        analysis["market"]["multi_market_identifier"] = f"condition-{index}"
        analysis["market"]["instrument_identifier"] = f"token-{index}"
    return report


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

    def test_distinct_candidates_cannot_copy_signal_rationale(self) -> None:
        report = load_report()
        duplicate = copy.deepcopy(report["candidates"][0])
        duplicate["market"]["id"] = "candidate-2"
        duplicate["market"]["question"] = "Will a third sample event resolve Yes?"
        duplicate["market"]["url"] = "https://polymarket.com/event/third-sample"
        report["candidates"].append(duplicate)
        with self.assertRaisesRegex(report_tool.ReportError, "signal.rationale duplicates"):
            report_tool.validate_report(report)

    def test_candidate_cannot_use_order_book_recalculation_as_only_reason(self) -> None:
        report = load_report()
        report["candidates"][0]["signal"]["rationale"] = (
            "基于最新订单簿重新计算；未触发可执行交叉或硬门槛，维持现状。"
        )
        with self.assertRaisesRegex(report_tool.ReportError, "generic execution boilerplate"):
            report_tool.validate_report(report)

    def test_distinct_candidates_cannot_copy_evidence_conclusion(self) -> None:
        candidates = [copy.deepcopy(load_report()["candidates"][0]) for _ in range(2)]
        candidates[1]["signal"]["rationale"] = "第三个事件的官方进度支持当前概率，且执行数值满足入场要求。"
        candidates[1]["model"]["conclusion"] = "第三个事件的专属公开记录支持当前概率判断。"
        candidates[1]["evidence"][0]["summary"] = "第三个事件的官方记录给出了独有的进度数据。"
        with self.assertRaisesRegex(report_tool.ReportError, "conclusion duplicates"):
            report_tool.validate_candidate_research_quality(candidates)

    def test_current_position_may_use_reassessment_language(self) -> None:
        report = load_report()
        report["current_positions"][0]["signal"]["rationale"] = (
            "本轮重新评估并基于最新订单簿重新计算，当前持仓继续维持现状。"
        )
        self.assertTrue(report_tool.validate_report(report))

    def test_closed_candidates_may_share_explicit_mechanical_reason(self) -> None:
        candidates = [copy.deepcopy(load_report()["candidates"][0]) for _ in range(2)]
        for candidate in candidates:
            candidate["signal"] = {
                "action": "MAINTAIN",
                "rationale": "市场已关闭且没有 active order book，因此维持空仓。",
            }
            candidate["opportunity"].update(
                {
                    "entry_eligible": False,
                    "market_status": "CLOSED",
                    "reason": "市场已关闭且没有 active order book，因此无法执行。",
                }
            )
            candidate["allocation"].update(
                {"priority": None, "allocated_cash": 0, "cash_after": 80}
            )
            candidate["evidence"][0]["summary"] = "市场元数据显示该市场已关闭。"
            candidate["evidence"][0]["conclusion"] = (
                "closed=true，且没有 active order book，不能提交订单。"
            )
        report_tool.validate_candidate_research_quality(candidates)

    def test_similar_execution_reason_must_use_each_candidates_numbers(self) -> None:
        candidates = [copy.deepcopy(load_report()["candidates"][0]) for _ in range(2)]
        candidates[1]["signal"]["rationale"] = "另一事件的专属证据支持当前概率，并满足买入要求。"
        candidates[1]["model"]["conclusion"] = "另一事件的独立政策证据支持当前概率。"
        candidates[1]["evidence"][0]["summary"] = "另一事件公布了独立的政策进度。"
        candidates[1]["evidence"][0]["conclusion"] = "该进度支持另一事件的因子权重。"
        report_tool.validate_candidate_research_quality(candidates)
        candidates[1]["opportunity"]["executable_price"] = 0.55
        with self.assertRaisesRegex(report_tool.ReportError, "executable_price"):
            report_tool.validate_candidate_research_quality(candidates)

    def test_history_marks_genuinely_new_event_as_first_research(self) -> None:
        report = adapter_report()
        report_tool.validate_candidate_history(report, [])

    def test_existing_event_new_market_cannot_be_called_first_research(self) -> None:
        report = adapter_report()
        candidate = report["candidates"][0]
        candidate["market"]["event_identifier"] = "known-event"
        with tempfile.TemporaryDirectory() as temporary:
            history_path = Path(temporary) / "report.json"
            history_path.write_text(
                json.dumps(
                    {
                        "run": {"account": {"adapter": "polymarket"}},
                        "current_positions": [],
                        "candidates": [
                            {
                                "market": {
                                    "id": "old-child",
                                    "event_identifier": "known-event",
                                    "multi_market_identifier": "old-condition",
                                }
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                report_tool.ReportError, "EXISTING_EVENT_NEW_MARKET"
            ):
                report_tool.validate_candidate_history(report, [history_path])
            candidate["research_novelty"].update(
                {
                    "classification": "EXISTING_EVENT_NEW_MARKET",
                    "event_previously_researched": True,
                }
            )
            report_tool.validate_candidate_history(report, [history_path])

    def test_previously_researched_concrete_market_is_not_a_new_candidate(self) -> None:
        report = adapter_report()
        candidate = report["candidates"][0]
        with tempfile.TemporaryDirectory() as temporary:
            history_path = Path(temporary) / "report.json"
            history_path.write_text(
                json.dumps(
                    {
                        "run": {"account": {"adapter": "polymarket"}},
                        "current_positions": [],
                        "candidates": [
                            {
                                "market": {
                                    "id": "different-report-id",
                                    "event_identifier": "old-event",
                                    "multi_market_identifier": candidate["market"][
                                        "multi_market_identifier"
                                    ],
                                }
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                report_tool.ReportError, "previously-researched market set"
            ):
                report_tool.validate_candidate_history(report, [history_path])

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

    def test_adapter_archive_checks_candidate_history_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate-report.json"
            report = adapter_report()
            source.write_text(json.dumps(report), encoding="utf-8")
            history_dir = root / "archive" / "reports" / "old"
            history_dir.mkdir(parents=True)
            candidate = report["candidates"][0]
            (history_dir / "report.json").write_text(
                json.dumps(
                    {
                        "run": {"account": {"adapter": "polymarket"}},
                        "current_positions": [],
                        "candidates": [
                            {
                                "market": {
                                    "id": "old-id",
                                    "event_identifier": "old-event",
                                    "multi_market_identifier": candidate["market"][
                                        "multi_market_identifier"
                                    ],
                                }
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(report_tool.ReportError, "previously-researched"):
                report_tool.archive_report(source, root / "archive", [])


if __name__ == "__main__":
    unittest.main()
