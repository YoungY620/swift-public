#!/usr/bin/env bash
# Read-only end-to-end account test. The account is injected at runtime and never stored.
set -euo pipefail

if [[ -z "${POLYMARKET_ACCOUNT:-}" ]]; then
  echo "Set POLYMARKET_ACCOUNT to a public Polymarket profile URL, handle, or proxy address." >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TIMEOUT="${POLYMARKET_LIVE_TIMEOUT:-30}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

DATA=("$PYTHON_BIN" "$ROOT/scripts/polymarket_data.py" --timeout "$TIMEOUT" --retries 4)
if [[ -n "${POLYMARKET_RPC_URL:-}" ]]; then
  DATA+=(--rpc-url "$POLYMARKET_RPC_URL")
fi

# Fetch the canonical snapshot once so identity, positions, and collateral all
# come from the same account resolution. Keep the injected account out of logs.
"${DATA[@]}" account "$POLYMARKET_ACCOUNT" > "$TMP/account-snapshot.json"

# Fetch metadata and, when tradable, an executable book plus fee for every holding.
mkdir -p "$TMP/holding-markets" "$TMP/holding-books"
"$PYTHON_BIN" - "$TMP/account-snapshot.json" > "$TMP/holdings.tsv" <<'PY'
import json
import sys

positions = json.load(open(sys.argv[1], encoding="utf-8"))["positions"]["positions"]
for index, item in enumerate(positions):
    asset = str(item.get("asset") or "")
    slug = str(item.get("slug") or "")
    if not asset or not slug or "\t" in asset or "\t" in slug:
        raise SystemExit(f"position {index} is missing a usable asset or slug")
    print(index, asset, slug, sep="\t")
PY

ACTIVE_BOOKS=0
while IFS=$'\t' read -r index asset slug; do
  [[ -n "$asset" ]] || continue
  market_file="$TMP/holding-markets/$index.json"
  "${DATA[@]}" market "$slug" > "$market_file"
  if "$PYTHON_BIN" - "$market_file" <<'PY'
import json
import sys
market = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if market.get("accepting_orders") and not market.get("closed") else 1)
PY
  then
    "${DATA[@]}" book "$asset" > "$TMP/holding-books/$index.json"
    ACTIVE_BOOKS=$((ACTIVE_BOOKS + 1))
  fi
done < "$TMP/holdings.tsv"

# Also prove that candidate discovery and a new-market executable book work.
"${DATA[@]}" discover --limit 1 > "$TMP/candidates.json"
CANDIDATE_TOKEN="$($PYTHON_BIN - "$TMP/candidates.json" <<'PY'
import json
import sys
markets = json.load(open(sys.argv[1], encoding="utf-8"))
if not markets or not markets[0].get("outcomes") or not markets[0]["outcomes"][0].get("token_id"):
    raise SystemExit("candidate discovery did not return a tradable token")
print(markets[0]["outcomes"][0]["token_id"])
PY
)"
"${DATA[@]}" book "$CANDIDATE_TOKEN" > "$TMP/candidate-book.json"

# Validate completeness without printing or preserving the account identifier.
"$PYTHON_BIN" - "$TMP" "$ACTIVE_BOOKS" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
active_books = int(sys.argv[2])
load = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
snapshot = load("account-snapshot.json")
account = snapshot["account"]
profile = snapshot["profile"]
positions = snapshot["positions"]
balance = snapshot["collateral"]
candidate_book = load("candidate-book.json")
assert account["address"].startswith("0x") and len(account["address"]) == 42
assert isinstance(profile, dict)
assert isinstance(positions["positions"], list) and positions["count"] == len(positions["positions"])
assert isinstance(balance["balance"], (int, float)) and balance["balance"] >= 0
assert snapshot["sizing_inputs"]["available_cash"] == balance["balance"]
assert len(list((root / "holding-markets").glob("*.json"))) == positions["count"]
assert len(list((root / "holding-books").glob("*.json"))) == active_books
assert isinstance(candidate_book["book"]["asks"], list)
assert isinstance(candidate_book["book"]["bids"], list)
assert isinstance(candidate_book["fee"]["fee_rate"], (int, float))
print(f"live read-only account test passed: {positions['count']} positions, {active_books} active books")
PY
