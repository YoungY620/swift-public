#!/usr/bin/env bash
# Linux-shell contract tests and copyable CLI examples for every bundled script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTS="$ROOT/tests"
FIXTURES="$TESTS/fixtures"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMP="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

"$PYTHON_BIN" "$TESTS/fixture_server.py" --port-file "$TMP/port" &
SERVER_PID=$!
for _ in {1..100}; do
  [[ -s "$TMP/port" ]] && break
  sleep 0.05
done
[[ -s "$TMP/port" ]]
BASE="http://127.0.0.1:$(cat "$TMP/port")"

# Public data CLI: profile resolution, positions, balance, market discovery, and book.
DATA=(
  "$PYTHON_BIN" "$ROOT/scripts/polymarket_data.py"
  --gamma-base "$BASE"
  --data-base "$BASE"
  --clob-base "$BASE"
  --site-base "$BASE"
  --assets-base "$BASE"
  --rpc-url "$BASE/rpc"
)
"${DATA[@]}" resolve '@fixture-user' > "$TMP/resolved.json"
"${DATA[@]}" positions '@fixture-user' > "$TMP/positions.json"
"${DATA[@]}" collateral-asset > "$TMP/collateral-asset.json"
"${DATA[@]}" balance '@fixture-user' > "$TMP/balance.json"
"${DATA[@]}" account '@fixture-user' > "$TMP/account.json"
if "${DATA[@]}" account '@fixture-user' \
  --token-address '0x5555555555555555555555555555555555555555' >/dev/null 2>&1; then
  echo "canonical account snapshots must reject token overrides" >&2
  exit 1
fi
"${DATA[@]}" discover --limit 1 > "$TMP/markets.json"
"${DATA[@]}" candidate-events \
  --account-snapshot "$TMP/account.json" --page-size 2 --safety-limit 10 \
  > "$TMP/candidate-events.json"
"${DATA[@]}" market 540817 > "$TMP/market.json"
"${DATA[@]}" event sample-event > "$TMP/event.json"
"${DATA[@]}" book yes-token > "$TMP/book.json"

# Probability CLI: recursive partition input produces a 0.56 settlement estimate.
"$PYTHON_BIN" "$ROOT/scripts/probability_tree.py" "$FIXTURES/probability-tree.json" > "$TMP/probability.json"

# Sizing CLI: this fixture has a valid BUY after fees, slippage, reserve, and half Kelly.
"$PYTHON_BIN" "$ROOT/scripts/trade_math.py" "$FIXTURES/trade-input.json" > "$TMP/trade.json"

# Portfolio CLI: all opportunities share one real cash budget and protected reserve.
"$PYTHON_BIN" "$ROOT/scripts/portfolio_allocate.py" \
  "$FIXTURES/portfolio-allocation.json" > "$TMP/allocation.json"

# Report CLI: validate, render, archive once, and read the latest two immutable reports.
"$PYTHON_BIN" "$ROOT/scripts/report_tool.py" validate "$FIXTURES/report.json" > "$TMP/valid.json"
"$PYTHON_BIN" "$ROOT/scripts/report_tool.py" render "$FIXTURES/report.json" "$TMP/report.md" > "$TMP/rendered.json"
"$PYTHON_BIN" "$ROOT/scripts/report_tool.py" archive "$FIXTURES/report.json" \
  --output-root "$TMP/runs" --snapshot "$TMP/book.json" > "$TMP/archived.json"
"$PYTHON_BIN" "$ROOT/scripts/report_tool.py" previous --output-root "$TMP/runs" --limit 2 > "$TMP/previous.json"

# Use Python for dependency-free JSON assertions; jq is not required on Linux.
"$PYTHON_BIN" - "$TMP" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
load = lambda name: json.loads((root / name).read_text())
assert load("resolved.json")["address"] == "0x2222222222222222222222222222222222222222"
assert load("positions.json")["count"] == 1
assert load("collateral-asset.json")["asset"] == "pUSD"
assert load("collateral-asset.json")["token_address"] == "0x4444444444444444444444444444444444444444"
assert load("balance.json")["asset"] == "pUSD"
assert load("balance.json")["balance"] == 123.456789
assert load("account.json")["positions"]["count"] == 1
assert load("account.json")["sizing_inputs"]["available_cash"] == 123.456789
assert load("account.json")["sizing_inputs"]["balance_source"] == "public_onchain_active_collateral"
assert load("account.json")["sizing_inputs"]["collateral_asset"] == "pUSD"
assert load("markets.json")[0]["outcomes"][0]["token_id"] == "yes-token"
universe = load("candidate-events.json")
assert universe["scan"]["complete"] is True
assert universe["scan"]["fetched_events"] == 4
assert universe["scan"]["expanded_markets"] == 4
assert universe["candidate_count"] == 2
assert universe["excluded_by_reason"] == {"current_holding_event": 1, "sports_or_esports": 1}
assert [item["id"] for item in universe["candidates"]] == ["event-540819", "event-540820"]
assert load("market.json")["id"] == "540817"
assert load("event.json")["markets"][0]["id"] == "540817"
assert load("book.json")["book"]["asks"][0]["price"] == 0.51
assert load("book.json")["fee"]["fee_rate"] == 0.04
assert abs(load("probability.json")["probability"] - 0.56) < 1e-12
assert load("trade.json")["signal"] == "BUY"
assert load("trade.json")["opportunity"]["entry_eligible"] is True
assert load("allocation.json")["portfolio"]["allocated_cash"] == 72.5
assert load("allocation.json")["portfolio"]["new_positions_allocated"] == 2
assert load("valid.json")["valid"] is True
assert len(load("previous.json")) == 1
assert (root / "report.md").is_file()
PY

echo "forecast-edge shell contract tests passed"
