"""Validate a Forecast Edge audit-agent response without making a trading judgment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ALLOWED_STATUSES = {"PASS", "WARN", "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:POLYMARKET|AWS|KIMI|FEISHU|GITHUB)_[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSPHRASE)\b"),
    re.compile(r"\b(?:ghp|github_pat|sk)-[A-Za-z0-9_-]{20,}\b"),
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
    """Extract the last complete JSON object from a CLI stream."""
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

    for item, rubric_item in zip(response_checks, rubric_checks):
        status = item["status"]
        evidence_keys = item["evidence"]
        if status not in ALLOWED_STATUSES:
            raise AuditValidationError(f"unknown status for {item['id']}")
        if status != "NOT_APPLICABLE" and not evidence_keys:
            raise AuditValidationError(f"{item['id']} has no evidence citation")
        for key in evidence_keys:
            exists, _ = resolve(evidence, key)
            if not exists and status in {"PASS", "WARN"}:
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
                missing_path_is_reported = any(
                    key == missing or key.startswith(missing + ".") or missing.startswith(key + ".")
                    for missing in response.get("missing_evidence", [])
                )
                if not missing_path_is_reported:
                    raise AuditValidationError("anomaly cites an unreported missing evidence path")


def fallback_response(rubric: dict[str, Any], parent_run_id: str, reason: str) -> dict[str, Any]:
    checks = [
        {
            "id": item["id"],
            "status": "INSUFFICIENT_EVIDENCE",
            "severity": item["severity"],
            "evidence": ["agent_response"],
            "reason": "审计 agent 未能提供可验证结论：" + reason[:700],
        }
        for item in rubric["checks"]
    ]
    return {
        "schema_version": "1.0",
        "rubric_id": rubric["rubric_id"],
        "rubric_version": rubric["schema_version"],
        "overall_status": "INSUFFICIENT_EVIDENCE",
        "confidence": "LOW",
        "parent_run_id": str(parent_run_id),
        "checks": checks,
        "anomalies": [],
        "missing_evidence": ["agent_response"],
        "agent_notes": "未采用未经 schema 校验的 agent 输出。" + reason[:800],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "extract", "fallback"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-run-id", default="0")
    args = parser.parse_args()

    rubric = load_json(args.rubric, "rubric")
    evidence = load_json(args.evidence, "evidence")
    schema = load_json(args.schema, "audit schema")
    if args.command == "extract":
        response = extract_last_json_object(args.input.read_text(encoding="utf-8"))
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
