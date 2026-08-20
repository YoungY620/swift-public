"""Validate a Forecast Edge audit-agent response without making a trading judgment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ALLOWED_STATUSES = {"PASS", "WARN", "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"}
ALLOWED_AGENT_TOOLS = {"Read", "Grep", "Glob", "WebSearch", "FetchURL", "Agent"}
REQUIRED_AGENT_TOOLS = {"Read", "Agent"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:POLYMARKET|AWS|KIMI|FEISHU|GITHUB)_[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSPHRASE)\b"),
    re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\b0x[0-9a-fA-F]{130}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)authorization\s*:\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?im)^(?:cookie|set-cookie)\s*:\s*[^\r\n]{8,}$"),
    re.compile(
        r'(?i)"(?:private[_-]?key|password|api[_-]?key|api[_-]?secret|client[_-]?secret|passphrase|access[_-]?token|refresh[_-]?token|session[_-]?token|auth[_-]?token|cookie|mnemonic|seed[_-]?phrase)"\s*:\s*"[^"\r\n]+"'
    ),
)


class AuditValidationError(ValueError):
    pass


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditValidationError(f"could not read {label}: {exc}") from exc


def resolve(document: Any, dotted: str) -> tuple[bool, Any]:
    value = document
    for part in dotted.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return False, None
    return True, value


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in walk_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in walk_strings(child)]
    return []


def reject_secret_like_text(document: Any) -> None:
    for text in walk_strings(document):
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise AuditValidationError("audit output contains secret-like text")


def extract_last_json_object(text: str) -> dict[str, Any]:
    """Extract the last complete JSON object from a Kimi Code output stream."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise AuditValidationError("audit output contains secret-like text")
    decoder = json.JSONDecoder()
    candidate: dict[str, Any] | None = None
    candidate_end = -1
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and index + end > candidate_end:
            candidate = value
            candidate_end = index + end
    if candidate is None:
        raise AuditValidationError("agent output did not contain a JSON object")
    return candidate


def extract_agent_stream(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract a final judgment and prove it came from a read-only agent run."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise AuditValidationError("audit output contains secret-like text")

    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditValidationError(
                f"agent stream line {line_number} is not JSON"
            ) from exc
        if not isinstance(event, dict):
            raise AuditValidationError(
                f"agent stream line {line_number} is not a JSON object"
            )
        events.append(event)

    assistant_messages = 0
    tool_calls: list[tuple[str, str]] = []
    completed_tool_call_ids: set[str] = set()
    final_content: str | None = None
    for event in events:
        role = event.get("role")
        if role == "assistant":
            assistant_messages += 1
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                final_content = content
            calls = event.get("tool_calls", [])
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise AuditValidationError("agent stream tool_calls is not an array")
            for call in calls:
                if not isinstance(call, dict):
                    raise AuditValidationError("agent stream contains an invalid tool call")
                function = call.get("function")
                call_id = call.get("id")
                if not isinstance(function, dict) or not isinstance(call_id, str):
                    raise AuditValidationError("agent stream contains an incomplete tool call")
                name = function.get("name")
                if not isinstance(name, str) or not name:
                    raise AuditValidationError("agent stream contains an unnamed tool call")
                if name not in ALLOWED_AGENT_TOOLS:
                    raise AuditValidationError(f"agent invoked forbidden tool {name}")
                tool_calls.append((call_id, name))
        elif role == "tool":
            call_id = event.get("tool_call_id")
            if isinstance(call_id, str):
                completed_tool_call_ids.add(call_id)

    observed_tools = {name for _, name in tool_calls}
    missing_tools = sorted(REQUIRED_AGENT_TOOLS - observed_tools)
    if missing_tools:
        raise AuditValidationError(
            "agent did not exercise required tools: " + ", ".join(missing_tools)
        )
    if len(tool_calls) < 2 or assistant_messages < 2:
        raise AuditValidationError("agent stream does not show a multi-step tool-using run")
    incomplete_calls = sorted(
        call_id for call_id, _ in tool_calls if call_id not in completed_tool_call_ids
    )
    if incomplete_calls:
        raise AuditValidationError("agent stream contains incomplete tool calls")
    if final_content is None:
        raise AuditValidationError("agent stream has no final assistant response")

    response = extract_last_json_object(final_content)
    counts = {
        name: sum(1 for _, called_name in tool_calls if called_name == name)
        for name in sorted(observed_tools)
    }
    trace = {
        "schema_version": "1.0",
        "stream_event_count": len(events),
        "assistant_message_count": assistant_messages,
        "tool_call_count": len(tool_calls),
        "completed_tool_result_count": len(completed_tool_call_ids),
        "tool_call_counts": counts,
        "required_tools_observed": sorted(REQUIRED_AGENT_TOOLS),
        "forbidden_tools_observed": [],
        "agentic_trace_verified": True,
    }
    return response, trace


def normalize_unsupported_judgments(
    response: dict[str, Any], rubric: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """Downgrade unsupported positive judgments; never upgrade an Agent judgment."""
    checks = response.get("checks")
    rubric_checks = rubric.get("checks")
    if not isinstance(checks, list) or not isinstance(rubric_checks, list):
        return response
    rubric_by_id = {
        str(item.get("id")): item for item in rubric_checks if isinstance(item, dict)
    }
    missing = {
        str(item) for item in response.get("missing_evidence", []) if isinstance(item, str)
    }
    downgraded = False
    for check in checks:
        if not isinstance(check, dict) or check.get("status") not in {"PASS", "WARN"}:
            continue
        citations = check.get("evidence")
        if not isinstance(citations, list):
            citations = []
        absent = [str(key) for key in citations if isinstance(key, str) and not resolve(evidence, key)[0]]
        if not citations:
            rubric_item = rubric_by_id.get(str(check.get("id")), {})
            required = rubric_item.get("evidence_required", [])
            absent = [str(key) for key in required if isinstance(key, str)]
        if not absent:
            continue
        missing.update(absent)
        check["status"] = "INSUFFICIENT_EVIDENCE"
        check["evidence"] = sorted(set(absent))
        check["reason"] = "缺少支持原判断的必要证据，已由确定性校验器降级。"
        downgraded = True
    anomalies = response.get("anomalies", [])
    if isinstance(anomalies, list):
        for anomaly in anomalies:
            if not isinstance(anomaly, dict) or not isinstance(anomaly.get("evidence"), list):
                continue
            for key in anomaly["evidence"]:
                if isinstance(key, str) and not resolve(evidence, key)[0]:
                    missing.add(key)
    if downgraded:
        response["confidence"] = "LOW"
        statuses = [
            str(item.get("status")) for item in checks if isinstance(item, dict)
        ]
        if "FAIL" in statuses:
            response["overall_status"] = "FAIL"
        else:
            response["overall_status"] = "INSUFFICIENT_EVIDENCE"
    response["missing_evidence"] = sorted(missing)
    return response


def validate_response(response: Any, rubric: dict[str, Any], evidence: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(response), key=lambda error: list(error.path))
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "root"
        raise AuditValidationError(f"audit response schema error at {location}")
    reject_secret_like_text(response)

    rubric_checks = rubric.get("checks")
    response_checks = response.get("checks")
    if not isinstance(rubric_checks, list) or not isinstance(response_checks, list):
        raise AuditValidationError("rubric and response checks must be arrays")
    expected = [(str(item.get("id")), str(item.get("severity"))) for item in rubric_checks]
    actual = [(str(item.get("id")), str(item.get("severity"))) for item in response_checks]
    if actual != expected:
        raise AuditValidationError("response checks must contain each rubric check exactly once, in rubric order")

    reported_missing = response.get("missing_evidence", [])

    def reported_as_missing(key: str) -> bool:
        return any(
            key == missing or key.startswith(missing + ".") or missing.startswith(key + ".")
            for missing in reported_missing
        )

    for item, rubric_item in zip(response_checks, rubric_checks):
        status = item["status"]
        evidence_keys = item["evidence"]
        if status not in ALLOWED_STATUSES:
            raise AuditValidationError(f"unknown status for {item['id']}")
        if status in {"PASS", "WARN"} and not evidence_keys:
            raise AuditValidationError(f"{item['id']} has no evidence citation")
        for key in evidence_keys:
            exists, _ = resolve(evidence, key)
            if not exists and status in {"PASS", "WARN"} and not reported_as_missing(key):
                raise AuditValidationError(f"{item['id']} cites a missing evidence path")

    statuses = [str(item["status"]) for item in response_checks]
    overall = str(response["overall_status"])
    if any(status == "FAIL" and severity == "critical" for status, (_, severity) in zip(statuses, expected)):
        if overall != "FAIL":
            raise AuditValidationError("critical check failure requires overall_status=FAIL")
    elif "FAIL" in statuses and overall not in {"FAIL", "INSUFFICIENT_EVIDENCE"}:
        raise AuditValidationError("a failed check requires overall_status=FAIL or INSUFFICIENT_EVIDENCE")
    elif "INSUFFICIENT_EVIDENCE" in statuses and overall == "PASS":
        raise AuditValidationError("insufficient evidence cannot have overall_status=PASS")
    elif "WARN" in statuses and overall == "PASS":
        raise AuditValidationError("warning cannot have overall_status=PASS")
    elif overall == "PASS" and any(status not in {"PASS", "NOT_APPLICABLE"} for status in statuses):
        raise AuditValidationError("overall PASS has a non-passing check")

    known_ids = {item[0] for item in expected}
    for anomaly in response.get("anomalies", []):
        if anomaly["check_id"] not in known_ids:
            raise AuditValidationError(f"anomaly references unknown check {anomaly['check_id']}")
        for key in anomaly["evidence"]:
            exists, _ = resolve(evidence, key)
            if not exists:
                missing_path_is_reported = reported_as_missing(key)
                if not missing_path_is_reported:
                    raise AuditValidationError("anomaly cites an unreported missing evidence path")


def fallback_response(rubric: dict[str, Any], parent_run_id: str, reason: str) -> dict[str, Any]:
    checks = [
        {
            "id": item["id"],
            "status": "WARN",
            "severity": item["severity"],
            "evidence": ["agent_response"],
            "reason": "审计 agent 未能提供可验证结论；本项未完成 Agent 判断：" + reason[:700],
        }
        for item in rubric["checks"]
    ]
    return {
        "schema_version": "1.0",
        "rubric_id": rubric["rubric_id"],
        "rubric_version": rubric["rubric_version"],
        "overall_status": "WARN",
        "confidence": "LOW",
        "parent_run_id": str(parent_run_id),
        "checks": checks,
        "anomalies": [],
        "missing_evidence": ["agent_response.validated_judgment"],
        "agent_notes": "未采用未经 schema 校验的 agent 输出。" + reason[:800],
    }


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def classify_serious_findings(
    response: dict[str, Any],
    evidence: dict[str, Any],
    *,
    agent_judgment_valid: bool,
) -> list[str]:
    """Return only evidence-backed conditions that require a trading halt."""

    findings: set[str] = set()
    if agent_judgment_valid:
        for check in _list(response.get("checks")):
            if (
                isinstance(check, dict)
                and check.get("severity") == "critical"
                and check.get("status") == "FAIL"
                and isinstance(check.get("id"), str)
            ):
                findings.add(str(check["id"]))

    safety = _dict(evidence.get("evidence_safety"))
    if safety.get("secret_value_scan_passed") is False:
        findings.add("SEC-EVIDENCE")

    jobs = [item for item in _list(evidence.get("jobs")) if isinstance(item, dict)]
    execution_transaction_step_started = False
    for job in jobs:
        if not str(job.get("name", "")).startswith("execution"):
            continue
        for step in _list(job.get("steps")):
            if not isinstance(step, dict):
                continue
            if str(step.get("name", "")).startswith(
                "Validate, cancel open orders, price, execute FOK market orders"
            ) and step.get("conclusion") not in {None, "skipped"}:
                execution_transaction_step_started = True

    diagnostics = _dict(evidence.get("diagnostics"))
    metadata = _dict(diagnostics.get("metadata"))
    if execution_transaction_step_started and any(
        metadata.get(key) is not True
        for key in ("found", "artifact_verified", "secret_pattern_scan_passed")
    ):
        findings.add("EXEC-DIAGNOSTICS")

    receipt = _dict(diagnostics.get("receipt"))
    if not receipt:
        receipt = _dict(_dict(evidence.get("release")).get("execution_receipt"))
    summary = _dict(diagnostics.get("summary"))
    if receipt.get("status") == "UNCERTAIN" or summary.get("execution_status") == "UNCERTAIN":
        findings.add("EXEC-UNCERTAIN")

    receipts = [item for item in _list(receipt.get("receipts")) if isinstance(item, dict)]
    statuses = [str(item.get("status", "")) for item in receipts]
    if "PARTIAL" in statuses:
        findings.add("EXEC-PARTIAL")
    if receipt.get("status") == "COMPLETED" and any(status != "FULL" for status in statuses):
        findings.add("EXEC-RECEIPT-INTEGRITY")
    if receipt.get("status") == "COMPLETED_WITH_WARNINGS" and any(
        status not in {"FULL", "NO_FILL"} for status in statuses
    ):
        findings.add("EXEC-RECEIPT-INTEGRITY")

    share_tolerance = Decimal("0.01")
    cash_tolerance = Decimal("0.01")
    for item in receipts:
        status = str(item.get("status", ""))
        planned = _decimal(item.get("planned_quantity"))
        filled = _decimal(item.get("filled_quantity"))
        limit_price = _decimal(item.get("planned_limit_price"))
        actual_vwap = _decimal(item.get("actual_vwap"))
        cash_effect = _decimal(item.get("collateral_effect"))
        side = str(item.get("side", "")).lower()
        if status == "NO_FILL":
            before_token = _decimal(item.get("before_token_balance"))
            after_token = _decimal(item.get("after_token_balance"))
            before_cash = _decimal(item.get("before_collateral_balance"))
            after_cash = _decimal(item.get("after_collateral_balance"))
            valid_no_fill = (
                item.get("code") == "FOK_NOT_FILLED"
                and not item.get("order_id")
                and item.get("order_type") == "FOK"
                and item.get("execution_style") == "protected_market"
                and side in {"buy", "sell"}
                and planned is not None
                and planned > 0
                and limit_price is not None
                and limit_price > 0
                and filled == 0
                and actual_vwap == 0
                and cash_effect == 0
                and not item.get("trades")
                and not item.get("transaction_hashes")
                and before_token is not None
                and after_token is not None
                and abs(after_token - before_token) <= share_tolerance
                and before_cash is not None
                and after_cash is not None
                and abs(after_cash - before_cash) <= cash_tolerance
            )
            if not valid_no_fill:
                findings.add("EXEC-INVALID-NO-FILL")
            continue
        if status != "FULL":
            continue
        if None in {planned, filled, limit_price, actual_vwap, cash_effect} or side not in {
            "buy",
            "sell",
        }:
            findings.add("EXEC-RECEIPT-INTEGRITY")
            continue
        assert planned is not None
        assert filled is not None
        assert limit_price is not None
        assert actual_vwap is not None
        assert cash_effect is not None
        if planned <= 0 or filled <= 0 or filled > planned + share_tolerance:
            findings.add("EXEC-PROTECTION")
        if side == "buy" and actual_vwap > limit_price:
            findings.add("EXEC-PROTECTION")
        if side == "sell" and actual_vwap < limit_price:
            findings.add("EXEC-PROTECTION")
        planned_cash_limit = _decimal(item.get("planned_cash_limit"))
        if planned_cash_limit is not None and planned > 0:
            protected_cash = planned_cash_limit * filled / planned
            if side == "buy" and cash_effect > protected_cash + cash_tolerance:
                findings.add("EXEC-PROTECTION")
            if side == "sell" and cash_effect + cash_tolerance < protected_cash:
                findings.add("EXEC-PROTECTION")

    preflight = _dict(diagnostics.get("preflight"))
    verification = _dict(diagnostics.get("verification"))
    cancellation = _dict(receipt.get("pre_execution_cancellation"))
    open_counts = (
        preflight.get("open_order_count"),
        verification.get("open_order_count"),
        cancellation.get("remaining_open_order_count"),
    )
    for value in open_counts:
        if value is None:
            continue
        parsed = _decimal(value)
        if parsed is None:
            findings.add("EXEC-RECEIPT-INTEGRITY")
        elif parsed != 0:
            findings.add("EXEC-OPEN-ORDERS")
    if any(
        isinstance(order, dict) and order.get("open") is not False
        for order in _list(verification.get("orders"))
    ):
        findings.add("EXEC-OPEN-ORDERS")
    return sorted(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("validate", "normalize", "extract", "fallback", "classify")
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path)
    parser.add_argument("--parent-run-id", default="0")
    parser.add_argument("--agent-judgment-valid", action="store_true")
    args = parser.parse_args()

    rubric = load_json(args.rubric, "rubric")
    evidence = load_json(args.evidence, "evidence")
    schema = load_json(args.schema, "audit schema")
    if args.command == "extract":
        if args.trace_output is None:
            raise AuditValidationError("extract requires --trace-output")
        response, trace = extract_agent_stream(args.input.read_text(encoding="utf-8"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        args.trace_output.parent.mkdir(parents=True, exist_ok=True)
        args.trace_output.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    if args.command == "classify":
        response = load_json(args.input, "audit result")
        result = {
            "overall_status": response.get("overall_status", "WARN"),
            "confidence": response.get("confidence", "LOW"),
            "serious_findings": classify_serious_findings(
                response,
                evidence,
                agent_judgment_valid=args.agent_judgment_valid,
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    if args.command == "normalize":
        response = load_json(args.input, "agent response")
        response = normalize_unsupported_judgments(response, rubric, evidence)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    if args.command == "fallback":
        response = fallback_response(rubric, args.parent_run_id, "agent 没有返回可校验的 JSON")
    else:
        response = load_json(args.input, "agent response")
        validate_response(response, rubric, evidence, schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditValidationError as exc:
        print(f"audit validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
