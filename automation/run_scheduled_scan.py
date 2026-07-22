#!/usr/bin/env python3
"""Render a forecast-edge prompt and dispatch it to an Agent runner."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


PLACEHOLDERS = {
    "ACCOUNT_PROFILE",
    "OUTPUT_ROOT",
    "REPOSITORY_ROOT",
    "PUBLIC_BRIEF_PATH",
}


class ScheduleError(ValueError):
    """Raised for an invalid scheduled-run configuration."""


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in {None, ""} else default


def parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ScheduleError(f"invalid boolean value: {value}")


def validate_account_profile(value: str) -> str:
    parsed = urlparse(value)
    valid_path = (
        parsed.path.startswith("/@") and len(parsed.path) > 2
    ) or (
        parsed.path.startswith("/profile/") and len(parsed.path) > len("/profile/")
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"polymarket.com", "www.polymarket.com"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
        or not valid_path
    ):
        raise ScheduleError("account must be a public Polymarket profile URL")
    return value


def render_prompt(template: str, values: dict[str, str]) -> str:
    rendered = template
    for name in PLACEHOLDERS:
        token = "{{" + name + "}}"
        if token not in rendered:
            raise ScheduleError(f"prompt template is missing {token}")
        rendered = rendered.replace(token, values[name])
    if "{{" in rendered or "}}" in rendered:
        raise ScheduleError("prompt template contains an unknown or unresolved placeholder")
    return rendered


def make_public_path(directory: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{stamp}.json"


def run_agent(command: str, prompt: str) -> int:
    argv = shlex.split(command)
    if not argv:
        raise ScheduleError("FORECAST_EDGE_RUNNER is empty")
    prompt_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="forecast-edge-", suffix=".md", delete=False
        ) as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name)
        prompt_path.chmod(0o600)
        return subprocess.run([*argv, str(prompt_path)], check=False).returncode
    finally:
        if prompt_path is not None:
            prompt_path.unlink(missing_ok=True)


def post_process(repository_root: Path, public_path: Path | None) -> None:
    if public_path is None:
        return
    if not public_path.is_file():
        raise ScheduleError(f"Agent did not create the requested public brief: {public_path}")

    validator = repository_root / "scripts" / "validate_public_brief.py"
    subprocess.run([sys.executable, str(validator), str(public_path)], check=True)

    site_data = env_value("FORECAST_EDGE_SITE_DATA_DIR")
    if site_data:
        publisher = repository_root / "scripts" / "publish_public_brief.py"
        subprocess.run(
            [sys.executable, str(publisher), str(public_path), "--site-data", site_data],
            check=True,
        )

    if parse_bool(env_value("FORECAST_EDGE_SEND_FEISHU")):
        sender = repository_root / "integrations" / "feishu" / "send_brief.py"
        subprocess.run([sys.executable, str(sender), str(public_path)], check=True)


def build_parser(repository_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=repository_root)
    parser.add_argument("--account", default=env_value("POLYMARKET_ACCOUNT"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(env_value("FORECAST_EDGE_OUTPUT_ROOT", "~/.forecast-edge-runs") or "").expanduser(),
    )
    parser.add_argument(
        "--prompt-template",
        type=Path,
        default=repository_root / "automation" / "prompt.zh-CN.md",
    )
    parser.add_argument(
        "--public-brief-dir",
        type=Path,
        default=(
            Path(env_value("FORECAST_EDGE_PUBLIC_BRIEF_DIR") or "").expanduser()
            if env_value("FORECAST_EDGE_PUBLIC_BRIEF_DIR")
            else None
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    args = build_parser(default_root).parse_args(argv)
    repository_root = args.repository_root.expanduser().resolve()
    if not args.account:
        raise ScheduleError("POLYMARKET_ACCOUNT or --account is required")
    runner = env_value("FORECAST_EDGE_RUNNER")
    if not runner:
        raise ScheduleError("FORECAST_EDGE_RUNNER is required")

    public_path = make_public_path(args.public_brief_dir.expanduser()) if args.public_brief_dir else None
    template = args.prompt_template.expanduser().read_text(encoding="utf-8")
    prompt = render_prompt(
        template,
        {
            "ACCOUNT_PROFILE": validate_account_profile(args.account),
            "OUTPUT_ROOT": str(args.output_root.expanduser()),
            "REPOSITORY_ROOT": str(repository_root),
            "PUBLIC_BRIEF_PATH": str(public_path) if public_path else "DISABLED",
        },
    )
    code = run_agent(runner, prompt)
    if code != 0:
        return code
    post_process(repository_root, public_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ScheduleError, subprocess.CalledProcessError) as exc:
        print(f"scheduled scan failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
