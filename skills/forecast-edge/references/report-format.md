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

## Required run sections

- Run identity, timestamps, account input, resolved address, data cutoff, and previous report IDs.
- Available collateral, conservative bankroll, and reserve cash.
- Every current position with complete new research.
- Fully researched new candidates, including rejected and held candidates when useful for the opportunity baseline.
- A consolidated list of `BUY`, `ADD`, `HOLD`, and `SELL` actions.
- A concise portfolio-level summary.

## Market research fields

For current positions and candidates, include:

- Market ID, question, URL, researched outcome, exact resolution criteria, and end date.
- Point estimate, held-outcome model value, causal-tree input and calculation result, and conclusion.
- Book timestamp, best prices, fee rate, VWAP, raw and actual edge, edge retention, and depth capacity.
- Half-Kelly result, reserve constraint, suggested shares/cash, and daily expected return.
- Signal and rationale.
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
