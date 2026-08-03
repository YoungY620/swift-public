#!/usr/bin/env python3
"""Validate one v3 forecast report and build immutable private/public presentations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_contract import (
    PresentationContractError,
    canonical_sha256,
    evaluate_tree,
    load_report,
    validate_public_signals,
)


PRIVATE_MODE = 0o600
PUBLIC_MODE = 0o644
DIRECTORY_MODE = 0o700
ACTION_LABELS = {
    "BUY": "买入",
    "ADD": "加仓",
    "MAINTAIN": "维持现状",
    "TRIM": "减仓",
    "SELL": "卖出",
}
POST_MARKERS = {
    ("BUY", "YES"): "➕✅ 买 YES",
    ("BUY", "NO"): "➕❌ 买 NO",
    ("ADD", "YES"): "➕✅ 买 YES",
    ("ADD", "NO"): "➕❌ 买 NO",
    ("SELL", "YES"): "➖✅ 卖 YES",
    ("SELL", "NO"): "➖❌ 卖 NO",
}
PROTECTED_RE = re.compile(
    r"<!-- GENERATED:([A-Za-z0-9._-]+):START -->\n(.*?)\n<!-- GENERATED:\1:END -->",
    re.S,
)


class PresentationError(ValueError):
    """Raised when presentation generation or collaborative finalization fails."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_component(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-.")
    if not cleaned:
        raise PresentationError("value cannot be converted to a safe path component")
    return cleaned


def percent(value: Any, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def pp(value: Any, digits: int = 1) -> str:
    return f"{float(value) * 100:+.{digits}f}pp"


def cash(value: Any) -> str:
    return f"{float(value):.2f}"


def shares(value: Any) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def analysis_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["market"]["id"]): item
        for item in report["current_positions"] + report["candidates"]
    }


def evidence_badges(ids: list[str]) -> str:
    return "、".join(f"`{identifier}`" for identifier in ids) if ids else "由子节点汇总"


def render_text_tree(node: dict[str, Any], path: str = "1", indent: str = "") -> list[str]:
    lines = [
        f"{indent}{path} {node['label']} — {percent(node['probability'])}",
        f"{indent}   原因：{node['reason']}",
        f"{indent}   证据：{evidence_badges(node['evidence_ids'])}",
    ]
    children = node["children"]
    for index, branch in enumerate(children, 1):
        branch_path = f"{path}.{index}"
        connector = "└──" if index == len(children) else "├──"
        child_indent = indent + ("    " if index == len(children) else "│   ")
        lines.append(
            f"{indent}{connector} {branch_path} {branch['label']}｜权重 {percent(branch['weight'])}"
        )
        lines.append(f"{child_indent}权重原因：{branch['weight_reason']}")
        lines.append(f"{child_indent}权重证据：{evidence_badges(branch['evidence_ids'])}")
        lines.extend(render_text_tree(branch["node"], branch_path, child_indent))
    return lines


def mermaid_label(node: dict[str, Any], path: str) -> str:
    refs = "、".join(node["evidence_ids"]) or "子节点汇总"
    return "<br/>".join(
        html.escape(value, quote=True)
        for value in (
            f"{path}｜{node['label']}",
            f"概率 {percent(node['probability'])}",
            f"证据 {refs}",
        )
    )


def mermaid_id(path: str) -> str:
    return "N" + path.replace(".", "_")


def render_mermaid(node: dict[str, Any], path: str = "1", *, max_relative_depth: int | None = None) -> str:
    lines = ["flowchart TD"]

    def visit(current: dict[str, Any], current_path: str, depth: int) -> None:
        lines.append(f'    {mermaid_id(current_path)}["{mermaid_label(current, current_path)}"]')
        if max_relative_depth is not None and depth >= max_relative_depth:
            return
        for index, branch in enumerate(current["children"], 1):
            child_path = f"{current_path}.{index}"
            visit(branch["node"], child_path, depth + 1)
            refs = "、".join(branch["evidence_ids"])
            edge = html.escape(
                f"{branch['label']}｜权重 {percent(branch['weight'])}｜{refs}", quote=True
            )
            lines.append(
                f'    {mermaid_id(current_path)} -->|"{edge}"| {mermaid_id(child_path)}'
            )

    visit(node, path, 0)
    return "\n".join(lines)


def render_tree_visuals(tree: dict[str, Any]) -> str:
    root = tree["root"]
    evaluation = evaluate_tree(tree)
    sections: list[str] = []
    if evaluation.node_count <= 24:
        sections.extend(["```mermaid", render_mermaid(root), "```"])
    else:
        sections.extend(
            [
                "树较深，先显示顶层总览，再按顶层分支展开。",
                "",
                "```mermaid",
                render_mermaid(root, max_relative_depth=1),
                "```",
            ]
        )
        for index, branch in enumerate(root["children"], 1):
            sections.extend(
                [
                    "",
                    f"##### 分支 1.{index}：{branch['label']}",
                    "",
                    "```mermaid",
                    render_mermaid(branch["node"], f"1.{index}"),
                    "```",
                ]
            )
    sections.extend(
        [
            "",
            "<details>",
            "<summary>纯文本概率树</summary>",
            "",
            "```text",
            *render_text_tree(root),
            "```",
            "</details>",
        ]
    )
    return "\n".join(sections)


def protected(block_id: str, content: str) -> str:
    return (
        f"<!-- GENERATED:{block_id}:START -->\n"
        f"{content.rstrip()}\n"
        f"<!-- GENERATED:{block_id}:END -->"
    )


def action_table(report: dict[str, Any]) -> str:
    lines = [
        "| 行动 | 市场 | 方向 | 本轮份额 | 本轮金额 |",
        "|---|---|---|---:|---:|",
    ]
    for action in report["actions"]:
        lines.append(
            f"| {ACTION_LABELS[action['action']]} | {action['market_question']} | "
            f"{action['outcome']} | {shares(action['recommended_shares'])} | "
            f"{cash(action['recommended_cash'])} |"
        )
    return "\n".join(lines)


def market_block(item: dict[str, Any], action: dict[str, Any], index: int) -> str:
    market = item["market"]
    model = item["model"]
    review = item["resolution_review"]
    opportunity = item["opportunity"]
    lines = [
        f"### {index}. [{market['question']}]({market['url']})",
        "",
        f"- 最终行动：{ACTION_LABELS[action['action']]} {market['outcome']}",
        f"- 市场 YES 概率：{percent(model['yes_probability'])}",
        f"- {market['outcome']} 合理价格：{percent(model['model_value'])}",
        f"- 当前可执行价格：{percent(opportunity['executable_price'])}",
        f"- 原始优势：{pp(opportunity['raw_edge'])}；净优势：{pp(opportunity['net_edge'])}",
        f"- 模型结论：{model['conclusion']}",
        f"- 行动理由：{action['rationale']}",
        "",
        "#### 结算标准审查",
        "",
        f"- 结果：`{review['status']}`",
        f"- YES 条件：{review['yes_condition']}",
        f"- NO 条件：{review['no_condition']}",
        f"- 截止时间：{review['deadline'] or '未指定'}（{review['timezone'] or '未指定时区'}）",
        f"- 判定来源：{review['resolution_source']}",
        f"- 审查理由：{review['reason']}",
        "",
        "#### 递归概率树",
        "",
        render_tree_visuals(model["tree"]),
        "",
        "#### 关键证据",
        "",
        "| ID | 来源 | 标题 | 内容摘要 | 概率影响 | 可信度说明 |",
        "|---|---|---|---|---|---|",
    ]
    for evidence in item["evidence"]:
        lines.append(
            f"| `{evidence['id']}` | [{evidence['source_name']}]({evidence['url']}) | "
            f"{evidence['title']} | {evidence['summary']} | {evidence['conclusion']} | "
            f"{evidence['credibility']} |"
        )
    execution = action["execution"]
    lines.extend(["", "#### 本轮执行"])
    if execution is None:
        lines.extend(["", "本轮不执行订单。"])
    else:
        direction = "预计支付" if execution["cash_direction"] == "PAY" else "预计收回"
        lines.extend(
            [
                "",
                f"- 本轮份额：{shares(execution['shares'])}",
                f"- {direction}：{cash(execution['cash_amount'])}",
                f"- VWAP：{percent(execution['vwap'])}",
                f"- 有效单位价格：{percent(execution['effective_unit_price'])}",
                f"- 手续费：{cash(execution['fee_cash'])}",
                f"- 滑点：{pp(execution['slippage'])}",
                f"- 净优势：{pp(execution['net_edge'])}",
                f"- 订单簿时间：{execution['book_timestamp']}",
            ]
        )
    return "\n".join(lines)


def research_blocks(report: dict[str, Any]) -> dict[str, str]:
    actions = {str(action["market_id"]): action for action in report["actions"]}
    blocks = {
        "run-facts": "\n".join(
            [
                f"- 来源报告：`{report['run']['id']}`",
                f"- 数据截止：{report['run']['data_cutoff']}",
                f"- Schema：`{report['schema_version']}`",
                f"- 摘要：{report['summary']}",
            ]
        ),
        "actions": action_table(report),
        "portfolio": "\n".join(
            [
                f"- 可用抵押资金：{cash(report['portfolio']['available_collateral'])}",
                f"- 持仓可变现价值：{cash(report['portfolio']['holdings_liquidation_value'])}",
                f"- 本轮分配：{cash(report['portfolio']['allocated_cash'])}",
                f"- 储备资金：{cash(report['portfolio']['reserve_cash'])}",
                f"- 组合结论：{report['portfolio']['diversification_summary']}",
            ]
        ),
        "discovery": "\n".join(
            [
                f"- 完成全量分页：{'是' if report['discovery_audit']['pagination_completed'] else '否'}",
                f"- 获取事件：{report['discovery_audit']['fetched_events']}",
                f"- 语义合格事件：{report['discovery_audit']['semantically_qualified_events']}",
                f"- 展开事件：{report['discovery_audit']['selected_events']}",
                f"- 审计结论：{report['discovery_audit']['summary']}",
            ]
        ),
    }
    all_markets = report["current_positions"] + report["candidates"]
    for index, item in enumerate(all_markets, 1):
        market_id = str(item["market"]["id"])
        blocks[f"market-{safe_component(market_id)}"] = market_block(
            item, actions[market_id], index
        )
    return blocks


def render_research_report(report: dict[str, Any]) -> str:
    blocks = research_blocks(report)
    market_ids = [key for key in blocks if key.startswith("market-")]
    lines = [
        "# 预测市场完整研究报告",
        "",
        "> 私人研究资料，不得公开发布。协作模式只能编辑生成区块之外的文字。",
        "",
        "## 概览",
        "",
        protected("run-facts", blocks["run-facts"]),
        "",
        "## 本轮行动",
        "",
        protected("actions", blocks["actions"]),
        "",
        "## 组合情况",
        "",
        protected("portfolio", blocks["portfolio"]),
        "",
        "## 市场研究",
        "",
    ]
    for key in market_ids:
        lines.extend([protected(key, blocks[key]), ""])
    lines.extend(["## 候选发现审计", "", protected("discovery", blocks["discovery"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def verify_collaborative_draft(report: dict[str, Any], markdown: str) -> None:
    expected = research_blocks(report)
    found: dict[str, str] = {}
    for block_id, content in PROTECTED_RE.findall(markdown):
        if block_id in found:
            raise PresentationError(f"collaborative draft duplicates protected block {block_id}")
        found[block_id] = content.rstrip()
    if set(found) != set(expected):
        missing = sorted(set(expected) - set(found))
        extra = sorted(set(found) - set(expected))
        raise PresentationError(f"protected block set changed; missing={missing}, extra={extra}")
    for block_id, content in expected.items():
        if found[block_id] != content.rstrip():
            raise PresentationError(f"protected block {block_id} was modified")


def render_private_brief(report: dict[str, Any]) -> str:
    analyses = analysis_map(report)
    current_ids = {str(item["market"]["id"]) for item in report["current_positions"]}
    selected = [
        action
        for action in report["actions"]
        if str(action["market_id"]) in current_ids or action["action"] != "MAINTAIN"
    ]
    lines = [
        "# 私人操作简报",
        "",
        "> 私人研究资料，不得公开发布。",
        "",
    ]
    for action in selected:
        item = analyses[str(action["market_id"])]
        model = item["model"]
        opportunity = item["opportunity"]
        lines.extend(
            [
                f"## {ACTION_LABELS[action['action']]} {action['outcome']}｜[{action['market_question']}]({item['market']['url']})",
                "",
                f"- 本轮份额：{shares(action['recommended_shares'])}",
                f"- 本轮金额：{cash(action['recommended_cash'])}",
                f"- YES 概率：{percent(model['yes_probability'])}",
                f"- {action['outcome']} 合理价格：{percent(model['model_value'])}",
                f"- 当前可执行价格：{percent(opportunity['executable_price'])}",
                f"- 净优势：{pp(opportunity['net_edge'])}",
                f"- 原因：{action['rationale']}",
                "",
            ]
        )
    if not selected:
        lines.append("本轮没有需要执行或复核的仓位。")
    return "\n".join(lines).rstrip() + "\n"


def build_public_signals(
    report: dict[str, Any], source_hash: str, generated_at: str
) -> dict[str, Any]:
    analyses = analysis_map(report)
    actions: list[dict[str, Any]] = []
    for action in report["actions"]:
        if action["action"] in {"MAINTAIN", "TRIM"}:
            continue
        item = analyses[str(action["market_id"])]
        review = item["resolution_review"]
        public_evidence = [
            {
                "id": evidence["id"],
                "source_url": evidence["url"],
                "source_title": evidence["title"],
                "source_content": evidence["summary"],
                "probability_impact": evidence["conclusion"],
            }
            for evidence in item["evidence"]
        ]
        actions.append(
            {
                "market_id": str(action["market_id"]),
                "market_name": action["market_question"],
                "action": action["action"],
                "outcome": action["outcome"],
                "polymarket_url": item["market"]["url"],
                "yes_probability": item["model"]["yes_probability"],
                "fair_price": item["model"]["model_value"],
                "current_price": item["opportunity"]["executable_price"],
                "raw_edge": item["opportunity"]["raw_edge"],
                "net_edge": item["opportunity"]["net_edge"],
                "reason": action["public_reason"],
                "resolution": {
                    "criteria_text": review["criteria_text"],
                    "yes_condition": review["yes_condition"],
                    "no_condition": review["no_condition"],
                    "deadline": review["deadline"],
                    "resolution_source": review["resolution_source"],
                },
                "evidence": public_evidence,
                "probability_tree": copy.deepcopy(item["model"]["tree"]),
            }
        )
    result = {
        "schema_version": "2.0",
        "brief_id": f"presentation-{safe_component(report['run']['id'])}",
        "source_report_id": report["run"]["id"],
        "source_report_sha256": source_hash,
        "generated_at": generated_at,
        "as_of": report["run"]["data_cutoff"],
        "title": "预测市场研究信号",
        "actions": actions,
    }
    return validate_public_signals(result)


def render_public_posts(public: dict[str, Any]) -> str:
    posts: list[str] = []
    for action in public["actions"]:
        marker = POST_MARKERS[(action["action"], action["outcome"])]
        fixed = (
            f"{marker}｜{action['market_name']}\n\n"
            f"YES 概率 {percent(action['yes_probability'])}｜{action['outcome']} 合理价 {percent(action['fair_price'])}\n"
            f"当前价 {percent(action['current_price'])}｜净优势 {pp(action['net_edge'])}\n\n"
            "理由：{reason}\n"
            f"{action['polymarket_url']}"
        )
        allowance = 400 - len(fixed.format(reason=""))
        reason = action["reason"]
        if allowance < 1:
            raise PresentationError(f"public post fixed fields exceed 400 characters: {action['market_id']}")
        if len(reason) > allowance:
            reason = reason[: max(1, allowance - 1)].rstrip() + "…"
        post = fixed.format(reason=reason)
        if len(post) > 400:
            raise PresentationError(f"public post exceeds 400 characters: {action['market_id']}")
        posts.append(post)
    return "\n\n---\n\n".join(posts) + ("\n" if posts else "今日无新增交易信号\n")


def write_text(path: Path, content: str, mode: int) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def write_json(path: Path, content: Any, mode: int) -> None:
    write_text(path, json.dumps(content, ensure_ascii=False, indent=2) + "\n", mode)


def artifact_hashes(directory: Path, names: list[str]) -> dict[str, str]:
    return {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }


def destination_for(report: dict[str, Any], output_root: Path) -> Path:
    timestamp = safe_component(str(report["run"]["completed_at"]).replace(":", "-"))
    run_id = safe_component(report["run"]["id"])
    return output_root / "presentations" / f"{timestamp}_{run_id}"


def generate(
    report: dict[str, Any],
    source_path: Path,
    source_hash: str,
    output_root: Path,
    *,
    mode: str,
    research_override: str | None = None,
) -> Path:
    generated_at = utc_now()
    destination = destination_for(report, output_root)
    if destination.exists():
        manifest_path = destination / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_report_sha256") == source_hash:
                return destination
        raise PresentationError(f"refusing to overwrite existing presentation: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    os.chmod(destination.parent, DIRECTORY_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=".present-", dir=destination.parent))
    temporary.chmod(DIRECTORY_MODE)
    try:
        private_brief = render_private_brief(report)
        research_report = research_override or render_research_report(report)
        public_signals = build_public_signals(report, source_hash, generated_at)
        public_posts = render_public_posts(public_signals)
        write_text(temporary / "private-brief.md", private_brief, PRIVATE_MODE)
        write_text(temporary / "research-report.md", research_report, PRIVATE_MODE)
        write_json(temporary / "public-signals.json", public_signals, PUBLIC_MODE)
        write_text(temporary / "public-posts.md", public_posts, PUBLIC_MODE)
        artifact_names = [
            "private-brief.md",
            "research-report.md",
            "public-signals.json",
            "public-posts.md",
        ]
        manifest = {
            "schema_version": "1.0",
            "presentation_id": destination.name,
            "source_report_id": report["run"]["id"],
            "source_report_schema": report["schema_version"],
            "source_report_sha256": source_hash,
            "source_report_name": source_path.name,
            "generated_at": generated_at,
            "mode": mode,
            "validation": "PASS",
            "artifacts": artifact_hashes(temporary, artifact_names),
        }
        write_json(temporary / "manifest.json", manifest, PRIVATE_MODE)
        temporary.rename(destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def prepare_draft(report: dict[str, Any], source_hash: str, workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    os.chmod(workspace, DIRECTORY_MODE)
    draft = workspace / "research-report.md"
    write_text(draft, render_research_report(report), PRIVATE_MODE)
    write_json(
        workspace / "draft-manifest.json",
        {
            "source_report_id": report["run"]["id"],
            "source_report_sha256": source_hash,
            "protected_blocks_sha256": canonical_sha256(research_blocks(report)),
        },
        PRIVATE_MODE,
    )
    return draft


def write_failure(output_root: Path, input_value: str, error: Exception) -> Path:
    generated_at = utc_now()
    digest = hashlib.sha256(input_value.encode("utf-8")).hexdigest()[:12]
    root = output_root / "presentations"
    root.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    os.chmod(root, DIRECTORY_MODE)
    destination = root / f"failed-{safe_component(generated_at.replace(':', '-'))}-{digest}"
    destination.mkdir(mode=DIRECTORY_MODE)
    write_json(
        destination / "manifest.json",
        {
            "schema_version": "1.0",
            "generated_at": generated_at,
            "validation": "FAIL",
        },
        PRIVATE_MODE,
    )
    write_json(
        destination / "errors.json",
        {"errors": [{"code": "PRESENTATION_INPUT_INVALID", "message": str(error)}]},
        PRIVATE_MODE,
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate", help="Generate immutable automatic outputs")
    generate_parser.add_argument("report")
    generate_parser.add_argument("--output-root", required=True)
    prepare_parser = subparsers.add_parser("prepare-draft", help="Create a collaborative draft")
    prepare_parser.add_argument("report")
    prepare_parser.add_argument("--workspace", required=True)
    finalize_parser = subparsers.add_parser("finalize-draft", help="Validate and archive a draft")
    finalize_parser.add_argument("report")
    finalize_parser.add_argument("--draft", required=True)
    finalize_parser.add_argument("--output-root", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate one source report")
    validate_parser.add_argument("report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(getattr(args, "output_root", ".")).expanduser()
    try:
        report, source_path, source_hash = load_report(args.report)
        if args.command == "validate":
            result: Any = {
                "valid": True,
                "run_id": report["run"]["id"],
                "source_report_sha256": source_hash,
            }
        elif args.command == "generate":
            result = {
                "presentation_directory": str(
                    generate(report, source_path, source_hash, output_root, mode="automatic")
                )
            }
        elif args.command == "prepare-draft":
            result = {
                "draft": str(
                    prepare_draft(report, source_hash, Path(args.workspace).expanduser())
                )
            }
        elif args.command == "finalize-draft":
            draft_path = Path(args.draft).expanduser()
            markdown = draft_path.read_text(encoding="utf-8")
            verify_collaborative_draft(report, markdown)
            result = {
                "presentation_directory": str(
                    generate(
                        report,
                        source_path,
                        source_hash,
                        output_root,
                        mode="collaborative",
                        research_override=markdown,
                    )
                )
            }
        else:  # pragma: no cover
            raise PresentationError(f"unsupported command: {args.command}")
    except (OSError, json.JSONDecodeError, PresentationContractError, PresentationError) as exc:
        if args.command in {"generate", "finalize-draft"}:
            try:
                failure = write_failure(output_root, args.report, exc)
                print(f"present_report: {exc}; failure={failure}", file=sys.stderr)
            except Exception:
                print(f"present_report: {exc}", file=sys.stderr)
        else:
            print(f"present_report: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
