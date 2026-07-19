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
from typing import Any


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
DEFAULT_GAMMA = "https://gamma-api.polymarket.com"
DEFAULT_DATA = "https://data-api.polymarket.com"
DEFAULT_CLOB = "https://clob.polymarket.com"
DEFAULT_SITE = "https://polymarket.com"
DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"
DEFAULT_COLLATERAL = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


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
    return {
        "id": str(raw.get("id")) if raw.get("id") is not None else None,
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
        "url": f"https://polymarket.com/event/{raw.get('slug')}" if raw.get("slug") else None,
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
    token_address: str,
    decimals: int,
) -> dict[str, Any]:
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
        "address": address,
        "token_address": token_address,
        "decimals": decimals,
        "atomic_balance": str(atomic),
        "balance": atomic / (10**decimals),
        "note": "Public on-chain collateral balance; it does not prove CLOB allowance or orderability.",
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
    parser.add_argument("--rpc-url", default=DEFAULT_RPC)
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
    balance.add_argument("--token-address", default=DEFAULT_COLLATERAL)
    balance.add_argument("--decimals", type=int, default=6)

    account = subparsers.add_parser(
        "account", help="Fetch one canonical account snapshot for portfolio sizing"
    )
    account.add_argument("account")
    account.add_argument("--page-size", type=int, default=500)
    account.add_argument("--token-address", default=DEFAULT_COLLATERAL)
    account.add_argument("--decimals", type=int, default=6)

    market = subparsers.add_parser("market", help="Fetch one market by numeric id or slug")
    market.add_argument("identifier")
    market.add_argument("--raw", action="store_true")

    discover = subparsers.add_parser("discover", help="Fetch active markets for Agent screening")
    discover.add_argument("--limit", type=int, default=100)
    discover.add_argument("--offset", type=int, default=0)
    discover.add_argument("--order", default="volume24hr")
    discover.add_argument("--ascending", action="store_true")
    discover.add_argument("--raw", action="store_true")

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
            profile = client.get_json(join_url(args.gamma_base, "/public-profile", {"address": account["address"]}))
            result = {"account": account, "profile": profile}
        elif args.command == "positions":
            account = resolve_account(args.account, client, args.site_base)
            positions = fetch_positions(account["address"], client, args.data_base, args.page_size)
            result = {"account": account, "count": len(positions), "positions": positions}
        elif args.command == "balance":
            account = resolve_account(args.account, client, args.site_base)
            result = fetch_collateral_balance(
                account["address"], client, args.rpc_url, args.token_address, args.decimals
            )
        elif args.command == "account":
            resolved = resolve_account(args.account, client, args.site_base)
            profile = client.get_json(
                join_url(args.gamma_base, "/public-profile", {"address": resolved["address"]})
            )
            positions = fetch_positions(resolved["address"], client, args.data_base, args.page_size)
            collateral = fetch_collateral_balance(
                resolved["address"], client, args.rpc_url, args.token_address, args.decimals
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
                    "balance_source": "public_onchain_collateral",
                    "holdings_liquidation_value": None,
                    "note": "Compute holdings liquidation value from complete bid VWAP after fees before allocation.",
                },
            }
        elif args.command == "market":
            path = f"/markets/{args.identifier}" if args.identifier.isdigit() else f"/markets/slug/{urllib.parse.quote(args.identifier)}"
            raw = client.get_json(join_url(args.gamma_base, path))
            if not isinstance(raw, dict):
                raise DataError("market endpoint did not return an object")
            result = raw if args.raw else normalize_market(raw)
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
