# Signals and position sizing

## Definitions

For a model probability `p`, define the held outcome's value:

```text
YES: v = p
NO:  v = 1 - p
```

Use fresh executable order-book prices, not a displayed last trade or midpoint. Fetch the token-specific fee rate and calculate fees at every filled price level:

```text
fee = shares × fee_rate × price × (1 - price)
```

If Polymarket changes its fee contract, update the script and tests rather than preserving this formula from memory.

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

- `BUY` for a new position that passes crossing, minimum-size, sizing, reserve, and quality rules.
- `ADD` only when the buy crossing survives on the incremental order, the post-cost edge retains at least 70% of raw edge, and the half-Kelly target exceeds current cost.
- `SELL` when the complete executable sell crossing holds.
- `HOLD` otherwise. If the recalculated target shrinks without a sell crossing, hold rather than performing an ordinary rebalance reduction.

Do not sell an un-crossed losing position to replace it with a better candidate.

## Edge retention and capacity

Define:

```text
raw_edge = v - best_ask
actual_edge(q) = v - ask_VWAP(q) - buy_fee(q) / q
edge_retention(q) = actual_edge(q) / raw_edge
```

Require `actual_edge(q) > 0` and `edge_retention(q) >= 0.70`. Fees and slippage together may consume at most 30% of the original price edge. Calculate the largest order-book quantity satisfying both conditions rather than using a fixed dollar cap.

## Half-Kelly target

At fixed all-in unit cost `c`:

```text
full_Kelly = max(0, (v - c) / (1 - c))
configured_Kelly = 0.50 × full_Kelly
Kelly_target_cash = conservative_bankroll × configured_Kelly
```

Keep the Kelly fraction configurable and enabled by default at `0.50`.

Define conservative bankroll as:

```text
publicly visible available collateral
+ current holdings valued at complete executable bid VWAP after sell fees
```

Report model value separately. Do not use it to inflate the sizing bankroll.

The final incremental position is bounded by:

```text
min(
  incremental half-Kelly target,
  order-book capacity retaining at least 70% of raw edge after fees and slippage,
  cash available after the opportunity reserve
)
```

Respect token minimum order size. If the computed position is smaller, do not place or recommend the order.

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
