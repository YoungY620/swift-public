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
    release = between(parent, "  release:\n")
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
        "\n      - name: Validate agent judgment or produce warning fallback",
    )

    audit_secrets = sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", audit)))
    research_secrets = sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", research)))
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
        "execution_runs_when_requested": (
            "needs.gate.outputs.execution-ready == 'true'" in execution_call
            and "needs.research.result == 'success'" in execution_call
            and "needs.research.outputs.has-intents" not in execution_call
        ),
        "zero_intent_execution_lifecycle": (
            'test -f "$RESEARCH_HANDOFF_DIR/trade-intents.jsonl"' in execution
            and 'test -s "$RESEARCH_HANDOFF_DIR/trade-intents.jsonl"' not in execution
            and "without actionable trade intents" not in execution
            and "validate-artifact" in execution_step
            and "cancel-open-orders" in execution_step
            and " preflight " in execution_step
            and " plan " in execution_step
            and " execute " in execution_step
            and " verify " in execution_step
        ),
        "diagnostics_after_execution": (
            "actions/upload-artifact@v4" in diagnostics_step
            and "actions: write" in execution_call
            and "actions: write" in execution
            and execution.index("Validate, cancel open orders, price, execute FOK market orders, and verify locally")
            < execution.index("Build and persist secret-free execution diagnostics")
            < execution.index("Upload private-fork execution diagnostics for post-run Judge")
        ),
        "research_ec2_isolation": (
            "runs-on: ubuntu-latest" in research
            and "self-hosted" not in research
            and "POLYMARKET_EC2" not in research
            and "EC2_HOST" not in research
            and re.search(r"\b(?:ssh|scp)\b", research) is None
            and "POLYMARKET_EC2" not in parent
            and re.search(r"\b(?:ssh|scp)\b", parent) is None
        ),
        "research_artifact_handoff": (
            "Build secret-free research artifact handoff" in research
            and "forecast-edge-research-handoff-${{ github.run_id }}-${{ github.run_attempt }}" in research
            and "actions/upload-artifact@v4" in research
            and "Download research handoff artifact" in execution
            and "actions/download-artifact@v4" in execution
            and "trade-intents.sha256" in execution
            and "Download completed research handoff artifact" in parent
            and "actions/download-artifact@v4" in parent
        ),
        "research_diagnostics": (
            "Prepare secret-free research diagnostics" in research
            and "Upload research diagnostics for post-run Judge" in research
            and "forecast-edge-research-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}" in research
            and "kimi-sessions" in research
            and "wire.jsonl" in research
            and "output.log" in research
            and "sanitize_research_diagnostics.py" in research
            and "redaction-report.json" in research
            and "Collect and verify research diagnostic artifact" in audit
            and "research_diagnostics" in audit
            and "retention-days: 90" in research
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
        "agentic_audit_business_contract": (
            "Enforce complete Agent Judge and briefing deliverables" in audit
            and "agent_judgment_valid:" in audit
            and "feishu_brief_valid:" in audit
            and "business_complete:" in audit
            and "FEISHU-BRIEF-INFRASTRUCTURE" in (
                root / "scripts/forecast_edge_audit.py"
            ).read_text(encoding="utf-8")
            and "does not by itself trigger the production trading circuit breaker" in audit
        ),
        "audit_agent_diagnostics": (
            "Package secret-scanned Agent audit diagnostics" in audit
            and "Upload private Agent audit diagnostics" in audit
            and "forecast-edge-audit-diagnostics-${{ env.PARENT_RUN_ID }}-${{ github.run_attempt }}" in audit
            and "judge-agent-stream.raw.jsonl" in audit
            and "judge-agent.stderr" in audit
            and "feishu-brief-agent-stream.raw.jsonl" in audit
            and "feishu-brief-agent.stderr" in audit
            and "kimi-code-sessions" in audit
            and "audit-diagnostics-manifest.json" in audit
            and "sanitize_research_diagnostics.py" in audit
            and "--secret-env KIMI_MODEL_API_KEY" in audit
        ),
        "missing_audit_configuration_is_noop": (
            "Forecast Edge audit is not configured; skipping this run" in audit
            and 'echo "audit-applicable=false"' in audit
            and "missing_configuration=()" in between(audit, "  select:\n", "\n  audit:\n")
        ),
        "research_kimi_live_stream": (
            research.count("--output-format stream-json") >= 4
            and research.count("kimi --skills-dir") >= 4
            and "--yolo" not in research
            and research.count("scripts/run_kimi_code_with_idle_timeout.sh") >= 4
            and research.count('--idle-seconds "$KIMI_IDLE_TIMEOUT_SECONDS"') >= 4
            and research.count('--activity-root "$KIMI_CODE_HOME/sessions"') >= 4
            and 'KIMI_IDLE_TIMEOUT_SECONDS: "900"' in research
            and '2> >(stdbuf -oL tee "$stderr_log" >&2)' in kimi_watchdog
            and '| stdbuf -oL tee "$stdout_log"' in kimi_watchdog
            and "current_bytes != last_bytes" in kimi_watchdog
            and "current_epoch - last_activity_epoch < idle_seconds" in kimi_watchdog
            and "-name 'wire.jsonl' -o -name 'output.log'" in kimi_watchdog
            and 'kill -TERM -- "-$session_pid"' in kimi_watchdog
            and "exit 124" in kimi_watchdog
        ),
        "nonretryable_kimi_failures_stop_repairs": (
            "Probe Kimi model routing" in research
            and research.count("scripts/classify_kimi_failure.py") == 4
            and "steps.kimi-initial.outputs.retryable == 'true'" in research
            and "steps.kimi-repair-1.outputs.retryable == 'true'" in research
            and "steps.kimi-repair-2.outputs.retryable == 'true'" in research
            and "Kimi Code initial invocation failed with a non-retryable authentication or quota error" in research
        ),
        "final_iteration_notification": (
            "  notify:\n" in audit
            and "always()" in between(audit, "  notify:\n")
            and "github.event_name == 'workflow_run'" in between(audit, "  notify:\n")
            and "FEISHU_APP_ID" in between(audit, "  notify:\n")
            and "AUDIT_OVERALL_STATUS" in between(audit, "  notify:\n")
            and "Run read-only Kimi Code Feishu briefing agent" in audit
            and "forecast-edge-feishu-reporter.md" in audit
            and "forecast-edge-feishu-brief-" in audit
            and "audit-notification.json" in audit
            and 'tag:"collapsible_panel"' in audit
            and "expanded:false" in audit
            and "template=green" in between(audit, "  notify:\n")
            and "template=red" in between(audit, "  notify:\n")
            and "Send post-release notification to Feishu" not in parent
        ),
        "release_after_execution": (
            "needs: [gate, research, execution]" in release
            and "needs.gate.outputs.execution-ready == 'true'" in release
            and "needs.execution.result == 'success'" in release
            and "needs.gate.outputs.execution-ready != 'true'" in release
            and "needs.execution.result == 'skipped'" in release
        ),
        "release_direct_report_inventory_safe": (
            'actual-direct-reports.txt' in parent
            and 'expected-direct-reports.txt' in parent
            and 'LC_ALL=C sort -u' in parent
            and 'if ! cmp -s "$actual_reports" "$expected_reports"' in parent
            and 'diff -u "$expected_reports" "$actual_reports" || true' in parent
            and 'actual_direct_reports[*]' not in parent
            and 'expected_direct_reports[*]' not in parent
        ),
        "release_accepts_verified_warning_receipts": (
            '.status == "COMPLETED_WITH_WARNINGS"' in parent
            and '.status == "NO_FILL" and .code == "FOK_NOT_FILLED"' in parent
            and '.status == "FULL"' in parent
            and '.status == "UNCERTAIN"' not in parent
            and '.status == "PARTIAL"' not in parent
            and '.status == "REJECTED"' not in parent
        ),
        "zero_intent_release_safe": (
            'trade_intents_published=false' in release
            and 'published:$trade_intents_published' in release
            and 'asset:(if $trade_intents_asset == "null" then null else $trade_intents_asset end)'
            in release
            and 'if [[ "$HAS_INTENTS" == "true" ]]' in release
            and 'assets+=("$RELEASE_RUNTIME_DIR/trade-intents.jsonl")' in release
            and 'test ! -s "$RELEASE_RUNTIME_DIR/trade-intents.jsonl"' in release
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
        "execution_id_derivation": {
            "algorithm": "sha256",
            "payload_format": (
                "repository:workflow_run_id:workflow_run_attempt:"
                "trade_intents_sha256"
            ),
            "source_verified": (
                '"$GITHUB_REPOSITORY:$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT:$actual_digest"'
                in execution
                and "| sha256sum | cut -d ' ' -f 1" in execution
                and 'echo "EXECUTION_ID=$execution_id" >> "$GITHUB_ENV"' in execution
            ),
        },
        "notification_order": (
            release.index("Publish one final iteration Release")
            < release.index("Create post-release Issue notification")
            and "Send post-release notification to Feishu" not in parent
            and "Send final production iteration status to Feishu" in audit
        ),
        "credential_isolation": (
            "SERVER_SECRET_PATH: /home/ubuntu/poly-edge-live/runtime/secrets.json" in execution
            and "secrets.POLYMARKET_" not in execution
            and research_secrets
            == ["FORECAST_EDGE_MARKET_ACCOUNTS_JSON", "KIMI_CODE_API_KEY"]
            and audit_agent_secrets == ["KIMI_CODE_API_KEY"]
        ),
        "missing_configuration_is_noop": (
            "missing_configuration=()" in parent
            and "Forecast Edge is not configured; skipping this run" in parent
            and 'echo "should-run=false"' in parent
            and 'echo "execution-requested=false"' in parent
            and 'echo "execution-ready=false"' in parent
            and "FORECAST_EDGE_MARKET_ACCOUNTS_JSON: ${{ secrets.FORECAST_EDGE_MARKET_ACCOUNTS_JSON }}"
            in parent
            and "MARKET_ACCOUNTS_JSON: ${{ secrets.FORECAST_EDGE_MARKET_ACCOUNTS_JSON }}"
            in research
            and "vars.FORECAST_EDGE_MARKET_ACCOUNTS_JSON" not in combined
        ),
        "audit_circuit_breaker": (
            "Trip production circuit breaker on serious findings" in audit
            and "GH_TOKEN: ${{ secrets.FORECAST_EDGE_BREAKER_TOKEN }}" in audit
            and "POLYMARKET_LIVE_TRADING_ENABLED" in audit
            and "actions/workflows/forecast-edge.yml/disable" in audit
            and "--agent-judgment-valid" in audit
            and "classify_serious_findings" in (
                root / "scripts/forecast_edge_audit.py"
            ).read_text(encoding="utf-8")
            and "EXEC-UNCERTAIN" in (
                root / "scripts/forecast_edge_audit.py"
            ).read_text(encoding="utf-8")
            and 'and .status == "INSUFFICIENT_EVIDENCE"' not in audit
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
