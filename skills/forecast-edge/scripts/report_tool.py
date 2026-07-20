#!/usr/bin/env python3
"""Validate, render, archive, and locate immutable forecast-edge reports."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any


SIGNALS = {"BUY", "ADD", "HOLD", "SELL"}
SIGNAL_LABELS = {"BUY": "买入", "ADD": "加仓", "HOLD": "持有", "SELL": "卖出"}
OUTCOME_LABELS = {"YES": "是", "NO": "否"}
CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
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


def require_chinese_text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not CHINESE_RE.search(value):
        raise ReportError(f"{path} must contain Simplified-Chinese narrative text")


def finite_number(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise ReportError(f"{path} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReportError(f"{path} must be numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise ReportError(f"{path} must be finite and at least {minimum}")
    return result


def approximately_equal(left: float, right: float, tolerance: float = 1e-7) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


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


def validate_analysis(raw: Any, path: str, *, current_position: bool, schema_version: str) -> None:
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
    if schema_version in {"2.0", "2.1"}:
        require_chinese_text(model["conclusion"], f"{path}.model.conclusion")
        require_chinese_text(signal["rationale"], f"{path}.signal.rationale")
        for index, evidence in enumerate(analysis["evidence"]):
            for field in ("summary", "conclusion", "credibility"):
                require_chinese_text(evidence[field], f"{path}.evidence[{index}].{field}")
        require_fields(analysis, {"opportunity", "allocation"}, path)
        opportunity = require_object(analysis["opportunity"], f"{path}.opportunity")
        require_fields(
            opportunity,
            {"entry_eligible", "exit_eligible", "model_value", "executable_price", "net_edge", "reason"},
            f"{path}.opportunity",
        )
        if not isinstance(opportunity["entry_eligible"], bool) or not isinstance(
            opportunity["exit_eligible"], bool
        ):
            raise ReportError(f"{path}.opportunity eligibility fields must be boolean")
        finite_number(opportunity["model_value"], f"{path}.opportunity.model_value")
        finite_number(opportunity["executable_price"], f"{path}.opportunity.executable_price")
        finite_number(opportunity["net_edge"], f"{path}.opportunity.net_edge")
        if schema_version == "2.1":
            require_fields(
                opportunity,
                {
                    "raw_edge",
                    "soft_minimum_raw_edge",
                    "soft_edge_pass",
                    "soft_edge_override_reason",
                },
                f"{path}.opportunity",
            )
            raw_edge = finite_number(
                opportunity["raw_edge"], f"{path}.opportunity.raw_edge", minimum=-1.0
            )
            soft_minimum = finite_number(
                opportunity["soft_minimum_raw_edge"],
                f"{path}.opportunity.soft_minimum_raw_edge",
            )
            if not approximately_equal(soft_minimum, 0.10):
                raise ReportError(f"{path}.opportunity.soft_minimum_raw_edge must equal 0.10")
            soft_edge_pass = opportunity["soft_edge_pass"]
            if not isinstance(soft_edge_pass, bool):
                raise ReportError(f"{path}.opportunity.soft_edge_pass must be boolean")
            expected_soft_pass = raw_edge > soft_minimum + 1e-12
            if soft_edge_pass != expected_soft_pass:
                raise ReportError(
                    f"{path}.opportunity.soft_edge_pass must match raw_edge > soft_minimum_raw_edge"
                )
            override_reason = opportunity["soft_edge_override_reason"]
            if override_reason is not None:
                require_chinese_text(
                    override_reason, f"{path}.opportunity.soft_edge_override_reason"
                )
            if opportunity["entry_eligible"] and not soft_edge_pass and override_reason is None:
                raise ReportError(
                    f"{path}.opportunity requires a soft-edge override reason below or at 10pp"
                )
        allocation = require_object(analysis["allocation"], f"{path}.allocation")
        require_fields(
            allocation,
            {
                "priority",
                "standalone_kelly_cash",
                "orderbook_capacity_cash",
                "portfolio_cash_cap",
                "minimum_order_cash",
                "cash_before",
                "allocated_cash",
                "cash_after",
                "reason",
            },
            f"{path}.allocation",
        )
        priority = allocation["priority"]
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int) or priority < 0
        ):
            raise ReportError(f"{path}.allocation.priority must be a non-negative integer or null")
        for field in (
            "standalone_kelly_cash",
            "orderbook_capacity_cash",
            "minimum_order_cash",
            "cash_before",
            "allocated_cash",
            "cash_after",
        ):
            finite_number(allocation[field], f"{path}.allocation.{field}")
        if allocation["portfolio_cash_cap"] is not None:
            finite_number(allocation["portfolio_cash_cap"], f"{path}.allocation.portfolio_cash_cap")
        before = float(allocation["cash_before"])
        allocated = float(allocation["allocated_cash"])
        after = float(allocation["cash_after"])
        if not approximately_equal(before - allocated, after):
            raise ReportError(f"{path}.allocation cash_before - allocated_cash must equal cash_after")


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
    schema_version = str(root["schema_version"])
    if schema_version not in {"1.0", "2.0", "2.1"}:
        raise ReportError("report.schema_version must be '1.0', '2.0', or '2.1'")
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
    portfolio_fields = {"available_collateral", "estimated_bankroll", "reserve_cash"}
    if schema_version in {"2.0", "2.1"}:
        portfolio_fields.update(
            {
                "holdings_liquidation_value",
                "deployable_cash",
                "allocated_cash",
                "remaining_cash",
                "cold_start",
                "new_position_limit",
                "diversification_summary",
            }
        )
    require_fields(portfolio, portfolio_fields, "report.portfolio")
    current = require_list(root["current_positions"], "report.current_positions")
    candidates = require_list(root["candidates"], "report.candidates")
    for index, item in enumerate(current):
        validate_analysis(
            item,
            f"report.current_positions[{index}]",
            current_position=True,
            schema_version=schema_version,
        )
    for index, item in enumerate(candidates):
        validate_analysis(
            item,
            f"report.candidates[{index}]",
            current_position=False,
            schema_version=schema_version,
        )
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
    if schema_version in {"2.0", "2.1"}:
        require_chinese_text(root["summary"], "report.summary")
        for index, action in enumerate(actions):
            require_chinese_text(action["rationale"], f"report.actions[{index}].rationale")
        validate_portfolio_consistency(root)
    return root


def validate_portfolio_consistency(root: dict[str, Any]) -> None:
    portfolio = root["portfolio"]
    available = finite_number(portfolio["available_collateral"], "report.portfolio.available_collateral")
    holdings = finite_number(
        portfolio["holdings_liquidation_value"], "report.portfolio.holdings_liquidation_value"
    )
    bankroll = finite_number(portfolio["estimated_bankroll"], "report.portfolio.estimated_bankroll")
    reserve = finite_number(portfolio["reserve_cash"], "report.portfolio.reserve_cash")
    deployable = finite_number(portfolio["deployable_cash"], "report.portfolio.deployable_cash")
    allocated = finite_number(portfolio["allocated_cash"], "report.portfolio.allocated_cash")
    remaining = finite_number(portfolio["remaining_cash"], "report.portfolio.remaining_cash")
    if not approximately_equal(bankroll, available + holdings):
        raise ReportError("portfolio bankroll must equal available collateral plus holdings liquidation value")
    if not approximately_equal(available, reserve + deployable):
        raise ReportError("available collateral must equal reserve cash plus deployable cash")
    if not approximately_equal(deployable, allocated + remaining):
        raise ReportError("deployable cash must equal allocated cash plus remaining cash")
    if not isinstance(portfolio["cold_start"], bool):
        raise ReportError("report.portfolio.cold_start must be boolean")
    new_position_limit = portfolio["new_position_limit"]
    if isinstance(new_position_limit, bool) or not isinstance(new_position_limit, int):
        raise ReportError("report.portfolio.new_position_limit must be an integer")
    if new_position_limit < 0:
        raise ReportError("report.portfolio.new_position_limit cannot be negative")
    if not isinstance(portfolio["diversification_summary"], str) or not portfolio[
        "diversification_summary"
    ].strip():
        raise ReportError("report.portfolio.diversification_summary must be non-empty")
    require_chinese_text(
        portfolio["diversification_summary"], "report.portfolio.diversification_summary"
    )

    current_market_ids = {str(analysis["market"]["id"]) for analysis in root["current_positions"]}
    candidate_market_ids = {str(analysis["market"]["id"]) for analysis in root["candidates"]}
    analyses = root["current_positions"] + root["candidates"]
    analysis_by_market: dict[str, dict[str, Any]] = {}
    prioritized_allocations: list[tuple[int, str, dict[str, Any]]] = []
    allocation_priorities: set[int] = set()
    for index, analysis in enumerate(analyses):
        market_id = str(analysis["market"]["id"])
        if market_id in analysis_by_market:
            raise ReportError(f"duplicate analyzed market id: {market_id}")
        analysis_by_market[market_id] = analysis
        sizing = analysis.get("sizing")
        if isinstance(sizing, dict) and isinstance(sizing.get("cash"), dict):
            sizing_bankroll = finite_number(
                sizing["cash"].get("bankroll"), f"analysis[{index}].sizing.cash.bankroll"
            )
            sizing_available = finite_number(
                sizing["cash"].get("available"), f"analysis[{index}].sizing.cash.available"
            )
            if not approximately_equal(sizing_bankroll, bankroll):
                raise ReportError(f"market {market_id} sizing bankroll differs from portfolio bankroll")
            if not approximately_equal(sizing_available, available):
                raise ReportError(f"market {market_id} sizing available cash differs from portfolio cash")
        allocation = analysis["allocation"]
        priority = allocation["priority"]
        allocated_for_market = finite_number(
            allocation["allocated_cash"], f"market {market_id} allocation"
        )
        if priority is None:
            if allocated_for_market > 1e-9:
                raise ReportError(f"funded allocation for market {market_id} requires a priority")
        else:
            if priority in allocation_priorities:
                raise ReportError(f"duplicate allocation priority: {priority}")
            allocation_priorities.add(priority)
            prioritized_allocations.append((priority, market_id, allocation))

    cash_cursor = deployable
    for _, market_id, allocation in sorted(prioritized_allocations):
        before = finite_number(allocation["cash_before"], f"market {market_id} cash_before")
        after = finite_number(allocation["cash_after"], f"market {market_id} cash_after")
        if not approximately_equal(before, cash_cursor):
            raise ReportError(f"market {market_id} allocation does not continue the shared cash chain")
        cash_cursor = after
    if not approximately_equal(cash_cursor, remaining):
        raise ReportError("portfolio remaining cash does not match the allocation cash chain")

    funded_total = 0.0
    funded_buys = 0
    action_markets: set[str] = set()
    forbidden_nominal = re.compile(r"theoretical|nominal|reference bankroll|参考资金|理论金额", re.I)
    for index, action in enumerate(root["actions"]):
        market_id = str(action["market_id"])
        if market_id in action_markets:
            raise ReportError(f"duplicate action market id: {market_id}")
        action_markets.add(market_id)
        if market_id not in analysis_by_market:
            raise ReportError(f"action market {market_id} has no matching analysis")
        analysis = analysis_by_market[market_id]
        if action["action"] != analysis["signal"]["action"]:
            raise ReportError(f"action for market {market_id} differs from analysis signal")
        if market_id in current_market_ids and action["action"] == "BUY":
            raise ReportError(f"current position {market_id} cannot use BUY; use ADD")
        if market_id in candidate_market_ids and action["action"] in {"ADD", "SELL"}:
            raise ReportError(f"new candidate {market_id} cannot use {action['action']}")
        amount = finite_number(action["recommended_cash"], f"report.actions[{index}].recommended_cash")
        allocated_for_market = finite_number(
            analysis["allocation"]["allocated_cash"], f"market {market_id} allocation"
        )
        if action["action"] in {"BUY", "ADD"}:
            if amount <= 0:
                raise ReportError(f"funded {action['action']} for market {market_id} must be positive")
            if not analysis["opportunity"]["entry_eligible"]:
                raise ReportError(f"funded entry for market {market_id} is not entry eligible")
            if not approximately_equal(amount, allocated_for_market):
                raise ReportError(f"action amount for market {market_id} differs from portfolio allocation")
            if forbidden_nominal.search(str(action.get("rationale", ""))):
                raise ReportError(f"funded action for market {market_id} uses nominal or theoretical cash")
            funded_total += amount
            if action["action"] == "BUY":
                funded_buys += 1
        else:
            if amount > 1e-9 or allocated_for_market > 1e-9:
                raise ReportError(f"non-entry action for market {market_id} must allocate zero cash")
    if not approximately_equal(funded_total, allocated):
        raise ReportError("sum of funded BUY and ADD actions must equal portfolio allocated cash")
    if available <= 1e-9 and funded_total > 1e-9:
        raise ReportError("zero available collateral cannot fund BUY or ADD actions")
    if portfolio["cold_start"] and funded_buys > new_position_limit:
        raise ReportError("cold-start funded BUY count exceeds the configured limit")
    missing_actions = sorted(set(analysis_by_market) - action_markets)
    if missing_actions:
        raise ReportError(f"analyzed markets missing final actions: {', '.join(missing_actions)}")


def markdown_escape(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def localized_enum(value: Any, labels: dict[str, str]) -> str:
    machine_value = str(value)
    label = labels.get(machine_value)
    return f"{label}（{machine_value}）" if label else machine_value


def render_comparison(comparison: Any) -> str:
    if comparison is None:
        return "首次研究，无上一轮报告"
    if not isinstance(comparison, dict):
        return markdown_escape(comparison)
    labels = {
        "previous_report_id": "上一轮报告",
        "probability_change": "概率变化",
        "price_change": "价格变化",
        "action_change": "操作变化",
        "reason": "变化原因",
    }
    parts = [
        f"{labels.get(key, key)}：{markdown_escape(value)}"
        for key, value in comparison.items()
    ]
    return "；".join(parts) if parts else "无可比字段"


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
        f"### [{market['question']}]({market['url']}) — {localized_enum(signal['action'], SIGNAL_LABELS)}",
        "",
        f"- 市场：`{market['id']}`；研究方向：{localized_enum(market['outcome'], OUTCOME_LABELS)}",
        f"- 最终结算概率：{model['probability']}；模型价值：{model['model_value']}",
        f"- 结论：{model['conclusion']}",
        f"- 行动理由：{signal['rationale']}",
        f"- 结算标准（原文）：{market['resolution_criteria']}",
    ]
    if current_position:
        lines.append(f"- 上轮对比：{render_comparison(item.get('comparison'))}")
    opportunity = item.get("opportunity")
    if isinstance(opportunity, dict) and "soft_edge_pass" in opportunity:
        soft_status = "超过" if opportunity["soft_edge_pass"] else "未超过"
        soft_line = (
            f"- 入场软门槛：原始优势 {float(opportunity['raw_edge']) * 100:.2f}pp；"
            f"{soft_status} 10pp。"
        )
        if opportunity.get("soft_edge_override_reason"):
            soft_line += f"例外理由：{opportunity['soft_edge_override_reason']}"
        lines.append(soft_line)
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
    ]
    if report.get("schema_version") in {"2.0", "2.1"}:
        lines.extend(
            [
                f"- 持仓可变现价值：{portfolio['holdings_liquidation_value']}",
                f"- 可部署现金：{portfolio['deployable_cash']}",
                f"- 本轮分配：{portfolio['allocated_cash']}",
                f"- 分配后剩余：{portfolio['remaining_cash']}",
                f"- 组合分散判断：{portfolio['diversification_summary']}",
            ]
        )
    lines.extend(
        [
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
    )
    for action in report["actions"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(localized_enum(action["action"], SIGNAL_LABELS)),
                    markdown_escape(action["market_question"]),
                    markdown_escape(localized_enum(action["outcome"], OUTCOME_LABELS)),
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
