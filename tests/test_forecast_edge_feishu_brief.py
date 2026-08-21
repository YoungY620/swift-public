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
            "candidates": [{"signal": {"action": action}}],
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
                "### 一、私人操作建议\n\n持有 YES｜示例市场，维持当前仓位。\n\n"
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
                    "reason": reason,
                    "output": output,
                },
            )()
            brief_module.command_fallback(args)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["venues"], [])
            self.assertEqual(result["researched_market_count"], 0)
            self.assertIn("无可执行交易意图", result["markdown"])


if __name__ == "__main__":
    unittest.main()
