#!/usr/bin/env python3
"""Fetch and normalize public, read-only Polymarket data.

The CLI prints exactly one JSON value to stdout. Diagnostics go to stderr.
It intentionally contains no credentials, authentication, signing, or order methods.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
DEFAULT_GAMMA = "https://gamma-api.polymarket.com"
DEFAULT_DATA = "https://data-api.polymarket.com"
DEFAULT_CLOB = "https://clob.polymarket.com"
DEFAULT_SITE = "https://polymarket.com"
DEFAULT_ASSETS = "https://api.perpetuals.polymarket.com"
DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"
DEFAULT_COLLATERAL_SYMBOL = "pUSD"
DEFAULT_EVENT_PAGE_SIZE = 100
DEFAULT_EVENT_SAFETY_LIMIT = 100_000

SPORTS_METADATA_TERMS = {
    "sport",
    "sports",
    "esport",
    "esports",
    "e-sports",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "wnba",
    "ufc",
    "mma",
    "fifa",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "hockey",
    "tennis",
    "golf",
    "cricket",
    "formula 1",
    "formula-one",
    "motogp",
}
SPORTS_TEXT_RE = re.compile(
    r"\b(?:nba|nfl|mlb|nhl|wnba|ufc|mma|fifa|esports?|e-sports|"
    r"premier league|champions league|super bowl|world cup|formula ?1|motogp|"
    r"league of legends|counter[- ]strike|dota ?2|valorant)\b",
    re.IGNORECASE,
)


class DataError(RuntimeError):
    """Raised when public data cannot be fetched or interpreted."""


class HttpClient:
    def __init__(self, timeout: float = 20.0, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries

    def request_bytes(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request_headers = {
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": "forecast-edge/1.0 (public read-only research)",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, data=data, headers=request_headers)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            if attempt + 1 < self.retries:
                time.sleep(0.25 * (2**attempt))
        # Some Polymarket/CDN edges intermittently reset Python/OpenSSL clients
        # while accepting curl. Keep urllib as the dependency-free primary path,
        # then use the standard Linux-shell client when it is available.
        curl = shutil.which("curl")
        if curl:
            command = [
                curl,
                "--ipv4",
                "--http1.1",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--max-time",
                str(max(1, int(self.timeout))),
                "--retry",
                str(max(0, self.retries - 1)),
            ]
            for name, value in request_headers.items():
                command.extend(["--header", f"{name}: {value}"])
            if data is not None:
                command.extend(["--data-binary", data.decode("utf-8")])
            command.append(url)
            try:
                completed = subprocess.run(command, check=True, capture_output=True)
                return completed.stdout
            except (OSError, subprocess.CalledProcessError) as exc:
                last_error = exc
        raise DataError(f"request failed for {url}: {last_error}")

    def get_json(self, url: str) -> Any:
        body = self.request_bytes(url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DataError(f"response from {url} was not valid JSON") from exc

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        body = self.request_bytes(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DataError(f"response from {url} was not valid JSON") from exc


def join_url(base: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    if query:
        values = {key: value for key, value in query.items() if value is not None}
        url = f"{url}?{urllib.parse.urlencode(values, doseq=True)}"
    return url


def extract_embedded_address(account: str) -> str | None:
    match = ADDRESS_RE.search(account)
    return match.group(0).lower() if match else None


def profile_slug(account: str) -> str:
    parsed = urllib.parse.urlparse(account)
    path = parsed.path if parsed.scheme and parsed.netloc else account
    path = path.strip().strip("/")
    if "/@" in path:
        path = path.rsplit("/@", 1)[1]
    elif path.startswith("@"):
        path = path[1:]
    if not path:
        raise DataError("profile input did not contain an address or username")
    return path


def extract_profile_wallet(page: bytes, username: str) -> str:
    text = html.unescape(page.decode("utf-8", "replace"))
    decoded = text.replace('\\"', '"').replace("\\u0026", "&")
    wallet_pattern = re.compile(r'"proxyWallet"\s*:\s*"(0x[a-fA-F0-9]{40})"')
    wallets = [match.lower() for match in wallet_pattern.findall(decoded)]
    if not wallets:
        raise DataError(f"could not resolve @{username} to a public proxy wallet")

    # The profile wallet is repeated in profile and position payloads, while
    # counterparties and contract addresses normally occur less often.
    return Counter(wallets).most_common(1)[0][0]


def resolve_account(account: str, client: HttpClient, site_base: str) -> dict[str, str]:
    address = extract_embedded_address(account)
    if address:
        return {"input": account, "address": address, "source": "embedded_address"}

    username = profile_slug(account)
    profile_url = join_url(site_base, f"@{urllib.parse.quote(username)}")
    page = client.request_bytes(profile_url)
    return {
        "input": account,
        "address": extract_profile_wallet(page, username),
        "source": "public_profile_page",
        "profile_url": profile_url,
        "username": username,
    }


def fetch_public_profile(address: str, client: HttpClient, gamma_base: str) -> dict[str, Any]:
    url = join_url(gamma_base, "/public-profile", {"address": address})
    try:
        profile = client.get_json(url)
    except DataError as exc:
        return {
            "available": False,
            "source": "gamma_public_profile",
            "error": str(exc),
        }
    if not isinstance(profile, dict):
        return {
            "available": False,
            "source": "gamma_public_profile",
            "error": "public-profile endpoint did not return an object",
        }
    return profile


def parse_jsonish_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def positive_int(value: int, label: str) -> int:
    if value <= 0:
        raise DataError(f"{label} must be greater than zero")
    return value


def load_json_object(path: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"could not read {label} JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DataError(f"{label} JSON must contain an object")
    return value


def normalize_collateral_asset(raw: Any, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataError("collateral asset must be an object")
    asset = str(raw.get("asset", "")).strip()
    address = str(raw.get("address", "")).strip()
    decimals_raw = raw.get("decimals")
    if not asset:
        raise DataError("collateral asset did not contain a symbol")
    if ADDRESS_RE.fullmatch(address) is None:
        raise DataError(f"collateral asset address was invalid: {address!r}")
    if isinstance(decimals_raw, bool) or not isinstance(decimals_raw, int):
        raise DataError(f"collateral asset decimals were invalid: {decimals_raw!r}")
    if decimals_raw < 0 or decimals_raw > 36:
        raise DataError(f"collateral asset decimals were invalid: {decimals_raw!r}")
    return {
        "asset": asset,
        "token_address": address.lower(),
        "decimals": decimals_raw,
        "collateral_ratio": optional_number(raw.get("collateral_ratio")),
        "withdrawal_fee": optional_number(raw.get("withdrawal_fee")),
        "source": source,
    }


def fetch_collateral_asset(
    client: HttpClient,
    assets_base: str,
    symbol: str,
) -> dict[str, Any]:
    raw = client.get_json(join_url(assets_base, "/v1/info/assets"))
    if not isinstance(raw, list):
        raise DataError("collateral-assets endpoint did not return a list")
    matches = [
        item
        for item in raw
        if isinstance(item, dict) and str(item.get("asset", "")).casefold() == symbol.casefold()
    ]
    if len(matches) != 1:
        raise DataError(f"expected exactly one active {symbol} collateral asset; found {len(matches)}")
    return normalize_collateral_asset(matches[0], "public_collateral_assets_api")


def resolve_collateral_asset(args: argparse.Namespace, client: HttpClient) -> dict[str, Any]:
    if args.token_address:
        return normalize_collateral_asset(
            {
                "asset": args.collateral_symbol,
                "address": args.token_address,
                "decimals": 6 if args.decimals is None else args.decimals,
            },
            "explicit_cli_override",
        )
    if args.decimals is not None:
        raise DataError("--decimals requires --token-address")
    return fetch_collateral_asset(client, args.assets_base, args.collateral_symbol)


def normalize_market(raw: dict[str, Any]) -> dict[str, Any]:
    outcomes = parse_jsonish_list(raw.get("outcomes"))
    prices = parse_jsonish_list(raw.get("outcomePrices"))
    tokens = parse_jsonish_list(raw.get("clobTokenIds"))
    outcome_rows = []
    for index, name in enumerate(outcomes):
        outcome_rows.append(
            {
                "name": str(name),
                "price": optional_number(prices[index]) if index < len(prices) else None,
                "token_id": str(tokens[index]) if index < len(tokens) else None,
            }
        )
    events = raw.get("events")
    parent_event = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}
    event_id = raw.get("eventId") or parent_event.get("id")
    event_slug = raw.get("eventSlug") or parent_event.get("slug")
    return {
        "id": str(raw.get("id")) if raw.get("id") is not None else None,
        "event_id": str(event_id) if event_id is not None else None,
        "event_slug": event_slug,
        "question": raw.get("question") or raw.get("title"),
        "slug": raw.get("slug"),
        "description": raw.get("description"),
        "resolution_source": raw.get("resolutionSource"),
        "condition_id": raw.get("conditionId"),
        "end_date": raw.get("endDate"),
        "active": raw.get("active"),
        "closed": raw.get("closed"),
        "accepting_orders": raw.get("acceptingOrders"),
        "fees_enabled": raw.get("feesEnabled"),
        "volume": optional_number(raw.get("volume")),
        "volume_24h": optional_number(raw.get("volume24hr")),
        "liquidity": optional_number(raw.get("liquidity")),
        "outcomes": outcome_rows,
        "url": market_url(raw),
    }


def market_url(raw: dict[str, Any]) -> str | None:
    """Build a public market page from its event slug and child-market id."""
    market_id = str(raw.get("id")) if raw.get("id") is not None else None
    event_slug = raw.get("eventSlug")
    if not event_slug:
        events = raw.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event_slug = events[0].get("slug")
    slug = event_slug or raw.get("slug")
    if not slug:
        return None
    url = f"https://polymarket.com/event/{slug}"
    return f"{url}?tid={market_id}" if event_slug and market_id and event_slug != raw.get("slug") else url


def normalized_identity_value(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    return text or None


def market_identity_keys(raw: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for prefix, field in (("id", "id"), ("condition", "conditionId"), ("slug", "slug")):
        value = normalized_identity_value(raw.get(field))
        if value:
            keys.add(f"{prefix}:{value}")
    for token in parse_jsonish_list(raw.get("clobTokenIds")):
        value = normalized_identity_value(token)
        if value:
            keys.add(f"token:{value}")
    return keys


def position_identity_keys(position: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    field_groups = {
        "id": ("marketId", "market_id"),
        "condition": ("conditionId", "condition_id"),
        "slug": ("slug", "marketSlug", "market_slug"),
        "token": ("asset", "tokenId", "token_id"),
        "event_id": ("eventId", "event_id"),
        "event_slug": ("eventSlug", "event_slug"),
    }
    for prefix, fields in field_groups.items():
        for field in fields:
            value = normalized_identity_value(position.get(field))
            if value:
                keys.add(f"{prefix}:{value}")
    return keys


def account_position_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    container = snapshot.get("positions", [])
    if isinstance(container, dict):
        container = container.get("positions", [])
    if not isinstance(container, list):
        raise DataError("account snapshot positions must contain a list")
    return [item for item in container if isinstance(item, dict)]


def event_identity_keys(raw: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for prefix, field in (("event_id", "id"), ("event_slug", "slug")):
        value = normalized_identity_value(raw.get(field))
        if value:
            keys.add(f"{prefix}:{value}")
    return keys


def event_primary_key(raw: dict[str, Any]) -> str | None:
    keys = event_identity_keys(raw)
    return sorted(keys)[0] if keys else None


def flatten_labels(value: Any) -> list[str]:
    labels: list[str] = []
    if isinstance(value, str):
        text = value.strip().casefold()
        if text:
            labels.append(text)
    elif isinstance(value, dict):
        for key in ("label", "name", "slug", "category", "subcategory"):
            labels.extend(flatten_labels(value.get(key)))
        for key in ("tags", "categories"):
            labels.extend(flatten_labels(value.get(key)))
    elif isinstance(value, list):
        for item in value:
            labels.extend(flatten_labels(item))
    return labels


def is_sports_or_esports_market(raw: dict[str, Any]) -> bool:
    metadata_labels: list[str] = []
    for field in ("category", "subcategory", "tags", "events", "series"):
        metadata_labels.extend(flatten_labels(raw.get(field)))
    for label in metadata_labels:
        words = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", label))
        if label in SPORTS_METADATA_TERMS or words.intersection(SPORTS_METADATA_TERMS):
            return True
        if SPORTS_TEXT_RE.search(label):
            return True

    title = " ".join(
        str(raw.get(field) or "")
        for field in ("question", "title", "groupItemTitle", "slug")
    )
    return SPORTS_TEXT_RE.search(title) is not None


def normalize_event(raw: dict[str, Any], *, include_markets: bool = False) -> dict[str, Any]:
    markets = [item for item in raw.get("markets") or [] if isinstance(item, dict)]
    tradeable = [item for item in markets if is_tradeable_candidate(item)]
    tags = sorted(set(flatten_labels(raw.get("tags"))))
    result: dict[str, Any] = {
        "id": str(raw.get("id")) if raw.get("id") is not None else None,
        "title": raw.get("title") or raw.get("question"),
        "slug": raw.get("slug"),
        "description": raw.get("description"),
        "start_date": raw.get("startDate"),
        "end_date": raw.get("endDate"),
        "active": raw.get("active"),
        "closed": raw.get("closed"),
        "volume": optional_number(raw.get("volume")),
        "volume_24h": optional_number(raw.get("volume24hr")),
        "liquidity": optional_number(raw.get("liquidity")),
        "tags": tags,
        "market_count": len(markets),
        "tradeable_market_count": len(tradeable),
        "market_examples": [
            str(item.get("question") or item.get("title"))
            for item in tradeable[:5]
            if item.get("question") or item.get("title")
        ],
        "url": f"https://polymarket.com/event/{raw.get('slug')}" if raw.get("slug") else None,
    }
    if include_markets:
        result["markets"] = [
            normalize_market({**market, "eventSlug": raw.get("slug")})
            for market in markets
        ]
    return result


def is_tradeable_candidate(raw: dict[str, Any]) -> bool:
    if raw.get("active") is False or raw.get("closed") is True:
        return False
    if raw.get("acceptingOrders") is False:
        return False
    if not str(raw.get("question") or raw.get("title") or "").strip():
        return False
    outcomes = parse_jsonish_list(raw.get("outcomes"))
    tokens = parse_jsonish_list(raw.get("clobTokenIds"))
    return (
        bool(outcomes)
        and len(tokens) >= len(outcomes)
        and all(str(token or "").strip() for token in tokens[: len(outcomes)])
    )


def fetch_active_event_universe(
    client: HttpClient,
    gamma_base: str,
    *,
    page_size: int = DEFAULT_EVENT_PAGE_SIZE,
    safety_limit: int = DEFAULT_EVENT_SAFETY_LIMIT,
    order: str = "volume24hr",
    ascending: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page_size = positive_int(page_size, "page size")
    safety_limit = positive_int(safety_limit, "event safety limit")
    events: list[dict[str, Any]] = []
    seen_page_fingerprints: set[str] = set()
    seen_cursors: set[str] = set()
    after_cursor: str | None = None
    pages = 0
    expanded_markets = 0

    while True:
        response = client.get_json(
            join_url(
                gamma_base,
                "/events/keyset",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "order": order,
                    "ascending": str(ascending).lower(),
                    "include_tag": "true",
                    "after_cursor": after_cursor,
                },
            )
        )
        if not isinstance(response, dict):
            raise DataError("events keyset endpoint did not return an object")
        page = response.get("events") or []
        if not isinstance(page, list):
            raise DataError("events keyset response did not contain an event list")
        pages += 1
        if not page:
            break

        fingerprint = json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fingerprint in seen_page_fingerprints:
            raise DataError("events keyset endpoint repeated a page")
        seen_page_fingerprints.add(fingerprint)

        rows = [item for item in page if isinstance(item, dict)]
        if len(events) + len(rows) > safety_limit:
            raise DataError(
                f"active event enumeration exceeded safety limit {safety_limit}; "
                "raise the limit only after verifying API pagination"
            )
        events.extend(rows)
        expanded_markets += sum(
            len(event.get("markets") or [])
            for event in rows
            if isinstance(event.get("markets") or [], list)
        )
        next_cursor = str(response.get("next_cursor") or "").strip()
        if not next_cursor:
            break
        if next_cursor in seen_cursors or next_cursor == after_cursor:
            raise DataError("events keyset endpoint repeated a cursor")
        seen_cursors.add(next_cursor)
        after_cursor = next_cursor

    return events, {
        "complete": True,
        "fetched_events": len(events),
        "expanded_markets": expanded_markets,
        "pages_requested": pages,
        "page_size": page_size,
        "safety_limit": safety_limit,
        "order": order,
        "ascending": ascending,
    }


def build_candidate_events(
    raw_events: list[dict[str, Any]],
    account_snapshot: dict[str, Any],
) -> dict[str, Any]:
    held_keys: set[str] = set()
    for position in account_position_rows(account_snapshot):
        held_keys.update(position_identity_keys(position))

    excluded = Counter()
    candidates: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for raw in raw_events:
        primary_key = event_primary_key(raw)
        if primary_key is None:
            excluded["invalid_identity"] += 1
            continue
        if primary_key in seen_events:
            excluded["duplicate"] += 1
            continue
        seen_events.add(primary_key)
        child_markets = [item for item in raw.get("markets") or [] if isinstance(item, dict)]
        contains_holding = event_identity_keys(raw).intersection(held_keys) or any(
            market_identity_keys(market).intersection(held_keys)
            for market in child_markets
        )
        if contains_holding:
            excluded["current_holding_event"] += 1
            continue
        if is_sports_or_esports_market(raw):
            excluded["sports_or_esports"] += 1
            continue
        if not any(is_tradeable_candidate(market) for market in child_markets):
            excluded["no_tradeable_markets"] += 1
            continue
        candidates.append(normalize_event(raw))

    return {
        "candidate_count": len(candidates),
        "excluded_count": sum(excluded.values()),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "candidates": candidates,
        "semantic_screen_required": (
            "Agent must continue through this complete deterministic event sequence until about 100 "
            "events pass causal analyzability, domain priority, horizon, portfolio, and plausible "
            "AI-advantage screening, or until events are exhausted. Expand only selected events into "
            "their child markets, then rank markets for deep research."
        ),
    }


def normalize_book(raw: dict[str, Any]) -> dict[str, Any]:
    def levels(name: str, reverse: bool) -> list[dict[str, float]]:
        result = []
        for level in raw.get(name, []) or []:
            try:
                result.append({"price": float(level["price"]), "size": float(level["size"])})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(result, key=lambda item: item["price"], reverse=reverse)

    return {
        "market": raw.get("market"),
        "asset_id": raw.get("asset_id"),
        "timestamp": raw.get("timestamp"),
        "min_order_size": optional_number(raw.get("min_order_size")),
        "tick_size": optional_number(raw.get("tick_size")),
        "neg_risk": raw.get("neg_risk"),
        "bids": levels("bids", True),
        "asks": levels("asks", False),
    }


def normalize_fee_rate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataError("fee-rate endpoint did not return an object")
    value = raw.get("base_fee", raw.get("fee_rate_bps", raw.get("feeRateBps")))
    if value is None:
        raise DataError(f"fee-rate response did not contain a recognized basis-point field: {raw}")
    try:
        basis_points = float(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"fee-rate basis points were invalid: {value!r}") from exc
    if not math.isfinite(basis_points) or basis_points < 0:
        raise DataError(f"fee-rate basis points were invalid: {value!r}")
    return {
        "fee_rate_bps": basis_points,
        "fee_rate": basis_points / 10_000.0,
        "raw": raw,
    }


def fetch_positions(
    address: str,
    client: HttpClient,
    data_base: str,
    page_size: int,
) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = join_url(
            data_base,
            "/positions",
            {"user": address, "limit": page_size, "offset": offset},
        )
        page = client.get_json(url)
        if not isinstance(page, list):
            raise DataError("positions endpoint did not return a list")
        positions.extend(item for item in page if isinstance(item, dict))
        if len(page) < page_size:
            return positions
        offset += page_size


def fetch_collateral_balance(
    address: str,
    client: HttpClient,
    rpc_url: str,
    collateral_asset: dict[str, Any],
) -> dict[str, Any]:
    token_address = str(collateral_asset["token_address"])
    decimals = int(collateral_asset["decimals"])
    clean_address = address.lower().removeprefix("0x")
    call_data = "0x70a08231" + clean_address.rjust(64, "0")
    response = client.post_json(
        rpc_url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": token_address, "data": call_data}, "latest"],
        },
    )
    if not isinstance(response, dict) or not isinstance(response.get("result"), str):
        raise DataError(f"RPC balance response was invalid: {response}")
    atomic = int(response["result"], 16)
    return {
        "asset": collateral_asset["asset"],
        "address": address,
        "token_address": token_address,
        "decimals": decimals,
        "atomic_balance": str(atomic),
        "balance": atomic / (10**decimals),
        "collateral_source": collateral_asset["source"],
        "note": (
            "Public on-chain active-collateral balance. It can exceed authenticated available-to-trade "
            "cash when private open orders or account constraints reserve funds."
        ),
    }


def emit(value: Any, pretty: bool) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gamma-base", default=DEFAULT_GAMMA)
    parser.add_argument("--data-base", default=DEFAULT_DATA)
    parser.add_argument("--clob-base", default=DEFAULT_CLOB)
    parser.add_argument("--site-base", default=DEFAULT_SITE)
    parser.add_argument("--assets-base", default=DEFAULT_ASSETS)
    parser.add_argument("--rpc-url", default=DEFAULT_RPC)
    parser.add_argument("--collateral-symbol", default=DEFAULT_COLLATERAL_SYMBOL)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="Resolve an address or public profile URL")
    resolve.add_argument("account")

    profile = subparsers.add_parser("profile", help="Fetch a public profile")
    profile.add_argument("account")

    positions = subparsers.add_parser("positions", help="Fetch all public positions")
    positions.add_argument("account")
    positions.add_argument("--page-size", type=int, default=500)

    balance = subparsers.add_parser("balance", help="Read public on-chain collateral balance")
    balance.add_argument("account")
    balance.add_argument("--token-address", help="Explicit collateral token override for diagnostics")
    balance.add_argument("--decimals", type=int, help="Decimals for an explicit token override")

    collateral_asset = subparsers.add_parser(
        "collateral-asset", help="Discover the active public collateral asset"
    )

    account = subparsers.add_parser(
        "account", help="Fetch one canonical account snapshot for portfolio sizing"
    )
    account.add_argument("account")
    account.add_argument("--page-size", type=int, default=500)

    market = subparsers.add_parser("market", help="Fetch one market by numeric id or slug")
    market.add_argument("identifier")
    market.add_argument("--raw", action="store_true")

    event = subparsers.add_parser("event", help="Fetch one event and expand its child markets")
    event.add_argument("identifier")
    event.add_argument("--raw", action="store_true")

    discover = subparsers.add_parser("discover", help="Fetch active markets for Agent screening")
    discover.add_argument("--limit", type=int, default=100)
    discover.add_argument("--offset", type=int, default=0)
    discover.add_argument("--order", default="volume24hr")
    discover.add_argument("--ascending", action="store_true")
    discover.add_argument("--raw", action="store_true")

    candidate_events = subparsers.add_parser(
        "candidate-events",
        help="Enumerate active events and remove held, sports/esports, duplicate, and unusable events",
    )
    candidate_events.add_argument("--account-snapshot", required=True)
    candidate_events.add_argument("--page-size", type=int, default=DEFAULT_EVENT_PAGE_SIZE)
    candidate_events.add_argument("--safety-limit", type=int, default=DEFAULT_EVENT_SAFETY_LIMIT)
    candidate_events.add_argument("--order", default="volume24hr")
    candidate_events.add_argument("--ascending", action="store_true")

    book = subparsers.add_parser("book", help="Fetch a token order book and fee rate")
    book.add_argument("token_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = HttpClient(timeout=args.timeout, retries=args.retries)
    try:
        if args.command == "resolve":
            result = resolve_account(args.account, client, args.site_base)
        elif args.command == "profile":
            account = resolve_account(args.account, client, args.site_base)
            profile = fetch_public_profile(account["address"], client, args.gamma_base)
            result = {"account": account, "profile": profile}
        elif args.command == "positions":
            account = resolve_account(args.account, client, args.site_base)
            positions = fetch_positions(account["address"], client, args.data_base, args.page_size)
            result = {"account": account, "count": len(positions), "positions": positions}
        elif args.command == "balance":
            account = resolve_account(args.account, client, args.site_base)
            collateral_asset = resolve_collateral_asset(args, client)
            result = fetch_collateral_balance(
                account["address"], client, args.rpc_url, collateral_asset
            )
        elif args.command == "collateral-asset":
            result = fetch_collateral_asset(client, args.assets_base, args.collateral_symbol)
        elif args.command == "account":
            resolved = resolve_account(args.account, client, args.site_base)
            profile = fetch_public_profile(resolved["address"], client, args.gamma_base)
            positions = fetch_positions(resolved["address"], client, args.data_base, args.page_size)
            collateral_asset = fetch_collateral_asset(
                client, args.assets_base, args.collateral_symbol
            )
            collateral = fetch_collateral_balance(
                resolved["address"], client, args.rpc_url, collateral_asset
            )
            mismatched = sorted(
                {
                    str(item.get("proxyWallet", "")).lower()
                    for item in positions
                    if item.get("proxyWallet")
                    and str(item.get("proxyWallet")).lower() != resolved["address"].lower()
                }
            )
            if mismatched:
                raise DataError("positions response contained a proxy wallet different from the resolved account")
            result = {
                "account": resolved,
                "profile": profile,
                "positions": {"count": len(positions), "positions": positions},
                "collateral": collateral,
                "sizing_inputs": {
                    "available_cash": collateral["balance"],
                    "balance_source": "public_onchain_active_collateral",
                    "collateral_asset": collateral["asset"],
                    "collateral_token_address": collateral["token_address"],
                    "holdings_liquidation_value": None,
                    "note": (
                        "Compute holdings liquidation value from complete bid VWAP after fees before allocation. "
                        "Public collateral does not reveal funds reserved by private open orders."
                    ),
                },
            }
        elif args.command == "market":
            if args.identifier.isdigit():
                param = {"id": args.identifier}
            elif re.fullmatch(r"0x[a-fA-F0-9]{64}", args.identifier):
                param = {"condition_ids": args.identifier}
            else:
                param = {"slug": args.identifier}
            raw = client.get_json(join_url(args.gamma_base, "/markets", param))
            if isinstance(raw, list):
                raw = raw[0] if raw and isinstance(raw[0], dict) else None
            if not isinstance(raw, dict):
                raise DataError("market endpoint did not return an object")
            result = raw if args.raw else normalize_market(raw)
        elif args.command == "event":
            path = f"/events/{args.identifier}" if args.identifier.isdigit() else f"/events/slug/{urllib.parse.quote(args.identifier)}"
            raw = client.get_json(join_url(args.gamma_base, path))
            if not isinstance(raw, dict):
                raise DataError("event endpoint did not return an object")
            result = raw if args.raw else normalize_event(raw, include_markets=True)
        elif args.command == "discover":
            raw = client.get_json(
                join_url(
                    args.gamma_base,
                    "/markets",
                    {
                        "active": "true",
                        "closed": "false",
                        "limit": args.limit,
                        "offset": args.offset,
                        "order": args.order,
                        "ascending": str(args.ascending).lower(),
                    },
                )
            )
            if not isinstance(raw, list):
                raise DataError("markets endpoint did not return a list")
            result = raw if args.raw else [normalize_market(item) for item in raw if isinstance(item, dict)]
        elif args.command == "candidate-events":
            snapshot = load_json_object(args.account_snapshot, "account snapshot")
            raw_events, scan = fetch_active_event_universe(
                client,
                args.gamma_base,
                page_size=args.page_size,
                safety_limit=args.safety_limit,
                order=args.order,
                ascending=args.ascending,
            )
            result = {
                "scan": scan,
                **build_candidate_events(raw_events, snapshot),
            }
        elif args.command == "book":
            raw_book = client.get_json(join_url(args.clob_base, "/book", {"token_id": args.token_id}))
            if not isinstance(raw_book, dict):
                raise DataError("book endpoint did not return an object")
            fee = client.get_json(join_url(args.clob_base, "/fee-rate", {"token_id": args.token_id}))
            result = {"book": normalize_book(raw_book), "fee": normalize_fee_rate(fee)}
        else:  # pragma: no cover - argparse enforces a command.
            raise DataError(f"unsupported command: {args.command}")
    except (DataError, ValueError, OSError) as exc:
        print(f"polymarket_data: {exc}", file=sys.stderr)
        return 2
    emit(result, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
