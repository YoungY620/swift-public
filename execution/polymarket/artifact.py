"""Validation and filtering for the upstream JSONL artifact."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import TradeIntent


class ArtifactError(ValueError):
    """Raised when an upstream artifact is not safe to consume."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_validate_jsonl(
    path: Path,
    *,
    schema_path: Path,
    venue: str = "polymarket",
) -> list[TradeIntent]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - deployment configuration error.
        raise ArtifactError("jsonschema is required to validate the artifact") from exc
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"trade-intent schema is unavailable: {exc}") from exc
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(f"trade-intent artifact is unavailable: {exc}") from exc
    if text == "":
        return []

    intents: list[TradeIntent] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ArtifactError(
                f"line {line_number} is blank; no-action artifacts must be zero-byte files"
            )
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactError(f"line {line_number} is not valid JSON: {exc.msg}") from exc
        if not isinstance(raw, dict):
            raise ArtifactError(f"line {line_number} must contain one JSON object")
        errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.path))
        if errors:
            field = ".".join(str(item) for item in errors[0].path) or "<item>"
            raise ArtifactError(f"line {line_number} field {field}: {errors[0].message}")
        if raw.get("venue") != venue:
            continue
        intent = TradeIntent.from_dict(raw)
        if intent.instrument_identifier in seen:
            raise ArtifactError(
                f"line {line_number} duplicates token {intent.instrument_identifier}"
            )
        seen.add(intent.instrument_identifier)
        if intent.reference_price > Decimal("1"):
            raise ArtifactError(f"line {line_number} reference_price exceeds 1")
        if abs(intent.notional - intent.reference_price * intent.quantity) > Decimal("0.01"):
            raise ArtifactError(
                f"line {line_number} notional differs from reference_price * quantity"
            )
        intents.append(intent)
    return intents


def write_filtered_jsonl(path: Path, intents: list[TradeIntent]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for intent in intents:
            json.dump(intent.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"{label} must be a JSON object")
    return value

