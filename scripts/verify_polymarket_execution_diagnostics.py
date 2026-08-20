#!/usr/bin/env python3
"""Verify and safely extract a Polymarket execution diagnostic archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any


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


class VerificationError(RuntimeError):
    """Raised when diagnostic evidence is unsafe or fails integrity checks."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} is not a JSON object")
    return value


def verify_secret_patterns(path: Path) -> None:
    data = path.read_bytes()
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise VerificationError(f"secret-like material detected in {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.destination.exists():
        raise VerificationError("diagnostic extraction destination already exists")
    checksum_fields = args.checksum.read_text(encoding="utf-8").strip().split()
    if len(checksum_fields) != 2 or checksum_fields[1] != "execution-diagnostics.tar.gz":
        raise VerificationError("diagnostic checksum file has an invalid format")
    archive_digest = sha256_file(args.archive)
    if checksum_fields[0] != archive_digest:
        raise VerificationError("diagnostic archive digest does not match its checksum")

    outer_summary = load_json(args.summary, "diagnostic summary")
    outer_manifest = load_json(args.manifest, "diagnostic manifest")
    for label, document in (("summary", outer_summary), ("manifest", outer_manifest)):
        if document.get("schema_version") != "1.0":
            raise VerificationError(f"diagnostic {label} has an unsupported schema")
        if str(document.get("workflow_run_id")) != args.expected_run_id:
            raise VerificationError(f"diagnostic {label} belongs to another workflow run")
        if document.get("secret_scan_passed") is not True:
            raise VerificationError(f"diagnostic {label} did not pass its source secret scan")

    expected_members = {
        f"execution-diagnostics/{item.get('path')}"
        for item in outer_manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    expected_members.add("execution-diagnostics/diagnostic-manifest.json")
    if len(expected_members) < 3:
        raise VerificationError("diagnostic manifest has too few evidence files")

    args.destination.mkdir(mode=0o700, parents=True)
    with tarfile.open(args.archive, "r:gz") as bundle:
        members = bundle.getmembers()
        actual_members: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise VerificationError("diagnostic archive contains an unsafe path")
            if member.isdir():
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise VerificationError("diagnostic archive contains a non-regular entry")
            actual_members.add(member.name)
        if actual_members != expected_members:
            raise VerificationError("diagnostic archive contents differ from its manifest")
        bundle.extractall(args.destination)

    extracted_root = args.destination / "execution-diagnostics"
    inner_summary = extracted_root / "diagnostic-summary.json"
    inner_manifest = extracted_root / "diagnostic-manifest.json"
    if sha256_file(inner_summary) != sha256_file(args.summary):
        raise VerificationError("external and archived diagnostic summaries differ")
    if sha256_file(inner_manifest) != sha256_file(args.manifest):
        raise VerificationError("external and archived diagnostic manifests differ")
    for item in outer_manifest["files"]:
        if not isinstance(item, dict):
            raise VerificationError("diagnostic manifest contains a non-object file entry")
        path = extracted_root / str(item.get("path", ""))
        if not path.is_file():
            raise VerificationError("diagnostic manifest references a missing file")
        if path.stat().st_size != item.get("size") or sha256_file(path) != item.get("sha256"):
            raise VerificationError(f"diagnostic file integrity check failed for {path.name}")
        verify_secret_patterns(path)
    verify_secret_patterns(inner_manifest)

    output = {
        "schema_version": "1.0",
        "found": True,
        "artifact_verified": True,
        "workflow_run_id": args.expected_run_id,
        "execution_id": outer_summary.get("execution_id"),
        "archive_sha256": archive_digest,
        "file_count": len(outer_manifest["files"]) + 1,
        "secret_pattern_scan_passed": True,
        "evidence_root": "diagnostics/files/execution-diagnostics",
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        raise SystemExit(f"execution diagnostic verification failed: {exc}")
