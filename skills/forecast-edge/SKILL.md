---
name: forecast-edge
description: Research and allocate long-horizon prediction-market edges with public account data, recursive Bayesian point estimates, executable order books, fees, portfolio-aware selection, semantic diversification review, half-Kelly sizing, and immutable reports. Use for scheduled or manual Polymarket portfolio scans, full re-evaluation of current positions, discovery and analysis of new political, economic, cultural, or market candidates, and BUY, ADD, HOLD, or SELL recommendations without placing orders.
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
- Use only the observed account snapshot for cash, holdings, and bankroll. Never invent a nominal or reference bankroll.
- Discover the current active Polymarket collateral from official public asset metadata on every scan. Size with its public on-chain balance; never assume a fixed legacy collateral contract.
- Distinguish a market entry opportunity from an executable account action. An unfunded opportunity may remain `entry_eligible`, but its account action must be `HOLD` with zero allocation; never emit a zero-cash `BUY` or `ADD`.
- Keep market-correlation analysis qualitative. Do not create risk-cluster fields, graphs, identifiers, or persistent state, and do not change a market's probability tree merely to represent portfolio correlation.
- Prioritize political and economic candidates. Treat culture and markets as secondary domains, and exclude sports and esports markets from new-candidate research.
- Write the final private report and operation guidance in Simplified Chinese. Preserve machine keys, enums, URLs, proper names, and exact source text when necessary.

## Run a full scan

1. Establish the account input and output directory. Read the latest two archived reports with `scripts/report_tool.py previous` for evidence comparison, recent opportunity quality, and reference-position sizing only.
2. Create one canonical account snapshot with `scripts/polymarket_data.py account`. Save it unchanged, verify the resolved identity and dynamically discovered active collateral, and derive public collateral cash plus all current positions from it. Read [polymarket-data.md](references/polymarket-data.md).
3. Fetch complete executable bids for all positions, then calculate holdings liquidation value and conservative bankroll. Treat the public pUSD balance as the public cash estimate; if a verified authenticated available-to-trade value is lower, use the lower value. If cash is zero or unavailable, keep researching but do not invent buying power.
4. Inspect the current portfolio before expensive candidate research. Discover new markets after excluding current holdings, then build a portfolio-aware research queue using the first pass in [portfolio-management.md](references/portfolio-management.md) and [market-selection.md](references/market-selection.md).
5. Reconstruct and re-research every current market. For every queued candidate, build and repeatedly revise a recursive scenario tree. Read [research-method.md](references/research-method.md) and [evidence-policy.md](references/evidence-policy.md); use `scripts/probability_tree.py` for arithmetic.
6. Fetch a fresh order book and fee rate for every held or proposed outcome. Use `scripts/trade_math.py` to separate standalone market opportunities from cash-constrained account actions and to calculate half-Kelly and liquidity caps. Read [signals-and-sizing.md](references/signals-and-sizing.md).
7. Reassess the researched opportunities as one portfolio. Apply the second semantic-diversification pass in [portfolio-management.md](references/portfolio-management.md), remove or demote redundant risks, and rank the retained BUY and ADD opportunities.
8. Submit the entire retained opportunity set to one shared `scripts/portfolio_allocate.py` call with the canonical cash, holdings value, reserve references, cold-start state, and final ranking. Never call it independently per market. Use its allocations as the only tentative BUY and ADD amounts.
9. Review the tentative portfolio after allocation. Confirm total funding, protected reserve, cold-start count, liquidity, concentration, shared-loss scenarios, and idle cash. If the review fails, revise eligibility, order, or portfolio caps and rerun the one shared allocation; discard the rejected output. Use only the final accepted allocation in the report.
10. Build `report.json`, validate cross-field portfolio consistency and required Chinese narrative fields, render the Simplified-Chinese `report.md`, and archive both with `scripts/report_tool.py`. Read [report-format.md](references/report-format.md) first.

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
python3 scripts/polymarket_data.py --pretty collateral-asset
python3 scripts/probability_tree.py --pretty tree.json
python3 scripts/trade_math.py --pretty sizing.json
python3 scripts/portfolio_allocate.py --pretty allocation.json
python3 scripts/report_tool.py validate report.json
python3 scripts/report_tool.py archive report.json --output-root ./forecast-edge-runs
```

Treat script results as deterministic calculations, not research judgments. The Agent chooses the causal structure, evidence weights, point estimates, semantic portfolio quality, candidate eligibility and priority, and whether an opportunity is exceptional enough to release reserve cash. Never replace a script's zero allocation with a theoretical dollar amount.

## Read resources progressively

- Read [polymarket-data.md](references/polymarket-data.md) only for account, market, fee, and order-book retrieval.
- Read [research-method.md](references/research-method.md) whenever estimating or updating a settlement probability.
- Read [evidence-policy.md](references/evidence-policy.md) whenever collecting, evaluating, or citing evidence.
- Read [market-selection.md](references/market-selection.md) for candidate screening, horizon, cadence, relative quality, and qualitative correlation checks.
- Read [portfolio-management.md](references/portfolio-management.md) before candidate research, after market research, and after tentative allocation.
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
POLYMARKET_ACCOUNT='https://polymarket.com/@public-user' \
POLYMARKET_EXPECTED_MIN_COLLATERAL='1' \
bash tests/test_live_account.sh
```

Never replace the placeholder with a personal account in a tracked file.
