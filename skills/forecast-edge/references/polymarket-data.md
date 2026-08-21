# Public Polymarket data

## Scope and limitations

Use the registered dispatcher, `scripts/market_data.py --adapter polymarket`, for all research access. The adapter delegates to the venue-native helper internally; research must not call that helper directly. Both layers are public and read-only and cannot authenticate, sign, approve collateral, or place orders.

Polymarket currently uses `pUSD` as its trading collateral. Resolve the active `pUSD` contract and decimals from the public collateral-assets endpoint on every scan, then read that ERC-20 balance on Polygon. Do not use a fixed legacy stablecoin contract as the account cash source.

The public pUSD balance is the best public cash estimate, but private open orders or authenticated account constraints can make the website's available-to-trade value lower. If the user supplies a verified authenticated value, use the lower of it and the public pUSD balance. Never increase cash above the public balance from an unauthenticated claim.

Fail closed when collateral discovery is missing, duplicated, or malformed. Do not silently fall back to another token and do not interpret that token's zero balance as a real account result.

## Account inputs

Accept:

- A `0x` proxy-wallet address.
- A profile URL whose slug embeds an address.
- A public profile URL or handle such as `https://polymarket.com/@public-user` or `@public-user`.

Username resolution reads public profile-page data and extracts the repeated proxy wallet. Preserve both the original account input and resolved address in snapshots and reports.

## CLI examples

```bash
python3 scripts/market_data.py --adapter polymarket --pretty account '@public-user'
python3 scripts/market_data.py --adapter polymarket --pretty candidate-events --account-snapshot account.json
python3 scripts/market_data.py --adapter polymarket --pretty event 12345
python3 scripts/market_data.py --adapter polymarket --pretty market 540817
python3 scripts/market_data.py --adapter polymarket --pretty book TOKEN_ID
python3 scripts/market_data.py --adapter polymarket canonical-url CONDITION_ID
```

The native book response preserves the venue fee metadata. The registered adapter applies the venue formula and adds `fee_per_unit` to every normalized bid and ask level. Pass those normalized levels to `trade_math.py`; shared sizing code never applies the venue formula itself.

The `candidate-events` command uses Gamma's `/events/keyset` endpoint and follows each opaque `next_cursor` as `after_cursor` until the active event feed is exhausted. Event pagination avoids the numeric-offset ceiling on `/markets` and prevents a single event with many contracts from consuming the prescreen target. Defaults are 100 events per page and a 100,000-event safety limit. The command normally stops when no next cursor remains; it does not mechanically request 1,000 pages. It fails rather than silently truncating when the API repeats a page or cursor, or when the event universe exceeds the safety limit. Increase the safety limit only after checking pagination behavior.

Supply the unchanged canonical `account` JSON through `--account-snapshot`. The command deterministically removes:

- Events containing a current position, matched by event identity or any child market's condition, token, market ID, or market slug.
- Sports and esports events identifiable from structured category/tag metadata or explicit competition names.
- Duplicate or identity-less events, and events without at least one active, order-accepting child market with valid outcome tokens.

Its `candidates` array is the complete compact event sequence remaining after deterministic exclusions, not the final research queue. Continue Agent semantic screening until about 100 events pass topic priority, causal analyzability, public-evidence availability, horizon, and portfolio checks, or until it is exhausted. Use the `event` command to retrieve all child markets only for those selected events, rank the resulting contracts into the descending raw-edge tiers, and research them per the attempt thresholds and the novel-event research quota in [market-selection.md](market-selection.md). Snapshot the candidate-event output and selected-event expansions so the report can prove whether enumeration completed and how many events each deterministic filter removed.

Use the `account` command once per scan as the canonical identity, position, collateral-metadata, and pUSD-balance snapshot. Do not assemble sizing inputs from different profile resolutions or fetch times. Verify that every returned position uses the resolved proxy wallet and that `collateral.asset` is `pUSD` with `collateral_source: public_collateral_assets_api`. Compute holdings liquidation value separately from complete live bid books; do not use the API's displayed `currentValue` as the sizing value.

Treat Gamma `/public-profile` metadata as optional display data. Some valid proxy wallets return `profile not found`; preserve that error in `profile`, but continue when profile-page identity resolution, complete positions, active collateral discovery, and on-chain pUSD balance succeed. Never reinterpret an optional profile lookup failure as a zero balance or an invalid account.

If the resolved account differs from the latest report, treat it as an account change and require explicit runtime intent. If a successfully discovered pUSD snapshot returns both zero positions and zero collateral, continue market research but produce no funded BUY or ADD action and prominently identify the resolved public profile in the private report. A collateral-discovery failure is a mechanical data error, not a zero balance.

An explicit `--token-address` override exists only on the standalone `balance` diagnostic command. The canonical `account` command does not accept it. Never persist a diagnostic token as a new default without verifying current official collateral metadata and updating tests.

## Data sources

- Gamma API: public profile and market metadata, including description and resolution criteria.
- Data API: public positions.
- CLOB API: token order books and fee rate.
- Public collateral-assets API: active pUSD address and decimals.
- Polygon JSON-RPC: public pUSD balance.
- Public Polymarket profile page: username-to-proxy-wallet resolution when no address is embedded.

Use retries for transient failures. The data script uses Python's standard HTTP client first and, when installed, falls back to `curl` for CDN/TLS resets commonly encountered from Linux shells. When a required response remains unavailable, try an alternative public endpoint or the public market page and record what was attempted.

For each current position, resolve market metadata before requesting its order book. Prefer the position's `market_slug` with `market`, then verify that the normalized market has `closed=false` and `accepting_orders=true`; only then call `book` with that position's `instrument_identifier`. Never pass an outcome token to `market`, and never pass a condition id or market slug to `book`. A closed market or a market that no longer accepts orders normally has no active CLOB book: record that state as a structured mechanical reason for `MAINTAIN` instead of treating the missing book as a command failure. Keep genuine network errors and malformed active-market responses as failures.

Official references: [pUSD](https://docs.polymarket.com/concepts/pusd) and [contract metadata](https://docs.polymarket.com/resources/contracts).

## Market page URLs

Polymarket serves market pages under the event slug, but research must not construct those URLs. Resolve each published link through `market_data.py --adapter polymarket canonical-url MARKET_IDENTIFIER`, then open it and verify HTTP 200 before archival and delivery. Re-resolve and recheck failures; never substitute a homepage or search-result link.

## Snapshots

Snapshot the exact portfolio, market metadata, order books, fee responses, and balance values used by a completed run. Market links alone cannot reproduce past executable prices.

Store snapshots inside the new immutable report directory through `report_tool.py archive --snapshot ...`. Do not place run data inside the Skill directory.
