# Report format and history

## Immutable run layout

Write runtime artifacts outside the Skill directory:

```text
<output-root>/
└── reports/
    └── <completed-at>_<run-id>/
        ├── report.json
        ├── report.md
        └── snapshots/
            ├── portfolio.json
            ├── markets.json
            └── orderbooks.json
```

Generate a unique run ID. Use UTC ISO-8601 timestamps. Never overwrite or edit an existing run directory. Every completed report remains permanently available.

Read only the latest two reports for recent-quality comparison, previous-evidence comparison, and reference-position sizing. Older reports remain archived but do not need to be loaded into the Agent context.

## Canonical representation

Treat `report.json` as canonical. Validate it against the required contract, then render `report.md` deterministically with `scripts/report_tool.py`. Do not independently author two reports whose fields can diverge.

The JSON Schema is [report.schema.json](report.schema.json). The script also performs dependency-free semantic validation, including evidence and action checks.

## Output language

Write the final `report.md` and all private operation guidance in Simplified Chinese. For schema version `3.0`, the validator requires Chinese narrative content in the portfolio summary, discovery summary, resolution review, model conclusions, signal rationales, action rationales, public reasons, and evidence summaries, conclusions, and credibility assessments.

Keep JSON keys, signal enums, outcome enums, IDs, URLs, exact market questions, source names, publication titles, and necessary verbatim quotations in their original form. Explain any retained foreign-language material in Chinese. Render signal and outcome enums with Chinese labels while preserving their machine values, such as `买入（BUY）` and `否（NO）`.

## Required run sections

- Run identity, timestamps, selected adapter, account input, resolved account identifier, data cutoff, and previous report IDs.
- Effective available cash in the adapter-reported currency, conservative bankroll, and reserve cash. Preserve the currency, native collateral/account provenance, and raw public balance in the immutable normalized account snapshot; record any lower authenticated available-to-trade cap separately with its runtime provenance.
- Holdings liquidation value, deployable cash, allocated cash, remaining cash, cold-start state, and new-position limit.
- Candidate-discovery audit: whether event keyset pagination completed, fetched/excluded event counts by reason, the number of semantically qualified events, the number of selected events expanded into child markets, deep-research attempt counts for the 30pp, 20pp, and greater-than-10pp tiers, each tier-relaxation reason, and whether the operation needs were satisfied. Keep the exact candidate-event and selected-event JSON as immutable snapshots.
- Every current position with complete new research.
- Fully researched new candidates, including rejected and unfunded `MAINTAIN` candidates when useful for the opportunity baseline.
- A consolidated list of `BUY`, `ADD`, `MAINTAIN`, `TRIM`, and `SELL` actions. Interpret `MAINTAIN` using the account snapshot: it means retaining current shares for an existing position and remaining uninvested for an unheld market.
- A concise Simplified-Chinese portfolio-level summary.

## Market research fields

For current positions and candidates, include:

- Research market ID, adapter-native parent `event_identifier`, concrete
  `multi_market_identifier`, directly tradable `instrument_identifier`, question, verified
  market URL, researched outcome, exact resolution criteria, and end date. The market and
  instrument identifiers must describe the same outcome on the report's
  `run.account.adapter`.
- A `PASS` resolution review with explicit YES/NO conditions, deadline, source, edge cases, reason, and evidence IDs.
- Market YES point estimate, held-outcome model value, the complete recursive weighted tree, canonical tree hash, calculation result, and conclusion.
- Real position shares and cost for current holdings; zero position fields for candidates.
- Book timestamp, best price, fee rate, VWAP, raw and actual edge, edge retention, and depth capacity.
- Whether raw edge is strictly above the hard 10pp entry floor and which 30pp, 20pp, or greater-than-10pp search tier produced the candidate. Never report a below-threshold entry opportunity as eligible. Keep the legacy `soft_edge_override_reason` machine field null.
- Fractional-Kelly result, reserve constraint, incremental suggested shares/cash, and daily expected return. Never encode a target total as the action delta.
- Signal and rationale.
- Standalone market opportunity, final account action, and portfolio allocation audit.
- Complete key-evidence records defined in `evidence-policy.md`.

Candidates require the same evidence fields as current positions.

## Current-position comparison

Every current position includes `comparison`. Use `null` only when no previous report exists. Otherwise record previous and current probability, executable price, action, and concise reasons for change.

Every current-position evidence item includes `new_since_previous`. When true, include:

- The causal factor affected.
- Its previous and current probability or an equivalent explicit change.
- The resulting change in final settlement probability.

An immediate crossing signal never waits for the comparison or a second confirming run.

## No incomplete signal

Do not create `INCOMPLETE`, `UNKNOWN`, or `REVIEW_REQUIRED` actions. Continue research, use defensible base rates or neutral leaves, and finish the model. A mechanical inability to retrieve an executable book produces `MAINTAIN` for a current position or exclusion of a candidate, together with an explicit data-failure note.

## Portfolio consistency

Use report schema version `3.0` for new runs. Older versions may remain readable as archived history, but downstream presentation rejects them. Validate all of the following before archival:

- Conservative bankroll equals available collateral plus holdings liquidation value.
- Deployable cash plus protected reserve equals available collateral.
- Allocated cash plus remaining cash equals deployable cash.
- Every sizing input uses the same bankroll and available cash as the portfolio summary.
- Every BUY or ADD amount equals its portfolio allocation.
- All BUY and ADD amounts combined equal portfolio allocated cash and never exceed deployable cash.
- Zero available cash implies zero funded BUY and ADD actions.
- A zero-cash entry opportunity must use final account action `MAINTAIN`; zero-amount `BUY` and `ADD` are invalid.
- Cold-start funded BUY count does not exceed the configured limit.
- Market opportunities without funding remain distinct from account actions.
- Every analysis includes a valid probability tree and calculation hash.
- The tree root equals `yes_probability`; `NO` model value equals `1 - yes_probability`.
- Every action carries both incremental shares and incremental cash. `MAINTAIN` carries zero deltas and null execution.
- BUY/ADD cash is the all-in payment; SELL and TRIM cash is the post-fee receipt.
- Embedded fill levels reproduce shares, gross cash, fee, VWAP, effective unit price, slippage, and net edge at 12-decimal precision.

Reject inconsistent or non-Chinese narrative reports. Do not archive them with explanatory labels such as “theoretical” or “nominal.” Include a concise private Simplified-Chinese portfolio-diversification conclusion, but no formal risk-cluster structure.

Before archival and delivery, resolve every published market URL through the selected adapter, verify that it opens, and confirm its native market identifier and resolution criteria match the researched market. A `PASS` review note must be market-specific; repeated boilerplate is invalid.
