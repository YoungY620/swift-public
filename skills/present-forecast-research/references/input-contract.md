# Input contract

## Accepted input

Accept exactly one of:

- an explicit file named `report.json`;
- one immutable run directory containing `report.json` at its root.

Do not search an output root, choose a latest report, parse `report.md`, or accept chat text. Treat the source JSON as read-only.

## Required producer contract

Accept only `schema_version: "3.0"`. The vendored [report.schema.json](report.schema.json) must be byte-identical to the canonical copy owned by `forecast-edge`.

Every analysis contains a verified Polymarket event URL, `PASS` resolution review, market-YES recursive tree, evidence records, real or empty position, opportunity, allocation, and final signal. Every analyzed market has exactly one top-level action.

## Independent validation

Recompute rather than trust stored summaries:

- use 12-place Decimal and `ROUND_HALF_EVEN`;
- require branch weights to sum to one;
- require every parent to equal `sum(weight * child)`;
- require the root to equal `yes_probability`;
- require `NO model_value = 1 - yes_probability`;
- require calculation hash, node count, and depth to match the embedded tree;
- require probability evidence to be referenced by the tree;
- require resolution evidence to be referenced by the resolution review;
- require fill levels to reproduce every execution number.

`MAINTAIN` uses zero shares, zero cash, and null execution. BUY/ADD use all-in payment cash; SELL uses post-fee receipt cash. All values are current-run deltas.

## Failure

Reject the complete run on any error. A failed generation writes only private `manifest.json` and `errors.json`; it produces no public file. Do not repair the input. A corrected upstream result must use a new report run ID.
