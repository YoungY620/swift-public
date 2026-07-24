---
name: present-forecast-research
description: Validate and present one completed forecast-edge report as private operation guidance, a full Chinese research document, privacy-safe public signal JSON, and short public posts. Use when an Agent must consume an explicit schema-3.0 report.json or its single run directory, independently verify its probability trees, evidence links, resolution reviews, execution fills, and portfolio actions, generate immutable per-run presentation artifacts, or collaboratively refine the long research document without performing new research or changing any result.
---

# Present Forecast Research

Turn one completed `forecast-edge` report into verified private and public presentations. Treat the input as read-only research truth. Never search, revise probabilities, change actions, recalculate sizing from new assumptions, send messages, commit repositories, or publish pages.

## Preserve these invariants

- Accept only one explicitly supplied `report.json` or one run directory containing exactly that file.
- Accept only report schema `3.0`. Reject older reports instead of guessing missing fields.
- Read no previous report. Historical comparisons must already exist in the current JSON.
- Independently recompute every 12-place Decimal probability tree and execution fill.
- Fail the entire presentation on any contradiction. Never publish a valid subset.
- Keep all probabilities rooted at market `YES`; derive the selected `NO` value mechanically.
- Treat action shares and cash as this-run deltas, never target totals.
- Exclude confidence labels.
- Generate Chinese presentation prose while preserving IDs, enums, URLs, market questions, source titles, and required quotations.
- Keep permanent immutable output directories. Never overwrite an archived presentation.
- Generate files only. Delivery and deployment belong to the caller.

Read [input-contract.md](references/input-contract.md) before validating any report. Read [output-contract.md](references/output-contract.md) before generating artifacts. Read [public-redaction.md](references/public-redaction.md) whenever producing or reviewing public output.

## Choose a workflow

Use automatic mode for scheduled tasks and normal one-shot presentation:

```bash
python3 scripts/present_report.py generate /path/to/report.json \
  --output-root "$HOME/.forecast-edge-runs"
```

Use collaborative mode only when the user asks to coauthor `research-report.md`. Read [document-collaboration.md](references/document-collaboration.md) first:

```bash
python3 scripts/present_report.py prepare-draft /path/to/report.json \
  --workspace /private/temporary/workspace

python3 scripts/present_report.py finalize-draft /path/to/report.json \
  --draft /private/temporary/workspace/research-report.md \
  --output-root "$HOME/.forecast-edge-runs"
```

The finalizer rejects modified generated blocks. Edit only surrounding narrative and document structure.

## Validate before presenting

Run the independent consumer validation even when `forecast-edge` already validated the report:

```bash
python3 scripts/present_report.py validate /path/to/report.json
```

Validation covers:

1. v3 top-level and portfolio identities.
2. `PASS` resolution reviews and their evidence IDs.
3. Recursive weighted probability trees, tree hashes, YES probabilities, and selected-outcome model values.
4. Required and non-dangling probability evidence.
5. One final action per analyzed market.
6. Incremental shares, all-in payments or net receipts, fill totals, fees, VWAP, effective prices, slippage, and net edge.
7. The hard `raw_edge > 0.10` funded-entry boundary.
8. Public allowlists and sensitive-data rejection.

Do not turn a validation error into `MAINTAIN`, a warning-only report, or an edited estimate. Return the error artifact and require a new upstream report run.

## Review generated outputs

Every successful run produces one immutable directory with:

```text
manifest.json
private-brief.md
research-report.md
public-signals.json
public-posts.md
```

Read [report-layout.md](references/report-layout.md) when reviewing the long document or tree rendering. Confirm that:

- every current holding appears in the private brief;
- every actionable candidate appears in private and public outputs;
- unheld `MAINTAIN` candidates appear only in the full research report;
- Mermaid and plain-text trees contain the same nodes and values;
- public posts use `➕✅`, `➕❌`, `➖✅`, or `➖❌` and remain within 400 Unicode characters;
- public files contain no account, funding, position-size, recommendation-size, local-path, email, or profile data.

## Use tests as executable documentation

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/test_cli.sh
```

The fixtures are synthetic. Never store a real account or personal path in this Skill.
