#!/usr/bin/env python3
"""Publish one validated public brief into the static site's data index."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from validate_public_brief import PublicBriefError, load_json, validate_brief, validate_index


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def publish(source: Path, site_data: Path) -> Path:
    brief = validate_brief(load_json(source))
    site_data.mkdir(parents=True, exist_ok=True)
    destination = site_data / f"{brief['brief_id']}.json"
    content = encoded(brief)
    if destination.exists() and destination.read_text(encoding="utf-8") != content:
        raise PublicBriefError(f"refusing to overwrite immutable public brief {destination.name}")
    atomic_write(destination, content)

    index_path = site_data / "index.json"
    if index_path.exists():
        index = load_json(index_path)
        if not isinstance(index, dict) or set(index) != {"schema_version", "briefs"}:
            raise PublicBriefError("existing index.json has an invalid shape")
        entries = index.get("briefs")
        if index.get("schema_version") != "1.0" or not isinstance(entries, list):
            raise PublicBriefError("existing index.json has an invalid version or entries")
    else:
        index = {"schema_version": "1.0", "briefs": []}

    entry = {
        "brief_id": brief["brief_id"],
        "generated_at": brief["generated_at"],
        "title": brief["title"],
        "file": destination.name,
    }
    index["briefs"] = [entry, *[row for row in index["briefs"] if row.get("brief_id") != brief["brief_id"]]]
    index["briefs"].sort(key=lambda row: row["generated_at"], reverse=True)
    atomic_write(index_path, encoded(index))
    atomic_write(site_data / "latest.json", content)
    validate_index(load_json(index_path), site_data)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", type=Path)
    parser.add_argument("--site-data", type=Path, default=Path("site/data"))
    args = parser.parse_args(argv)
    try:
        destination = publish(args.brief, args.site_data)
    except (OSError, PublicBriefError) as exc:
        parser.error(str(exc))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
