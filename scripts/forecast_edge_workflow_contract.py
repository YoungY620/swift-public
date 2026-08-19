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
    combined = parent + research + execution
    release = between(parent, "  release:\n", "\n  cleanup:\n")
    execution_call = between(parent, "  execution:\n", "\n  release:\n")
    execution_step = between(
        execution,
        "      - name: Validate, cancel open orders, price, execute FOK market orders, and verify locally\n",
        "\n      - name: Confirm sanitized execution handoff\n",
    )

    audit_secrets = sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", audit)))
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
        "no_actions_artifacts": (
            "actions/upload-artifact" not in combined
            and "actions/download-artifact" not in combined
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
            and audit_secrets == ["KIMI_CODE_API_KEY"]
        ),
        "audit_circuit_breaker": (
            "Trip production circuit breaker on serious findings" in audit
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
        "audit_agent_credential_count": len(audit_secrets),
    }
    print(json.dumps(contract, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
