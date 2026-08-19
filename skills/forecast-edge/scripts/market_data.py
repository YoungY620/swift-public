#!/usr/bin/env python3
"""Dispatch normalized read-only requests to a registered prediction-market adapter."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ADAPTER_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
COMMANDS = {"account", "book", "candidate-events", "canonical-url", "event", "market"}


class AdapterError(RuntimeError):
    """Raised when adapter selection or output violates the trusted contract."""


def load_registry(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"could not read adapter registry {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise AdapterError("adapter registry must be a schema_version 1.0 object")
    adapters = value.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        raise AdapterError("adapter registry must contain at least one adapter")
    return value


def adapter_script(
    registry: dict[str, Any], adapter: str, skill_root: Path, command: str
) -> Path:
    if ADAPTER_RE.fullmatch(adapter) is None:
        raise AdapterError(f"invalid adapter id: {adapter!r}")
    entry = registry["adapters"].get(adapter)
    if not isinstance(entry, dict):
        raise AdapterError(f"unknown market adapter: {adapter}")
    commands = entry.get("commands")
    if not isinstance(commands, list) or command not in commands:
        raise AdapterError(f"adapter {adapter} does not support command {command}")
    relative = entry.get("script")
    if not isinstance(relative, str) or not relative:
        raise AdapterError(f"adapter {adapter} has no script")
    script = (skill_root / relative).resolve()
    try:
        script.relative_to(skill_root.resolve())
    except ValueError as exc:
        raise AdapterError(f"adapter {adapter} script escapes the Skill directory") from exc
    if not script.is_file():
        raise AdapterError(f"adapter {adapter} script is unavailable: {script}")
    return script


def require_safe_https_url(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or any(ch.isspace() for ch in value):
        raise AdapterError("adapter canonical URL must be a trimmed HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AdapterError("adapter canonical URL must be absolute and credential-free")
    return value


def invoke(script: Path, adapter: str, command: str, arguments: list[str], pretty: bool) -> Any:
    invocation = [sys.executable, str(script)]
    if pretty:
        invocation.append("--pretty")
    invocation.extend([command, *arguments])
    completed = subprocess.run(invocation, text=True, capture_output=True)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise AdapterError(f"adapter {adapter} command {command} failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"adapter {adapter} did not return one JSON value") from exc
    if not isinstance(result, (dict, list)):
        raise AdapterError(f"adapter {adapter} returned an unsupported JSON value")
    if isinstance(result, dict):
        declared = result.get("adapter")
        if declared != adapter:
            raise AdapterError(f"adapter response declared {declared!r}, expected {adapter!r}")
        if command == "canonical-url":
            require_safe_https_url(result.get("url"))
    return result


def build_parser(skill_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", type=Path, default=skill_root / "config" / "adapters.json"
    )
    parser.add_argument(
        "--adapter",
        default=os.environ.get("FORECAST_EDGE_MARKET_ADAPTER"),
        help="Adapter id; defaults to FORECAST_EDGE_MARKET_ADAPTER",
    )
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("command", choices=["adapters", *sorted(COMMANDS)])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    skill_root = Path(__file__).resolve().parents[1]
    args = build_parser(skill_root).parse_args(argv)
    try:
        registry = load_registry(args.registry)
        if args.command == "adapters":
            if args.arguments:
                raise AdapterError("adapters command does not accept arguments")
            result: Any = {
                "schema_version": registry["schema_version"],
                "adapters": sorted(registry["adapters"]),
            }
        else:
            if not args.adapter:
                raise AdapterError(
                    "select an adapter with --adapter or FORECAST_EDGE_MARKET_ADAPTER"
                )
            script = adapter_script(registry, args.adapter, skill_root, args.command)
            result = invoke(script, args.adapter, args.command, args.arguments, args.pretty)
    except (AdapterError, OSError) as exc:
        print(f"market_data: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
