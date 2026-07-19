# Public Polymarket data

## Scope and limitations

Use `scripts/polymarket_data.py` for normalized, public, read-only access. It intentionally cannot authenticate, sign, approve collateral, or place orders.

Publicly visible on-chain collateral is an estimate of available funds. It does not prove CLOB allowance, account health, or that an authenticated order would succeed. Label it accordingly.

## Account inputs

Accept:

- A `0x` proxy-wallet address.
- A profile URL whose slug embeds an address.
- A public profile URL or handle such as `https://polymarket.com/@public-user` or `@public-user`.

Username resolution reads public profile-page data and extracts the repeated proxy wallet. Preserve both the original account input and resolved address in snapshots and reports.

## CLI examples

```bash
# Resolve only
python3 scripts/polymarket_data.py --pretty resolve '@public-user'

# Profile and complete public positions
python3 scripts/polymarket_data.py --pretty account '@public-user'
python3 scripts/polymarket_data.py --pretty profile '@public-user'
python3 scripts/polymarket_data.py --pretty positions '@public-user'

# Public Polygon collateral balance
python3 scripts/polymarket_data.py --pretty balance '@public-user'

# Active candidate feed and one market
python3 scripts/polymarket_data.py --pretty discover --limit 100
python3 scripts/polymarket_data.py --pretty market 540817

# Executable token book plus current fee-rate response
python3 scripts/polymarket_data.py --pretty book TOKEN_ID
```

The book command preserves the raw fee response and also returns `fee_rate_bps` plus decimal `fee_rate`. Pass the decimal field to `trade_math.py`.

The discover command supplies markets for Agent screening; it does not decide topic, quality, causal analyzability, or settlement horizon.

Use the `account` command once per scan as the canonical identity, position, and collateral snapshot. Do not assemble sizing inputs from different profile resolutions or fetch times. Verify that every returned position uses the resolved proxy wallet. Compute holdings liquidation value separately from complete live bid books; do not use the API's displayed `currentValue` as the sizing value.

If the resolved account differs from the latest report, treat it as an account change and require explicit runtime intent. If the first snapshot returns both zero positions and zero collateral, continue market research but produce no funded BUY or ADD action and prominently identify the resolved public profile in the private report.

## Data sources

- Gamma API: public profile and market metadata, including description and resolution criteria.
- Data API: public positions.
- CLOB API: token order books and fee rate.
- Polygon JSON-RPC: public collateral-token balance.
- Public Polymarket profile page: username-to-proxy-wallet resolution when no address is embedded.

Use retries for transient failures. The data script uses Python's standard HTTP client first and, when installed, falls back to `curl` for CDN/TLS resets commonly encountered from Linux shells. When a required response remains unavailable, try an alternative public endpoint or the public market page and record what was attempted.

## Snapshots

Snapshot the exact portfolio, market metadata, order books, fee responses, and balance values used by a completed run. Market links alone cannot reproduce past executable prices.

Store snapshots inside the new immutable report directory through `report_tool.py archive --snapshot ...`. Do not place run data inside the Skill directory.
