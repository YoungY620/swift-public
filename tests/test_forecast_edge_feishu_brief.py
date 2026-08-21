from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "forecast_edge_feishu_brief.py"
SCHEMA_PATH = ROOT / "schemas" / "forecast-edge-feishu-brief.schema.json"

spec = importlib.util.spec_from_file_location("forecast_edge_feishu_brief", MODULE_PATH)
assert spec is not None and spec.loader is not None
brief_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief_module)


class ForecastEdgeFeishuBriefTests(unittest.TestCase):
    def write_report(self, directory: Path, *, actionable: bool = False) -> None:
        action = "BUY" if actionable else "MAINTAIN"
        report = {
            "schema_version": "3.0",
            "run": {
                "account": {"adapter": "polymarket"},
                "completed_at": "2026-08-21T03:26:33+00:00",
            },
            "current_positions": [{"signal": {"action": "MAINTAIN"}}],
            "candidates": [
                {
                    "research_novelty": {"classification": "FIRST_RESEARCH"},
                    "signal": {
                        "action": action,
                        "rationale": "该事件的官方进度数据支持模型 P(YES) 为 40%，执行判断采用报告数值。",
                    },
                    "model": {
                        "yes_probability": 0.4,
                        "conclusion": "该事件独有的官方进度与历史基准率支持模型 P(YES) 为 40%。"
                    },
                    "evidence": [
                        {
                            "summary": "官方记录公布了该事件最新的专属进度数据。",
                            "conclusion": "这一进度提高了该事件按期发生的概率。",
                        }
                    ],
                }
            ],
            "actions": [
                {"action": "MAINTAIN", "execution": None},
                {"action": action, "execution": {} if actionable else None},
            ],
        }
        (directory / "report-polymarket.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        (directory / "report-polymarket.md").write_text("# report\n", encoding="utf-8")

    def response(self, *, actionable: int = 0) -> dict[str, object]:
        no_action = "本轮无可执行交易意图。" if actionable == 0 else "本轮有一条买入建议。"
        return {
            "schema_version": "1.0",
            "research_date": "2026-08-21",
            "title": "forecast-edge 扫描简报 2026-08-21",
            "venues": ["polymarket"],
            "current_position_count": 1,
            "researched_market_count": 2,
            "actionable_recommendation_count": actionable,
            "markdown": (
                "## forecast-edge 全量扫描｜2026-08-21（Polymarket）\n\n"
                "**组合概况**：可用抵押资金 **20 pUSD**，保守总资金 **30 pUSD**。\n\n"
                "**研究覆盖**：重新评估 **1 个当前持仓**并深度研究 **1 个新增候选**。\n\n"
                "**本轮结论**：**维持现状**，等待更清晰的机会。\n\n"
                "### 一、私人操作建议\n\n持有 YES｜示例市场，维持当前仓位。\n\n"
                "新增候选（本轮首次研究）（1）\n\n候选 YES｜另一示例市场。\n\n"
                f"### 二、简报\n\n{no_action}\n\n"
                "[盘口链接](https://polymarket.com/event/example)"
            ),
        }

    def test_inventory_counts_all_research_and_actionable_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports, actionable=True)
            self.assertEqual(
                brief_module.report_inventory(reports),
                {
                    "research_date": "2026-08-21",
                    "venues": ["polymarket"],
                    "current_position_count": 1,
                    "researched_market_count": 2,
                    "actionable_recommendation_count": 1,
                },
            )

    def test_inventory_command_writes_authoritative_agent_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            output = root / "source-inventory.json"
            self.write_report(reports, actionable=True)
            args = type(
                "Args",
                (),
                {"reports_dir": reports, "output": output},
            )()
            brief_module.command_inventory(args)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {
                    "research_date": "2026-08-21",
                    "venues": ["polymarket"],
                    "current_position_count": 1,
                    "researched_market_count": 2,
                    "actionable_recommendation_count": 1,
                },
            )

    def test_zero_action_brief_must_explicitly_say_no_executable_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            inventory = brief_module.report_inventory(reports)
            response = self.response()
            response["markdown"] = str(response["markdown"]).replace(
                "本轮无可执行交易意图。", "本轮扫描结束。"
            )
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "no intent is executable"
            ):
                brief_module.validate_brief(response, schema, inventory)

    def test_generic_candidate_rationale_refuses_normal_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            report_path = reports / "report-polymarket.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["candidates"][0]["signal"]["rationale"] = (
                "基于最新订单簿重新计算；未触发可执行交叉或硬门槛，维持现状。"
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "generic signal rationale"
            ):
                brief_module.report_inventory(reports)

    def test_probability_mismatch_refuses_normal_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            report_path = reports / "report-polymarket.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["candidates"][0]["signal"]["rationale"] = (
                "该事件的官方进度数据支持模型 P(YES) 为 58%，"
                "执行判断采用报告数值。"
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "does not match model.yes_probability"
            ):
                brief_module.report_inventory(reports)

    def test_existing_event_child_requires_distinct_brief_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            report_path = reports / "report-polymarket.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["candidates"][0]["research_novelty"]["classification"] = (
                "EXISTING_EVENT_NEW_MARKET"
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")
            response = self.response()
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "既有事件的新子市场"
            ):
                brief_module.validate_candidate_novelty_headings(response, reports)
            response["markdown"] = str(response["markdown"]).replace(
                "新增候选（本轮首次研究）", "既有事件的新子市场"
            )
            brief_module.validate_candidate_novelty_headings(response, reports)

    def test_portfolio_summary_is_formatted_prose_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            inventory = brief_module.report_inventory(reports)
            response = self.response()
            response["markdown"] = str(response["markdown"]).replace(
                "**组合概况**：", "- **组合概况**："
            )
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "must not be a list"
            ):
                brief_module.validate_brief(response, schema, inventory)

    def test_each_summary_line_emphasizes_its_key_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            self.write_report(reports)
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            inventory = brief_module.report_inventory(reports)
            response = self.response()
            response["markdown"] = str(response["markdown"]).replace(
                "**维持现状**", "维持现状"
            )
            with self.assertRaisesRegex(
                brief_module.BriefValidationError, "no emphasized key point"
            ):
                brief_module.validate_brief(response, schema, inventory)

    def test_extract_requires_read_and_subagent_trace_then_validates_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports)
            response = self.response()
            events = [
                {
                    "role": "assistant",
                    "content": "正在读取报告。",
                    "tool_calls": [
                        {"id": "read-1", "function": {"name": "Read", "arguments": "{}"}},
                        {"id": "agent-1", "function": {"name": "Agent", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "read-1", "content": "report"},
                {"role": "tool", "tool_call_id": "agent-1", "content": "inventory"},
                {
                    "role": "assistant",
                    "content": json.dumps(response, ensure_ascii=False),
                    "tool_calls": [],
                },
            ]
            input_path = root / "stream.jsonl"
            output_path = root / "brief.json"
            trace_path = root / "trace.json"
            input_path.write_text(
                "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "schema": SCHEMA_PATH,
                    "reports_dir": reports,
                    "input": input_path,
                    "output": output_path,
                    "trace_output": trace_path,
                },
            )()
            brief_module.command_extract(args)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), response)
            self.assertTrue(
                json.loads(trace_path.read_text(encoding="utf-8"))["agentic_trace_verified"]
            )

    def test_fallback_still_builds_a_notification_when_research_has_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            reason = root / "reason.txt"
            output = root / "brief.json"
            reason.write_text("research failed", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "schema": SCHEMA_PATH,
                    "reports_dir": reports,
                    "research_date": "2026-08-21",
                    "context": None,
                    "reason": reason,
                    "output": output,
                },
            )()
            brief_module.command_fallback(args)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["venues"], [])
            self.assertIsNone(result["current_position_count"])
            self.assertIsNone(result["researched_market_count"])
            self.assertIsNone(result["actionable_recommendation_count"])
            self.assertNotIn("0 个当前持仓", result["markdown"])
            self.assertNotIn("无可执行交易意图", result["markdown"])
            self.assertIn("不能据此声称为零", result["markdown"])
            self.assertIn("Warning", result["markdown"])

    def test_fallback_uses_snapshot_and_baseline_without_claiming_research_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            reason = root / "reason.txt"
            context = root / "fallback-context.json"
            output = root / "brief.json"
            reason.write_text("report validation failed", encoding="utf-8")
            context.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "research_date": "2026-08-21",
                        "venues": ["polymarket"],
                        "account_snapshots": [
                            {
                                "venue": "polymarket",
                                "currency": "pUSD",
                                "available_cash": "134.72",
                                "current_position_count": 14,
                                "positions": [
                                    {
                                        "question": "Will the held policy pass?",
                                        "outcome": "NO",
                                        "quantity": "12.5",
                                        "displayed_value": "4.25",
                                        "url": "https://polymarket.com/event/held-policy",
                                    }
                                ],
                            }
                        ],
                        "latest_validated_baselines": [
                            {
                                "venue": "polymarket",
                                "completed_at": "2026-08-20T03:00:00+00:00",
                                "current_position_count": 13,
                                "candidate_count": 10,
                                "researched_market_count": 23,
                                "actionable_recommendation_count": 4,
                                "currency": "pUSD",
                                "available_collateral": "120",
                                "estimated_bankroll": "190",
                                "market_summaries": [
                                    {
                                        "kind": "CURRENT_POSITION",
                                        "question": "Will the held policy pass?",
                                        "url": "https://polymarket.com/event/held-policy",
                                        "outcome": "NO",
                                        "action": "MAINTAIN",
                                        "research_novelty": None,
                                        "yes_probability": "0.25",
                                        "executable_price": "0.7",
                                        "raw_edge": "0.05",
                                        "net_edge": "0.04",
                                        "recommended_cash": "0",
                                        "recommended_shares": "0",
                                        "research_conclusion": (
                                            "官方投票日程支持 25% 的 YES 概率。"
                                        ),
                                        "execution_reason": "未触发卖出交叉，维持持仓。",
                                    },
                                    {
                                        "kind": "CANDIDATE",
                                        "question": "Will the new bill pass?",
                                        "url": "https://polymarket.com/event/new-bill",
                                        "outcome": "YES",
                                        "action": "BUY",
                                        "research_novelty": "FIRST_RESEARCH",
                                        "yes_probability": "0.6",
                                        "executable_price": "0.4",
                                        "raw_edge": "0.2",
                                        "net_edge": "0.19",
                                        "recommended_cash": "5",
                                        "recommended_shares": "12.5",
                                        "research_conclusion": (
                                            "委员会票数支持 60% 的 YES 概率。"
                                        ),
                                        "execution_reason": (
                                            "费后优势超过硬门槛并获得组合分配。"
                                        ),
                                    },
                                ],
                            }
                        ],
                        "current_validated_reports": [],
                        "progress": {
                            "last_reached_stage": "REPORT_VALIDATION_ATTEMPTED",
                            "restored_historical_report_count": 20,
                            "account_snapshot_count": 1,
                            "research_agent_attempt_count": 1,
                            "report_validation_attempt_count": 1,
                            "generated_report_count": 1,
                            "validated_report_count": 0,
                            "validated_trade_intent_count": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "schema": SCHEMA_PATH,
                    "reports_dir": reports,
                    "research_date": "2026-08-21",
                    "context": context,
                    "reason": reason,
                    "output": output,
                },
            )()
            brief_module.command_fallback(args)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["venues"], ["polymarket"])
            self.assertEqual(result["current_position_count"], 14)
            self.assertIsNone(result["researched_market_count"])
            self.assertIsNone(result["actionable_recommendation_count"])
            self.assertIn("134.72 pUSD", result["markdown"])
            self.assertIn("账户当前持仓 **14 个**", result["markdown"])
            self.assertIn("上一份验证报告基线", result["markdown"])
            self.assertIn("**13 个当前持仓**", result["markdown"])
            self.assertIn("当前账户持仓快照（本轮研究前）", result["markdown"])
            self.assertIn("持有 NO", result["markdown"])
            self.assertIn("份额 12.5", result["markdown"])
            self.assertIn("快照价值 4.25 pUSD", result["markdown"])
            self.assertIn("历史参考，非本轮建议", result["markdown"])
            self.assertIn("Will the new bill pass?", result["markdown"])
            self.assertIn("P(YES) 60%", result["markdown"])
            self.assertIn("历史研究结论：委员会票数支持 60%", result["markdown"])
            self.assertIn("报告校验已运行，但尚未通过", result["markdown"])
            self.assertNotIn("0 个当前持仓", result["markdown"])

    def test_fallback_preserves_real_zero_from_a_validated_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports, actionable=False)
            report_path = reports / "report-polymarket.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["current_positions"] = []
            report["candidates"] = []
            report["actions"] = []
            report_path.write_text(json.dumps(report), encoding="utf-8")
            reason = root / "reason.txt"
            reason.write_text("brief agent unavailable", encoding="utf-8")
            output = root / "brief.json"
            args = type(
                "Args",
                (),
                {
                    "schema": SCHEMA_PATH,
                    "reports_dir": reports,
                    "research_date": "2026-08-21",
                    "context": None,
                    "reason": reason,
                    "output": output,
                },
            )()
            brief_module.command_fallback(args)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["current_position_count"], 0)
            self.assertEqual(result["researched_market_count"], 0)
            self.assertEqual(result["actionable_recommendation_count"], 0)
            self.assertIn("本轮无可执行交易意图", result["markdown"])

    def test_fallback_lists_current_validated_report_when_brief_agent_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            self.write_report(reports, actionable=True)
            reason = root / "reason.txt"
            context = root / "fallback-context.json"
            output = root / "brief.json"
            reason.write_text("brief agent quota unavailable", encoding="utf-8")
            context.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "research_date": "2026-08-21",
                        "venues": ["polymarket"],
                        "account_snapshots": [],
                        "latest_validated_baselines": [],
                        "current_validated_reports": [
                            {
                                "venue": "polymarket",
                                "completed_at": "2026-08-21T03:26:33+00:00",
                                "current_position_count": 1,
                                "candidate_count": 1,
                                "researched_market_count": 2,
                                "actionable_recommendation_count": 1,
                                "market_summaries": [
                                    {
                                        "kind": "CANDIDATE",
                                        "question": "Will the new bill pass?",
                                        "url": "https://polymarket.com/event/new-bill",
                                        "outcome": "YES",
                                        "action": "BUY",
                                        "research_novelty": "FIRST_RESEARCH",
                                        "yes_probability": "0.6",
                                        "executable_price": "0.4",
                                        "raw_edge": "0.2",
                                        "net_edge": "0.19",
                                        "recommended_cash": "5",
                                        "recommended_shares": "12.5",
                                        "research_conclusion": (
                                            "委员会票数支持 60% 的 YES 概率。"
                                        ),
                                        "execution_reason": (
                                            "费后优势超过硬门槛并获得组合分配。"
                                        ),
                                    }
                                ],
                            }
                        ],
                        "progress": {
                            "last_reached_stage": "REPORT_VALIDATED",
                            "restored_historical_report_count": 2,
                            "account_snapshot_count": 1,
                            "research_agent_attempt_count": 1,
                            "report_validation_attempt_count": 1,
                            "generated_report_count": 1,
                            "validated_report_count": 1,
                            "validated_trade_intent_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "schema": SCHEMA_PATH,
                    "reports_dir": reports,
                    "research_date": "2026-08-21",
                    "context": context,
                    "reason": reason,
                    "output": output,
                },
            )()
            brief_module.command_fallback(args)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["current_position_count"], 1)
            self.assertEqual(result["researched_market_count"], 2)
            self.assertEqual(result["actionable_recommendation_count"], 1)
            self.assertIn("本轮已验证报告市场明细", result["markdown"])
            self.assertIn("Will the new bill pass?", result["markdown"])
            self.assertIn("本轮研究结论：委员会票数支持 60%", result["markdown"])
            self.assertNotIn("历史参考，非本轮建议", result["markdown"])


if __name__ == "__main__":
    unittest.main()
