#!/usr/bin/env python3
"""Validate, render, archive, and locate immutable forecast-edge reports."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


SIGNALS = {"BUY", "ADD", "HOLD", "SELL"}
EVIDENCE_FIELDS = {
    "source_name",
    "source_type",
    "title",
    "url",
    "published_at",
    "retrieved_at",
    "summary",
    "conclusion",
    "factor_id",
    "credibility",
}


class ReportError(ValueError):
    """Raised when a report violates the forecast-edge report contract."""


def require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(f"{path} must be an object")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReportError(f"{path} must be a list")
    return value


def require_fields(value: dict[str, Any], fields: set[str], path: str) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        raise ReportError(f"{path} is missing fields: {', '.join(missing)}")


def validate_evidence(evidence: Any, path: str, *, current_position: bool) -> None:
    rows = require_list(evidence, path)
    if not rows:
        raise ReportError(f"{path} must contain at least one evidence item")
    for index, raw in enumerate(rows):
        item_path = f"{path}[{index}]"
        item = require_object(raw, item_path)
        require_fields(item, EVIDENCE_FIELDS, item_path)
        if current_position:
            require_fields(item, {"new_since_previous"}, item_path)
            if not isinstance(item["new_since_previous"], bool):
                raise ReportError(f"{item_path}.new_since_previous must be boolean")
            if item["new_since_previous"]:
                impact = require_object(item.get("impact"), f"{item_path}.impact")
                require_fields(
                    impact,
                    {"affected_factor", "factor_probability_change", "final_probability_change"},
                    f"{item_path}.impact",
                )


def validate_analysis(raw: Any, path: str, *, current_position: bool) -> None:
    analysis = require_object(raw, path)
    require_fields(analysis, {"market", "model", "evidence", "signal"}, path)
    market = require_object(analysis["market"], f"{path}.market")
    require_fields(market, {"id", "question", "url", "outcome", "resolution_criteria"}, f"{path}.market")
    model = require_object(analysis["model"], f"{path}.model")
    require_fields(model, {"probability", "model_value", "conclusion"}, f"{path}.model")
    signal = require_object(analysis["signal"], f"{path}.signal")
    require_fields(signal, {"action", "rationale"}, f"{path}.signal")
    if signal["action"] not in SIGNALS:
        raise ReportError(f"{path}.signal.action must be one of {sorted(SIGNALS)}")
    validate_evidence(analysis["evidence"], f"{path}.evidence", current_position=current_position)
    if current_position and "comparison" not in analysis:
        raise ReportError(f"{path}.comparison is required for current positions; use null on the first run")


def validate_report(report: Any) -> dict[str, Any]:
    root = require_object(report, "report")
    require_fields(
        root,
        {
            "schema_version",
            "run",
            "portfolio",
            "current_positions",
            "candidates",
            "actions",
            "summary",
        },
        "report",
    )
    if root["schema_version"] != "1.0":
        raise ReportError("report.schema_version must be '1.0'")
    run = require_object(root["run"], "report.run")
    require_fields(
        run,
        {"id", "started_at", "completed_at", "data_cutoff", "account", "previous_report_ids"},
        "report.run",
    )
    account = require_object(run["account"], "report.run.account")
    require_fields(account, {"input", "address"}, "report.run.account")
    require_list(run["previous_report_ids"], "report.run.previous_report_ids")
    portfolio = require_object(root["portfolio"], "report.portfolio")
    require_fields(
        portfolio,
        {"available_collateral", "estimated_bankroll", "reserve_cash"},
        "report.portfolio",
    )
    current = require_list(root["current_positions"], "report.current_positions")
    candidates = require_list(root["candidates"], "report.candidates")
    for index, item in enumerate(current):
        validate_analysis(item, f"report.current_positions[{index}]", current_position=True)
    for index, item in enumerate(candidates):
        validate_analysis(item, f"report.candidates[{index}]", current_position=False)
    actions = require_list(root["actions"], "report.actions")
    for index, raw in enumerate(actions):
        action = require_object(raw, f"report.actions[{index}]")
        require_fields(
            action,
            {"action", "market_id", "market_question", "outcome", "recommended_cash", "rationale"},
            f"report.actions[{index}]",
        )
        if action["action"] not in SIGNALS:
            raise ReportError(f"report.actions[{index}].action must be one of {sorted(SIGNALS)}")
    if not isinstance(root["summary"], str) or not root["summary"].strip():
        raise ReportError("report.summary must be a non-empty string")
    return root


def markdown_escape(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_evidence(items: list[dict[str, Any]], *, current_position: bool) -> list[str]:
    lines = [
        "| 来源 | 发布时间 | 摘要 | 结论 | 概率因子 | 可信度 |" + (" 新证据 |" if current_position else ""),
        "|---|---|---|---|---|---|" + ("---|" if current_position else ""),
    ]
    for item in items:
        source = f"[{markdown_escape(item['source_name'])}]({item['url']})"
        row = [
            source,
            markdown_escape(item["published_at"]),
            markdown_escape(item["summary"]),
            markdown_escape(item["conclusion"]),
            markdown_escape(item["factor_id"]),
            markdown_escape(item["credibility"]),
        ]
        if current_position:
            row.append("是" if item.get("new_since_previous") else "否")
        lines.append("| " + " | ".join(row) + " |")
        if current_position and item.get("new_since_previous") and item.get("impact"):
            impact = item["impact"]
            lines.append(
                f"\n新增证据影响：`{markdown_escape(impact['affected_factor'])}`；"
                f"子概率变化 {markdown_escape(impact['factor_probability_change'])}；"
                f"最终概率变化 {markdown_escape(impact['final_probability_change'])}。"
            )
    return lines


def render_analysis(item: dict[str, Any], *, current_position: bool) -> list[str]:
    market = item["market"]
    model = item["model"]
    signal = item["signal"]
    lines = [
        f"### [{market['question']}]({market['url']}) — {signal['action']}",
        "",
        f"- 市场：`{market['id']}`；研究方向：{market['outcome']}",
        f"- 最终结算概率：{model['probability']}; 模型价值：{model['model_value']}",
        f"- 结论：{model['conclusion']}",
        f"- 行动理由：{signal['rationale']}",
        f"- 结算标准：{market['resolution_criteria']}",
    ]
    if current_position:
        comparison = item.get("comparison")
        lines.append(f"- 上轮对比：{comparison if comparison is not None else '首次研究，无上一轮报告'}")
    lines.extend(["", "#### 关键证据", ""])
    lines.extend(render_evidence(item["evidence"], current_position=current_position))
    return lines


def render_report(report: dict[str, Any]) -> str:
    run = report["run"]
    portfolio = report["portfolio"]
    lines = [
        "# Forecast Edge 扫描报告",
        "",
        f"- 运行 ID：`{run['id']}`",
        f"- 数据截止：{run['data_cutoff']}",
        f"- 账户：`{run['account']['address']}`",
        f"- 可用抵押资金：{portfolio['available_collateral']}",
        f"- 保守总资金：{portfolio['estimated_bankroll']}",
        f"- 机会储备：{portfolio['reserve_cash']}",
        "",
        "## 摘要",
        "",
        report["summary"],
        "",
        "## 行动",
        "",
        "| 信号 | 市场 | 方向 | 建议金额 | 理由 |",
        "|---|---|---|---:|---|",
    ]
    for action in report["actions"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(action["action"]),
                    markdown_escape(action["market_question"]),
                    markdown_escape(action["outcome"]),
                    markdown_escape(action["recommended_cash"]),
                    markdown_escape(action["rationale"]),
                ]
            )
            + " |"
        )
    if not report["actions"]:
        lines.append("| — | 本轮没有交易信号 | — | 0 | 保持资金闲置或继续持有 |")

    lines.extend(["", "## 当前持仓", ""])
    if report["current_positions"]:
        for item in report["current_positions"]:
            lines.extend(render_analysis(item, current_position=True))
            lines.append("")
    else:
        lines.append("当前没有持仓。")

    lines.extend(["", "## 新增候选", ""])
    if report["candidates"]:
        for item in report["candidates"]:
            lines.extend(render_analysis(item, current_position=False))
            lines.append("")
    else:
        lines.append("本轮没有完成研究的新增候选。")
    return "\n".join(lines).rstrip() + "\n"


def read_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return validate_report(json.load(handle))


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise ReportError("run id cannot be converted to a safe directory name")
    return cleaned


def archive_report(report_path: Path, output_root: Path, snapshots: list[Path]) -> Path:
    report = read_report(report_path)
    timestamp = safe_component(str(report["run"]["completed_at"]).replace(":", "-"))
    run_id = safe_component(str(report["run"]["id"]))
    destination = output_root / "reports" / f"{timestamp}_{run_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise ReportError(f"refusing to modify existing report directory: {destination}") from exc
    try:
        shutil.copy2(report_path, destination / "report.json")
        (destination / "report.md").write_text(render_report(report), encoding="utf-8")
        if snapshots:
            snapshot_dir = destination / "snapshots"
            snapshot_dir.mkdir()
            names: set[str] = set()
            for source in snapshots:
                if source.name in names:
                    raise ReportError(f"duplicate snapshot filename: {source.name}")
                names.add(source.name)
                shutil.copy2(source, snapshot_dir / source.name)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def previous_reports(output_root: Path, limit: int) -> list[dict[str, Any]]:
    reports_root = output_root / "reports"
    if not reports_root.exists():
        return []
    results = []
    for directory in sorted((item for item in reports_root.iterdir() if item.is_dir()), reverse=True):
        report_path = directory / "report.json"
        if not report_path.is_file():
            continue
        try:
            report = read_report(report_path)
        except (OSError, json.JSONDecodeError, ReportError):
            continue
        results.append(
            {
                "directory": str(directory),
                "report_path": str(report_path),
                "run_id": report["run"]["id"],
                "completed_at": report["run"]["completed_at"],
            }
        )
        if len(results) >= limit:
            break
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate report JSON")
    validate.add_argument("report")
    render = subparsers.add_parser("render", help="Render report JSON as Markdown")
    render.add_argument("report")
    render.add_argument("output", help="Output Markdown path, or - for stdout")
    archive = subparsers.add_parser("archive", help="Create a new immutable report directory")
    archive.add_argument("report")
    archive.add_argument("--output-root", required=True)
    archive.add_argument("--snapshot", action="append", default=[])
    previous = subparsers.add_parser("previous", help="Locate recent immutable reports")
    previous.add_argument("--output-root", required=True)
    previous.add_argument("--limit", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            report = read_report(Path(args.report))
            result: Any = {"valid": True, "run_id": report["run"]["id"]}
        elif args.command == "render":
            report = read_report(Path(args.report))
            markdown = render_report(report)
            if args.output == "-":
                sys.stdout.write(markdown)
                return 0
            Path(args.output).write_text(markdown, encoding="utf-8")
            result = {"rendered": str(Path(args.output))}
        elif args.command == "archive":
            destination = archive_report(
                Path(args.report), Path(args.output_root), [Path(path) for path in args.snapshot]
            )
            result = {"archived": str(destination)}
        elif args.command == "previous":
            if args.limit < 1:
                raise ReportError("--limit must be positive")
            result = previous_reports(Path(args.output_root), args.limit)
        else:  # pragma: no cover - argparse enforces a command.
            raise ReportError(f"unsupported command: {args.command}")
    except (OSError, json.JSONDecodeError, ReportError) as exc:
        print(f"report_tool: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
