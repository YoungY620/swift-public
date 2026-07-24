#!/usr/bin/env bash
# Linux-shell executable documentation for the presenter CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/report.json"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$PYTHON_BIN" "$ROOT/scripts/present_report.py" validate "$FIXTURE" > "$TMP/valid.json"
"$PYTHON_BIN" "$ROOT/scripts/present_report.py" generate "$FIXTURE" \
  --output-root "$TMP/automatic" > "$TMP/generated.json"
"$PYTHON_BIN" "$ROOT/scripts/present_report.py" prepare-draft "$FIXTURE" \
  --workspace "$TMP/workspace" > "$TMP/draft.json"
"$PYTHON_BIN" "$ROOT/scripts/present_report.py" finalize-draft "$FIXTURE" \
  --draft "$TMP/workspace/research-report.md" \
  --output-root "$TMP/collaborative" > "$TMP/finalized.json"

"$PYTHON_BIN" - "$FIXTURE" "$TMP/invalid-report.json" <<'PY'
import json
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
report = json.loads(source.read_text(encoding="utf-8"))
report["schema_version"] = "2.0"
destination.write_text(json.dumps(report), encoding="utf-8")
PY

if "$PYTHON_BIN" "$ROOT/scripts/present_report.py" generate "$TMP/invalid-report.json" \
  --output-root "$TMP/failure" > /dev/null 2>&1; then
  echo "invalid input unexpectedly generated a presentation" >&2
  exit 1
fi

"$PYTHON_BIN" - "$TMP" <<'PY'
import json
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
load = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
assert load("valid.json")["valid"] is True
for result_name in ("generated.json", "finalized.json"):
    destination = pathlib.Path(load(result_name)["presentation_directory"])
    assert {path.name for path in destination.iterdir()} == {
        "manifest.json", "private-brief.md", "research-report.md",
        "public-signals.json", "public-posts.md",
    }
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
failures = list((root / "failure" / "presentations").iterdir())
assert len(failures) == 1
assert {path.name for path in failures[0].iterdir()} == {"manifest.json", "errors.json"}
assert json.loads((failures[0] / "manifest.json").read_text())["validation"] == "FAIL"
PY

echo "present-forecast-research shell contract tests passed"
