"""Extract verified report assets from a private Forecast Edge research handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any


ADAPTER_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class HandoffError(ValueError):
    """The handoff archive is unsafe, malformed, or belongs to another run."""


def _load_manifest(archive: tarfile.TarFile) -> dict[str, Any]:
    try:
        member = archive.getmember("release-assets/research-release-manifest.json")
    except KeyError as exc:
        raise HandoffError("handoff archive has no research release manifest") from exc
    source = archive.extractfile(member)
    if source is None:
        raise HandoffError("handoff research release manifest is not a regular file")
    try:
        document = json.loads(source.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("handoff research release manifest is not valid JSON") from exc
    if not isinstance(document, dict):
        raise HandoffError("handoff research release manifest is not an object")
    return document


def _archive_files(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    files: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise HandoffError("handoff archive contains an unsafe path")
        if member.isdir():
            continue
        if not member.isfile():
            raise HandoffError("handoff archive contains a non-regular file")
        if member.name in files:
            raise HandoffError("handoff archive contains a duplicate file")
        files[member.name] = member
    return files


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    source = archive.extractfile(member)
    if source is None:
        raise HandoffError(f"could not read {member.name}")
    return source.read()


def extract_reports(
    *,
    archive_path: Path,
    destination: Path,
    expected_repository: str,
    expected_run_id: str,
    expected_run_attempt: str,
    expected_commit_sha: str,
) -> dict[str, Any]:
    if destination.exists() and any(destination.iterdir()):
        raise HandoffError("report destination is not empty")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)

    with tarfile.open(archive_path, mode="r:gz") as archive:
        files = _archive_files(archive)
        manifest = _load_manifest(archive)
        expected_identity = {
            "repository": expected_repository,
            "workflow_run_id": expected_run_id,
            "workflow_run_attempt": expected_run_attempt,
            "commit_sha": expected_commit_sha,
        }
        for key, expected in expected_identity.items():
            if str(manifest.get(key, "")) != expected:
                raise HandoffError(f"handoff manifest {key} does not match the parent run")
        if manifest.get("schema_version") != "2.0":
            raise HandoffError("handoff manifest schema version is unsupported")
        reports = manifest.get("reports")
        if not isinstance(reports, list) or not reports:
            raise HandoffError("handoff manifest contains no reports")
        if manifest.get("report_count") != len(reports):
            raise HandoffError("handoff report count does not match its manifest")

        selected: list[tuple[str, bytes, str]] = []
        metadata_files: list[dict[str, Any]] = []
        seen_assets: set[str] = set()
        for report in reports:
            if not isinstance(report, dict):
                raise HandoffError("handoff manifest contains a non-object report")
            adapter = str(report.get("adapter", ""))
            if not ADAPTER_PATTERN.fullmatch(adapter):
                raise HandoffError("handoff report adapter is invalid")
            json_asset = str(report.get("json_asset", ""))
            markdown_asset = str(report.get("markdown_asset", ""))
            if json_asset != f"report-{adapter}.json" or markdown_asset != f"report-{adapter}.md":
                raise HandoffError("handoff report asset names do not match the adapter")
            expected_sha256 = str(report.get("sha256", ""))
            if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
                raise HandoffError("handoff report digest is invalid")
            for asset, kind in ((json_asset, "json"), (markdown_asset, "markdown")):
                if asset in seen_assets:
                    raise HandoffError("handoff manifest repeats a report asset")
                seen_assets.add(asset)
                archive_name = f"release-assets/{asset}"
                member = files.get(archive_name)
                if member is None:
                    raise HandoffError(f"handoff archive is missing {asset}")
                data = _read_member(archive, member)
                digest = hashlib.sha256(data).hexdigest()
                if kind == "json" and digest != expected_sha256:
                    raise HandoffError(f"handoff report digest does not match {asset}")
                selected.append((asset, data, kind))
                metadata_files.append(
                    {
                        "path": asset,
                        "kind": kind,
                        "sha256": digest,
                        "size": len(data),
                    }
                )

        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n"
        selected.append(("research-release-manifest.json", manifest_bytes, "manifest"))
        metadata_files.append(
            {
                "path": "research-release-manifest.json",
                "kind": "manifest",
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "size": len(manifest_bytes),
            }
        )
        for name, data, _kind in selected:
            target = destination / name
            target.write_bytes(data)
            target.chmod(0o600)

    return {
        "schema_version": "1.0",
        "found": True,
        "verified": True,
        "source": "private_research_handoff",
        "repository": expected_repository,
        "workflow_run_id": expected_run_id,
        "workflow_run_attempt": expected_run_attempt,
        "commit_sha": expected_commit_sha,
        "files": metadata_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-attempt", required=True)
    parser.add_argument("--expected-commit-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        metadata = extract_reports(
            archive_path=args.archive,
            destination=args.destination,
            expected_repository=args.expected_repository,
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_commit_sha=args.expected_commit_sha,
        )
        args.output.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (HandoffError, OSError, tarfile.TarError) as exc:
        print(f"research handoff verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
