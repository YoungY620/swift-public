# Output contract

## Immutable layout

```text
<output-root>/
└── presentations/
    └── <completed-at>_<report-run-id>/
        ├── manifest.json
        ├── private-brief.md
        ├── research-report.md
        ├── public-signals.json
        └── public-posts.md
```

Never overwrite an existing directory. A repeated invocation with the same input hash returns the existing directory. A reused run ID with a different hash fails.

Keep every archived presentation permanently. Recent-report loading belongs to `forecast-edge`; this Skill reads only the current report.

## File classification

Private files:

- `manifest.json`
- `private-brief.md`
- `research-report.md`

Public files:

- `public-signals.json`
- `public-posts.md`

Use directory mode `0700`, private file mode `0600`, and public file mode `0644`. A publisher may copy only the two public files, never the whole directory.

## Content

The private brief includes every current holding and every BUY, ADD, TRIM, or SELL candidate. It omits unheld `MAINTAIN` candidates and full evidence trees.

The research report includes every current holding, every deeply researched candidate, resolution review, probability tree, evidence, execution calculation, portfolio conclusion, and discovery audit.

The public JSON contains only BUY, ADD, and SELL. It preserves the action enum but excludes all position and recommendation sizes.

The public post renderer maps BUY and ADD to buy markers and SELL to sell markers:

```text
➕✅ 买 YES
➕❌ 买 NO
➖✅ 卖 YES
➖❌ 卖 NO
```

Generate one post per market and enforce at most 400 Unicode characters. If there is no public action, write `今日无新增交易信号`.
