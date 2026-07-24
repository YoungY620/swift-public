# Public redaction

Build public output from an allowlist. Never copy the source report and delete known private fields afterward.

## Allowed

- BUY, ADD, or SELL
- market ID, question, outcome, verified event URL
- market YES probability and selected-outcome fair price
- current executable price, raw edge, net edge
- concise public reason
- resolution criteria, YES/NO conditions, deadline, and source
- public evidence ID, HTTPS URL, title, summary, and probability impact
- recursive probability tree and evidence IDs

## Forbidden

- account input, username, address, wallet, or profile URL
- collateral, bankroll, reserve, remaining cash, or total assets
- position shares, cost, value, or profit and loss
- recommended shares or cash
- allocation priorities, Kelly cash, or portfolio caps
- previous-report account comparisons
- local paths, emails, environment details, stack traces, or internal error text
- `MAINTAIN`
- confidence labels

Use `public-signals.schema.json` version `2.0` with `additionalProperties: false`. Reject unknown versions. Scan serialized output for wallet addresses, emails, local paths, and Polymarket profile links before writing public files.
