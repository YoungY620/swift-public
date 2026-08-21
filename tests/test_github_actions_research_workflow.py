from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_PATH = ROOT / ".github" / "workflows" / "forecast-edge.yml"
RESEARCH_PATH = ROOT / ".github" / "workflows" / "forecast-edge-research.yml"
EXECUTION_PATH = ROOT / ".github" / "workflows" / "execute-polymarket.yml"
AUDIT_PATH = ROOT / ".github" / "workflows" / "forecast-edge-audit.yml"
SCHEDULED_PROMPT_PATH = ROOT / "automation" / "prompts" / "scheduled-scan.zh-CN.md"
REPAIR_PROMPT_PATH = ROOT / "automation" / "prompts" / "repair-trade-intents.zh-CN.md"
KIMI_WATCHDOG_PATH = ROOT / "scripts" / "run_kimi_code_with_idle_timeout.sh"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parent = PARENT_PATH.read_text(encoding="utf-8")
        cls.research = RESEARCH_PATH.read_text(encoding="utf-8")
        cls.execution = EXECUTION_PATH.read_text(encoding="utf-8")
        cls.audit = AUDIT_PATH.read_text(encoding="utf-8")
        cls.kimi_watchdog = KIMI_WATCHDOG_PATH.read_text(encoding="utf-8")

    @staticmethod
    def step(workflow: str, name: str) -> str:
        marker = f"      - name: {name}\n"
        start = workflow.index(marker)
        end = workflow.find("\n      - name: ", start + len(marker))
        return workflow[start:] if end == -1 else workflow[start:end]

    def test_parent_owns_schedule_and_calls_two_reusable_subworkflows(self) -> None:
        self.assertIn('cron: "23 * * * *"', self.parent)
        self.assertIn("FORECAST_EDGE_INTERVAL_HOURS || '8'", self.parent)
        self.assertIn("uses: ./.github/workflows/forecast-edge-research.yml", self.parent)
        self.assertIn("uses: ./.github/workflows/execute-polymarket.yml", self.parent)
        self.assertIn("needs: [gate, research]", self.parent)
        self.assertIn("needs.gate.outputs.execution-ready == 'true'", self.parent)
        execution_call = self.parent[
            self.parent.index("  execution:\n") : self.parent.index("\n  release:\n")
        ]
        self.assertIn("needs.research.result == 'success'", execution_call)
        self.assertNotIn("needs.research.outputs.has-intents", execution_call)
        self.assertIn("inputs['execute-live-trades']", self.parent)
        self.assertIn("inputs['research-only']", self.parent)
        self.assertNotIn("workflow_run:", self.parent)

    def test_manual_research_only_bypasses_pause_without_execution(self) -> None:
        self.assertIn("research-only:", self.parent)
        self.assertIn(
            'research-only cannot be combined with execute-live-trades', self.parent
        )
        self.assertIn('"$RESEARCH_ONLY" != "true"', self.parent)
        self.assertIn('"$EVENT_NAME" == "workflow_dispatch"', self.parent)

    def test_parent_manifest_records_the_effective_schedule_gate(self) -> None:
        self.assertIn("interval-hours=$interval", self.parent)
        self.assertIn("anchor-utc-hour=$anchor", self.parent)
        self.assertIn("gate-event-name=$EVENT_NAME", self.parent)
        self.assertIn("schedule:{", self.parent)
        self.assertIn("AUTOMATION_PAUSED", self.parent)
        self.assertIn("interval_hours:($schedule_interval_hours|tonumber)", self.parent)

    def test_audit_is_triggered_only_after_the_parent_completes(self) -> None:
        self.assertIn("workflow_run:", self.audit)
        self.assertIn("Forecast Edge research and optional execution", self.audit)
        self.assertIn("types:\n      - completed", self.audit)
        self.assertNotIn("cron:", self.audit)

    def test_missing_audit_secrets_skip_agent_and_notification(self) -> None:
        select = self.step(
            self.audit, "Select completed iterations that need agent review"
        )
        self.assertIn("missing_configuration=()", select)
        self.assertIn("Forecast Edge audit is not configured; skipping this run", select)
        self.assertIn('echo "audit-applicable=false"', select)
        self.assertIn("exit 0", select)

    def test_parent_grants_children_artifact_and_checkout_permissions(self) -> None:
        research_call = self.parent[
            self.parent.index("  research:\n") : self.parent.index("\n  execution:\n")
        ]
        execution_call = self.parent[
            self.parent.index("  execution:\n") : self.parent.index("\n  release:\n")
        ]
        self.assertIn("contents: read", research_call)
        self.assertIn("actions: write", research_call)
        for child_call in (research_call, execution_call):
            self.assertIn("contents: read", child_call)
            self.assertNotIn("contents: write", child_call)
        self.assertIn("actions: write", execution_call)

    def test_children_are_reusable_and_do_not_publish(self) -> None:
        for child in (self.research, self.execution):
            self.assertIn("workflow_call:", child)
            self.assertNotIn("workflow_run:", child)
            self.assertNotIn("gh release create", child)
            self.assertNotIn("gh issue create", child)
        self.assertNotIn("actions/download-artifact", self.research)
        self.assertIn("actions/download-artifact@v4", self.execution)
        self.assertIn("actions/upload-artifact@v4", self.research)
        self.assertIn("actions/upload-artifact@v4", self.execution)
        self.assertNotIn("cron:", self.research)

    def test_research_uses_github_hosted_linux(self) -> None:
        self.assertIn("runs-on: ubuntu-latest", self.research)
        self.assertNotIn("self-hosted", self.research)
        validate = self.step(self.research, "Validate workflow configuration")
        self.assertIn("BASH_VERSINFO[0] < 4", validate)
        self.assertIn(
            "awk curl find gh jq npm python3 setsid sha256sum stat stdbuf tar tee",
            validate,
        )

    def test_trade_intents_use_digest_bound_actions_artifact_without_ec2(self) -> None:
        stage = self.step(self.research, "Build secret-free research artifact handoff")
        upload = self.step(self.research, "Upload research handoff for downstream jobs")
        download = self.step(self.execution, "Download research handoff artifact")
        self.assertIn('install -d -m 700 "$artifact_root"', stage)
        self.assertIn('"$TRADE_INTENTS_PATH"', stage)
        self.assertIn("trade-intents.jsonl", stage)
        self.assertIn("trade-intents.sha256", stage)
        self.assertIn("forecast-edge-research-handoff-", upload)
        self.assertIn("forecast-edge-research-handoff-", download)
        for forbidden in (
            "POLYMARKET_EC2_SSH_PRIVATE_KEY",
            "EC2_HOST_FINGERPRINT",
            "ssh-keyscan",
            "scp ",
            "ssh ",
        ):
            self.assertNotIn(forbidden, self.research)
            self.assertNotIn(forbidden, self.parent)
        self.assertNotIn("trade-intents.jsonl", self.step(
            self.research, "Build current-iteration research Release"
        ))

    def test_private_research_details_are_not_written_to_actions_logs(self) -> None:
        self.assertNotIn('cat "$VALIDATION_ERRORS_PATH"', self.research)
        self.assertNotIn('cat "$VALIDATION_SUMMARY_PATH"', self.research)
        self.assertNotRegex(self.research, r"sed -n .*kimi-attempt-[1-4]\.stderr")
        self.assertNotIn("HTTP 200: $canonical_url", self.research)

    def test_kimi_attempts_stream_cli_events_and_retain_raw_jsonl(self) -> None:
        for attempt, name in enumerate(
            (
                "Run Kimi Code — initial attempt",
                "Run Kimi Code — repair 1 of 3",
                "Run Kimi Code — repair 2 of 3",
                "Run Kimi Code — repair 3 of 3",
            ),
            start=1,
        ):
            step = self.step(self.research, name)
            self.assertIn("kimi --skills-dir", step)
            self.assertNotIn("--yolo", step)
            self.assertIn("--output-format stream-json", step)
            self.assertIn(
                f'--stdout-log "$RUNTIME_DIR/kimi-attempt-{attempt}.stream.jsonl"',
                step,
            )
            self.assertIn(
                f'--stderr-log "$RUNTIME_DIR/kimi-attempt-{attempt}.stderr"',
                step,
            )
            self.assertIn("scripts/run_kimi_code_with_idle_timeout.sh", step)
            self.assertIn('--idle-seconds "$KIMI_IDLE_TIMEOUT_SECONDS"', step)
            self.assertIn('--activity-root "$KIMI_CODE_HOME/sessions"', step)
            self.assertIn("status=$?", step)

        self.assertIn('KIMI_IDLE_TIMEOUT_SECONDS: "900"', self.research)
        self.assertIn('2> >(stdbuf -oL tee "$stderr_log" >&2)', self.kimi_watchdog)
        self.assertIn('| stdbuf -oL tee "$stdout_log"', self.kimi_watchdog)
        self.assertIn("current_bytes != last_bytes", self.kimi_watchdog)
        self.assertIn("current_epoch - last_activity_epoch < idle_seconds", self.kimi_watchdog)
        self.assertIn("-name 'wire.jsonl' -o -name 'output.log'", self.kimi_watchdog)
        self.assertIn('kill -TERM -- "-$session_pid"', self.kimi_watchdog)
        self.assertIn('| tee -a "$stderr_log" >&2', self.kimi_watchdog)
        self.assertIn("exit 124", self.kimi_watchdog)

        audit_agent = self.step(self.audit, "Run read-only Kimi Code Judge agent")
        self.assertIn("kimi --agent-file", audit_agent)
        self.assertNotIn("--yolo", audit_agent)
        self.assertIn(
            '| stdbuf -oL tee "$AUDIT_AGENT_RAW_RESPONSE_PATH"', audit_agent
        )
        self.assertIn('tee "$AUDIT_RUNTIME_DIR/agent.stderr" >&2', audit_agent)
        self.assertIn("kimi_status=${PIPESTATUS[0]}", audit_agent)
        self.assertNotIn('> "$AUDIT_AGENT_RAW_RESPONSE_PATH"', audit_agent)

    def test_recoverable_research_attempts_do_not_leave_error_steps(self) -> None:
        for name in (
            "Run Kimi Code — initial attempt",
            "Run Kimi Code — repair 1 of 3",
            "Run Kimi Code — repair 2 of 3",
            "Run Kimi Code — repair 3 of 3",
        ):
            step = self.step(self.research, name)
            self.assertNotIn("continue-on-error", step)
            self.assertIn('echo "succeeded=false" >> "$GITHUB_OUTPUT"', step)
            self.assertIn('echo "succeeded=true" >> "$GITHUB_OUTPUT"', step)
            self.assertNotIn("::error::", step)

        for name in (
            "Validate initial trade intents",
            "Validate repaired trade intents — 1 of 3",
            "Validate repaired trade intents — 2 of 3",
            "Validate repaired trade intents — 3 of 3",
        ):
            step = self.step(self.research, name)
            self.assertNotIn("continue-on-error", step)
            self.assertIn('echo "valid=false" >> "$GITHUB_OUTPUT"', step)
            self.assertIn('echo "valid=true" >> "$GITHUB_OUTPUT"', step)
            self.assertNotIn("::error::", step)

        enforce = self.step(self.research, "Enforce retry limit")
        self.assertIn("steps.validate-initial.outputs.valid", enforce)
        self.assertIn("::error::Trade intents failed validation", enforce)

        for attempt, name in enumerate(
            (
                "Validate initial trade intents",
                "Validate repaired trade intents — 1 of 3",
                "Validate repaired trade intents — 2 of 3",
                "Validate repaired trade intents — 3 of 3",
            ),
            start=1,
        ):
            step = self.step(self.research, name)
            self.assertIn(f"validation-attempt-{attempt}.stdout", step)
            self.assertIn(f"validation-attempt-{attempt}.errors.txt", step)
            self.assertIn('install -m 600 "$attempt_errors" "$VALIDATION_ERRORS_PATH"', step)

        diagnostics = self.step(self.research, "Prepare secret-free research diagnostics")
        self.assertIn("validation-attempt-*.stdout", diagnostics)
        self.assertIn("validation-attempt-*.errors.txt", diagnostics)

    def test_parent_publishes_only_after_optional_execution_resolves(self) -> None:
        self.assertIn("needs: [gate, research, execution]", self.parent)
        release = self.step(self.parent, "Publish one final iteration Release")
        self.assertIn('gh release create "$RELEASE_TAG"', release)
        self.assertIn('assets+=("$RELEASE_RUNTIME_DIR/trade-intents.jsonl")', release)
        self.assertIn("execution-receipt.json", release)
        release_index = self.parent.index("      - name: Publish one final iteration Release\n")
        issue_index = self.parent.index("      - name: Create post-release Issue notification\n")
        self.assertLess(release_index, issue_index)
        self.assertNotIn("Send post-release notification to Feishu", self.parent)
        self.assertIn("Send final production iteration status to Feishu", self.audit)

    def test_zero_intent_iteration_does_not_upload_an_empty_release_asset(self) -> None:
        manifest = self.step(self.parent, "Build final iteration manifest")
        release = self.step(self.parent, "Publish one final iteration Release")
        self.assertIn('trade_intents_published=false', manifest)
        self.assertIn('if [[ "$HAS_INTENTS" == "true" ]]', manifest)
        self.assertIn('test ! -s "$RELEASE_RUNTIME_DIR/trade-intents.jsonl"', manifest)
        self.assertIn('published:$trade_intents_published', manifest)
        self.assertIn(
            'asset:(if $trade_intents_asset == "null" then null else $trade_intents_asset end)',
            manifest,
        )
        initial_assets = release[
            release.index("          assets=(") : release.index(
                "\n          )", release.index("          assets=(")
            )
        ]
        self.assertNotIn('trade-intents.jsonl', initial_assets)
        self.assertIn('if [[ "$HAS_INTENTS" == "true" ]]', release)
        self.assertIn('assets+=("$RELEASE_RUNTIME_DIR/trade-intents.jsonl")', release)
        self.assertIn('test ! -s "$RELEASE_RUNTIME_DIR/trade-intents.jsonl"', release)

    def test_zero_intent_production_iteration_runs_complete_execution_lifecycle(self) -> None:
        load = self.step(
            self.execution, "Load and verify research trade-intent artifact locally"
        )
        self.assertIn('test -f "$RESEARCH_HANDOFF_DIR/trade-intents.jsonl"', load)
        self.assertNotIn('test -s "$RESEARCH_HANDOFF_DIR/trade-intents.jsonl"', load)
        self.assertNotIn("without actionable trade intents", load)
        execute = self.step(
            self.execution,
            "Validate, cancel open orders, price, execute FOK market orders, and verify locally",
        )
        for command in ("validate", "cancel", "preflight", "plan", "execute", "verify"):
            self.assertIn(f"run_phase {command}", execute)

        release_job = self.parent[self.parent.index("  release:\n") :]
        self.assertIn("needs.gate.outputs.execution-ready == 'true'", release_job)
        self.assertIn("needs.execution.result == 'success'", release_job)
        self.assertIn("needs.gate.outputs.execution-ready != 'true'", release_job)
        self.assertIn("needs.execution.result == 'skipped'", release_job)

    def test_release_uses_file_comparison_for_signed_report_inventory(self) -> None:
        verify = self.step(
            self.parent, "Verify and extract private research package"
        )
        self.assertIn('actual-direct-reports.txt', verify)
        self.assertIn('expected-direct-reports.txt', verify)
        self.assertIn('LC_ALL=C sort -u', verify)
        self.assertIn('if ! cmp -s "$actual_reports" "$expected_reports"', verify)
        self.assertIn('diff -u "$expected_reports" "$actual_reports" || true', verify)
        self.assertNotIn('actual_direct_reports[*]', verify)
        self.assertNotIn('expected_direct_reports[*]', verify)

    def test_release_accepts_only_verified_warning_receipts(self) -> None:
        release = self.step(self.parent, "Build final iteration manifest")
        self.assertIn('.status == "COMPLETED_WITH_WARNINGS"', release)
        self.assertIn('.status == "NO_FILL" and .code == "FOK_NOT_FILLED"', release)
        self.assertIn('.status == "FULL"', release)
        self.assertNotIn('.status == "UNCERTAIN"', release)
        self.assertNotIn('.status == "PARTIAL"', release)
        self.assertNotIn('.status == "REJECTED"', release)

    def test_parent_has_no_ec2_handoff_cleanup_or_ssh_credentials(self) -> None:
        self.assertNotIn("  cleanup:\n", self.parent)
        self.assertNotIn("POLYMARKET_EC2_SSH_PRIVATE_KEY", self.parent)
        self.assertNotIn("handoff-directory", self.parent)
        self.assertNotIn("ssh-keyscan", self.parent)

    def test_research_handoff_and_diagnostics_use_private_fork_artifacts(self) -> None:
        combined = self.parent + self.research + self.execution
        self.assertNotIn("actions/download-artifact", self.research)
        self.assertGreaterEqual(self.research.count("actions/upload-artifact@v4"), 2)
        self.assertIn("Upload research diagnostics for post-run Judge", self.research)
        self.assertIn("wire.jsonl", self.research)
        self.assertIn("output.log", self.research)
        self.assertIn("retention-days: 90", self.research)
        self.assertIn("actions/download-artifact@v4", self.execution)
        self.assertIn("actions/upload-artifact@v4", self.execution)
        self.assertIn("Upload private-fork execution diagnostics for post-run Judge", self.execution)
        self.assertIn("forecast-edge-research-handoff-", combined)

    def test_execution_gate_checks_toggle_without_loading_trading_credentials(self) -> None:
        gate = self.step(self.parent, "Validate schedule and private workflow configuration")
        self.assertIn("POLYMARKET_LIVE_TRADING_ENABLED", gate)
        self.assertIn("execution_ready=$execution_requested", gate)
        for name in (
            "POLYMARKET_SIGNER_PRIVATE_KEY",
            "POLYMARKET_CLOB_API_KEY",
            "POLYMARKET_CLOB_API_SECRET",
            "POLYMARKET_CLOB_API_PASSPHRASE",
        ):
            self.assertNotIn(name, gate)

    def test_execution_uses_persistent_ec2_and_sqlite_ledger(self) -> None:
        for label in ("self-hosted", "Linux", "X64", "polymarket-execution", "eu-north-1"):
            self.assertIn(f"      - {label}", self.execution)
        self.assertIn(
            "LEDGER_PATH: /home/ubuntu/poly-edge-live/runtime/execution-ledger.sqlite3",
            self.execution,
        )
        self.assertIn("cancel-open-orders", self.execution)
        adapter = (ROOT / "execution" / "polymarket" / "sdk_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('order_type="FOK"', adapter)
        self.assertNotIn("ssh ", self.execution)
        self.assertNotIn("scp ", self.execution)
        self.assertNotIn("aws ec2 run-instances", self.execution)
        self.assertNotIn("Dynamo", self.execution)

    def test_execution_details_are_captured_in_phase_logs_and_not_streamed(self) -> None:
        install = self.step(self.execution, "Install isolated execution dependencies")
        self.assertIn('> "$RUNTIME_DIR/install-command.log" 2>&1', install)
        self.assertIn('>> "$RUNTIME_DIR/phase-events.ndjson"', install)
        execute = self.step(
            self.execution,
            "Validate, cancel open orders, price, execute FOK market orders, and verify locally",
        )
        for command in ("validate", "cancel", "preflight", "plan", "execute", "verify"):
            self.assertIn(f'run_phase {command}', execute)
        self.assertIn('"$@" > "$RUNTIME_DIR/$phase-command.log" 2>&1', execute)
        self.assertLess(execute.index("cancel-open-orders"), execute.index(" preflight "))
        self.assertIn('--cancellations "$RUNTIME_DIR/cancellations.json"', execute)
        self.assertIn("phase-events.ndjson", execute)
        diagnostics = self.step(
            self.execution, "Build and persist secret-free execution diagnostics"
        )
        self.assertIn("build_polymarket_execution_diagnostics.py", diagnostics)
        self.assertIn('--secret-file "$SERVER_SECRET_PATH"', diagnostics)
        self.assertIn("PERSISTENT_DIAGNOSTICS_ROOT", self.execution)
        self.assertIn('"install-command.log"', (
            ROOT / "scripts" / "build_polymarket_execution_diagnostics.py"
        ).read_text(encoding="utf-8"))
        upload = self.step(
            self.execution, "Upload private-fork execution diagnostics for post-run Judge"
        )
        self.assertIn("retention-days: 90", upload)

    def test_polymarket_credentials_exist_only_in_server_local_file(self) -> None:
        self.assertIn(
            "SERVER_SECRET_PATH: /home/ubuntu/poly-edge-live/runtime/secrets.json",
            self.execution,
        )
        self.assertIn('"$SERVER_SECRET_PATH"', self.execution)
        self.assertNotIn("secrets.POLYMARKET_", self.execution)
        execution_call = self.parent[
            self.parent.index("  execution:\n") : self.parent.index("\n  release:\n")
        ]
        self.assertNotIn("secrets:", execution_call)
        self.assertNotIn("POLYMARKET_SIGNER_PRIVATE_KEY", self.parent)
        self.assertNotIn("POLYMARKET_CLOB_API_", self.parent)

    def test_execution_price_threshold_is_unique_and_configurable(self) -> None:
        self.assertIn("POLYMARKET_MAX_PRICE_DEVIATION_BPS", self.execution)
        self.assertIn("max_price_deviation_bps", self.execution)
        self.assertNotIn("max_notional_deviation_bps", self.execution)

    def test_research_uses_only_model_and_account_identity_secrets(self) -> None:
        configured = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.research))
        self.assertEqual(
            configured,
            {"KIMI_CODE_API_KEY", "FORECAST_EDGE_MARKET_ACCOUNTS_JSON"},
        )
        research_call = self.parent[
            self.parent.index("  research:\n") : self.parent.index("\n  execution:\n")
        ]
        self.assertNotIn("secrets: inherit", research_call)
        self.assertNotIn("POLYMARKET_SIGNER_PRIVATE_KEY", research_call)
        self.assertNotIn("POLYMARKET_CLOB_API_", research_call)

    def test_public_release_requires_post_execution_disclosure_opt_ins(self) -> None:
        validate = self.step(self.research, "Validate workflow configuration")
        self.assertIn("ALLOW_PUBLIC_RESEARCH_RELEASES", validate)
        self.assertIn("ALLOW_PUBLIC_TRADE_INTENTS", validate)

    def test_multi_venue_release_package_is_staged_without_publishing(self) -> None:
        restore = self.step(self.research, "Restore recent research Releases")
        package = self.step(self.research, "Build current-iteration research Release")
        stage = self.step(self.research, "Build secret-free research artifact handoff")
        self.assertIn("gh release download", restore)
        self.assertIn("report_archive_sha256", restore)
        self.assertIn('"${current_report_directories[@]}"', package)
        self.assertIn('--arg schema_version "2.0"', package)
        self.assertIn('reports: $reports[0]', package)
        self.assertIn('report-$adapter.json', package)
        self.assertIn(".reports[] | .json_asset, .markdown_asset", stage)
        self.assertNotIn("gh release create", self.research)

    def test_history_restore_fails_on_transient_gh_api_errors(self) -> None:
        restore = self.step(self.research, "Restore recent research Releases")
        # The API response must be captured before jq runs; piping gh directly
        # into jq inside a process substitution would mask transient failures
        # as "no history" under set -euo pipefail.
        self.assertIn("releases_json=$(gh api --paginate --slurp", restore)
        self.assertIn('<<< "$releases_json"', restore)

    def test_execution_validates_price_deviation_variable_before_jq(self) -> None:
        validate = self.step(
            self.execution, "Validate EC2 runner and private execution configuration"
        )
        self.assertIn('"$POLYMARKET_MAX_PRICE_DEVIATION_BPS" =~ ^[0-9]+$', validate)

    def test_market_adapter_is_variable_and_account_identity_is_secret(self) -> None:
        self.assertIn(
            "ENABLED_PREDICTION_MARKETS: ${{ vars.FORECAST_EDGE_MARKET_ADAPTERS }}",
            self.research,
        )
        self.assertIn(
            "MARKET_ACCOUNTS_JSON: ${{ secrets.FORECAST_EDGE_MARKET_ACCOUNTS_JSON }}",
            self.research,
        )
        self.assertNotIn("vars.FORECAST_EDGE_MARKET_ACCOUNTS_JSON", self.parent)
        self.assertNotIn("vars.FORECAST_EDGE_MARKET_ACCOUNTS_JSON", self.research)
        self.assertNotIn("|| 'polymarket'", self.research)
        self.assertNotIn("vars.POLYMARKET_ACCOUNT", self.research)

    def test_missing_private_configuration_skips_all_downstream_jobs(self) -> None:
        gate = self.step(
            self.parent, "Validate schedule and private workflow configuration"
        )
        self.assertIn("missing_configuration=()", gate)
        self.assertIn("Forecast Edge is not configured; skipping this run", gate)
        self.assertIn('echo "should-run=false"', gate)
        self.assertIn('echo "execution-requested=false"', gate)
        self.assertIn('echo "execution-ready=false"', gate)
        self.assertIn("exit 0", gate)
        self.assertLess(
            gate.index("missing_configuration=()"),
            gate.index("FORECAST_EDGE_INTERVAL_HOURS must be an integer"),
        )

    def test_all_validation_attempts_use_multi_venue_validator(self) -> None:
        self.assertEqual(self.research.count("scripts/validate_research_iteration.py"), 4)
        self.assertEqual(
            self.research.count('--current-reports-manifest "$CURRENT_REPORTS_MANIFEST"'),
            4,
        )
        self.assertNotIn("CURRENT_REPORT_PATH_FILE", self.research)
        self.assertNotIn("--polymarket-account-snapshot", self.research)

    def test_market_urls_are_venue_neutral_and_verified(self) -> None:
        verify = self.step(self.research, "Resolve and verify published market URLs")
        self.assertIn("--location --fail-with-body", verify)
        self.assertIn('[[ "$http_status" != "200" ]]', verify)
        self.assertIn("market_data.py", verify)
        self.assertIn('--adapter "$venue" canonical-url "$market_identifier"', verify)
        self.assertNotIn("polymarket_data.py", verify)
        self.assertNotIn("gamma", verify.lower())

    def test_prompt_requires_current_holdings_and_novel_event_research(self) -> None:
        prompt = SCHEDULED_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("重新研究它的全部当前持仓", prompt)
        self.assertIn("至少 10 个", prompt)
        self.assertIn("不得直接沿用历史概率", prompt)
        self.assertIn("新增且只新增一个报告", prompt)
        self.assertIn(
            "previously_researched.py REPORTS_DIR --adapter ADAPTER --pretty", prompt
        )
        self.assertIn("绝不能把 `.py` 源文件当 JSON", prompt)
        self.assertIn("仅当 `closed=false` 且 `accepting_orders=true`", prompt)

    def test_repair_prompt_distinguishes_history_from_current_report(self) -> None:
        prompt = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
        render = self.step(self.research, "Render initial and repair prompts")
        self.assertIn("{{BASELINE_REPORTS_PATH}}", prompt)
        self.assertIn("路径不在历史报告清单", prompt)
        self.assertIn("每个已启用 adapter 必须恰好有一个本轮报告", prompt)
        self.assertIn("不得把历史报告误当成", prompt)
        self.assertIn("{{BASELINE_REPORTS_PATH}}", render)


if __name__ == "__main__":
    unittest.main()
