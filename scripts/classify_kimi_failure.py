#!/usr/bin/env python3
"""Classify a failed Kimi Code invocation without copying provider error text."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


AUTH_OR_QUOTA_PATTERNS = (
    re.compile(r"provider\.auth_error", re.IGNORECASE),
    re.compile(r"usage\s+limit", re.IGNORECASE),
    re.compile(r"billing\s+cycle", re.IGNORECASE),
    re.compile(r"(?:quota|credits?)\s+(?:exceeded|exhausted|reached)", re.IGNORECASE),
    re.compile(r"(?:invalid|expired|missing)\s+(?:api\s+)?key", re.IGNORECASE),
    re.compile(r"(?:http(?:\s+status)?|status(?:\s+code)?)?\s*[:=]?\s*(?:401|403)\b", re.IGNORECASE),
)


def classify_failure(stderr_text: str) -> dict[str, object]:
    if any(pattern.search(stderr_text) for pattern in AUTH_OR_QUOTA_PATTERNS):
        return {
            "schema_version": "1.0",
            "retryable": False,
            "category": "AUTH_OR_QUOTA",
            "summary": "Kimi Code 认证、计费额度或配额错误；修改研究内容无法修复。",
        }
    return {
        "schema_version": "1.0",
        "retryable": True,
        "category": "TRANSIENT_OR_AGENT",
        "summary": "未发现明确的认证或配额错误；允许按研究 repair 契约继续。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stderr", type=Path)
    args = parser.parse_args()
    try:
        stderr_text = args.stderr.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"could not read Kimi stderr: {exc}", file=sys.stderr)
        return 2
    json.dump(classify_failure(stderr_text), sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
