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
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "skills" / "forecast-edge" / "scripts"),
)
from report_tool import (  # noqa: E402
    CANDIDATE_NOVELTY_CLASSES,
    ReportError,
    is_generic_candidate_research_text,
    market_has_mechanical_status_evidence,
    narrative_mentions_probability,
    normalize_narrative,
)


ACTIONABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}


class BriefValidationError(ValueError):
    pass


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BriefValidationError(f"could not read {label}: {exc}") from exc


def validate_candidate_brief_sources(candidates: list[Any], report_path: Path) -> None:
    """Refuse to turn generic upstream candidate prose into a normal briefing."""

    signal_owners: dict[str, tuple[int, bool]] = {}
    conclusion_owners: dict[str, tuple[int, bool]] = {}
    evidence_owners: dict[tuple[str, str], tuple[int, bool]] = {}
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            raise BriefValidationError(f"candidate {index} is malformed in {report_path}")
        novelty = raw.get("research_novelty")
        classification = novelty.get("classification") if isinstance(novelty, dict) else None
        if classification not in CANDIDATE_NOVELTY_CLASSES:
            raise BriefValidationError(
                f"candidate {index} lacks validated research_novelty in {report_path}"
            )
        signal = raw.get("signal")
        model = raw.get("model")
        evidence = raw.get("evidence")
        try:
            mechanical = market_has_mechanical_status_evidence(raw)
        except ReportError as exc:
            raise BriefValidationError(
                f"candidate {index} mechanical status is malformed in {report_path}: {exc}"
            ) from exc
        rationale = signal.get("rationale") if isinstance(signal, dict) else None
        conclusion = model.get("conclusion") if isinstance(model, dict) else None
        if not isinstance(rationale, str) or (
            not mechanical and is_generic_candidate_research_text(rationale)
        ):
            raise BriefValidationError(
                f"Warning: candidate {index} has generic signal rationale in {report_path}"
            )
        if not isinstance(conclusion, str) or (
            not mechanical and is_generic_candidate_research_text(conclusion)
        ):
            raise BriefValidationError(
                f"Warning: candidate {index} has generic model conclusion in {report_path}"
            )
        if not mechanical:
            yes_probability = model.get("yes_probability") if isinstance(model, dict) else None
            try:
                probability_bound = (
                    narrative_mentions_probability(rationale, yes_probability)
                    and narrative_mentions_probability(conclusion, yes_probability)
                )
            except ReportError as exc:
                raise BriefValidationError(
                    f"Warning: candidate {index} has malformed model probability in "
                    f"{report_path}: {exc}"
                ) from exc
            if not probability_bound:
                raise BriefValidationError(
                    f"Warning: candidate {index} research prose does not match "
                    f"model.yes_probability in {report_path}"
                )
        for label, value, owners in (
            ("signal rationale", rationale, signal_owners),
            ("model conclusion", conclusion, conclusion_owners),
        ):
            normalized = normalize_narrative(value)
            owner = owners.get(normalized)
            if owner is not None and not (mechanical and owner[1]):
                raise BriefValidationError(
                    f"Warning: candidate {index} duplicates {label} from candidate {owner[0]}"
                )
            owners.setdefault(normalized, (index, mechanical))
        if not isinstance(evidence, list) or not evidence:
            raise BriefValidationError(
                f"candidate {index} has no validated evidence in {report_path}"
            )
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise BriefValidationError(
                    f"candidate {index} evidence {evidence_index} is malformed in {report_path}"
                )
            for field in ("summary", "conclusion"):
                value = item.get(field)
                if not isinstance(value, str) or (
                    not mechanical and is_generic_candidate_research_text(value)
                ):
                    raise BriefValidationError(
                        f"Warning: candidate {index} evidence {field} is generic in {report_path}"
                    )
                key = (field, normalize_narrative(value))
                owner = evidence_owners.get(key)
                if owner is not None and owner[0] != index and not (mechanical and owner[1]):
                    raise BriefValidationError(
                        f"Warning: candidate {index} duplicates evidence {field} from candidate "
                        f"{owner[0]}"
                    )
                evidence_owners.setdefault(key, (index, mechanical))


def candidate_novelty_counts(reports_dir: Path) -> dict[str, int]:
    counts = {classification: 0 for classification in CANDIDATE_NOVELTY_CLASSES}
    for path in sorted(reports_dir.glob("report-*.json")):
        report = load_json(path, str(path))
        candidates = report.get("candidates") if isinstance(report, dict) else None
        if not isinstance(candidates, list):
            raise BriefValidationError(f"report candidates are malformed: {path}")
        for candidate in candidates:
            novelty = candidate.get("research_novelty") if isinstance(candidate, dict) else None
            classification = novelty.get("classification") if isinstance(novelty, dict) else None
            if classification not in counts:
                raise BriefValidationError(f"candidate lacks validated research novelty: {path}")
            counts[classification] += 1
    return counts


def validate_candidate_novelty_headings(document: dict[str, Any], reports_dir: Path) -> None:
    markdown = str(document.get("markdown", ""))
    counts = candidate_novelty_counts(reports_dir)
    required_labels = {
        "FIRST_RESEARCH": "新增候选（本轮首次研究）",
        "EXISTING_EVENT_NEW_MARKET": "既有事件的新子市场",
    }
    for classification, label in required_labels.items():
        if counts[classification] > 0 and label not in markdown:
            raise BriefValidationError(
                f"brief is missing {label} for {counts[classification]} candidate(s)"
            )


def report_inventory(
    reports_dir: Path,
    *,
    allow_empty: bool = False,
    research_date: str | None = None,
    validate_candidate_quality: bool = True,
) -> dict[str, Any]:
    paths = sorted(reports_dir.glob("report-*.json"))
    if not paths:
        if not allow_empty or research_date is None:
            raise BriefValidationError("no report JSON files were supplied")
        try:
            parsed_date = datetime.fromisoformat(research_date).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise BriefValidationError("fallback research date is invalid") from exc
        return {
            "research_date": parsed_date,
            "venues": [],
            "current_position_count": None,
            "researched_market_count": None,
            "actionable_recommendation_count": None,
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
        if validate_candidate_quality:
            validate_candidate_brief_sources(candidates, path)
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
    validate_candidate_novelty_headings(response, args.reports_dir)
    write_json(args.output, response)
    write_json(args.trace_output, trace)


def command_inventory(args: argparse.Namespace) -> None:
    write_json(args.output, report_inventory(args.reports_dir))


def fallback_context(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    context = load_json(path, "fallback briefing context")
    if not isinstance(context, dict) or context.get("schema_version") != "1.0":
        raise BriefValidationError("fallback briefing context is malformed")
    for field in (
        "venues",
        "account_snapshots",
        "latest_validated_baselines",
        "current_validated_reports",
    ):
        if not isinstance(context.get(field), list):
            raise BriefValidationError(f"fallback briefing context {field} is malformed")
    if not isinstance(context.get("progress"), dict):
        raise BriefValidationError("fallback briefing context progress is malformed")
    return context


def total_known(rows: list[Any], field: str) -> int | None:
    if not rows or any(not isinstance(row, dict) for row in rows):
        return None
    values = [row.get(field) for row in rows]
    if not values or any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return None
    if any(value < 0 for value in values):
        return None
    return sum(values)


def fallback_inventory(
    reports_dir: Path,
    *,
    research_date: str,
    context: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    paths = sorted(reports_dir.glob("report-*.json"))
    if paths:
        return (
            report_inventory(
                reports_dir,
                allow_empty=True,
                research_date=research_date,
                validate_candidate_quality=False,
            ),
            True,
        )
    inventory = report_inventory(
        reports_dir,
        allow_empty=True,
        research_date=research_date,
        validate_candidate_quality=False,
    )
    if context is None:
        return inventory, False
    venues = [
        venue
        for venue in context["venues"]
        if isinstance(venue, str) and re.fullmatch(r"[a-z][a-z0-9-]*", venue)
    ]
    inventory["venues"] = sorted(set(venues))
    current_reports = context["current_validated_reports"]
    if current_reports:
        inventory["current_position_count"] = total_known(
            current_reports, "current_position_count"
        )
        inventory["researched_market_count"] = total_known(
            current_reports, "researched_market_count"
        )
        inventory["actionable_recommendation_count"] = total_known(
            current_reports, "actionable_recommendation_count"
        )
    else:
        inventory["current_position_count"] = total_known(
            context["account_snapshots"], "current_position_count"
        )
    return inventory, bool(current_reports)


def display_count(value: Any, noun: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"**{value} {noun}**"
    return f"**{noun}数量未获得**"


def display_money(value: Any, currency: Any) -> str:
    if isinstance(value, str) and value and isinstance(currency, str) and currency:
        return f"可用抵押资金 **{value} {currency}**"
    return "可用抵押资金 **未获得**"


def account_snapshot_markdown(context: dict[str, Any] | None) -> str:
    accounts = context.get("account_snapshots", []) if context is not None else []
    rendered: list[str] = []
    for row in accounts:
        if not isinstance(row, dict):
            continue
        venue = row.get("venue")
        if not isinstance(venue, str) or not venue:
            continue
        rendered.append(
            f"{venue}：{display_money(row.get('available_cash'), row.get('currency'))}，"
            f"账户当前持仓 {display_count(row.get('current_position_count'), '个')}"
        )
    if not rendered:
        return "账户快照 **未获得或不可验证**"
    return "；".join(rendered)


def baseline_markdown(context: dict[str, Any] | None) -> str:
    baselines = context.get("latest_validated_baselines", []) if context is not None else []
    rendered: list[str] = []
    for row in baselines:
        if not isinstance(row, dict):
            continue
        venue = row.get("venue")
        completed_at = row.get("completed_at")
        if not isinstance(venue, str) or not isinstance(completed_at, str):
            continue
        date = completed_at[:10]
        rendered.append(
            f"{venue} {date}：{display_count(row.get('current_position_count'), '个当前持仓')}、"
            f"{display_count(row.get('candidate_count'), '个新增候选')}、"
            f"{display_count(row.get('actionable_recommendation_count'), '条可操作建议')}"
        )
    return "；".join(rendered) if rendered else "未取得上一份验证报告基线"


def progress_markdown(context: dict[str, Any] | None) -> str:
    if context is None:
        return "研究诊断 context 未提供，无法确定本轮到达的具体研究步骤"
    progress = context["progress"]
    stage_labels = {
        "SETUP_NOT_REACHED": "尚未完成工作流初始化",
        "WORKFLOW_CONFIGURED": "工作流配置已完成",
        "HISTORY_RESTORED": "历史报告已恢复",
        "ACCOUNT_SNAPSHOT_CAPTURED": "账户快照已完成",
        "RESEARCH_AGENT_RAN": "研究 Agent 已运行，但尚无验证报告",
        "REPORT_VALIDATION_ATTEMPTED": "报告校验已运行，但尚未通过",
        "REPORT_GENERATED_UNVALIDATED": "报告文件已生成，但未通过验证",
        "REPORT_VALIDATED": "报告已经验证，但正常简报不可用",
    }
    stage = stage_labels.get(
        progress.get("last_reached_stage"), "最近研究阶段无法识别"
    )
    counts = []
    for label, field in (
        ("已恢复历史报告", "restored_historical_report_count"),
        ("账户快照", "account_snapshot_count"),
        ("研究 Agent 尝试", "research_agent_attempt_count"),
        ("报告校验尝试", "report_validation_attempt_count"),
        ("已验证当轮报告", "validated_report_count"),
    ):
        value = progress.get(field)
        counts.append(f"{label} {value if isinstance(value, int) else '未获得'}")
    return f"最近到达 **{stage}**；" + "，".join(counts)


def command_fallback(args: argparse.Namespace) -> None:
    context = fallback_context(getattr(args, "context", None))
    inventory, validated_report_inventory_available = fallback_inventory(
        args.reports_dir,
        research_date=args.research_date,
        context=context,
    )
    reason = args.reason.read_text(encoding="utf-8").strip()[:500]
    venue_text = "、".join(inventory["venues"]) or "未完成报告"
    current_count = inventory["current_position_count"]
    researched_count = inventory["researched_market_count"]
    actionable_count = inventory["actionable_recommendation_count"]
    if validated_report_inventory_available:
        candidate_count = (
            researched_count - current_count
            if isinstance(researched_count, int) and isinstance(current_count, int)
            else None
        )
        portfolio_summary = account_snapshot_markdown(context)
        research_summary = (
            f"本轮已验证报告覆盖 {display_count(current_count, '个当前持仓')}、"
            f"{display_count(candidate_count, '个新增候选')}"
        )
        baseline_summary = "本轮已验证报告仍可作为数量清单，市场专属结论未在 fallback 中二次生成"
    else:
        portfolio_summary = account_snapshot_markdown(context)
        research_summary = (
            "本轮经过验证的 report JSON **未取得**；账户持仓快照不等于研究覆盖，"
            "当前持仓重研数量、新增候选数量和市场专属结论均为 **未获得/不可验证**"
        )
        baseline_summary = f"上一份验证报告基线：{baseline_markdown(context)}"

    if actionable_count == 0:
        action_summary = "经验证报告确认：本轮无可执行交易意图。"
    elif isinstance(actionable_count, int):
        action_summary = f"经验证报告含 **{actionable_count} 条可操作建议**。"
    else:
        action_summary = "本轮可执行交易意图数量 **未获得**；不能据此声称为零。"
    document = {
        "schema_version": "1.0",
        **inventory,
        "title": f"forecast-edge 扫描简报 {inventory['research_date']}",
        "markdown": (
            f"## forecast-edge 全量扫描｜{inventory['research_date']}（{venue_text}）\n\n"
            f"**组合概况**：⚠️ Warning：{portfolio_summary}。\n\n"
            f"**研究覆盖**：{research_summary}。\n\n"
            "**本轮结论**：⚠️ **Warning：未取得可验证的市场专属研究结论**，请查看完整报告和下方异常详情。\n\n"
            "### 一、私人操作建议\n\n"
            "Agent 简报生成失败，本卡不会补造研究理由或操作建议；"
            "只有经过验证的报告才能作为交易建议来源。\n\n"
            "### 二、简报\n\n"
            f"**本轮已完成步骤**：{progress_markdown(context)}。\n\n"
            f"**可用基线**：{baseline_summary}。\n\n"
            f"**执行判断状态**：{action_summary}\n\n"
            f"**Warning 详情**：{reason or 'unknown'}"
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
    fallback.add_argument("--research-date", required=True)
    fallback.add_argument("--context", type=Path)
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
