#!/usr/bin/env python3
"""Build a complete, secret-free forensic bundle for one execution attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RUNTIME_FILES = (
    "trade-intents.jsonl",
    "validated-intents.jsonl",
    "risk-limits.json",
    "validation.json",
    "cancellations.json",
    "preflight.json",
    "plan.json",
    "receipt.json",
    "verification.json",
    "phase-events.ndjson",
    "install-command.log",
    "validate-command.log",
    "cancel-command.log",
    "preflight-command.log",
    "plan-command.log",
    "execute-command.log",
    "verify-command.log",
)

SECRET_KEY_NAME = re.compile(
    r"(?:private.?key|password|secret|api.?key|passphrase|access.?token|"
    r"refresh.?token|session.?token|auth.?token|cookie|mnemonic|seed.?phrase)",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(rb"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\b0x[0-9a-fA-F]{130}\b"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(rb"(?i)authorization\s*:\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(rb"(?im)^(?:cookie|set-cookie)\s*:\s*[^\r\n]{8,}$"),
    re.compile(
        rb'(?i)"(?:private[_-]?key|password|api[_-]?key|api[_-]?secret|client[_-]?secret|passphrase|access[_-]?token|refresh[_-]?token|session[_-]?token|auth[_-]?token|cookie|mnemonic|seed[_-]?phrase)"\s*:\s*"[^"\r\n]+"'
    ),
)


class DiagnosticError(RuntimeError):
    """Raised when a diagnostic bundle would be incomplete or unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, errors: list[str]) -> Any:
    if path.is_symlink():
        raise DiagnosticError(f"{path.name}: symbolic links are not accepted")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: {exc}")
        return None


def read_ndjson(path: Path, errors: list[str]) -> list[Any]:
    if path.is_symlink():
        raise DiagnosticError(f"{path.name}: symbolic links are not accepted")
    if not path.is_file():
        return []
    values: list[Any] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{path.name}: {exc}")
        return []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            errors.append(f"{path.name}:{line_number}: blank line")
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: {exc}")
    return values


def secret_values(value: Any, *, key_name: str = "") -> set[bytes]:
    found: set[bytes] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.update(secret_values(child, key_name=str(key)))
    elif isinstance(value, list):
        for child in value:
            found.update(secret_values(child, key_name=key_name))
    elif SECRET_KEY_NAME.search(key_name) and isinstance(value, (str, int)):
        encoded = str(value).encode("utf-8")
        if len(encoded) >= 8:
            found.add(encoded)
    return found


def load_known_secrets(path: Path) -> set[bytes]:
    if path.is_symlink():
        raise DiagnosticError("server credential file must not be a symbolic link")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"could not read the server credential file for leak checks: {exc}") from exc
    values = secret_values(document)
    if not values:
        raise DiagnosticError("server credential file exposed no recognizable secret fields")
    return values


def assert_secret_free(paths: Iterable[Path], known_secrets: set[bytes]) -> None:
    for path in paths:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise DiagnosticError(f"secret-like material detected in diagnostic file {path.name}")
        if any(secret in data for secret in known_secrets):
            raise DiagnosticError(f"server credential value detected in diagnostic file {path.name}")


def parse_embedded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def sanitized_ledger_snapshot(path: Path, execution_id: str, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "available": False,
        "execution_id": execution_id,
        "run": None,
        "orders": [],
    }
    if path.is_symlink():
        raise DiagnosticError("ledger snapshot: symbolic links are not accepted")
    if not path.is_file() or not execution_id:
        return result
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT pk, document FROM ledger_entries WHERE pk LIKE ? ORDER BY pk",
            (f"%{execution_id}%",),
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        errors.append(f"ledger snapshot: {exc}")
        return result
    result["available"] = True
    for key, raw_document in rows:
        try:
            document = json.loads(raw_document)
        except (TypeError, json.JSONDecodeError) as exc:
            errors.append(f"ledger entry {key}: {exc}")
            continue
        if str(key).startswith("RUN#"):
            result["run"] = {
                name: document.get(name)
                for name in (
                    "artifact_sha256",
                    "state",
                    "planned_notional",
                    "created_at",
                    "updated_at",
                    "lease_until",
                )
            }
            result["run"]["summary"] = parse_embedded_json(document.get("summary"))
            continue
        if not str(key).startswith("ORDER#"):
            continue
        response = parse_embedded_json(document.get("post_response"))
        if not isinstance(response, dict):
            response = None
        result["orders"].append(
            {
                "ledger_key": key,
                "intent_index": document.get("intent_index"),
                "state": document.get("state"),
                "order_id": document.get("order_id"),
                "plan_digest": document.get("plan_digest"),
                "signed_order_digest": document.get("signed_order_digest"),
                "before_token_balance": document.get("before_token_balance"),
                "before_collateral_balance": document.get("before_collateral_balance"),
                "updated_at": document.get("updated_at"),
                "plan": parse_embedded_json(document.get("plan")),
                "post_response": (
                    {
                        name: response.get(name)
                        for name in ("ok", "code", "message", "order_id", "trade_ids")
                        if name in response
                    }
                    if response is not None
                    else None
                ),
                "receipt": parse_embedded_json(document.get("receipt")),
            }
        )
    return result


def source_intents(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    values = read_ndjson(path, errors)
    return [value for value in values if isinstance(value, dict)]


def build_intent_trace(
    intents: list[dict[str, Any]], plan: Any, receipt: Any
) -> list[dict[str, Any]]:
    planned: dict[int, dict[str, Any]] = {}
    skipped: dict[int, dict[str, Any]] = {}
    if isinstance(plan, dict):
        planned = {
            int(item.get("intent_index", 0)): item
            for item in plan.get("orders", [])
            if isinstance(item, dict) and int(item.get("intent_index", 0)) > 0
        }
        skipped = {
            int(item.get("intent_index", 0)): item
            for item in plan.get("skipped", [])
            if isinstance(item, dict) and int(item.get("intent_index", 0)) > 0
        }
    receipts: dict[int, dict[str, Any]] = {}
    if isinstance(receipt, dict):
        receipts = {
            int(item.get("intent_index", 0)): item
            for item in receipt.get("receipts", [])
            if isinstance(item, dict) and int(item.get("intent_index", 0)) > 0
        }
    trace: list[dict[str, Any]] = []
    for index, intent in enumerate(intents, start=1):
        item: dict[str, Any] = {"intent_index": index, "intent": intent}
        if index in skipped:
            item.update(
                {
                    "planning_disposition": "skipped",
                    "skip_code": skipped[index].get("code"),
                    "skip_message": skipped[index].get("message"),
                    "execution_disposition": "not_submitted",
                }
            )
        elif index in planned:
            item.update(
                {
                    "planning_disposition": "planned",
                    "planned_order": planned[index],
                    "execution_disposition": (
                        str(receipts[index].get("status", "unknown")).lower()
                        if index in receipts
                        else "not_completed"
                    ),
                    "receipt": receipts.get(index),
                }
            )
        else:
            item.update(
                {
                    "planning_disposition": "not_planned",
                    "execution_disposition": "not_submitted",
                }
            )
        trace.append(item)
    return trace


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--ledger-path", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--execution-id", default="")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--trade-intents-sha256", default="")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()

    runtime_dir = args.runtime_dir.resolve()
    output_dir = args.output_directory.resolve()
    archive = args.archive.resolve()
    if not runtime_dir.is_dir():
        raise DiagnosticError("execution runtime directory does not exist")
    if output_dir.exists() or archive.exists():
        raise DiagnosticError("diagnostic output already exists")
    if output_dir == runtime_dir or runtime_dir in output_dir.parents:
        pass
    else:
        raise DiagnosticError("diagnostic output must stay under the execution runtime directory")
    output_dir.mkdir(mode=0o700, parents=False)

    errors: list[str] = []
    copied: list[Path] = []
    for name in RUNTIME_FILES:
        source = runtime_dir / name
        if source.is_symlink():
            raise DiagnosticError(f"{name}: symbolic links are not accepted")
        if not source.is_file():
            continue
        destination = output_dir / name
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        copied.append(destination)

    plan = read_json(runtime_dir / "plan.json", errors)
    receipt = read_json(runtime_dir / "receipt.json", errors)
    phases = read_ndjson(runtime_dir / "phase-events.ndjson", errors)
    intents = source_intents(runtime_dir / "trade-intents.jsonl", errors)
    trace = build_intent_trace(intents, plan, receipt)
    ledger = sanitized_ledger_snapshot(args.ledger_path, args.execution_id, errors)
    ledger_path = output_dir / "ledger-snapshot.json"
    write_json(ledger_path, ledger)
    ledger_path.chmod(0o600)
    copied.append(ledger_path)

    failed_phases = [
        item
        for item in phases
        if isinstance(item, dict) and int(item.get("exit_code", 0)) != 0
    ]
    execution_status = (
        receipt.get("status")
        if isinstance(receipt, dict) and isinstance(receipt.get("status"), str)
        else ("FAILED" if failed_phases else "INCOMPLETE")
    )
    summary = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "repository": args.repository,
        "workflow_run_id": args.run_id,
        "workflow_run_attempt": args.run_attempt,
        "commit_sha": args.commit_sha,
        "execution_id": args.execution_id or None,
        "trade_intents_sha256": args.trade_intents_sha256 or None,
        "execution_status": execution_status,
        "failed_phase": failed_phases[0].get("phase") if failed_phases else None,
        "phase_events": phases,
        "source_intent_count": len(intents),
        "planned_order_count": (
            len(plan.get("orders", [])) if isinstance(plan, dict) else 0
        ),
        "skipped_intent_count": (
            len(plan.get("skipped", [])) if isinstance(plan, dict) else 0
        ),
        "receipt_count": (
            len(receipt.get("receipts", [])) if isinstance(receipt, dict) else 0
        ),
        "intent_trace": trace,
        "ledger_snapshot_available": ledger["available"],
        "collection_errors": errors,
        "secret_scan_passed": True,
    }
    summary_path = output_dir / "diagnostic-summary.json"
    write_json(summary_path, summary)
    summary_path.chmod(0o600)
    copied.append(summary_path)

    known_secrets = load_known_secrets(args.secret_file)
    assert_secret_free(copied, known_secrets)
    records = [
        {
            "path": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(copied)
    ]
    manifest_path = output_dir / "diagnostic-manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": "1.0",
            "generated_at": summary["generated_at"],
            "execution_id": summary["execution_id"],
            "workflow_run_id": args.run_id,
            "secret_scan_passed": True,
            "files": records,
        },
    )
    manifest_path.chmod(0o600)
    assert_secret_free((manifest_path,), known_secrets)

    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(output_dir, arcname="execution-diagnostics", recursive=True)
    archive.chmod(0o600)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DiagnosticError as exc:
        raise SystemExit(f"execution diagnostic packaging failed: {exc}")
