"""Extract and validate an agent-generated Forecast Edge Feishu briefing."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast_edge_audit import (  # noqa: E402
    AuditValidationError,
    extract_agent_stream,
    reject_secret_like_text,
)


ACTIONABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}


class BriefValidationError(ValueError):
    pass


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BriefValidationError(f"could not read {label}: {exc}") from exc


def report_inventory(
    reports_dir: Path, *, allow_empty: bool = False, research_date: str | None = None
) -> dict[str, Any]:
    paths = sorted(reports_dir.glob("report-*.json"))
    if not paths:
        if not allow_empty or research_date is None:
            raise BriefValidationError("no report JSON files were supplied")
        try:
            parsed_date = datetime.fromisoformat(research_date).date().isoformat()
        except ValueError as exc:
            raise BriefValidationError("fallback research date is invalid") from exc
        return {
            "research_date": parsed_date,
            "venues": [],
            "current_position_count": 0,
            "researched_market_count": 0,
            "actionable_recommendation_count": 0,
        }
    venues: list[str] = []
    dates: set[str] = set()
    current_position_count = 0
    researched_market_count = 0
    actionable_recommendation_count = 0
    for path in paths:
        report = load_json(path, str(path))
        if not isinstance(report, dict):
            raise BriefValidationError(f"report is not an object: {path}")
        run = report.get("run")
        account = run.get("account") if isinstance(run, dict) else None
        venue = account.get("adapter") if isinstance(account, dict) else None
        completed_at = run.get("completed_at") if isinstance(run, dict) else None
        current = report.get("current_positions")
        candidates = report.get("candidates")
        actions = report.get("actions")
        if not isinstance(venue, str) or not venue:
            raise BriefValidationError(f"report has no venue adapter: {path}")
        if not isinstance(completed_at, str):
            raise BriefValidationError(f"report has no completion time: {path}")
        try:
            report_date = datetime.fromisoformat(completed_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError as exc:
            raise BriefValidationError(f"report has invalid completion time: {path}") from exc
        if not isinstance(current, list) or not isinstance(candidates, list) or not isinstance(actions, list):
            raise BriefValidationError(f"report analysis collections are malformed: {path}")
        venues.append(venue)
        dates.add(report_date)
        current_position_count += len(current)
        researched_market_count += len(current) + len(candidates)
        actionable_recommendation_count += sum(
            1
            for action in actions
            if isinstance(action, dict) and action.get("action") in ACTIONABLE_ACTIONS
        )
    if len(venues) != len(set(venues)):
        raise BriefValidationError("duplicate venue reports were supplied")
    if len(dates) != 1:
        raise BriefValidationError("report completion dates do not agree")
    return {
        "research_date": next(iter(dates)),
        "venues": sorted(venues),
        "current_position_count": current_position_count,
        "researched_market_count": researched_market_count,
        "actionable_recommendation_count": actionable_recommendation_count,
    }


def validate_brief(document: Any, schema: dict[str, Any], inventory: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "root"
        raise BriefValidationError(f"brief schema error at {location}")
    if not isinstance(document, dict):
        raise BriefValidationError("brief is not an object")
    try:
        reject_secret_like_text(document)
    except AuditValidationError as exc:
        raise BriefValidationError(str(exc)) from exc
    for field, expected in inventory.items():
        if document.get(field) != expected:
            raise BriefValidationError(f"brief {field} does not match source reports")
    expected_title = f"forecast-edge 扫描简报 {inventory['research_date']}"
    if document.get("title") != expected_title:
        raise BriefValidationError("brief title does not match the report completion date")
    markdown = str(document.get("markdown", ""))
    summary_end = markdown.find("### 一、私人操作建议")
    summary = markdown[:summary_end] if summary_end >= 0 else markdown
    for label in ("**组合概况**", "**研究覆盖**", "**本轮结论**"):
        if label not in summary:
            raise BriefValidationError(f"brief is missing formatted summary {label}")
        summary_line = next(line for line in summary.splitlines() if label in line)
        emphasized_content = summary_line.replace(label, "", 1)
        if re.search(r"\*\*[^*\r\n]+\*\*", emphasized_content) is None:
            raise BriefValidationError(f"brief summary {label} has no emphasized key point")
    if any(line.lstrip().startswith(("- ", "* ")) for line in summary.splitlines()[1:]):
        raise BriefValidationError("formatted portfolio summary must not be a list")
    for heading in ("一、私人操作建议", "二、简报"):
        if heading not in markdown:
            raise BriefValidationError(f"brief is missing section {heading}")
    if inventory["actionable_recommendation_count"] == 0 and "无可执行交易意图" not in markdown:
        raise BriefValidationError("zero-action briefing does not say that no intent is executable")


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command_extract(args: argparse.Namespace) -> None:
    schema = load_json(args.schema, "brief schema")
    inventory = report_inventory(args.reports_dir)
    try:
        response, trace = extract_agent_stream(args.input.read_text(encoding="utf-8"))
    except (OSError, AuditValidationError) as exc:
        raise BriefValidationError(str(exc)) from exc
    validate_brief(response, schema, inventory)
    write_json(args.output, response)
    write_json(args.trace_output, trace)


def command_inventory(args: argparse.Namespace) -> None:
    write_json(args.output, report_inventory(args.reports_dir))


def command_fallback(args: argparse.Namespace) -> None:
    inventory = report_inventory(
        args.reports_dir, allow_empty=True, research_date=args.research_date
    )
    reason = args.reason.read_text(encoding="utf-8").strip()[:500]
    venue_text = "、".join(inventory["venues"]) or "未完成报告"
    document = {
        "schema_version": "1.0",
        **inventory,
        "title": f"forecast-edge 扫描简报 {inventory['research_date']}",
        "markdown": (
            f"## forecast-edge 全量扫描｜{inventory['research_date']}（{venue_text}）\n\n"
            "**组合概况**：Agent 简报生成失败，**无法安全复述资金数据**。\n\n"
            f"**研究覆盖**：本轮报告覆盖 **{inventory['current_position_count']} 个当前持仓**、"
            f"**{max(inventory['researched_market_count'] - inventory['current_position_count'], 0)} 个新增候选**。\n\n"
            "**本轮结论**：**未取得可验证的 Agent 研究结论**，请查看完整报告和下方异常详情。\n\n"
            "### 一、私人操作建议\n\n"
            "Agent 简报生成失败，本卡未复述未经 Agent 核验的研究结论；请打开父流程 Release 查看完整报告。\n\n"
            "### 二、简报\n\n"
            f"本轮报告覆盖 {inventory['current_position_count']} 个当前持仓、"
            f"{inventory['researched_market_count']} 个研究市场。"
            + ("本轮无可执行交易意图。" if inventory["actionable_recommendation_count"] == 0 else
               f"报告含 {inventory['actionable_recommendation_count']} 条可操作建议。")
            + f"\n\nAgent 错误：{reason or 'unknown'}"
        ),
    }
    schema = load_json(args.schema, "brief schema")
    validate_brief(document, schema, inventory)
    write_json(args.output, document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract = subparsers.add_parser("extract")
    extract.add_argument("--input", type=Path, required=True)
    extract.add_argument("--schema", type=Path, required=True)
    extract.add_argument("--reports-dir", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--trace-output", type=Path, required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--reports-dir", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    fallback = subparsers.add_parser("fallback")
    fallback.add_argument("--reason", type=Path, required=True)
    fallback.add_argument("--schema", type=Path, required=True)
    fallback.add_argument("--reports-dir", type=Path, required=True)
    fallback.add_argument("--research-date")
    fallback.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "extract":
            command_extract(args)
        elif args.command == "inventory":
            command_inventory(args)
        else:
            command_fallback(args)
    except BriefValidationError as exc:
        print(f"forecast_edge_feishu_brief: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
