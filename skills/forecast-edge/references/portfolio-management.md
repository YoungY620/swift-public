# Portfolio management

Apply portfolio management throughout a scan. Use qualitative semantic reasoning for relationships and deterministic scripts for cash arithmetic. Do not create risk-cluster entities, identifiers, matrices, graphs, or persistent relationship state.

## Establish the real portfolio

Use one canonical account snapshot. Calculate:

- Public pUSD balance from the dynamically discovered active collateral contract.
- Every current position and its complete bid-VWAP liquidation value after fees.
- Total holdings liquidation value.
- Conservative bankroll as cash plus holdings liquidation value.
- Each position's share of conservative bankroll.
- Protected opportunity reserve and remaining deployable cash.

Never use model value, a displayed last price, a legacy token balance, a nominal bankroll, or an illustrative account size as real buying power. When a verified authenticated available-to-trade value is supplied, cap cash at the lower of that value and the public pUSD balance. When cash is zero, BUY and ADD allocations must be zero. Continue identifying market opportunities without presenting them as executable account actions.

## Pass 1: build the research queue

Research every current position; never screen one out. Screen new opportunities at the event level first so sibling deadlines, thresholds, and outcomes do not consume independent candidate slots. Select about 100 events matching the domain and AI-advantage preferences, expand their child markets, then inspect contract metadata, resolution criteria, horizon, liquidity, and semantic relationship to the current portfolio before expensive research.

Build a varied research queue. Ask whether a candidate:

- Repeats the same real-world thesis, actor, institution, or causal driver.
- Could lose with current positions under one shared scenario.
- Is a genuine hedge rather than a similarly worded contract.
- Offers a better instrument, deadline, liquidity profile, or resolution criterion for an existing thesis.
- Adds an independent source of expected return.
- Deserves scarce research capacity based on plausible AI advantage.

Use this as a soft research-priority rule. Do not reject every related market before learning whether it is a hedge, a superior instrument, or an unusually mispriced opportunity. Compare sibling contracts inside a selected event before ranking them, and do not let the deep-research queue fill with superficial variants of one event.

## Pass 2: select researched opportunities

After calculating evidence-backed probabilities, executable prices, fees, slippage, and time-adjusted quality, judge each opportunity by its marginal effect on the full portfolio rather than its standalone edge alone.

Consider:

- Net probability edge and evidence quality.
- Settlement horizon and capital lock-up.
- Depth, minimum size, and exit execution.
- Existing exposure to the same causal driver.
- Direction of relationship: common loss, common gain, conditional hedge, or superficial similarity.
- Dependence on the same evidence source or modeling assumption.
- Whether a slightly lower-edge but independent opportunity improves portfolio quality.

Remove, demote, or cap redundant candidates before allocation. Prefer the strongest instrument for a repeated thesis unless another contract supplies a meaningful hedge or independently valuable payoff. Treat related positions as less diversification, but do not automatically assume that shared subject matter means positive correlation.

Semantic concentration alone never creates a SELL. Continue to apply the pure sell crossing. When existing positions are concentrated but un-crossed, avoid further ADD or related BUY, favor independent opportunities, or retain cash.

## Allocate actual cash

Rank retained BUY and ADD opportunities only after the second pass. Submit the whole set in one shared `portfolio_allocate.py` request, never one request per market. Supply real cash, holdings liquidation value, reference positions, cold-start state, and per-market Kelly, order-book, and optional portfolio caps.

Let higher-quality opportunities receive larger allocations, while enforcing:

- Protected opportunity reserve.
- Sequential reduction of remaining cash.
- Cold-start new-position limit.
- Minimum executable order size.
- Per-market half-Kelly and order-book caps.
- Zero allocation when real cash is unavailable.

## Pass 3: review the tentative portfolio

After allocation, review the resulting portfolio again. Check total funding, remaining reserve, largest exposures, shared-loss scenarios, settlement-date concentration, liquidity, and whether the new positions truly improve effective diversification. Change eligibility, priority, or an optional portfolio cash cap and rerun the full shared allocation when the result is not acceptable. Discard every rejected tentative output and report only the final accepted one.

Store only a concise natural-language conclusion in the private report, including material concentration and how it changed selection or allocation. Do not store a formal relationship graph. Omit this private assessment from public social-media output.
