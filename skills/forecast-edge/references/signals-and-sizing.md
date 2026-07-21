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

- `BUY` for a new position that passes crossing, the 10pp soft preference or a documented exception, minimum-size, sizing, reserve, and quality rules.
- `ADD` only when the buy crossing survives on the incremental order, the 10pp soft preference or a documented exception applies, the post-cost edge retains at least 70% of raw edge, and the half-Kelly target exceeds current cost.
- `SELL` when the complete executable sell crossing holds.
- `HOLD` otherwise. If the recalculated target shrinks without a sell crossing, hold rather than performing an ordinary rebalance reduction.

Do not sell an un-crossed losing position to replace it with a better candidate.

## Soft 10pp entry preference and 30pp strong signals

For `BUY` and `ADD`, normally require:

```text
raw_edge = v - best_ask
raw_edge > 0.10
```

This is a soft preference, not a hard execution constraint. An Agent may recommend an entry at `raw_edge <= 0.10` only when the full research supports a specific exception, such as unusually strong primary evidence, unusually short capital lock-up, exceptionally low execution drag, or material independent portfolio value. Record the exception reason explicitly in the trade-math input and private report. Do not use an exception merely to fill a position count or deploy idle cash.

The exception never relaxes the hard requirements that the minimum executable order has positive post-fee and post-slippage edge, retains at least 70% of raw edge, satisfies minimum order size, and receives a positive shared portfolio allocation. The 10pp preference applies only to buying or adding; it does not delay a sell crossing.

Label `raw_edge > 0.30` as a strong signal in private guidance and the public brief. This is presentational only and changes no execution rule.

## Market opportunity versus account action

Calculate an entry opportunity at the minimum executable order independently of account cash. Record whether its post-fee and post-slippage edge retains at least 70% of raw edge.

Then calculate the account action with the real bankroll and available cash:

- An entry opportunity may be `entry_eligible: true` while the account action remains `HOLD` with zero allocation.
- Emit account `BUY` or `ADD` only after `portfolio_allocate.py` assigns a positive executable amount.
- Never emit `BUY` or `ADD` with zero recommended cash. Preserve the standalone opportunity in `opportunity`, not in the account action.
- Never present a nominal, illustrative, or theoretical bankroll as an account recommendation.
- Keep public market commentary separate from private executable instructions.
- Pass `soft_minimum_raw_edge: 0.10` to `trade_math.py`. For a below-threshold exception, also pass `soft_edge_override: true` and a non-empty Chinese `soft_edge_override_reason`; otherwise the script returns no BUY or ADD allocation.

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
public pUSD balance from the dynamically discovered active collateral contract
+ current holdings valued at complete executable bid VWAP after sell fees
```

If a verified authenticated available-to-trade value is lower than the public pUSD balance, use the lower value as cash. Never substitute a fixed legacy token balance.

Report model value separately. Do not use it to inflate the sizing bankroll.

The standalone incremental position cap is bounded by:

```text
min(
  incremental half-Kelly target,
  order-book capacity retaining at least 70% of raw edge after fees and slippage,
  cash available after the opportunity reserve
)
```

Respect token minimum order size. If the computed position is smaller, do not place or recommend the order.

After calculating standalone caps for all retained opportunities, send them together in one `portfolio_allocate.py` request. Its sequential allocation, not independently repeated per-market cash budgets, determines tentative BUY and ADD amounts. A failed post-allocation portfolio review may revise the shared input and rerun it; retain only the accepted result.

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
