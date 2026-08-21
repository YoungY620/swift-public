#!/usr/bin/env python3
"""Build a secret-free, market-named execution summary for Feishu."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


POLYMARKET_URL = re.compile(
    r"https://(?:www\.)?polymarket\.com/event/[A-Za-z0-9._~%-]+(?:\?tid=[0-9]+)?/?"
)
STATUS_LABELS = {
    "FULL": ("✅", "已成交"),
    "NO_FILL": ("⚪", "FOK 未成交"),
    "SKIPPED": ("⏭️", "规划跳过"),
    "REJECTED": ("❌", "交易所拒绝"),
    "PARTIAL": ("⚠️", "部分成交"),
    "NOT_RECORDED": ("⚠️", "未取得回执"),
}
SIDE_LABELS = {"buy": "买入", "sell": "卖出"}


class ExecutionBriefError(ValueError):
    pass


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionBriefError(f"could not read {label}: {exc}") from exc


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def clean_text(value: Any, *, limit: int = 320) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = " ".join(value.split())
    return rendered[:limit] if rendered else None


def safe_url(value: Any) -> str | None:
    if isinstance(value, str) and POLYMARKET_URL.fullmatch(value):
        return value
    return None


def markdown_text(value: Any, *, fallback: str = "未命名市场") -> str:
    rendered = clean_text(value, limit=320) or fallback
    for character in ("\\", "`", "*", "_", "[", "]"):
        rendered = rendered.replace(character, f"\\{character}")
    return rendered


def market_link(question: Any, url: Any) -> str:
    label = markdown_text(question)
    normalized_url = safe_url(url)
    return f"[{label}]({normalized_url})" if normalized_url else label


def report_market_index(reports_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(reports_dir.glob("report-*.json")):
        report = load_json(path, f"report {path.name}")
        if not isinstance(report, dict):
            continue
        for collection_name in ("current_positions", "candidates"):
            collection = report.get(collection_name)
            if not isinstance(collection, list):
                continue
            for analysis in collection:
                market = analysis.get("market") if isinstance(analysis, dict) else None
                if not isinstance(market, dict):
                    continue
                instrument = market.get("instrument_identifier")
                question = clean_text(market.get("question"))
                outcome = str(market.get("outcome") or "").lower()
                if (
                    not isinstance(instrument, str)
                    or not instrument
                    or question is None
                    or outcome not in {"yes", "no"}
                ):
                    continue
                index[instrument] = {
                    "question": question,
                    "url": safe_url(market.get("url")),
                    "outcome": outcome,
                }
    return index


def receipt_by_intent(receipt: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    values = receipt.get("receipts")
    if not isinstance(values, list):
        return result
    for item in values:
        index = item.get("intent_index") if isinstance(item, dict) else None
        if isinstance(index, int) and index > 0 and index not in result:
            result[index] = item
    return result


def item_from_outcome(
    raw: dict[str, Any],
    *,
    receipt_index: dict[int, dict[str, Any]],
    markets: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    intent_index = raw.get("intent_index")
    intent = raw.get("intent")
    disposition = raw.get("disposition")
    if not isinstance(intent_index, int) or intent_index <= 0 or not isinstance(intent, dict):
        return None
    instrument = intent.get("instrument_identifier")
    side = str(intent.get("side") or "").lower()
    outcome = str(intent.get("outcome") or "").lower()
    if (
        not isinstance(instrument, str)
        or not instrument
        or side not in SIDE_LABELS
        or outcome not in {"yes", "no"}
    ):
        return None
    metadata = markets.get(instrument, {})
    if metadata and metadata.get("outcome") != outcome:
        raise ExecutionBriefError(
            f"intent {intent_index} outcome differs from the verified report"
        )
    base = {
        "intent_index": intent_index,
        "question": metadata.get("question"),
        "url": metadata.get("url"),
        "side": side,
        "outcome": outcome,
        "currency": clean_text(intent.get("currency"), limit=24),
        "requested_quantity": decimal_text(intent.get("quantity")),
        "requested_notional": decimal_text(intent.get("notional")),
    }
    if disposition == "skipped":
        skip = raw.get("skip")
        skip = skip if isinstance(skip, dict) else {}
        return {
            **base,
            "status": "SKIPPED",
            "code": clean_text(skip.get("code"), limit=80),
            "message": clean_text(skip.get("message")),
            "planned_quantity": None,
            "filled_quantity": "0",
            "actual_vwap": None,
            "collateral_effect": "0",
        }
    actual = receipt_index.get(intent_index)
    planned = raw.get("planned_order")
    planned = planned if isinstance(planned, dict) else {}
    if actual is None:
        return {
            **base,
            "status": "NOT_RECORDED",
            "code": None,
            "message": "该计划订单没有取得终态回执。",
            "planned_quantity": decimal_text(planned.get("quantity")),
            "filled_quantity": None,
            "actual_vwap": None,
            "collateral_effect": None,
        }
    status = str(actual.get("status") or "").upper()
    if status not in STATUS_LABELS:
        status = "NOT_RECORDED"
    return {
        **base,
        "status": status,
        "code": clean_text(actual.get("code"), limit=80),
        "message": clean_text(actual.get("message")),
        "planned_quantity": decimal_text(
            actual.get("planned_quantity", planned.get("quantity"))
        ),
        "filled_quantity": decimal_text(actual.get("filled_quantity")),
        "actual_vwap": decimal_text(actual.get("actual_vwap")),
        "collateral_effect": decimal_text(actual.get("collateral_effect")),
    }


def render_item(item: dict[str, Any]) -> str:
    status = str(item["status"])
    icon, status_label = STATUS_LABELS[status]
    side = str(item["side"])
    outcome = str(item["outcome"]).upper()
    details: list[str] = []
    if status == "FULL":
        filled = item.get("filled_quantity")
        vwap = item.get("actual_vwap")
        effect = item.get("collateral_effect")
        currency = item.get("currency") or "pUSD"
        if filled is not None:
            details.append(f"实际成交 {filled} 股")
        if vwap is not None:
            details.append(f"VWAP {vwap}")
        if effect is not None:
            effect_label = "实际支出" if side == "buy" else "实际回收"
            details.append(f"{effect_label} {effect} {markdown_text(currency, fallback='pUSD')}")
    elif status == "NO_FILL":
        planned = item.get("planned_quantity")
        if planned is not None:
            details.append(f"计划 {planned} 股，实际 0 股")
    elif status == "SKIPPED":
        details.append("未提交订单")
    else:
        filled = item.get("filled_quantity")
        if filled is not None:
            details.append(f"记录成交 {filled} 股")
    code = item.get("code")
    if code:
        details.append(f"代码 {markdown_text(code, fallback='unknown')}")
    line = (
        f"- {icon} **{status_label}｜{SIDE_LABELS[side]} {outcome}**｜"
        f"{market_link(item.get('question'), item.get('url'))}"
    )
    if details:
        line += "｜" + "｜".join(details)
    message = item.get("message")
    if message and status != "FULL":
        line += f"\n  - 说明：{markdown_text(message, fallback='未提供')}"
    return line


def build_document(receipt: dict[str, Any], reports_dir: Path) -> dict[str, Any]:
    if receipt.get("schema_version") != "1.0":
        raise ExecutionBriefError("execution receipt has an unsupported schema version")
    planning = receipt.get("planning")
    outcomes = planning.get("intent_outcomes") if isinstance(planning, dict) else None
    if not isinstance(outcomes, list):
        raise ExecutionBriefError("execution receipt has no planning intent outcomes")
    receipt_index = receipt_by_intent(receipt)
    markets = report_market_index(reports_dir)
    items = [
        item
        for raw in outcomes
        if isinstance(raw, dict)
        for item in [item_from_outcome(raw, receipt_index=receipt_index, markets=markets)]
        if item is not None
    ]
    if len(items) != len(outcomes):
        raise ExecutionBriefError("not every planned intent could be summarized")
    counts = {status: 0 for status in STATUS_LABELS}
    for item in items:
        counts[str(item["status"])] += 1
    markdown = "**实际交易逐项回执**\n" + "\n".join(render_item(item) for item in items)
    return {
        "schema_version": "1.0",
        "execution_status": clean_text(receipt.get("status"), limit=80) or "UNKNOWN",
        "item_count": len(items),
        "status_counts": counts,
        "items": items,
        "markdown": markdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = load_json(args.receipt, "execution receipt")
        if not isinstance(receipt, dict):
            raise ExecutionBriefError("execution receipt is not an object")
        write_json(args.output, build_document(receipt, args.reports_dir))
    except ExecutionBriefError as exc:
        print(f"forecast_edge_execution_brief: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
