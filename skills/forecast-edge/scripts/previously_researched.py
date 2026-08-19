#!/usr/bin/env python3
"""Collect venue-local market and event identifiers from archived reports.

The deep-research novelty quota in references/market-selection.md requires each
full scan to research events absent from earlier runs. This script derives that
researched set read-only from immutable archived report.json files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class ResearchHistoryError(ValueError):
    """Raised when the reports archive is unavailable or malformed."""


def legacy_event_slug_from_url(url: str) -> str:
    """Extract a legacy Polymarket event slug for pre-adapter report compatibility."""
    marker = "/event/"
    if marker not in url:
        return ""
    slug = url.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0].strip()
    return slug


def collect_researched(reports_dir: Path, adapter: str | None = None) -> dict[str, Any]:
    if not reports_dir.is_dir():
        raise ResearchHistoryError(f"reports directory not found: {reports_dir}")
    market_ids: set[str] = set()
    event_identifiers: set[str] = set()
    legacy_event_slugs: set[str] = set()
    scanned = 0
    for path in sorted(reports_dir.glob("*/report.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchHistoryError(f"unreadable report {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ResearchHistoryError(f"report {path} is not a JSON object")
        run = document.get("run")
        account = run.get("account") if isinstance(run, dict) else None
        report_adapter = account.get("adapter") if isinstance(account, dict) else None
        if report_adapter is None and isinstance(account, dict) and account.get("address"):
            report_adapter = "polymarket"
        if adapter is not None and report_adapter != adapter:
            continue
        scanned += 1
        for section in ("current_positions", "candidates"):
            entries = document.get(section, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                market = entry.get("market")
                if not isinstance(market, dict):
                    continue
                market_identifier = str(
                    market.get("multi_market_identifier") or market.get("id") or ""
                ).strip()
                if market_identifier:
                    market_ids.add(market_identifier)
                event_identifier = str(market.get("event_identifier") or "").strip()
                if event_identifier:
                    event_identifiers.add(event_identifier)
                slug = legacy_event_slug_from_url(str(market.get("url", "")))
                if slug:
                    legacy_event_slugs.add(slug)
                    if not event_identifier:
                        event_identifiers.add(slug)
    def sort_key(value: str) -> tuple[int, object]:
        return (0, int(value)) if value.isdigit() else (1, value)

    return {
        "reports_scanned": scanned,
        "researched_market_count": len(market_ids),
        "adapter": adapter,
        "researched_event_count": len(event_identifiers),
        "researched_market_identifiers": sorted(market_ids, key=sort_key),
        "researched_event_identifiers": sorted(event_identifiers),
        # Compatibility aliases for reports produced before the adapter contract.
        "researched_market_ids": sorted(market_ids, key=sort_key),
        "researched_event_slugs": sorted(legacy_event_slugs),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_dir", help="Directory containing archived <run>/report.json reports")
    parser.add_argument("--adapter", help="Return only this venue's research history")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = collect_researched(Path(args.reports_dir), args.adapter)
    except ResearchHistoryError as exc:
        print(f"previously_researched: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
