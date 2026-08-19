# Prediction-market adapter contract

Forecast Edge research uses a venue-neutral core. All venue API calls go through the
registered adapter dispatcher; the core must not construct venue URLs, interpret native
identifier formats, or read native account payload keys.

## Selection

`config/adapters.json` is the trusted registry. An enabled venue must have a registered
adapter, a venue-specific `reference`, an identifier contract, tests, and read-only commands
for the data needed by the research process.

```bash
python3 scripts/market_data.py adapters
python3 scripts/market_data.py --adapter "$FORECAST_EDGE_MARKET_ADAPTER" account ACCOUNT
```

Adding a venue means adding an adapter and its venue-specific reference document. It must
not add venue branches to the workflow, shared trade-intent schema, or core research
instructions.

## Normalized account snapshot

`account` returns:

- `schema_version` and `adapter`;
- `account.input`, stable `account.identifier`, and optional `account.public_url`;
- `portfolio.currency`, `portfolio.available_cash`, and provenance fields;
- `positions.count` and normalized `positions.positions`.

Each position provides `market_identifier`, `instrument_identifier`, `outcome`, and
`quantity`. Optional market/event IDs, question, URL, average price, and displayed value
are informational. The core and generic validator use only normalized fields.

## Normalized markets and books

`market` and the market rows returned by `event` expose:

- `adapter`, `id`, `market_identifier`, `question`, resolution fields, status, and URL;
- a stable parent `event_identifier` used only for venue-local research-history novelty;
- an `outcomes` array whose items contain `name`, `price`, and
  `instrument_identifier`.

`book INSTRUMENT_IDENTIFIER` returns normalized bids/asks and fee metadata. The adapter
owns fee interpretation, URL construction, native pagination, and identifier mapping.

`canonical-url MARKET_IDENTIFIER_OR_ID` returns an adapter-produced HTTPS URL. Delivery
steps still request the final URL and require HTTP 200 before publishing it.

## Trade-intent boundary

Trade-intent JSONL is deliberately venue-specific even though the research method is not.
Every line identifies one exact routing target with:

```text
(venue, multi_market_identifier, instrument_identifier)
```

The shared validator checks the common envelope, then applies identifier patterns from the
trusted adapter registry and validates sells against that venue's normalized snapshot.
Unknown or disabled venues fail closed. No adapter used by the research workflow may
authenticate, sign, or submit an order.
