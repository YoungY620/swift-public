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

## Resolution precedent

Before finalizing a market whose criteria leave material interpretation room (de facto versus official status, time-window edges, qualifying-actor boundaries) or that belongs to a recurring series, research settlement precedent:

1. Past resolutions of the same market series, especially edge-case outcomes.
2. UMA Optimistic Oracle proposals and dispute votes on this market or similar wording (vote.uma.xyz, on-chain OO records).
3. Official platform clarifications and market-comment announcements.

UMA dispute history skews toward literal criteria readings, but "spirit of the market" reversals exist; model material interpretation risk as scenario branches in the tree, never as a silent assumption. Record precedent as `RESOLUTION` evidence referenced by `resolution_review`.

## Market-divergence diagnosis

For any candidate entering `BUY`/`ADD` evaluation, explain why the executable price deviates from the model probability before trusting the edge:

1. Identify the mechanism moving the price: surface reading of criteria, narrative momentum, liquidity neglect, favorite-longshot bias, stale information, or settlement-interpretation premium.
2. Classify it: probability-neutral noise (does not touch the tree), missing-information risk (the market may know something; re-verify the evidence before proceeding), or settlement-interpretation risk (belongs in the tree's branches).
3. A large divergence with no credible mechanism is model risk, not a signal: revisit the research instead of sizing up.

Record the diagnosis in the model conclusion.

## Public-forecast cross-check

After completing the independent estimate, cross-check it against public forecasting aggregates when the same or a near-equivalent question exists there (multi-model consensus on Prophet Arena, Metaculus community prediction, Manifold). Complete the tree first; never anchor on or copy an aggregate.

- Divergence beyond 10pp against the aggregate requires re-verifying the evidence or documenting the concrete reason the aggregate is wrong; record the comparison in the conclusion.
- Treat aggregates as weak evidence, never as standalone probability sources: they cover different venues and may be stale, thin, or gameable.

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
