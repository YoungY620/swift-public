"""Emit machine-readable static invariants for the Forecast Edge workflow graph."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def between(text: str, start: str, end: str | None = None) -> str:
    begin = text.index(start)
    finish = len(text) if end is None else text.index(end, begin)
    return text[begin:finish]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    parent = (root / ".github/workflows/forecast-edge.yml").read_text(encoding="utf-8")
    research = (root / ".github/workflows/forecast-edge-research.yml").read_text(encoding="utf-8")
    execution = (root / ".github/workflows/execute-polymarket.yml").read_text(encoding="utf-8")
    audit = (root / ".github/workflows/forecast-edge-audit.yml").read_text(encoding="utf-8")
    kimi_watchdog = (root / "scripts/run_kimi_code_with_idle_timeout.sh").read_text(
        encoding="utf-8"
    )
    combined = parent + research + execution
    release = between(parent, "  release:\n", "\n  cleanup:\n")
    execution_call = between(parent, "  execution:\n", "\n  release:\n")
    execution_step = between(
        execution,
        "      - name: Validate, cancel open orders, price, execute FOK market orders, and verify locally\n",
        "\n      - name: Build and persist secret-free execution diagnostics\n",
    )
    diagnostics_step = between(
        execution,
        "      - name: Build and persist secret-free execution diagnostics\n",
        "\n      - name: Remove private execution runtime\n",
    )
    audit_agent_step = between(
        audit,
        "      - name: Run read-only Kimi Code Judge agent\n",
        "\n      - name: Validate agent judgment or produce fail-closed audit",
    )

    audit_secrets = sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", audit)))
    audit_agent_secrets = sorted(
        set(re.findall(r"secrets\.([A-Z0-9_]+)", audit_agent_step))
    )
    labels = [
        label
        for label in (
            "self-hosted",
            "Linux",
            "X64",
            "forecast-edge",
            "polymarket-execution",
            "ec2",
            "eu-north-1",
        )
        if f"      - {label}\n" in execution
    ]
    contract = {
        "hourly_wakeup_with_interval_gate": (
            'cron: "23 * * * *"' in parent
            and "(epoch_hour - anchor) % interval == 0" in parent
            and "FORECAST_EDGE_INTERVAL_HOURS" in parent
        ),
        "parent_concurrency_safe": (
            "group: forecast-edge-parent-${{ github.repository }}" in parent
            and "cancel-in-progress: false" in parent
        ),
        "execution_requires_intents": (
            "needs.gate.outputs.execution-ready == 'true'" in execution_call
            and "needs.research.outputs.has-intents == 'true'" in execution_call
        ),
        "diagnostics_after_execution": (
            "actions/upload-artifact@v4" in diagnostics_step
            and "actions: write" in execution_call
            and "actions: write" in execution
            and execution.index("Validate, cancel open orders, price, execute FOK market orders, and verify locally")
            < execution.index("Build and persist secret-free execution diagnostics")
            < execution.index("Upload private-fork execution diagnostics for post-run Judge")
            and "actions/upload-artifact" not in research
        ),
        "execution_diagnostics": (
            "build_polymarket_execution_diagnostics.py" in diagnostics_step
            and "execution-diagnostics.tar.gz" in diagnostics_step
            and "PERSISTENT_DIAGNOSTICS_ROOT" in execution
            and "retention-days: 90" in diagnostics_step
            and "verify_polymarket_execution_diagnostics.py" in audit
        ),
        "diagnostic_secret_guards": (
            "--secret-file \"$SERVER_SECRET_PATH\"" in diagnostics_step
            and "server credential value detected" in
            (root / "scripts/build_polymarket_execution_diagnostics.py").read_text(encoding="utf-8")
            and "signed_order_digest" in
            (root / "scripts/build_polymarket_execution_diagnostics.py").read_text(encoding="utf-8")
            and '"signed_order":' not in
            (root / "scripts/build_polymarket_execution_diagnostics.py").read_text(encoding="utf-8")
        ),
        "agentic_judge_trace": (
            "kimi --agent-file" in audit_agent_step
            and "--yolo" not in audit_agent_step
            and "--output-format stream-json" in audit_agent_step
            and '| stdbuf -oL tee "$AUDIT_AGENT_RAW_RESPONSE_PATH"' in audit_agent_step
            and 'tee "$AUDIT_RUNTIME_DIR/agent.stderr" >&2' in audit_agent_step
            and "--trace-output \"$AUDIT_AGENT_TRACE_PATH\"" in audit_agent_step
            and "extract_agent_stream" in
            (root / "scripts/forecast_edge_audit.py").read_text(encoding="utf-8")
            and audit_agent_secrets == ["KIMI_CODE_API_KEY"]
        ),
        "research_kimi_live_stream": (
            research.count("--output-format stream-json") >= 4
            and research.count("kimi --skills-dir") >= 4
            and "--yolo" not in research
            and research.count("scripts/run_kimi_code_with_idle_timeout.sh") >= 4
            and research.count('--idle-seconds "$KIMI_IDLE_TIMEOUT_SECONDS"') >= 4
            and 'KIMI_IDLE_TIMEOUT_SECONDS: "900"' in research
            and '2> >(stdbuf -oL tee "$stderr_log" >&2)' in kimi_watchdog
            and '| stdbuf -oL tee "$stdout_log"' in kimi_watchdog
            and "current_bytes != last_bytes" in kimi_watchdog
            and "current_epoch - last_activity_epoch < idle_seconds" in kimi_watchdog
            and 'kill -TERM -- "-$session_pid"' in kimi_watchdog
            and "exit 124" in kimi_watchdog
        ),
        "final_iteration_notification": (
            "  notify:\n" in audit
            and "always()" in between(audit, "  notify:\n")
            and "github.event_name == 'workflow_run'" in between(audit, "  notify:\n")
            and "FEISHU_APP_ID" in between(audit, "  notify:\n")
            and "AUDIT_OVERALL_STATUS" in between(audit, "  notify:\n")
        ),
        "release_after_execution": (
            "needs: [gate, research, execution]" in release
            and "needs.execution.result == 'success'" in release
            and "needs.execution.result == 'skipped'" in release
        ),
        "cancel_before_preflight": (
            execution_step.index("cancel-open-orders") < execution_step.index(" preflight ")
        ),
        "preflight_requires_zero_open_orders": (
            'if preflight.get("open_order_count") != 0:'
            in (root / "execution/polymarket/cli.py").read_text(encoding="utf-8")
        ),
        "fok_protected_market": (
            'order_type="FOK"'
            in (root / "execution/polymarket/sdk_adapter.py").read_text(encoding="utf-8")
            and '"execution_style": "protected_market"'
            in (root / "execution/polymarket/engine.py").read_text(encoding="utf-8")
        ),
        "notification_order": (
            release.index("Publish one final iteration Release")
            < release.index("Create post-release Issue notification")
            < release.index("Send post-release notification to Feishu")
        ),
        "credential_isolation": (
            "SERVER_SECRET_PATH: /home/ubuntu/poly-edge-live/runtime/secrets.json" in execution
            and "secrets.POLYMARKET_" not in execution
            and audit_agent_secrets == ["KIMI_CODE_API_KEY"]
        ),
        "audit_circuit_breaker": (
            "Trip production circuit breaker on serious findings" in audit
            and "secrets.FORECAST_EDGE_BREAKER_TOKEN || github.token" in audit
            and "POLYMARKET_LIVE_TRADING_ENABLED" in audit
            and "actions/workflows/forecast-edge.yml/disable" in audit
            and '(.status == "FAIL")' in audit
            and 'and .status == "INSUFFICIENT_EVIDENCE"' in audit
            and "github.event_name == 'workflow_run'" in audit
        ),
        "persistent_pause_gate": (
            "AUTOMATION_PAUSED" in parent
            and 'Forecast Edge automation is paused by the production circuit breaker' in parent
            and "FORECAST_EDGE_AUTOMATION_PAUSED" in audit
        ),
        "execution_runner_labels": labels,
        "audit_agent_credential_count": len(audit_agent_secrets),
        "audit_workflow_credential_count": len(audit_secrets),
    }
    print(json.dumps(contract, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
