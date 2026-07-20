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

Write the final `report.md` and all private operation guidance in Simplified Chinese. For schema version `2.0`, the validator requires Chinese narrative content in the portfolio summary, report summary, model conclusions, signal rationales, action rationales, and evidence summaries, conclusions, and credibility assessments.

Keep JSON keys, signal enums, outcome enums, IDs, URLs, exact market questions, source names, publication titles, and necessary verbatim quotations in their original form. Explain any retained foreign-language material in Chinese. Render signal and outcome enums with Chinese labels while preserving their machine values, such as `买入（BUY）` and `否（NO）`.

## Required run sections

- Run identity, timestamps, account input, resolved address, data cutoff, and previous report IDs.
- Effective available pUSD cash, conservative bankroll, and reserve cash. Preserve the discovered asset symbol, token address, and raw public balance in the unchanged account snapshot; record any lower authenticated available-to-trade cap separately with its runtime provenance.
- Holdings liquidation value, deployable cash, allocated cash, remaining cash, cold-start state, and new-position limit.
- Every current position with complete new research.
- Fully researched new candidates, including rejected and held candidates when useful for the opportunity baseline.
- A consolidated list of `BUY`, `ADD`, `HOLD`, and `SELL` actions.
- A concise Simplified-Chinese portfolio-level summary.

## Market research fields

For current positions and candidates, include:

- Market ID, question, URL, researched outcome, exact resolution criteria, and end date.
- Point estimate, held-outcome model value, causal-tree input and calculation result, and conclusion.
- Book timestamp, best prices, fee rate, VWAP, raw and actual edge, edge retention, and depth capacity.
- Half-Kelly result, reserve constraint, suggested shares/cash, and daily expected return.
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

Do not create `INCOMPLETE`, `UNKNOWN`, or `REVIEW_REQUIRED` actions. Continue research, use defensible base rates or neutral leaves, and finish the model. A mechanical inability to retrieve an executable book produces `HOLD` for a current position or exclusion of a candidate, together with an explicit data-failure note.

## Portfolio consistency

Use report schema version `2.0` for new runs. Keep older reports readable as history. Validate all of the following before archival:

- Conservative bankroll equals available collateral plus holdings liquidation value.
- Deployable cash plus protected reserve equals available collateral.
- Allocated cash plus remaining cash equals deployable cash.
- Every sizing input uses the same bankroll and available cash as the portfolio summary.
- Every BUY or ADD amount equals its portfolio allocation.
- All BUY and ADD amounts combined equal portfolio allocated cash and never exceed deployable cash.
- Zero available cash implies zero funded BUY and ADD actions.
- A zero-cash entry opportunity must use final account action `HOLD`; zero-amount `BUY` and `ADD` are invalid.
- Cold-start funded BUY count does not exceed the configured limit.
- Market opportunities without funding remain distinct from account actions.

Reject inconsistent or non-Chinese narrative reports. Do not archive them with explanatory labels such as “theoretical” or “nominal.” Include a concise private Simplified-Chinese portfolio-diversification conclusion, but no formal risk-cluster structure.
