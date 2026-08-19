#!/usr/bin/env python3
"""Validate exactly one new report per enabled adapter and the combined trade JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class IterationError(RuntimeError):
    """Raised when one research iteration violates its delivery contract."""


def read_lines(path: Path) -> set[Path]:
    try:
        return {Path(line) for line in path.read_text(encoding="utf-8").splitlines() if line}
    except OSError as exc:
        raise IterationError(f"could not read baseline report list: {exc}") from exc


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IterationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise IterationError(f"{label} must be a JSON object")
    return value


def run_checked(command: list[str], label: str) -> str:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise IterationError(f"{label} failed: {diagnostic}")
    return completed.stdout


def account_snapshot_bundle_sha256(manifest_path: Path) -> str:
    manifest = load_object(manifest_path, "account snapshot manifest")
    snapshots = manifest.get("snapshots")
    if manifest.get("schema_version") != "1.0" or not isinstance(snapshots, dict):
        raise IterationError("account snapshot manifest violates schema_version 1.0")
    digest = hashlib.sha256()
    digest.update(manifest_path.read_bytes())
    for adapter, raw_path in sorted(snapshots.items()):
        if not isinstance(adapter, str) or not isinstance(raw_path, str):
            raise IterationError("account snapshot manifest contains an invalid entry")
        digest.update(b"\0")
        digest.update(adapter.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(Path(raw_path).read_bytes())
        except OSError as exc:
            raise IterationError(f"could not hash {adapter} account snapshot: {exc}") from exc
    return digest.hexdigest()


def validate(args: argparse.Namespace) -> dict[str, Any]:
    expected = [item.strip() for item in args.enabled_venues.split(",") if item.strip()]
    if not expected or len(expected) != len(set(expected)):
        raise IterationError("enabled venues must be a non-empty unique comma-separated list")

    expected_snapshot_digest = getattr(args, "expected_account_snapshots_sha256", None)
    if expected_snapshot_digest is not None:
        actual_snapshot_digest = account_snapshot_bundle_sha256(
            args.account_snapshots_manifest
        )
        if actual_snapshot_digest != expected_snapshot_digest:
            raise IterationError("trusted account snapshots changed after capture")

    repository_root = getattr(args, "repository_root", None)
    if repository_root is not None:
        status = run_checked(
            [
                "git",
                "-C",
                str(repository_root),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            "repository integrity check",
        )
        if status.strip():
            raise IterationError("Kimi modified files inside the trusted repository checkout")

    baseline = read_lines(args.baseline_reports)
    all_reports = sorted(args.reports_root.glob("reports/*/report.json"))
    current = [path for path in all_reports if path not in baseline]
    if len(current) != len(expected):
        raise IterationError(
            f"expected {len(expected)} newly archived private report(s); found {len(current)}"
        )

    report_rows: list[dict[str, str]] = []
    validation_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report_path in current:
        report = load_object(report_path, f"private report {report_path}")
        run = report.get("run")
        account = run.get("account") if isinstance(run, dict) else None
        adapter = account.get("adapter") if isinstance(account, dict) else None
        if not isinstance(adapter, str) or adapter not in expected:
            raise IterationError(
                f"private report {report_path} has missing or unexpected run.account.adapter"
            )
        if adapter in seen:
            raise IterationError(f"multiple current reports declare adapter {adapter}")
        seen.add(adapter)
        report_rows.append(
            {
                "adapter": adapter,
                "path": str(report_path),
                "directory": str(report_path.parent),
            }
        )

    missing = set(expected) - seen
    if missing:
        raise IterationError(f"current reports are missing adapters: {', '.join(sorted(missing))}")

    for row in report_rows:
        output = run_checked(
            [sys.executable, str(args.report_tool), "validate", row["path"]],
            f"private report validation for {row['adapter']}",
        )
        try:
            report_validation = json.loads(output)
        except json.JSONDecodeError as exc:
            raise IterationError("report validator returned invalid JSON") from exc
        validation_rows.append(
            {"adapter": row["adapter"], "validation": report_validation}
        )

    trade_command = [
        sys.executable,
        str(args.trade_validator),
        str(args.trade_intents),
        "--schema",
        str(args.trade_schema),
        "--enabled-venues",
        ",".join(expected),
        "--account-snapshots-manifest",
        str(args.account_snapshots_manifest),
        "--adapter-registry",
        str(args.adapter_registry),
    ]
    for row in report_rows:
        trade_command.extend(["--report", row["path"]])
    trade_output = run_checked(trade_command, "trade-intent validation")
    try:
        trade_summary = json.loads(trade_output)
    except json.JSONDecodeError as exc:
        raise IterationError("trade-intent validator returned invalid JSON") from exc

    args.current_reports_manifest.write_text(
        json.dumps(
            {"schema_version": "1.0", "reports": report_rows},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    args.report_validation.write_text(
        json.dumps(validation_rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.validation_summary.write_text(
        json.dumps(trade_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return trade_summary


def build_parser(root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--baseline-reports", type=Path, required=True)
    parser.add_argument("--enabled-venues", required=True)
    parser.add_argument("--trade-intents", type=Path, required=True)
    parser.add_argument("--trade-schema", type=Path, required=True)
    parser.add_argument("--account-snapshots-manifest", type=Path, required=True)
    parser.add_argument("--expected-account-snapshots-sha256", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--adapter-registry", type=Path, required=True)
    parser.add_argument("--current-reports-manifest", type=Path, required=True)
    parser.add_argument("--report-validation", type=Path, required=True)
    parser.add_argument("--validation-summary", type=Path, required=True)
    parser.add_argument(
        "--report-tool",
        type=Path,
        default=root / "skills" / "forecast-edge" / "scripts" / "report_tool.py",
    )
    parser.add_argument(
        "--trade-validator",
        type=Path,
        default=root / "scripts" / "validate_trade_intents.py",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    args = build_parser(root).parse_args(argv)
    try:
        summary = validate(args)
    except (IterationError, OSError) as exc:
        print(f"research iteration validation failed: {exc}", file=sys.stderr)
        return 2
    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
