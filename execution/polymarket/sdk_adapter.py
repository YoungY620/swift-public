"""Thin production adapter over Polymarket's official unified Python SDK.

The adapter deliberately signs protected FOK market orders with
``create_market_order`` and submits them separately through ``post_order``.
Unlike the convenience ``place_market_order`` method, that path does not submit
an on-chain approval transaction after an allowance rejection.  A scheduled
executor must fail closed instead of unexpectedly spending gas or widening an
allowance.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from .models import (
    ZERO,
    BookLevel,
    FeeSchedule,
    MarketSnapshot,
    ModelError,
    TradeIntent,
    decimal_string,
    decimal_value,
)


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
ASSETS_URL = "https://api.perpetuals.polymarket.com/v1/info/assets"
POLYGON_RPC_URL = "https://polygon.drpc.org"
BASE_UNITS = Decimal("1000000")
PINNED_SDK_VERSION = "0.6.0"
ORDER_PROTOCOL_VERSION = "2"
SIGNATURE_TYPE_BY_WALLET = {
    "EOA": 0,
    "POLY_PROXY": 1,
    "GNOSIS_SAFE": 2,
    "DEPOSIT_WALLET": 3,
}
SUPPORTED_WALLET_TYPES = frozenset(SIGNATURE_TYPE_BY_WALLET)

# These are the production contracts shipped by polymarket-client 0.6.0.  They
# are used only to select the matching allowance entry.  The SDK itself still
# signs against its own pinned environment configuration.
STANDARD_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
PINNED_COLLATERAL = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class AdapterError(RuntimeError):
    """Raised when a live dependency cannot establish a trusted state."""


class OrderNotFilledError(AdapterError):
    """A protected FOK order was definitively killed without any fill."""


class OrderRejectedError(AdapterError):
    """The CLOB definitively rejected an order before accepting it."""


def _is_request_rejected(exc: BaseException) -> bool:
    try:
        from polymarket.errors import RequestRejectedError
    except ImportError:
        return False
    return isinstance(exc, RequestRejectedError)


def _is_definitive_fok_no_fill(exc: BaseException) -> bool:
    """Recognize only the pinned SDK's explicit FOK kill response."""

    try:
        from polymarket.errors import RequestRejectedError
    except ImportError:
        return False
    if not isinstance(exc, RequestRejectedError):
        return False
    message = " ".join(str(exc).casefold().split())
    return (
        "order couldn't be fully filled" in message
        and "fok orders are fully filled or killed" in message
    )


@dataclass(frozen=True)
class Credentials:
    signer_private_key: str = field(repr=False)
    account_wallet: str
    clob_api_key: str = field(repr=False)
    clob_api_secret: str = field(repr=False)
    clob_api_passphrase: str = field(repr=False)
    expected_wallet_type: str = "AUTO"

    @classmethod
    def from_env(cls) -> "Credentials":
        names = {
            "signer_private_key": "POLYMARKET_SIGNER_PRIVATE_KEY",
            "account_wallet": "POLYMARKET_ACCOUNT_WALLET",
            "clob_api_key": "POLYMARKET_CLOB_API_KEY",
            "clob_api_secret": "POLYMARKET_CLOB_API_SECRET",
            "clob_api_passphrase": "POLYMARKET_CLOB_API_PASSPHRASE",
        }
        values: dict[str, str] = {}
        missing: list[str] = []
        for field_name, name in names.items():
            value = os.environ.get(name, "").strip()
            if not value:
                missing.append(name)
            values[field_name] = value
        if missing:
            raise AdapterError("missing required secret/config: " + ", ".join(sorted(missing)))
        expected_wallet_type = os.environ.get(
            "POLYMARKET_EXPECTED_WALLET_TYPE", "AUTO"
        ).strip().upper()
        if expected_wallet_type not in {"AUTO", *SUPPORTED_WALLET_TYPES}:
            supported = ", ".join(["AUTO", *sorted(SUPPORTED_WALLET_TYPES)])
            raise AdapterError(
                "POLYMARKET_EXPECTED_WALLET_TYPE must be one of: " + supported
            )
        values["expected_wallet_type"] = expected_wallet_type
        return cls(**values)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    attempts: int = 4,
    timeout: float = 15,
) -> Any:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "forecast-edge-executor/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last_error: BaseException | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise AdapterError(f"request failed after {attempts} attempts: {url}: {last_error}")


def _gamma_market(condition_id: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"condition_ids": condition_id})
    value = _request_json(f"{GAMMA_BASE}/markets?{query}")
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise AdapterError(f"Gamma returned {len(value) if isinstance(value, list) else 0} markets")
    return value[0]


def _jsonish_list(value: Any, label: str) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{label} is not valid JSON") from exc
    if not isinstance(value, list):
        raise AdapterError(f"{label} must be a list")
    return value


def _active_collateral() -> dict[str, Any]:
    values = _request_json(ASSETS_URL)
    matches = [
        item
        for item in values
        if isinstance(item, dict) and str(item.get("asset", "")).casefold() == "pusd"
    ] if isinstance(values, list) else []
    if len(matches) != 1:
        raise AdapterError(f"expected one active pUSD asset, found {len(matches)}")
    if matches[0].get("decimals") != 6:
        raise AdapterError("active pUSD asset does not use the expected six decimals")
    address = str(matches[0].get("address", ""))
    if not EVM_ADDRESS.fullmatch(address):
        raise AdapterError("active pUSD asset has an invalid contract address")
    if address.casefold() != PINNED_COLLATERAL.casefold():
        raise AdapterError(
            "active pUSD contract differs from the contract pinned by polymarket-client 0.6.0"
        )
    return matches[0]


def _native_balance(address: str) -> Decimal:
    value = _request_json(
        POLYGON_RPC_URL,
        method="POST",
        body={"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]},
    )
    if not isinstance(value, dict) or not isinstance(value.get("result"), str):
        raise AdapterError("Polygon RPC did not return a native balance")
    try:
        return Decimal(int(value["result"], 16)) / Decimal(10**18)
    except ValueError as exc:
        raise AdapterError("Polygon RPC native balance is invalid") from exc


def _contract_code(address: str) -> str:
    value = _request_json(
        POLYGON_RPC_URL,
        method="POST",
        body={"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]},
    )
    if not isinstance(value, dict) or not isinstance(value.get("result"), str):
        raise AdapterError("Polygon RPC did not return wallet contract code")
    return str(value["result"])


def _erc20_balance(token_address: str, wallet: str, decimals: int) -> Decimal:
    if not EVM_ADDRESS.fullmatch(token_address) or not EVM_ADDRESS.fullmatch(wallet):
        raise AdapterError("cannot query collateral for an invalid address")
    calldata = "0x70a08231" + wallet[2:].lower().rjust(64, "0")
    value = _request_json(
        POLYGON_RPC_URL,
        method="POST",
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": token_address, "data": calldata}, "latest"],
        },
    )
    if not isinstance(value, dict) or not isinstance(value.get("result"), str):
        raise AdapterError("Polygon RPC did not return the public collateral balance")
    try:
        return Decimal(int(value["result"], 16)) / (Decimal(10) ** decimals)
    except ValueError as exc:
        raise AdapterError("Polygon RPC collateral balance is invalid") from exc


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        raw = value.model_dump(mode="json")
        if isinstance(raw, dict):
            return raw
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise AdapterError(f"cannot serialize SDK value {type(value).__name__}")


class LivePolymarketAdapter:
    def __init__(self, credentials: Credentials) -> None:
        try:
            from polymarket import ApiKeyCreds, SecureClient
            from importlib.metadata import version
            from eth_account import Account
        except ImportError as exc:  # pragma: no cover - production dependency failure.
            raise AdapterError("polymarket-client is not installed") from exc
        sdk_version = version("polymarket-client")
        if sdk_version != PINNED_SDK_VERSION:
            raise AdapterError(
                f"executor requires the audited polymarket-client {PINNED_SDK_VERSION}"
            )
        self.sdk_version = sdk_version
        try:
            signer = str(Account.from_key(credentials.signer_private_key).address)
        except (TypeError, ValueError) as exc:
            raise AdapterError(
                "POLYMARKET_SIGNER_PRIVATE_KEY is not a valid EVM signing key"
            ) from exc
        if not EVM_ADDRESS.fullmatch(credentials.account_wallet):
            raise AdapterError("POLYMARKET_ACCOUNT_WALLET is not an EVM address")
        if credentials.account_wallet.casefold() != signer.casefold():
            code = _contract_code(credentials.account_wallet)
            if code.casefold() in {"0x", "0x0", "0x00"}:
                raise AdapterError(
                    "configured Polymarket account wallet is not deployed; scheduled execution "
                    "will not deploy it automatically"
                )
        api_creds = ApiKeyCreds(
            key=credentials.clob_api_key,
            secret=credentials.clob_api_secret,
            passphrase=credentials.clob_api_passphrase,
        )
        try:
            # SecureClient.create() can derive API credentials and deploy a default
            # Deposit Wallet.  The pinned SDK's non-public constructor is used
            # deliberately so initialization remains read-only; the first
            # authenticated preflight call then proves the supplied credentials.
            self.client = SecureClient._create(  # type: ignore[attr-defined]
                private_key=credentials.signer_private_key,
                wallet=credentials.account_wallet,
                credentials=api_creds,
                validate_credentials=False,
            )
        except Exception as exc:
            raise AdapterError(f"authenticated Polymarket client creation failed: {exc}") from exc
        if str(self.client.wallet).casefold() != credentials.account_wallet.casefold():
            self.client.close()
            raise AdapterError("authenticated wallet differs from POLYMARKET_ACCOUNT_WALLET")
        detected_wallet_type = str(self.client.wallet_type)
        if detected_wallet_type not in SUPPORTED_WALLET_TYPES:
            self.client.close()
            raise AdapterError(f"SDK returned unsupported wallet type {detected_wallet_type!r}")
        if (
            credentials.expected_wallet_type != "AUTO"
            and detected_wallet_type != credentials.expected_wallet_type
        ):
            self.client.close()
            raise AdapterError(
                "detected wallet type "
                f"{detected_wallet_type} differs from POLYMARKET_EXPECTED_WALLET_TYPE "
                f"{credentials.expected_wallet_type}"
            )
        used = self.client.credentials
        if (
            str(used.key) != credentials.clob_api_key
            or str(used.secret) != credentials.clob_api_secret
            or str(used.passphrase) != credentials.clob_api_passphrase
        ):
            self.client.close()
            raise AdapterError("authenticated client did not retain the supplied API credentials")

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "LivePolymarketAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def wallet(self) -> str:
        return str(self.client.wallet)

    @property
    def signer(self) -> str:
        return str(self.client.signer)

    @property
    def wallet_type(self) -> str:
        return str(self.client.wallet_type)

    @property
    def signature_type(self) -> int:
        return SIGNATURE_TYPE_BY_WALLET[self.wallet_type]

    def health(self) -> dict[str, Any]:
        geoblock = _request_json(GEOBLOCK_URL)
        if not isinstance(geoblock, dict) or geoblock.get("blocked") is not False:
            country = geoblock.get("country") if isinstance(geoblock, dict) else "unknown"
            raise AdapterError(f"Polymarket geoblock check failed (country={country})")
        server_time = _request_json(f"{CLOB_BASE}/time")
        try:
            server_epoch = int(server_time)
        except (TypeError, ValueError) as exc:
            raise AdapterError("CLOB time endpoint returned an invalid timestamp") from exc
        clock_skew = abs(int(time.time()) - server_epoch)
        if clock_skew > 30:
            raise AdapterError(f"runner clock differs from CLOB by {clock_skew} seconds")
        try:
            closed_only = bool(self.client.get_closed_only_mode())
        except Exception as exc:
            raise AdapterError(f"authenticated CLOB status check failed: {exc}") from exc
        return {
            "geoblock": {
                "blocked": False,
                "country": str(geoblock.get("country", "")),
                "region": str(geoblock.get("region", "")),
            },
            "clob_server_epoch": server_epoch,
            "clock_skew_seconds": clock_skew,
            "closed_only": closed_only,
        }

    def positions(self) -> tuple[dict[str, Any], ...]:
        try:
            values = tuple(self.client.list_positions(page_size=100).iter_items())
        except Exception as exc:
            raise AdapterError(f"position query failed: {exc}") from exc
        result: list[dict[str, Any]] = []
        for value in values:
            if value.wallet is not None and str(value.wallet).casefold() != self.wallet.casefold():
                raise AdapterError("position query returned another wallet")
            result.append(
                {
                    "condition_id": str(value.condition_id),
                    "token_id": str(value.token_id or ""),
                    "outcome": str(value.outcome or "").lower(),
                    "size": decimal_string(value.size or ZERO),
                }
            )
        return tuple(result)

    def open_orders(self, token_id: str | None = None) -> tuple[dict[str, Any], ...]:
        try:
            values = tuple(self.client.list_open_orders(token_id=token_id).iter_items())
        except Exception as exc:
            raise AdapterError(f"open-order query failed for {token_id or 'account'}: {exc}") from exc
        return tuple(_model_dict(value) for value in values)

    def cancel_all_open_orders(self) -> dict[str, Any]:
        """Cancel every resting account order and prove that none remain open."""

        before = self.open_orders()
        canceled: list[str] = []
        remaining = before
        for _ in range(3):
            if not remaining:
                break
            round_ids: list[str] = []
            for order in remaining:
                order_id = str(order.get("id", "")).strip()
                if not order_id:
                    raise AdapterError("open-order query returned an order without an id")
                if order_id in round_ids:
                    raise AdapterError(f"open-order query returned duplicate order {order_id}")
                round_ids.append(order_id)
            for order_id in round_ids:
                result = self.cancel_order(order_id)
                not_canceled = result.get("not_canceled", {})
                if order_id in not_canceled and self.order_if_open(order_id) is not None:
                    raise AdapterError(f"could not cancel pre-existing open order {order_id}")
                if order_id not in canceled:
                    canceled.append(order_id)
            remaining = self.open_orders()
        if remaining:
            remaining_ids = sorted(
                str(order.get("id", "<missing>")) for order in remaining
            )
            raise AdapterError(
                "account still has open orders after the required cancellation sweep: "
                + ", ".join(remaining_ids)
            )
        return {
            "initial_open_order_count": len(before),
            "canceled_order_count": len(canceled),
            "canceled_order_ids": canceled,
            "remaining_open_order_count": 0,
        }

    def balance_allowance(self, *, asset_type: str, token_id: str | None = None) -> dict[str, Any]:
        try:
            value = self.client.get_balance_allowance(asset_type=asset_type, token_id=token_id)
        except Exception as exc:
            raise AdapterError(f"balance/allowance query failed for {asset_type}: {exc}") from exc
        return {
            "balance": decimal_string(Decimal(value.balance) / BASE_UNITS),
            "allowances": {
                str(key): decimal_string(Decimal(amount) / BASE_UNITS)
                for key, amount in value.allowances.items()
            },
        }

    def market_snapshot(self, intent: TradeIntent) -> MarketSnapshot:
        gamma = _gamma_market(intent.multi_market_identifier)
        if str(gamma.get("conditionId", "")).casefold() != intent.multi_market_identifier.casefold():
            raise AdapterError("Gamma condition does not match the intent")
        outcomes = _jsonish_list(gamma.get("outcomes"), "Gamma outcomes")
        token_ids = [str(item) for item in _jsonish_list(gamma.get("clobTokenIds"), "Gamma tokens")]
        if len(outcomes) != 2 or len(token_ids) != 2:
            raise AdapterError("executor supports binary markets with two outcome tokens")
        try:
            token_index = token_ids.index(intent.instrument_identifier)
        except ValueError as exc:
            raise AdapterError("intent token is not part of the Gamma market") from exc
        outcome = str(outcomes[token_index]).casefold()
        if outcome != intent.outcome:
            raise AdapterError("intent outcome label does not match the live token")
        try:
            market = self.client.get_market(id=str(gamma["id"]))
            book = self.client.get_order_book(token_id=intent.instrument_identifier)
        except Exception as exc:
            raise AdapterError(f"live market/order-book query failed: {exc}") from exc
        if str(book.condition_id).casefold() != intent.multi_market_identifier.casefold():
            raise AdapterError("CLOB book condition differs from the intent")
        if str(market.id) != str(gamma["id"]):
            raise AdapterError("SDK market id differs from the Gamma market")
        if bool(market.state.neg_risk) != bool(book.neg_risk):
            raise AdapterError("Gamma and CLOB disagree on negative-risk routing")
        fee = market.trading.fee_schedule
        fee_schedule = FeeSchedule(
            rate=fee.rate if fee is not None else ZERO,
            exponent=decimal_value(fee.exponent, "fee exponent") if fee is not None else Decimal("1"),
        )
        timestamp = (
            book.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if book.timestamp is not None
            else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        return MarketSnapshot(
            market_id=str(market.id),
            condition_id=str(book.condition_id),
            token_id=str(book.token_id),
            outcome=outcome,  # type: ignore[arg-type]
            active=market.state.active is True,
            closed=market.state.closed is True,
            accepting_orders=market.state.accepting_orders is True,
            enable_order_book=market.state.enable_order_book is True,
            neg_risk=book.neg_risk is True,
            min_order_size=book.min_order_size,
            tick_size=book.tick_size,
            fee_schedule=fee_schedule,
            bids=tuple(BookLevel(level.price, level.size) for level in book.bids),
            asks=tuple(BookLevel(level.price, level.size) for level in book.asks),
            book_timestamp=timestamp,
        )

    def preflight(self, intents: Iterable[TradeIntent]) -> dict[str, Any]:
        intents = tuple(intents)
        health = self.health()
        if health["closed_only"] and any(intent.side == "buy" for intent in intents):
            raise AdapterError("account is in closed-only mode but the batch contains buys")
        collateral_asset = _active_collateral()
        collateral = self.balance_allowance(asset_type="COLLATERAL")
        authenticated_balance = decimal_value(
            collateral.get("balance"), "authenticated collateral balance"
        )
        public_balance = _erc20_balance(
            str(collateral_asset["address"]), self.wallet, int(collateral_asset["decimals"])
        )
        effective_balance = min(authenticated_balance, public_balance)
        positions = self.positions()
        position_by_token = {
            str(item["token_id"]): decimal_value(item["size"], "position size")
            for item in positions
        }
        markets: dict[str, Any] = {}
        conditional: dict[str, Any] = {}
        all_open_orders = self.open_orders()
        open_order_count = len(all_open_orders)
        if all_open_orders:
            raise AdapterError(
                "account has open orders after the required pre-execution cancellation sweep"
            )
        effective_available_balance = effective_balance
        for intent in intents:
            snapshot = self.market_snapshot(intent)
            markets[intent.instrument_identifier] = snapshot.to_dict()
            if intent.side == "sell":
                conditional[intent.instrument_identifier] = self.balance_allowance(
                    asset_type="CONDITIONAL", token_id=intent.instrument_identifier
                )
                if position_by_token.get(intent.instrument_identifier, ZERO) <= ZERO:
                    raise AdapterError(
                        f"sell intent has no live position for {intent.instrument_identifier}"
                    )

        native_balance = _native_balance(self.signer)
        return {
            "schema_version": "1.0",
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "account": {
                "wallet": self.wallet,
                "signer": self.signer,
                "wallet_type": self.wallet_type,
                "signature_type": self.signature_type,
                "sdk_version": self.sdk_version,
                "order_protocol_version": ORDER_PROTOCOL_VERSION,
            },
            "health": health,
            "collateral_asset": {
                "asset": str(collateral_asset.get("asset")),
                "address": str(collateral_asset.get("address")),
                "decimals": int(collateral_asset.get("decimals")),
            },
            "collateral": {
                **collateral,
                "authenticated_balance": decimal_string(authenticated_balance),
                "public_onchain_balance": decimal_string(public_balance),
                "effective_available_balance": decimal_string(effective_available_balance),
            },
            "conditional": conditional,
            "native_gas_balance": decimal_string(native_balance),
            "gas_required_for_clob_order_submission": False,
            "allowance_policy": "preexisting-only; scheduled execution never submits approvals",
            "positions": list(positions),
            "open_order_count": open_order_count,
            "open_order_policy": "all pre-existing orders canceled before preflight",
            "markets": markets,
        }

    def create_market_order(
        self,
        *,
        token_id: str,
        price_limit: Decimal,
        quantity: Decimal,
        side: str,
        max_buy_spend: Decimal | None = None,
    ) -> Any:
        try:
            if side == "buy":
                if max_buy_spend is None:
                    raise AdapterError("protected BUY market order is missing max_buy_spend")
                return self.client.create_market_order(
                    token_id=token_id,
                    side="BUY",
                    amount=price_limit * quantity,
                    max_spend=max_buy_spend,
                    max_price=price_limit,
                    order_type="FOK",
                )
            if side == "sell":
                return self.client.create_market_order(
                    token_id=token_id,
                    side="SELL",
                    shares=quantity,
                    min_price=price_limit,
                    order_type="FOK",
                )
            raise AdapterError(f"unsupported market-order side {side!r}")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"protected FOK market-order signing failed: {exc}") from exc

    @staticmethod
    def signed_market_order_quantity(signed_order: Any, *, side: str) -> Decimal:
        """Return the SDK-rounded share quantity encoded in a signed order."""

        field_name = "taker_amount" if side == "buy" else "maker_amount"
        if side not in {"buy", "sell"}:
            raise AdapterError(f"unsupported market-order side {side!r}")
        raw = getattr(signed_order, field_name, None)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise AdapterError(
                f"signed protected market order has invalid {field_name}"
            )
        return Decimal(raw) / BASE_UNITS

    def post_order(self, signed_order: Any) -> Any:
        try:
            return self.client.post_order(signed_order)
        except Exception as exc:
            if _is_definitive_fok_no_fill(exc):
                raise OrderNotFilledError(
                    "protected FOK order was killed without a fill"
                ) from exc
            if _is_request_rejected(exc):
                raise OrderRejectedError(
                    "CLOB definitively rejected the protected FOK order"
                ) from exc
            raise AdapterError(f"CLOB order submission failed: {exc}") from exc

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        try:
            return _model_dict(self.client.cancel_order(order_id=order_id))
        except Exception as exc:
            raise AdapterError(f"order cancellation failed for {order_id}: {exc}") from exc

    def account_trades(self, trade_ids: Iterable[str]) -> tuple[dict[str, Any], ...]:
        values: dict[str, dict[str, Any]] = {}
        for trade_id in trade_ids:
            try:
                matches = tuple(self.client.list_account_trades(id=trade_id).iter_items())
            except Exception as exc:
                raise AdapterError(f"trade query failed for {trade_id}: {exc}") from exc
            for match in matches:
                raw = _model_dict(match)
                values[str(raw.get("id", trade_id))] = raw
        return tuple(values[key] for key in sorted(values))

    def order(self, order_id: str) -> dict[str, Any] | None:
        try:
            values = tuple(self.client.list_open_orders(id=order_id).iter_items())
        except Exception as exc:
            raise AdapterError(f"order query failed for {order_id}: {exc}") from exc
        if len(values) > 1:
            raise AdapterError(f"CLOB returned duplicate order {order_id}")
        if not values:
            return None
        return _model_dict(values[0])

    def order_if_open(self, order_id: str) -> dict[str, Any] | None:
        order = self.order(order_id)
        if order is None:
            return None
        status = str(order.get("status", "")).strip().upper()
        if status == "LIVE":
            return order
        if status in {"MATCHED", "CANCELED", "CANCELLED", "DELAYED", "UNMATCHED"}:
            return None
        raise AdapterError(
            f"CLOB returned unknown order status {status or '<missing>'} for {order_id}"
        )

    def conditional_balance(self, token_id: str) -> Decimal:
        value = self.balance_allowance(asset_type="CONDITIONAL", token_id=token_id)
        return decimal_value(value["balance"], "conditional balance")

    def collateral_balance(self) -> Decimal:
        value = self.balance_allowance(asset_type="COLLATERAL")
        return decimal_value(value["balance"], "collateral balance")

    def wait_for_settlement(self, response: Any, *, timeout_seconds: float) -> tuple[str, ...]:
        if not getattr(response, "ok", False) or not getattr(response, "trade_ids", ()):
            return ()
        try:
            values = self.client.wait_for_order_fill_settlement(
                response, timeout_s=timeout_seconds
            )
        except Exception as exc:
            raise AdapterError(f"order fill did not reach a terminal settlement state: {exc}") from exc
        return tuple(str(value) for value in values)


def required_exchange_allowance(snapshot: MarketSnapshot, allowance: dict[str, Any]) -> Decimal:
    exchange = NEG_RISK_EXCHANGE if snapshot.neg_risk else STANDARD_EXCHANGE
    for key, value in allowance.get("allowances", {}).items():
        if str(key).casefold() == exchange.casefold():
            return decimal_value(value, "exchange allowance")
    return ZERO
