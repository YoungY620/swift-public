# Market selection

## Order of work

1. Research every current position first; none may be omitted because it appears unchanged.
2. Exclude current holdings from new-market discovery.
3. Pull active Polymarket markets in API order and screen them sequentially until the research round is full.
4. Treat about ten fully researched new markets as a soft operational ceiling. Continue beyond it only when the opportunity set or available parallel capacity clearly justifies doing so.

The limit applies to new research, not to re-evaluating current holdings. There is no maximum research time. Use independent sub-agents when available and useful.

## Preferred domains

Cover politics, economics, culture, and markets. Prefer questions where systematic public-information retrieval, slow-moving developments, causal analysis, empirical research, or exact reading of resolution criteria gives an Agent a plausible advantage.

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

- Prefer candidates above the recent normal quality baseline.
- Watch rather than buy candidates materially below it.
- Reject candidates far below it even if their raw price difference is positive.
- Let cash remain idle when too few candidates meet the quality standard.
- Use reserve cash only for an opportunity that is clearly exceptional relative to the current average or best opportunity. Document the comparison; do not pretend that this qualitative judgment is a script result.

## Diversification without a risk structure

Do not require a fixed number of holdings. Half-Kelly sizing, order-book capacity, opportunity quality, and available cash determine the number naturally.

During final selection, reason qualitatively about whether several contracts depend on the same underlying event. Do not count highly related contracts as independent diversification. This is a process reminder only:

- Do not create a risk-cluster entity or identifier.
- Do not add a correlation matrix or automatic script.
- Do not persist dedicated correlation state.
- Do not alter the Bayesian tree to represent portfolio correlation.

If correlation materially affects a recommendation, mention it briefly in the natural-language rationale.
