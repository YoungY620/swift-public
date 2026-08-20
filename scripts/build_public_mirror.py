#!/usr/bin/env python3
"""Build a workflow-disabled, identity-free public snapshot from a private ref."""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


EXCLUDED_FROM_PUBLIC = {
    "scripts/build_public_mirror.py",
    "scripts/sync_public_mirror.sh",
    "tests/test_public_mirror.py",
}

# These are deployment implementation details, not portable project defaults.
# The exporter itself is private-only and is removed from the public snapshot.
TEXT_REPLACEMENTS = (
    ("/home/ubuntu/poly-edge-workflows", "/srv/poly-edge/handoffs"),
    ("/home/ubuntu/poly-edge-live", "/srv/poly-edge"),
    ("/home/ubuntu", "/srv/runner"),
    ("ubuntu:ubuntu", "runner:runner"),
    ("poly-edge-ec2-eu-north-1", "poly-edge-execution-node"),
    ("eu-north-1", "deployment-region"),
)

OFFICIAL_PROTOCOL_ADDRESSES = {
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
}

TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}


class PublicMirrorError(RuntimeError):
    """The source cannot be safely exported as a public snapshot."""


def _run(*args: str, cwd: Path) -> bytes:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def _extract_tracked_ref(source: Path, ref: str, destination: Path) -> None:
    archive_bytes = _run("git", "archive", "--format=tar", ref, cwd=source)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise PublicMirrorError(f"unsafe path in source archive: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise PublicMirrorError(
                    f"unsupported non-regular public file: {member.name}"
                )
        archive.extractall(destination)


def _disable_workflows(root: Path) -> None:
    source = root / ".github" / "workflows"
    if not source.exists():
        return
    destination = root / ".github" / "workflows-disabled"
    if destination.exists():
        raise PublicMirrorError("public workflow-disabled directory already exists")
    source.rename(destination)


def _sanitize_text(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PublicMirrorError(f"text file is not UTF-8: {path}") from exc
        sanitized = text
        for original, replacement in TEXT_REPLACEMENTS:
            sanitized = sanitized.replace(original, replacement)
        if sanitized != text:
            path.write_text(sanitized, encoding="utf-8")


def _is_fixture_address(address: str) -> bool:
    body = address[2:].casefold()
    return len(set(body)) == 1


def _scan_public_tree(root: Path) -> None:
    failures: list[str] = []
    patterns = {
        "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "GitHub token": re.compile(r"(?:github_pat_|gh[oprsu]_)[A-Za-z0-9_]{20,}"),
        "AWS access key": re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
        "AWS account id": re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])"),
        "EC2 hostname": re.compile(r"\bec2-[a-z0-9.-]+\.compute\.amazonaws\.com\b", re.I),
        "local user path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
        "SSH fingerprint": re.compile(r"\bSHA256:[A-Za-z0-9+/]{20,}={0,2}\b"),
        "personal GitHub URL": re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+", re.I),
        "personal GitLab URL": re.compile(r"https?://[^\s/]*gitlab[^\s/]*/[A-Za-z0-9_.-]+", re.I),
    }
    address_pattern = re.compile(r"0x[0-9a-fA-F]{40}")
    profile_pattern = re.compile(r"https?://(?:www\.)?polymarket\.com/@([^\s)`\"/]+)", re.I)
    email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    ipv4_pattern = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in patterns.items():
            if pattern.search(text):
                failures.append(f"{relative}: contains {label}")
        for match in address_pattern.finditer(text):
            address = match.group(0)
            if (
                address.casefold() not in OFFICIAL_PROTOCOL_ADDRESSES
                and not _is_fixture_address(address)
            ):
                failures.append(f"{relative}: contains a non-public wallet address")
        for match in profile_pattern.finditer(text):
            identifier = match.group(1)
            if identifier != "public-user" and not identifier.startswith("0x333333"):
                failures.append(f"{relative}: contains a Polymarket account identifier")
        for match in email_pattern.finditer(text):
            if match.group(1).casefold() != "example.com":
                failures.append(f"{relative}: contains a non-example email address")
        for match in ipv4_pattern.finditer(text):
            if match.group(0) not in {"0.0.0.0", "127.0.0.1"}:
                failures.append(f"{relative}: contains a non-loopback IP address")

    if failures:
        raise PublicMirrorError("public disclosure scan failed:\n" + "\n".join(failures))


def build_public_snapshot(source: Path, ref: str, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise PublicMirrorError(f"destination already exists: {destination}")
    _extract_tracked_ref(source, ref, destination)
    for relative in EXCLUDED_FROM_PUBLIC:
        target = destination / relative
        if target.exists():
            target.unlink()
    _disable_workflows(destination)
    _sanitize_text(destination)
    (destination / "PUBLIC_MIRROR.md").write_text(
        """# Public mirror safety boundary

This repository is an identity-free source snapshot. It contains no operator
account, wallet, host, cloud account, notification route, credential, or local
path. Workflow source is retained under `.github/workflows-disabled/` for code
review, but GitHub does not execute files from that directory.

Production forks must supply their own protected configuration. Missing
configuration is a successful no-op: research, execution, release, and Judge
jobs remain skipped. Never commit deployment values when adapting this mirror.
""",
        encoding="utf-8",
    )
    _scan_public_tree(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    build_public_snapshot(args.source, args.ref, args.destination)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublicMirrorError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
