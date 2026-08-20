#!/usr/bin/env python3
"""Redact credential-shaped text from research diagnostics before upload."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


class SanitizationError(RuntimeError):
    pass


PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "private_key_block",
        re.compile(
            rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("github_token", re.compile(rb"(?:github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,})")),
    ("hex_signature", re.compile(rb"0x[0-9a-fA-F]{130}")),
    (
        "authorization_header",
        re.compile(
            rb"[Aa]uthorization\s*:\s*(?:Bearer|Basic)"
            rb"\s+[A-Za-z0-9._~+/=-]{8,}"
        ),
    ),
    (
        "password_assignment",
        re.compile(rb"[Pp]assword\s*[:=]\s*\S{8,}"),
    ),
)


RESIDUAL_PATTERNS: tuple[re.Pattern[bytes], ...] = tuple(
    pattern for _, pattern in PATTERNS
) + (re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),)


def sanitize_tree(root: Path, secrets: list[bytes], report_path: Path) -> dict[str, object]:
    root = root.resolve()
    if not root.is_dir():
        raise SanitizationError("diagnostic root is not a directory")
    try:
        report_path = report_path.resolve()
        report_path.relative_to(root)
    except ValueError as exc:
        raise SanitizationError("redaction report must stay inside the diagnostic root") from exc

    counts: dict[str, int] = {"exact_secret": 0}
    counts.update({label: 0 for label, _ in PATTERNS})
    sanitized_files = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SanitizationError("diagnostic tree contains a non-regular file")
        if path == report_path or path.is_dir():
            continue
        if not path.is_file():
            raise SanitizationError("diagnostic tree contains a non-regular file")
        data = path.read_bytes()
        sanitized = data
        for secret in secrets:
            if not secret:
                continue
            occurrences = sanitized.count(secret)
            if occurrences:
                sanitized = sanitized.replace(secret, b"[REDACTED_EXACT_SECRET]")
                counts["exact_secret"] += occurrences
        for label, pattern in PATTERNS:
            sanitized, replacements = pattern.subn(
                f"[REDACTED_{label.upper()}]".encode("ascii"), sanitized
            )
            counts[label] += replacements
        if sanitized != data:
            temporary = path.with_name(path.name + ".redacting")
            temporary.write_bytes(sanitized)
            temporary.chmod(path.stat().st_mode & 0o777)
            temporary.replace(path)
            sanitized_files += 1

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == report_path:
            continue
        data = path.read_bytes()
        if any(secret and secret in data for secret in secrets):
            raise SanitizationError("exact protected value remains after redaction")
        if any(pattern.search(data) for pattern in RESIDUAL_PATTERNS):
            raise SanitizationError("credential-shaped text remains after redaction")

    report: dict[str, object] = {
        "schema_version": "1.0",
        "secret_scan_passed": True,
        "sanitized_file_count": sanitized_files,
        "redaction_counts": counts,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path.chmod(0o600)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--secret-env", action="append", default=[])
    args = parser.parse_args()
    secrets: list[bytes] = []
    for name in args.secret_env:
        value = os.environ.get(name)
        if not value:
            raise SanitizationError(f"required protected environment value is unavailable: {name}")
        secrets.append(value.encode("utf-8"))
    sanitize_tree(args.root, secrets, args.report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SanitizationError as exc:
        print(f"research diagnostic sanitization failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
