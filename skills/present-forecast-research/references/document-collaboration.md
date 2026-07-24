# Document collaboration

Use collaborative mode only for `research-report.md`. Scheduled tasks use automatic mode.

## Workflow

1. Run `prepare-draft` in a private temporary workspace.
2. Review audience, desired emphasis, outline, and length with the user.
3. Edit only narrative outside `GENERATED` markers.
4. Preserve every generated block exactly, although whole blocks may be reordered.
5. Run `finalize-draft` to verify protected blocks and archive all five artifacts.

Allowed edits include headings, transitions, explanatory framing, section ordering, and compression or expansion of facts already present in the source report.

Never change or add probabilities, prices, edges, actions, shares, cash, links, evidence IDs, tree data, execution tables, or facts absent from the report. Never remove contrary evidence needed to understand a conclusion.

If a generated block needs factual correction, stop. Request a new upstream forecast-edge report rather than editing the protected block.

Archived presentations are immutable. A later editorial revision creates a new presentation revision linked to the same source hash; it never overwrites the original.
