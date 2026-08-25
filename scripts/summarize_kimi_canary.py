#!/usr/bin/env python3
"""Summarize one Kimi Code canary without reproducing prompts or provider errors."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def extract_last_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidate: dict[str, Any] | None = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidate = value
    return candidate


def numeric_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): number
        for key, number in value.items()
        if isinstance(number, (int, float)) and not isinstance(number, bool)
    }


def walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def collect_observability(
    values: list[dict[str, Any]],
) -> tuple[list[dict[str, int | float]], list[str]]:
    usage_samples: list[dict[str, int | float]] = []
    request_ids: set[str] = set()
    request_id_keys = {"request_id", "request-id", "x-request-id", "x_request_id"}
    for value in values:
        for item in walk_json(value):
            if not isinstance(item, dict):
                continue
            usage = numeric_usage(item.get("usage"))
            if usage and usage not in usage_samples:
                usage_samples.append(usage)
            for key, candidate in item.items():
                if str(key).lower() not in request_id_keys:
                    continue
                if isinstance(candidate, str) and 0 < len(candidate) <= 200:
                    request_ids.add(candidate)
    return usage_samples, sorted(request_ids)[:20]


def failure_category(stderr_text: str, exit_status: int) -> str:
    if exit_status == 0:
        return "NONE"
    if exit_status == 124 or "no captured or session events" in stderr_text.lower():
        return "IDLE_TIMEOUT"
    if re.search(
        r"provider\.auth_error|usage\s+limit|billing\s+cycle|"
        r"(?:quota|credits?)\s+(?:exceeded|exhausted|reached)|"
        r"(?:invalid|expired|missing)\s+(?:api\s+)?key|(?:401|403)\b",
        stderr_text,
        re.IGNORECASE,
    ):
        return "AUTH_OR_QUOTA"
    return "PROVIDER_OR_AGENT"


def summarize(
    *,
    stream_path: Path,
    stderr_path: Path,
    provider_probe_path: Path,
    evidence_path: Path,
    sessions_path: Path,
    requested_model: str,
    cli_version: str,
    exit_status: int,
) -> dict[str, Any]:
    provider_probe = load_json(provider_probe_path, "provider probe")
    evidence = load_json(evidence_path, "canary evidence")
    events: list[dict[str, Any]] = []
    malformed_lines = 0
    try:
        stream_text = stream_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        stream_text = ""
    for line in stream_text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            malformed_lines += 1

    assistant_messages = 0
    tool_calls: list[tuple[str, str]] = []
    completed_ids: set[str] = set()
    final_content: str | None = None
    observed_models: set[str] = set()
    for event in events:
        model = event.get("model")
        if isinstance(model, str) and model:
            observed_models.add(model)
        role = event.get("role")
        if role == "assistant":
            assistant_messages += 1
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                final_content = content
            calls = event.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    call_id = call.get("id")
                    function = call.get("function")
                    name = function.get("name") if isinstance(function, dict) else None
                    if isinstance(call_id, str) and isinstance(name, str):
                        tool_calls.append((call_id, name))
        elif role == "tool":
            call_id = event.get("tool_call_id")
            if isinstance(call_id, str):
                completed_ids.add(call_id)

    final_object = extract_last_object(final_content or "")
    expected_final = {
        "status": "ok",
        "nonce": evidence.get("nonce"),
        "evidence_name": evidence.get("evidence_name"),
        "evidence_value": evidence.get("evidence_value"),
    }
    read_calls = [call_id for call_id, name in tool_calls if name == "Read"]
    unexpected_tools = sorted({name for _, name in tool_calls if name != "Read"})
    model_ids = provider_probe.get("model_ids")
    checks = {
        "provider_http_ok": (
            provider_probe.get("http_status") == 200
            and provider_probe.get("curl_exit_status", 0) == 0
        ),
        "requested_model_advertised": (
            isinstance(model_ids, list) and requested_model in model_ids
        ),
        "cli_exit_zero": exit_status == 0,
        "stream_is_ndjson": malformed_lines == 0 and bool(events),
        "exactly_one_read_call": len(read_calls) == 1,
        "read_call_completed": len(read_calls) == 1 and read_calls[0] in completed_ids,
        "no_unexpected_tools": not unexpected_tools,
        "final_evidence_matches": final_object == expected_final,
    }
    try:
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        stderr_text = ""
    stderr_bytes = len(stderr_text.encode("utf-8"))
    session_files = []
    session_events: list[dict[str, Any]] = []
    if sessions_path.is_dir():
        for path in sorted(sessions_path.rglob("*")):
            if not path.is_file():
                continue
            session_files.append(str(path.relative_to(sessions_path)))
            if path.name not in {"wire.jsonl", "output.log"}:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    session_events.append(value)
    usage_samples, request_ids = collect_observability(events + session_events)
    return {
        "schema_version": "1.0",
        "passed": all(checks.values()),
        "checks": checks,
        "requested_model": requested_model,
        "provider": {
            "base_url_host": provider_probe.get("base_url_host"),
            "http_status": provider_probe.get("http_status"),
            "curl_exit_status": provider_probe.get("curl_exit_status"),
            "request_id": provider_probe.get("request_id"),
            "rate_limit_headers": provider_probe.get("rate_limit_headers", {}),
            "advertised_model_count": len(model_ids) if isinstance(model_ids, list) else 0,
        },
        "kimi_code": {
            "cli_version": cli_version,
            "exit_status": exit_status,
            "failure_category": failure_category(stderr_text, exit_status),
            "stream_event_count": len(events),
            "malformed_stream_line_count": malformed_lines,
            "assistant_message_count": assistant_messages,
            "tool_call_count": len(tool_calls),
            "tool_call_counts": {
                name: sum(1 for _, called_name in tool_calls if called_name == name)
                for name in sorted({name for _, name in tool_calls})
            },
            "completed_tool_result_count": len(completed_ids),
            "unexpected_tools": unexpected_tools,
            "observed_models": sorted(observed_models),
            "usage_samples": usage_samples,
            "agent_request_ids": request_ids,
            "stderr_bytes": stderr_bytes,
            "session_file_count": len(session_files),
            "session_files": session_files,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--provider-probe", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--requested-model", required=True)
    parser.add_argument("--cli-version", required=True)
    parser.add_argument("--exit-status", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(
        stream_path=args.stream,
        stderr_path=args.stderr,
        provider_probe_path=args.provider_probe,
        evidence_path=args.evidence,
        sessions_path=args.sessions,
        requested_model=args.requested_model,
        cli_version=args.cli_version,
        exit_status=args.exit_status,
    )
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
