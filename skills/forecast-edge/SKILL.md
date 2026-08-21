---
name: forecast-edge
description: Research and allocate long-horizon prediction-market edges through registered read-only venue adapters, recursive conditional-probability trees, executable order books, fees, portfolio-aware selection, semantic diversification review, quarter-Kelly sizing (fraction configurable), and immutable presentation-closed reports. Use for scheduled or manual prediction-market portfolio scans, full re-evaluation of current positions, discovery and analysis of new political, economic, cultural, or market candidates, and BUY, ADD, MAINTAIN, TRIM, or SELL recommendations without placing orders.
---

# Forecast Edge

Estimate final settlement probabilities more accurately than the market, compare them with executable prices, and produce evidence-backed actions. Treat this as a long-horizon advantage strategy, not risk-free arbitrage.

## Preserve these invariants

- Use only the venue adapters registered in `config/adapters.json`. Never branch on a venue inside the research method.
- Use public, read-only data. Never request credentials, authenticate, sign, or submit orders.
- For every automated research iteration, always write and validate the required trade-intent JSONL. Write a valid empty file when there are no actionable intents; never omit the artifact or weaken the strategy gates merely to populate it.
- Estimate the probability of the exact settlement outcome, not the next short-term price move.
- Express every model as `P(market settles YES)`. Derive the `NO` direction value as `1 - P(YES)`.
- Use a point estimate. Do not introduce probability distributions or confidence intervals.
- Re-research every current position on every full scan. Do not reuse the previous probability as the new answer.
- Emit an immediate crossing signal. Do not require confirmation across multiple runs.
- Emit only `BUY`, `ADD`, `MAINTAIN`, `TRIM`, or `SELL` as the final action for a researched market. Use `TRIM` only to partially reduce an existing held position so its market value returns to the drift threshold; it never increases exposure and never fully exits — a full exit is always `SELL`.
- Use `MAINTAIN` whenever the account should leave its current exposure unchanged. For an existing position, this means retaining its current shares; for an unheld market, this means remaining uninvested. Never use the action name alone to infer whether a position exists.
- Allow cash to remain idle. Never add a weak candidate merely to reach a position count or deployment target.
- Recommend `BUY` or `ADD` only when the raw probability edge at the executable best ask is strictly greater than 10 percentage points. Treat this as a hard floor with no exceptions; positive post-cost edge and 70% edge retention remain independently mandatory.
- Search for actionable raw edge in descending tiers: prioritize at least 30 percentage points, relax to at least 20 percentage points only after the search and attempt thresholds are met without satisfying the account's operation needs, and consider edges strictly above 10 percentage points only as the final tier. If no opportunity clears the hard floor and all execution and portfolio rules, skip `BUY` and `ADD` for the cycle.
- Never modify an archived report.
- Use only the observed account snapshot for cash, holdings, and bankroll. Never invent a nominal or reference bankroll.
- Use the selected adapter's fresh normalized portfolio currency and public available-cash value on every scan. Never assume a fixed currency, collateral contract, or transferable balance between venues.
- Distinguish a market entry opportunity from an executable account action. An unfunded opportunity may remain `entry_eligible`, but its account action must be `MAINTAIN` with zero allocation; never emit a zero-cash `BUY` or `ADD`.
- Keep market-correlation analysis qualitative. Do not create risk-cluster fields, graphs, identifiers, or persistent state, and do not change a market's probability tree merely to represent portfolio correlation.
- Prioritize political and economic candidates. Treat culture and markets as secondary domains, and exclude sports and esports markets from new-candidate research.
- Enumerate the complete active-event metadata universe before new-candidate screening. Select events before expanding their child markets, and never describe a truncated API page or deeply researched subset as the global optimum.
- Deep-research at least ten eligible events absent from the archived previously-researched set in every full scan, unless fewer novel eligible events exist; never weaken the resolution-clarity, liquidity, or research gates to fill the quota.
- Reject a new market when its resolution criteria are ambiguous, contradictory, materially subjective, or require the Agent to invent a standard.
- Before pricing or signaling any researched market, independently verify that the rule interpretation omits nothing, adds nothing, and does not turn examples into exhaustive definitions.
- Write the final private report and operation guidance in Simplified Chinese. Preserve machine keys, enums, URLs, proper names, and exact source text when necessary.
- Produce only schema `3.0` for new reports. Embed a complete validated probability tree, evidence links, resolution review, and incremental execution deltas so a separate presenter never needs to research or infer missing facts.

## Run a full scan

Run the complete sequence independently for every enabled adapter. Keep each venue's account,
currency, holdings, order books, portfolio allocation, and report separate. Research logic is
shared, while the resulting trade intents retain their exact venue and native identifiers.

1. Establish the account input and output directory. Read the latest two archived reports for this adapter with `scripts/report_tool.py previous --adapter ADAPTER` for evidence comparison, recent opportunity quality, and reference-position sizing only. Never use another venue's reports as this venue's history.
2. Read [market-adapters.md](references/market-adapters.md). Create one normalized canonical account snapshot with `scripts/market_data.py --adapter ADAPTER account ACCOUNT`. Save it unchanged, verify `adapter`, resolved identity, portfolio currency, public cash, and all normalized positions. Read the selected adapter's reference document for its API and native semantics.
3. Fetch complete executable bids through the same adapter for all positions, then calculate holdings liquidation value and conservative bankroll. Treat the normalized public available-cash value as the public cash estimate; if a verified authenticated available-to-trade value is lower, use the lower value. If cash is zero or unavailable, keep researching but do not invent buying power.
4. Inspect the current venue portfolio before expensive candidate research. Run `scripts/market_data.py --adapter ADAPTER candidate-events --account-snapshot SNAPSHOT` and require the adapter's complete active-event traversal before screening. Deterministically remove events containing current holdings, sports/esports, duplicates, and events without usable markets. Continue cheap Agent screening through the returned sequence until about 100 events pass domain, causal-analyzability, horizon, portfolio, and rule-clarity checks, or until the sequence is exhausted. Derive this adapter's already-researched market and event set with `scripts/previously_researched.py REPORTS_DIR --adapter ADAPTER --pretty`; `REPORTS_DIR` is the required positional archive path. Fetch selected events through `market_data.py event`, expand child markets, reject unclear criteria, then rank the remaining contracts into the descending 30pp, 20pp, and greater-than-10pp deep-research tiers using [portfolio-management.md](references/portfolio-management.md) and [market-selection.md](references/market-selection.md), satisfying the novel-event research quota.
5. Reconstruct and re-research every current market. For every queued candidate, build and repeatedly revise one recursive weighted conditional-probability tree rooted at market `YES`. Read [research-method.md](references/research-method.md) and [evidence-policy.md](references/evidence-policy.md); use `scripts/probability_tree.py` for 12-place Decimal arithmetic. Before calculating a final probability or action, run the independent rule-understanding verification in [research-method.md](references/research-method.md).
6. Fetch a fresh order book and fee rate for every held or proposed outcome. Pass the adapter-normalized book, including its venue-supplied `min_order_size`, unchanged to `scripts/trade_math.py`; never invent or override the minimum order shares. Use the script to separate standalone market opportunities from cash-constrained account actions and to calculate fractional-Kelly and liquidity caps. Before sizing, apply the drawdown circuit breaker in [signals-and-sizing.md](references/signals-and-sizing.md): derive peak bankroll from archived reports and scale or suspend entries when the drawdown crosses its thresholds. Read [signals-and-sizing.md](references/signals-and-sizing.md).
7. Reassess the researched opportunities as one portfolio. Apply the second semantic-diversification pass in [portfolio-management.md](references/portfolio-management.md), remove or demote redundant risks, and rank the retained BUY and ADD opportunities.
8. Submit the entire retained opportunity set to one shared `scripts/portfolio_allocate.py` call with the canonical cash, holdings value, reserve references, cold-start state, and final ranking. Never call it independently per market. Use its allocations as the only tentative BUY and ADD amounts.
9. Review the tentative portfolio after allocation. Confirm total funding, protected reserve, cold-start count, liquidity, concentration, shared-loss scenarios, and idle cash. If the review fails, revise eligibility, order, or portfolio caps and rerun the one shared allocation; discard the rejected output. Use only the final accepted allocation in the report.
10. Build one schema `3.0` `report.json` for this adapter. Set `run.account.adapter`, keep the normalized account identifier, and ensure every action belongs to that adapter. Recompute every embedded tree and execution fill, validate evidence and resolution links, cross-field portfolio consistency, action deltas, and required Chinese narrative fields, resolve canonical URLs through the adapter, verify every published URL opens and matches the researched criteria, then render `report.md` and archive both with `scripts/report_tool.py`. Read [report-format.md](references/report-format.md) first.

When holdings or candidates are numerous, parallelize independent market research if sub-agents are available. Give each worker the exact market, settlement criteria, and required output fields; independently verify its citations and calculations before merging.

## Resolve insufficient evidence

Do not stop at an actionless “insufficient evidence” conclusion.

1. Search additional query formulations, date ranges, languages, primary records, reputable reporting, academic work, and credible base-rate research.
2. Rebuild the causal tree if the search reveals a better explanatory framework.
3. Assign unresolved leaf conditions a defensible base rate; use `0.5` only when the evidence genuinely provides no directional information.
4. Complete the point estimate and compute a normal crossing signal from it.
5. If an executable book remains unavailable after retries and alternative public endpoints, use `MAINTAIN` for an existing position and exclude a new candidate. State the mechanical data failure and the sources attempted in the report; do not label this as a probabilistic conclusion.

## Use the bundled scripts

All scripts accept `--help`, print machine-readable JSON to stdout, send diagnostics to stderr, and return exit code `2` for invalid inputs or unavailable required data.

```bash
python3 scripts/market_data.py adapters
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" --pretty account "$FORECAST_EDGE_ACCOUNT" > account.json
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" --pretty candidate-events --account-snapshot account.json > candidate-events.json
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" --pretty event EVENT_ID_OR_SLUG
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" --pretty book INSTRUMENT_IDENTIFIER
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" canonical-url MARKET_IDENTIFIER
python3 scripts/probability_tree.py --pretty tree.json
python3 scripts/trade_math.py --pretty sizing.json
python3 scripts/portfolio_allocate.py --pretty allocation.json
python3 scripts/previously_researched.py REPORTS_DIR --adapter "$FORECAST_EDGE_MARKET_ADAPTER" --pretty
python3 scripts/report_tool.py validate report.json
python3 scripts/report_tool.py archive report.json --output-root ./forecast-edge-runs
```

Use these exact argument shapes. If an invocation is not shown, run that script with `--help` before constructing it; never guess option names. Parse only the JSON written by a command to stdout or an explicitly named `.json`/`.jsonl` output file. Source files such as `.py` are executable code, never JSON input.

Treat script results as deterministic calculations, not research judgments. The Agent chooses the causal structure, evidence weights, point estimates, semantic portfolio quality, candidate eligibility and priority, and whether an opportunity is exceptional enough to release reserve cash. Enforce the prompt-level hard `raw_edge > 0.10` rule even though legacy script fields call it soft; never request or honor a below-threshold override. Never replace a script's zero allocation with a theoretical dollar amount.

## Read resources progressively

- Read [market-adapters.md](references/market-adapters.md) before any account, market, URL, fee, or order-book retrieval. Then read only the selected adapter's venue-specific reference document declared by `config/adapters.json`.
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

Use the selected adapter's live connectivity test and inject its public account only at runtime.

Never replace the placeholder with a personal account in a tracked file.
