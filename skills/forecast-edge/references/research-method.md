# Research and probability method

## Objective

Estimate the probability that the researched outcome will settle at `1` under the market's exact resolution criteria. Model final settlement, even when the position may be sold before settlement.

## Recursive procedure

1. Copy the complete resolution criteria and identify ambiguous terms, dates, sources, cancellation clauses, and exceptional settlement conditions.
2. State the top-level event whose probability is required.
3. Partition the event into mutually exclusive and collectively exhaustive scenarios whenever possible.
4. Estimate each scenario weight and each conditional outcome probability from evidence.
5. Recursively decompose a child when available evidence cannot yet support a defensible estimate.
6. Search while modeling. When research reveals a credible empirical or theoretical framework, restructure or replace the earlier tree.
7. Stop decomposing a node when further detail would not materially improve its estimate or evidence already supports a direct estimate. Do not split merely to create depth.
8. Compute the root with `scripts/probability_tree.py` and retain the input tree plus its audit output in the report data.

Use maximum depth `64` only as runaway-recursion protection. It is deliberately high and is not a target.

## Supported calculation nodes

- `estimate`: a directly supported point estimate.
- `partition`: a weighted total-probability decomposition whose branch weights sum to one.
- `bayes_update`: a prior updated by one or more evidence likelihood ratios.
- `complement`: one minus a child probability.

The Agent determines the structure and numbers. The script validates ranges, branch sums, duplicate identifiers, recursion depth, and arithmetic.

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
- If it changes a causal factor, update that node and propagate the change to the root.
- Compare the new model value with the current executable book. A lower model probability does not itself require selling; the sell crossing rule decides.

## Portfolio correlation

Do not add portfolio correlation to this probability tree. A shared external event may naturally appear in separate markets' causal reasoning, but keep each market tree self-contained and auditable. Consider cross-market correlation qualitatively only during final candidate selection and sizing review.
