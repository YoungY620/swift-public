# Research and probability method

## Objective

Estimate the probability that the market will settle `YES` under its exact resolution criteria. Every probability tree uses `P(market settles YES)` as its root, regardless of whether the researched trading outcome is `YES` or `NO`. Derive a `NO` model value as `1 - P(YES)`.

## Recursive procedure

1. Copy the complete resolution criteria and identify ambiguous terms, dates, sources, cancellation clauses, and exceptional settlement conditions.
2. State the top-level event whose probability is required.
3. Partition the event into mutually exclusive and collectively exhaustive scenarios whenever possible.
4. Estimate each scenario weight and each conditional outcome probability from evidence.
5. Recursively decompose a child when available evidence cannot yet support a defensible estimate.
6. Search while modeling. When research reveals a credible empirical or theoretical framework, restructure or replace the earlier tree.
7. Stop decomposing a node when further detail would not materially improve its estimate or evidence already supports a direct estimate. Do not split merely to create depth.
8. Compute the root with `scripts/probability_tree.py`. Embed that exact tree, its canonical SHA-256, and its calculation summary in `report.json`.

Before finalizing the probability or action, independently verify the rule interpretation against the complete raw criteria:

- Check every actor, scope, date, window, exclusion, source, and conflict clause.
- Check that `such as`/`including` examples were not made exhaustive and that no threshold was invented.
- For interval rules, identify exactly what the end date modifies; never require completion by the end date when the criteria constrain only the start or latest qualifying event.
- Test an edge case for timing and for an unlisted but potentially qualifying action.
- Resolve any omission, contradiction, or self-invented certainty by rebuilding the interpretation and tree; do not issue a signal until it passes.

Use maximum depth `64` only as runaway-recursion protection. It is deliberately high and is not a target.

## One recursive tree shape

Use one recursive weighted conditional-probability tree. Every node contains `id`, `label`, `reason`, `probability`, `evidence_ids`, and `children`.

- `children: []` is a directly supported leaf estimate and must reference evidence.
- A non-leaf has at least two mutually exclusive and collectively exhaustive branches.
- Every branch contains `id`, `label`, `weight`, `weight_reason`, `evidence_ids`, and `node`.
- Branch weights sum to one.
- A non-leaf probability equals `sum(weight * child probability)`.
- Further decomposition replaces a leaf with another node using the same shape.

Do not reuse the previous report's probability as a prior and do not store `bayes_update` or `complement` node types. Rebuild the complete tree on every run. Use Decimal arithmetic with 12 fractional places and `ROUND_HALF_EVEN`; every recorded non-leaf probability must equal the recomputed value exactly at that precision.

## Information that is genuinely uninformative

Do not create a separate uncertainty variable. After exhausting reasonable searches:

- Use a relevant historical or theoretical base rate when available.
- Use `0.5` for a leaf only when available information provides no defensible direction.
- Let neutral leaves pull the final point estimate toward `0.5` through the normal tree arithmetic.
- Explain why the leaf remains neutral and which searches were attempted.

Never convert “I have not searched enough” into `0.5`. Continue searching first.

## New events during an existing position

Recalculate the final settlement probability rather than reacting to the market price alone.

- If a dramatic event changes short-term sentiment but not the relevant long-run causal path, keep the long-run node estimates and document why.
- If it changes a causal factor, rebuild the affected current-run branch and propagate the change to the root.
- Compare the new model value with the current executable book. A lower model probability does not itself require selling; the sell crossing rule decides.

## Portfolio correlation

Do not add portfolio correlation to this probability tree. A shared external event may naturally appear in separate markets' causal reasoning, but keep each market tree self-contained and auditable. Consider cross-market correlation qualitatively only during final candidate selection and sizing review.
