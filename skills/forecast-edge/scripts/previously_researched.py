#!/usr/bin/env python3
"""Collect market and event identifiers already researched in archived reports.

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


def event_slug_from_url(url: str) -> str:
    """Extract the event slug from a Polymarket event URL, or return ''."""
    marker = "/event/"
    if marker not in url:
        return ""
    slug = url.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0].strip()
    return slug


def collect_researched(reports_dir: Path) -> dict[str, Any]:
    if not reports_dir.is_dir():
        raise ResearchHistoryError(f"reports directory not found: {reports_dir}")
    market_ids: set[str] = set()
    event_slugs: set[str] = set()
    scanned = 0
    for path in sorted(reports_dir.glob("*/report.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchHistoryError(f"unreadable report {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ResearchHistoryError(f"report {path} is not a JSON object")
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
                market_id = str(market.get("id", "")).strip()
                if market_id:
                    market_ids.add(market_id)
                slug = event_slug_from_url(str(market.get("url", "")))
                if slug:
                    event_slugs.add(slug)
    def sort_key(value: str) -> tuple[int, object]:
        return (0, int(value)) if value.isdigit() else (1, value)

    return {
        "reports_scanned": scanned,
        "researched_market_count": len(market_ids),
        "researched_event_count": len(event_slugs),
        "researched_market_ids": sorted(market_ids, key=sort_key),
        "researched_event_slugs": sorted(event_slugs),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_dir", help="Directory containing archived <run>/report.json reports")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = collect_researched(Path(args.reports_dir))
    except ResearchHistoryError as exc:
        print(f"previously_researched: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
