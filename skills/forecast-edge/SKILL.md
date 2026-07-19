---
name: forecast-edge
description: Research and size long-horizon prediction-market edges with public data, recursive Bayesian point estimates, executable order books, fees, half-Kelly sizing, and immutable reports. Use for scheduled or manual Polymarket portfolio scans, full re-evaluation of current positions, discovery and analysis of new political, economic, cultural, or market candidates, and BUY, ADD, HOLD, or SELL recommendations without placing orders.
---

# Forecast Edge

Estimate final settlement probabilities more accurately than the market, compare them with executable prices, and produce evidence-backed actions. Treat this as a long-horizon advantage strategy, not risk-free arbitrage.

## Preserve these invariants

- Use Polymarket only in v1.
- Use public, read-only data. Never request credentials, authenticate, sign, or submit orders.
- Estimate the probability of the exact settlement outcome, not the next short-term price move.
- Use a point estimate. Do not introduce probability distributions or confidence intervals.
- Re-research every current position on every full scan. Do not reuse the previous probability as the new answer.
- Emit an immediate crossing signal. Do not require confirmation across multiple runs.
- Emit only `BUY`, `ADD`, `HOLD`, or `SELL` as the final action for a researched market.
- Allow cash to remain idle. Never add a weak candidate merely to reach a position count or deployment target.
- Never modify an archived report.
- Keep market-correlation analysis qualitative. Do not create risk-cluster fields, graphs, identifiers, or persistent state, and do not change a market's probability tree merely to represent portfolio correlation.

## Run a full scan

1. Establish the account input and output directory.
2. Read the latest two archived reports with `scripts/report_tool.py previous`. Use them only for evidence comparison, recent opportunity quality, and reference-position sizing.
3. Resolve the public Polymarket profile, read public collateral balance and every current position, and save the source responses as run snapshots. Read [polymarket-data.md](references/polymarket-data.md) before fetching.
4. Reconstruct every current market's exact settlement criteria. Research it from scratch, then compare the completed result with the latest report.
5. Discover new markets after excluding current holdings. Screen in order until the research round is full; treat approximately ten fully researched new markets as a soft operational limit, not a hard cap.
6. For every current position and retained candidate, build and repeatedly revise a recursive scenario tree. Read [research-method.md](references/research-method.md) and [evidence-policy.md](references/evidence-policy.md). Use `scripts/probability_tree.py` for arithmetic and validation.
7. Fetch a fresh order book and fee rate for the held or proposed outcome. Use `scripts/trade_math.py` for crossings, VWAP, fees, edge retention, half-Kelly sizing, reserve constraints, and time-adjusted quality.
8. Rank only candidates that pass the absolute executable-edge rules. Apply the qualitative correlation check while selecting; do not mistake several contracts driven by one event for independent diversification. Read [market-selection.md](references/market-selection.md) and [signals-and-sizing.md](references/signals-and-sizing.md).
9. Build `report.json`, validate it, render `report.md`, and archive both with `scripts/report_tool.py`. Read [report-format.md](references/report-format.md) first.

When holdings or candidates are numerous, parallelize independent market research if sub-agents are available. Give each worker the exact market, settlement criteria, and required output fields; independently verify its citations and calculations before merging.

## Resolve insufficient evidence

Do not stop at an actionless “insufficient evidence” conclusion.

1. Search additional query formulations, date ranges, languages, primary records, reputable reporting, academic work, and credible base-rate research.
2. Rebuild the causal tree if the search reveals a better explanatory framework.
3. Assign unresolved leaf conditions a defensible base rate; use `0.5` only when the evidence genuinely provides no directional information.
4. Complete the point estimate and compute a normal crossing signal from it.
5. If an executable book remains unavailable after retries and alternative public endpoints, use `HOLD` for an existing position and exclude a new candidate. State the mechanical data failure and the sources attempted in the report; do not label this as a probabilistic conclusion.

## Use the bundled scripts

All scripts accept `--help`, print machine-readable JSON to stdout, send diagnostics to stderr, and return exit code `2` for invalid inputs or unavailable required data.

```bash
python3 scripts/polymarket_data.py --pretty positions 'https://polymarket.com/@account'
python3 scripts/probability_tree.py --pretty tree.json
python3 scripts/trade_math.py --pretty sizing.json
python3 scripts/report_tool.py validate report.json
python3 scripts/report_tool.py archive report.json --output-root ./forecast-edge-runs
```

Treat script results as deterministic calculations, not research judgments. The Agent chooses the causal structure, evidence weights, point estimates, candidate quality, and whether an opportunity is exceptional enough to use reserve cash.

## Read resources progressively

- Read [polymarket-data.md](references/polymarket-data.md) only for account, market, fee, and order-book retrieval.
- Read [research-method.md](references/research-method.md) whenever estimating or updating a settlement probability.
- Read [evidence-policy.md](references/evidence-policy.md) whenever collecting, evaluating, or citing evidence.
- Read [market-selection.md](references/market-selection.md) for candidate screening, horizon, cadence, relative quality, and qualitative correlation checks.
- Read [signals-and-sizing.md](references/signals-and-sizing.md) before calculating any action or position.
- Read [report-format.md](references/report-format.md) and [report.schema.json](references/report.schema.json) before producing a report.

## Use tests as executable documentation

Read `tests/test_cli.sh` for complete Linux-shell invocation examples. Read the matching `tests/test_*.py` file for each script's numerical contract and edge cases. Run both layers after changing a script:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/test_cli.sh
```

To verify real connectivity without storing an account in the Skill, inject it only at runtime:

```bash
POLYMARKET_ACCOUNT='https://polymarket.com/@public-user' bash tests/test_live_account.sh
```

Never replace the placeholder with a personal account in a tracked file.
