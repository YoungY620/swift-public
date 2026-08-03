# Market selection

## Order of work

1. Research every current position first; none may be omitted because it appears unchanged.
2. Exclude current holdings from new-market discovery.
3. Use `scripts/polymarket_data.py candidate-events --account-snapshot account.json` to follow the active Gamma event feed's opaque keyset cursor until exhaustion. Require `scan.complete=true`; never use a single API page as the candidate universe.
4. Let the script remove events containing current holdings, sports/esports, duplicates, and events without mechanically usable markets. Do not apply these exclusions to the separate current-position review.
5. Continue cheap semantic screening through the complete remaining sequence until about 100 events qualify, or until the sequence is exhausted. Reject events where public information, causal attribution, or exact settlement-rule analysis gives the Agent no plausible advantage. The target is about 100 qualified events after exclusions, not 100 raw API rows or 100 contracts from a few large events.
6. Fetch each selected event with `scripts/polymarket_data.py event EVENT_ID_OR_SLUG` and expand its child markets. Apply the resolution-clarity gate below, then compare deadlines, thresholds, prices, criteria, and instrument redundancy before ranking contracts.
7. Apply the first pass in `portfolio-management.md` to the passing contracts and build a varied deep-research queue. Follow the descending raw-edge search below and treat about ten distinct fully researched markets per tier as the attempt threshold. Continue beyond a tier's threshold only when a small amount of additional research is likely to satisfy the operation needs.

## Resolution-clarity gate

For a new market, require complete criteria, explicit outcome/scope/actors/time, defined exclusions and sources, and no unresolved contradiction or material subjective standard. Reject it when the Agent must invent a threshold, treat examples as exhaustive, or infer whether a time window may cross the end date. Record the short rejection reason; do not research, rank, size, or trade a failed market.

The limits apply to new research, not to re-evaluating current holdings. Full-event pagination and the roughly 100-event prescreen limit cheap inspection; contract expansion and ranking happen only after event selection; the roughly ten-market-per-tier limit controls expensive evidence and probability research. There is no maximum research time. Use independent sub-agents when available and useful.

Do not claim that the final set is the theoretical global optimum. Full metadata enumeration prevents first-page sampling bias, but only the deeply researched subset receives evidence-backed probability estimates and executable-edge calculations.

## Descending raw-edge search

Treat the operation needs as the number and size of funded `BUY` and `ADD` actions permitted by actual cash, reserve, cold-start cadence, portfolio quality, diversification, and execution constraints. Never invent a target position count merely to declare the needs unmet.

1. Start with the `raw_edge >= 0.30` tier. Prefer contracts whose initial causal thesis and executable ask make a 30pp edge plausible. Defer lower-tier opportunities even if they already clear the 10pp hard floor.
2. Do not relax the first tier until both thresholds are met: semantically screen about 100 qualified events or exhaust the eligible event sequence, and deeply research about ten distinct plausible 30pp markets or exhaust that tier's plausible candidates. Count a completed deep research attempt even when its final estimate misses 30pp.
3. If operation needs remain unmet, open the `raw_edge >= 0.20` tier and deeply research about ten distinct plausible 20pp markets or exhaust that tier. Reuse completed research; assign each market to the tier targeted when research began and never count it twice.
4. If operation needs still remain unmet, open the final `raw_edge > 0.10` tier and research the best remaining candidates up to the same attempt threshold or pool exhaustion.
5. Stop relaxing at `raw_edge > 0.10`. If no retained opportunity also passes crossing, post-cost edge, 70% edge retention, minimum size, cash, reserve, and portfolio review, recommend no `BUY` or `ADD` for the cycle and leave the relevant exposure unchanged.

Treat 30pp and 20pp as search-priority tiers, not independent guarantees of an action. Confirm the tier only after full probability research against the fresh executable best ask.
Stop the staged search as soon as the operation needs are satisfied; do not open a lower tier merely to produce more recommendations.

## Preferred domains

Prioritize politics and economics. Consider culture and markets only as secondary domains. Prefer questions where systematic public-information retrieval, slow-moving developments, causal analysis, empirical research, or exact reading of resolution criteria gives an Agent a plausible advantage.

Exclude sports and esports from new-candidate research, including matches, tournaments, athlete performance, team results, roster moves, transfers, and future-team markets. These outcomes contain substantial irreducible randomness and offer less durable information-retrieval or causal-reasoning advantage for an Agent. Continue to fully re-research an already-held sports position until it is sold or settled; the exclusion applies to new discovery, not portfolio oversight.

Do not assume that a popular or liquid market is inefficient. Liquidity and volume improve price responsiveness and exit execution; the model-price comparison must independently demonstrate the edge.

## Resolution horizon

Do not impose a fixed date range. One to two months is often attractive because capital turns over faster and evidence is developed enough to analyze, but allow:

- Shorter markets when the book has not incorporated a material public event.
- Longer markets when the causal framework and edge compensate for capital lock-up.

Use the time-adjusted quality calculation from `signals-and-sizing.md` to compare opportunities with different settlement dates. Do not sell a non-crossed losing position merely because another candidate has a better time-adjusted score.

## New-position cadence

At cold start, add only one or two selected markets per run. The purpose is to spend capital gradually while learning the available opportunity set, not to divide the bankroll equally.

After cold start, let exits, settlements, available cash, quality, and reserve policy determine whether to add positions. Do not force a new entry every run.

## Quality and idle cash

A candidate must first pass the absolute executable-edge and sizing rules. Then compare its time-adjusted quality with current qualified holdings and the accepted opportunities in the latest two reports.

Require raw probability edge strictly above 10pp for every `BUY` or `ADD`. Treat 10pp as a hard execution boundary with no evidence, horizon, liquidity, diversification, or portfolio-quality exception. Never lower the post-cost edge or 70% edge-retention requirements either.

- Prefer candidates above the recent normal quality baseline.
- Watch rather than buy candidates materially below it.
- Reject candidates far below it even if their raw price difference is positive.
- Let cash remain idle when too few candidates meet the quality standard.
- Use reserve cash only for an opportunity that is clearly exceptional relative to the current average or best opportunity. Document the comparison; do not pretend that this qualitative judgment is a script result.

## Diversification without a risk structure

Do not require a fixed number of holdings. Fractional-Kelly sizing, order-book capacity, opportunity quality, and available cash determine the number naturally.

Apply the qualitative process in `portfolio-management.md` both while building the research queue and during final selection. Do not count highly related contracts as independent diversification:

- Do not create a risk-cluster entity or identifier.
- Do not add a correlation matrix or automatic script.
- Do not persist dedicated correlation state.
- Do not alter the Bayesian tree to represent portfolio correlation.

If correlation materially affects a recommendation, mention it briefly in the natural-language rationale.
