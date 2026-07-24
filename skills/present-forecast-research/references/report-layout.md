# Research report layout

Use this fixed factual backbone:

1. Overview and source report identity.
2. This-run action table with incremental shares and cash.
3. Private portfolio summary.
4. Every current position.
5. Every deeply researched candidate, including unheld `MAINTAIN` candidates.
6. Candidate-discovery audit.

For every market show:

- final action and direction;
- market YES probability and selected-outcome fair value;
- executable price, raw edge, and net edge;
- resolution review;
- recursive probability tree;
- evidence table;
- this-run execution or explicit no-operation text.

## Tree visualization

Render Mermaid as the primary view and a plain-text tree as fallback. Generate display paths (`1`, `1.1`, `1.1.1`) from array order; never read or write a path number in JSON.

Show node label, probability, and evidence IDs. Show branch label, weight, and weight evidence on edges. When a tree has more than 24 nodes, render one top-level overview followed by one full diagram for each top-level branch.

Escape Mermaid labels. Keep evidence IDs searchable and place the evidence table after the tree.
