# Signals and position sizing

## Definitions

For a model probability `p`, define the held outcome's value:

```text
YES: v = p
NO:  v = 1 - p
```

Use fresh executable order-book prices, not a displayed last trade or midpoint. Use the selected adapter's executable instrument and its `fee_per_unit` value at every filled price level. The adapter must calculate that normalized amount from the venue's native fee rule; shared research and `trade_math.py` must never derive or select a venue fee formula.

## Crossing rules

For a new or incremental buy of quantity `q`:

```text
BUY crossing: v > ask_VWAP(q) + buy_fee(q) / q
```

For an existing holding of total quantity `h`:

```text
SELL crossing: bid_VWAP(h) - sell_fee(h) / h > v
```

Apply the sell rule to the complete holding. Issue `SELL` immediately when crossed, even at an accounting loss. Do not wait for a second run.

Issue:

- `BUY` for a new position that passes crossing, the hard `raw_edge > 0.10` floor, minimum-size, sizing, reserve, and quality rules.
- `ADD` only when the buy crossing survives on the incremental order, the hard `raw_edge > 0.10` floor, the post-cost edge retains at least 70% of raw edge, and the fractional-Kelly target exceeds current cost.
- `SELL` when the complete executable sell crossing holds.
- `TRIM` when a held position's liquidation share of bankroll drifts above its threshold; see the drift-trim rules below. A `SELL` crossing always takes priority over `TRIM`.
- `MAINTAIN` otherwise. This means preserving the account's current exposure: retain the current shares when already positioned, or remain uninvested when no position exists. If the recalculated target shrinks without a sell crossing, retain the position rather than performing an ordinary rebalance reduction.

Do not sell an un-crossed losing position to replace it with a better candidate.

## Hard 10pp entry floor and 30pp strong signals

For every `BUY` and `ADD`, require:

```text
raw_edge = v - best_ask
raw_edge > 0.10
```

This is a hard prompt-level execution constraint. Never recommend an entry at `raw_edge <= 0.10`; strong primary evidence, short capital lock-up, low execution drag, diversification value, idle cash, or any other consideration cannot override it.

The minimum executable order must also have positive post-fee and post-slippage edge, retain at least 70% of raw edge, satisfy minimum order size, and receive a positive shared portfolio allocation. The 10pp hard floor applies only to buying or adding; it does not delay a sell crossing.

Label `raw_edge > 0.30` as a strong signal in private guidance and the public brief. This is presentational only and changes no execution rule. Every `BUY` or `ADD` must carry the market-divergence diagnosis from [research-method.md](research-method.md); a missing or unconvincing diagnosis demotes the candidate regardless of raw edge.

## Market opportunity versus account action

Calculate an entry opportunity at the minimum executable order independently of account cash. Record whether its post-fee and post-slippage edge retains at least 70% of raw edge.

Then calculate the account action with the real bankroll and available cash:

- An entry opportunity may be `entry_eligible: true` while the account action remains `MAINTAIN` with zero allocation, meaning the account stays uninvested in that market.
- Emit account `BUY` or `ADD` only after `portfolio_allocate.py` assigns a positive executable amount.
- Never emit `BUY` or `ADD` with zero recommended cash. Preserve the standalone opportunity in `opportunity`, not in the account action.
- Never present a nominal, illustrative, or theoretical bankroll as an account recommendation.
- Keep public market commentary separate from private executable instructions.
- Pass the legacy machine field `soft_minimum_raw_edge: 0.10` to `trade_math.py`, but enforce it as hard policy in the Agent instructions. Always keep `soft_edge_override: false` and `soft_edge_override_reason` empty or null. Discard any below-threshold entry result regardless of what a legacy script field permits.

## Edge retention and capacity

Define:

```text
raw_edge = v - best_ask
actual_edge(q) = v - ask_VWAP(q) - buy_fee(q) / q
edge_retention(q) = actual_edge(q) / raw_edge
```

Require `actual_edge(q) > 0` and `edge_retention(q) >= 0.70`. Fees and slippage together may consume at most 30% of the original price edge. Calculate the largest order-book quantity satisfying both conditions rather than using a fixed dollar cap.

## Fractional-Kelly target

At fixed all-in unit cost `c`:

```text
full_Kelly = max(0, (v - c) / (1 - c))
configured_Kelly = 0.25 × full_Kelly
Kelly_target_cash = conservative_bankroll × configured_Kelly
```

Keep the Kelly fraction configurable and enabled by default at `0.25`.

Define conservative bankroll as:

```text
public available cash from the selected adapter's normalized snapshot
+ current holdings valued at complete executable bid VWAP after sell fees
```

If a verified authenticated available-to-trade value is lower than the normalized public balance, use the lower value as cash. Never substitute a fixed legacy balance.

Report model value separately. Do not use it to inflate the sizing bankroll.

The standalone incremental position cap is bounded by:

```text
min(
  incremental fractional-Kelly target,
  order-book capacity retaining at least 70% of raw edge after fees and slippage,
  cash available after the opportunity reserve,
  remaining per-market position cap
)
```

A single market's total cost (existing cost plus new entry) never exceeds 5% of bankroll by default. Keep the fraction configurable via `max_position_fraction`; the remaining cap is `max(0, max_position_fraction × bankroll - current_cost)`.

Respect the token minimum order size read from the adapter-normalized live order book. Pass that book unchanged to `trade_math.py`; do not supply a guessed or hard-coded replacement. If the computed position is smaller, do not place or recommend the order.

After calculating standalone caps for all retained opportunities, send them together in one `portfolio_allocate.py` request. Its sequential allocation, not independently repeated per-market cash budgets, determines tentative BUY and ADD amounts. A failed post-allocation portfolio review may revise the shared input and rerun it; retain only the accepted result.

## Drift trim

A held position can grow past its intended share of bankroll when its price rises. Measure the position at its complete executable exit value and trim the drift without waiting for a sell crossing:

```text
best_bid_net = best_bid - sell_fee_per_unit(best_bid)
t = kelly_fraction × max(0, (v - best_bid_net) / (1 - best_bid_net))
share = liquidation_value(all held shares at bid VWAP after sell fees) / bankroll
T = max(trim_band_multiplier × t, trim_ceiling_fraction)
```

Defaults: `trim_band_multiplier = 2.0`, `trim_ceiling_fraction = 0.15`.

- Signal priority is `SELL` > `TRIM` > `ADD`/`BUY` > `MAINTAIN`: an executable sell crossing always fires first, and a trim suppresses any incremental buy on the same market this run.
- Issue `TRIM` only when `share > T`. Trim back to the threshold `T`, not to `t` and not to zero: sell the quantity whose post-fee bid-side proceeds equal `(share - T) × bankroll`.
- Skip a trim whose computed quantity is below the token minimum order size; do not round a dust trim up.
- `TRIM` never increases exposure and never liquidates the position; only `SELL` exits completely. When `TRIM` fires, `recommended_target_cash` keeps its existing semantics and remains the current position cost.

## Drawdown circuit breaker

On every full scan, derive the historical peak bankroll from archived reports (`scripts/report_tool.py previous`) and compute:

```text
drawdown = 1 - current_bankroll / peak_bankroll
```

- At `drawdown >= 0.20`, pass `kelly_fraction: 0.125` (half of the default) explicitly to every sizing call this run, and open no new `BUY`; `ADD` on existing positions remains allowed at the reduced fraction.
- At `drawdown >= 0.33`, allow no `BUY` and no `ADD` this run; only `MAINTAIN`, `TRIM`, and `SELL`.
- Record the peak bankroll, the current drawdown, and the breaker state in the report's run or portfolio narrative fields.

## Opportunity reserve

Keep reserve cash near two to three reference positions without a fixed dollar cap. Use the median actual cost of current holdings and the latest two runs as the reference position. At cold start, use the final suggested sizes of the selected one or two candidates as the provisional reference.

Do not treat reserve as an inviolable percentage. An Agent may use it for an opportunity clearly better than the current average or current best, but must record the relative-quality rationale.

## Time-adjusted quality

For proposed quantity `q`, including buy fees:

```text
C(q) = executable purchase cost + fee
expected_profit = q × v - C(q)
daily_expected_return = expected_profit / C(q) / days_to_resolution
```

Use daily expected return for opportunity-cost ranking across settlement horizons. It does not independently create a crossing and does not trigger replacement of an existing non-crossed position.
