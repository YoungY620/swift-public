"""Strict value objects used by the Polymarket executor.

All financial arithmetic remains in :class:`decimal.Decimal`.  JSON numbers are
serialized as ordinary decimal strings so no binary floating-point value can
silently change an order amount.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal


ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
SHARE_QUANTUM = Decimal("0.01")


class ModelError(ValueError):
    """Raised when an execution value object cannot be constructed safely."""


def decimal_value(value: Any, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ModelError(f"{label} must be a decimal value") from exc
    if not result.is_finite():
        raise ModelError(f"{label} must be finite")
    if positive and result <= ZERO:
        raise ModelError(f"{label} must be positive")
    return result


def decimal_string(value: Decimal) -> str:
    value = value.normalize()
    if value == ZERO:
        return "0"
    return format(value, "f")


@dataclass(frozen=True)
class TradeIntent:
    schema_version: str
    venue: str
    multi_market_identifier: str
    instrument_identifier: str
    outcome: Literal["yes", "no"]
    side: Literal["buy", "sell"]
    reference_price: Decimal
    notional: Decimal
    quantity: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ModelError("trade intent schema_version must be 1.0")
        if self.venue != "polymarket":
            raise ModelError("trade intent venue must be polymarket")
        if not self.multi_market_identifier or not self.instrument_identifier:
            raise ModelError("trade intent identifiers must not be empty")
        if not ZERO < self.reference_price <= ONE:
            raise ModelError("reference_price must be strictly positive and at most 1")
        if self.notional <= ZERO or self.quantity <= ZERO:
            raise ModelError("trade intent notional and quantity must be positive")
        if not self.currency:
            raise ModelError("trade intent currency must not be empty")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TradeIntent":
        try:
            outcome = str(raw["outcome"]).lower()
            side = str(raw["side"]).lower()
            if outcome not in {"yes", "no"}:
                raise ModelError("outcome must be yes or no")
            if side not in {"buy", "sell"}:
                raise ModelError("side must be buy or sell")
            return cls(
                schema_version=str(raw["schema_version"]),
                venue=str(raw["venue"]),
                multi_market_identifier=str(raw["multi_market_identifier"]),
                instrument_identifier=str(raw["instrument_identifier"]),
                outcome=outcome,  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
                reference_price=decimal_value(
                    raw["reference_price"], "reference_price", positive=True
                ),
                notional=decimal_value(raw["notional"], "notional", positive=True),
                quantity=decimal_value(raw["quantity"], "quantity", positive=True),
                currency=str(raw["currency"]),
            )
        except KeyError as exc:
            raise ModelError(f"trade intent is missing {exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "venue": self.venue,
            "multi_market_identifier": self.multi_market_identifier,
            "instrument_identifier": self.instrument_identifier,
            "outcome": self.outcome,
            "side": self.side,
            "reference_price": decimal_string(self.reference_price),
            "notional": decimal_string(self.notional),
            "quantity": decimal_string(self.quantity),
            "currency": self.currency,
        }


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if not self.price.is_finite() or not ZERO < self.price < ONE:
            raise ModelError("book level price must be strictly between 0 and 1")
        if not self.size.is_finite() or self.size <= ZERO:
            raise ModelError("book level size must be positive and finite")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BookLevel":
        return cls(
            price=decimal_value(raw.get("price"), "book level price", positive=True),
            size=decimal_value(raw.get("size"), "book level size", positive=True),
        )

    def to_dict(self) -> dict[str, str]:
        return {"price": decimal_string(self.price), "size": decimal_string(self.size)}


@dataclass(frozen=True)
class FeeSchedule:
    rate: Decimal = ZERO
    exponent: Decimal = ONE

    def __post_init__(self) -> None:
        if not self.rate.is_finite() or not self.exponent.is_finite():
            raise ModelError("fee rate and exponent must be finite")
        if self.rate < ZERO or self.exponent < ZERO:
            raise ModelError("fee rate and exponent must not be negative")

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "FeeSchedule":
        if not raw:
            return cls()
        rate = decimal_value(raw.get("rate", "0"), "fee rate")
        exponent = decimal_value(raw.get("exponent", "1"), "fee exponent")
        if rate < ZERO or exponent < ZERO:
            raise ModelError("fee rate and exponent must not be negative")
        return cls(rate=rate, exponent=exponent)

    def fee_per_share(self, price: Decimal) -> Decimal:
        if not ZERO < price < ONE:
            raise ModelError("fee price must be strictly between 0 and 1")
        if self.rate == ZERO:
            return ZERO
        return self.rate * ((price * (ONE - price)) ** self.exponent)

    def maximum_fee_per_share(self) -> Decimal:
        """Return a conservative fee bound over every valid execution price."""

        if self.rate == ZERO:
            return ZERO
        return self.rate * (Decimal("0.25") ** self.exponent)

    def to_dict(self) -> dict[str, str]:
        return {"rate": decimal_string(self.rate), "exponent": decimal_string(self.exponent)}


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    condition_id: str
    token_id: str
    outcome: Literal["yes", "no"]
    active: bool
    closed: bool
    accepting_orders: bool
    enable_order_book: bool
    neg_risk: bool
    min_order_size: Decimal
    tick_size: Decimal
    fee_schedule: FeeSchedule
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    book_timestamp: str

    def __post_init__(self) -> None:
        if not self.market_id or not self.condition_id or not self.token_id:
            raise ModelError("market snapshot identifiers must not be empty")
        if not self.min_order_size.is_finite() or self.min_order_size <= ZERO:
            raise ModelError("minimum order size must be positive and finite")
        if not self.tick_size.is_finite() or not ZERO < self.tick_size < ONE:
            raise ModelError("tick size must be strictly between 0 and 1")
        if not self.book_timestamp:
            raise ModelError("book timestamp must not be empty")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MarketSnapshot":
        outcome = str(raw.get("outcome", "")).lower()
        if outcome not in {"yes", "no"}:
            raise ModelError("market outcome must be yes or no")
        return cls(
            market_id=str(raw.get("market_id", "")),
            condition_id=str(raw.get("condition_id", "")),
            token_id=str(raw.get("token_id", "")),
            outcome=outcome,  # type: ignore[arg-type]
            active=raw.get("active") is True,
            closed=raw.get("closed") is True,
            accepting_orders=raw.get("accepting_orders") is True,
            enable_order_book=raw.get("enable_order_book") is True,
            neg_risk=raw.get("neg_risk") is True,
            min_order_size=decimal_value(
                raw.get("min_order_size"), "minimum order size", positive=True
            ),
            tick_size=decimal_value(raw.get("tick_size"), "tick size", positive=True),
            fee_schedule=FeeSchedule.from_dict(raw.get("fee_schedule")),
            bids=tuple(BookLevel.from_dict(item) for item in raw.get("bids", [])),
            asks=tuple(BookLevel.from_dict(item) for item in raw.get("asks", [])),
            book_timestamp=str(raw.get("book_timestamp", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "outcome": self.outcome,
            "active": self.active,
            "closed": self.closed,
            "accepting_orders": self.accepting_orders,
            "enable_order_book": self.enable_order_book,
            "neg_risk": self.neg_risk,
            "min_order_size": decimal_string(self.min_order_size),
            "tick_size": decimal_string(self.tick_size),
            "fee_schedule": self.fee_schedule.to_dict(),
            "bids": [item.to_dict() for item in self.bids],
            "asks": [item.to_dict() for item in self.asks],
            "book_timestamp": self.book_timestamp,
        }


@dataclass(frozen=True)
class RiskLimits:
    max_price_deviation_bps: int = 300
    max_notional_deviation_bps: int = 500
    max_quantity_reduction_bps: int = 2500
    max_order_notional: Decimal = Decimal("50")
    max_batch_notional: Decimal = Decimal("100")
    max_order_quantity: Decimal = Decimal("1000")
    minimum_cash_reserve: Decimal = Decimal("5")
    max_fee_per_share: Decimal = Decimal("0.02")
    max_intents: int = 20
    plan_ttl_seconds: int = 90
    order_expiration_seconds: int = 300

    def __post_init__(self) -> None:
        if not 0 <= self.max_price_deviation_bps <= 5000:
            raise ModelError("max_price_deviation_bps must be between 0 and 5000")
        if not 0 <= self.max_notional_deviation_bps <= 5000:
            raise ModelError("max_notional_deviation_bps must be between 0 and 5000")
        if not 0 <= self.max_quantity_reduction_bps <= 10000:
            raise ModelError("max_quantity_reduction_bps must be between 0 and 10000")
        if self.max_order_notional <= ZERO or self.max_batch_notional <= ZERO:
            raise ModelError("notional limits must be positive")
        if self.max_order_notional > self.max_batch_notional:
            raise ModelError("max_order_notional cannot exceed max_batch_notional")
        if self.max_order_quantity <= ZERO or self.minimum_cash_reserve < ZERO:
            raise ModelError("quantity must be positive and reserve must not be negative")
        if not ZERO <= self.max_fee_per_share <= Decimal("0.10"):
            raise ModelError("max_fee_per_share must be between 0 and 0.10")
        if self.max_intents < 1 or self.plan_ttl_seconds < 15:
            raise ModelError("max_intents must be positive and plan_ttl_seconds at least 15")
        if not 240 <= self.order_expiration_seconds <= 900:
            raise ModelError("order_expiration_seconds must be between 240 and 900")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RiskLimits":
        integer_fields = {
            "max_price_deviation_bps",
            "max_notional_deviation_bps",
            "max_quantity_reduction_bps",
            "max_intents",
            "plan_ttl_seconds",
            "order_expiration_seconds",
        }
        decimal_fields = {
            "max_order_notional",
            "max_batch_notional",
            "max_order_quantity",
            "minimum_cash_reserve",
            "max_fee_per_share",
        }
        values: dict[str, Any] = {}
        defaults = asdict(cls())
        for name in integer_fields:
            value = raw.get(name, defaults[name])
            if isinstance(value, bool) or not str(value).isdigit():
                raise ModelError(f"{name} must be a non-negative integer")
            values[name] = int(value)
        for name in decimal_fields:
            values[name] = decimal_value(raw.get(name, defaults[name]), name)
        result = cls(**values)
        if not 0 <= result.max_price_deviation_bps <= 5000:
            raise ModelError("max_price_deviation_bps must be between 0 and 5000")
        if not 0 <= result.max_notional_deviation_bps <= 5000:
            raise ModelError("max_notional_deviation_bps must be between 0 and 5000")
        if not 0 <= result.max_quantity_reduction_bps <= 10000:
            raise ModelError("max_quantity_reduction_bps must be between 0 and 10000")
        if result.max_order_notional <= ZERO or result.max_batch_notional <= ZERO:
            raise ModelError("notional limits must be positive")
        if result.max_order_notional > result.max_batch_notional:
            raise ModelError("max_order_notional cannot exceed max_batch_notional")
        if result.max_order_quantity <= ZERO or result.minimum_cash_reserve < ZERO:
            raise ModelError("quantity must be positive and reserve must not be negative")
        if not ZERO <= result.max_fee_per_share <= Decimal("0.10"):
            raise ModelError("max_fee_per_share must be between 0 and 0.10")
        if result.max_intents < 1 or result.plan_ttl_seconds < 15:
            raise ModelError("max_intents must be positive and plan_ttl_seconds at least 15")
        if not 240 <= result.order_expiration_seconds <= 900:
            raise ModelError("order_expiration_seconds must be between 240 and 900")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_price_deviation_bps": self.max_price_deviation_bps,
            "max_notional_deviation_bps": self.max_notional_deviation_bps,
            "max_quantity_reduction_bps": self.max_quantity_reduction_bps,
            "max_order_notional": decimal_string(self.max_order_notional),
            "max_batch_notional": decimal_string(self.max_batch_notional),
            "max_order_quantity": decimal_string(self.max_order_quantity),
            "minimum_cash_reserve": decimal_string(self.minimum_cash_reserve),
            "max_fee_per_share": decimal_string(self.max_fee_per_share),
            "max_intents": self.max_intents,
            "plan_ttl_seconds": self.plan_ttl_seconds,
            "order_expiration_seconds": self.order_expiration_seconds,
        }


@dataclass(frozen=True)
class PlannedFill:
    price: Decimal
    quantity: Decimal
    gross_notional: Decimal
    fee: Decimal

    def __post_init__(self) -> None:
        if self.price <= ZERO or self.price >= ONE:
            raise ModelError("fill price must be strictly between 0 and 1")
        if self.quantity <= ZERO or self.gross_notional <= ZERO or self.fee < ZERO:
            raise ModelError("fill quantity/notional must be positive and fee non-negative")
        if self.gross_notional != self.price * self.quantity:
            raise ModelError("fill gross notional does not equal price * quantity")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlannedFill":
        return cls(
            price=decimal_value(raw.get("price"), "fill price", positive=True),
            quantity=decimal_value(raw.get("quantity"), "fill quantity", positive=True),
            gross_notional=decimal_value(
                raw.get("gross_notional"), "fill gross notional", positive=True
            ),
            fee=decimal_value(raw.get("fee", "0"), "fill fee"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "price": decimal_string(self.price),
            "quantity": decimal_string(self.quantity),
            "gross_notional": decimal_string(self.gross_notional),
            "fee": decimal_string(self.fee),
        }


@dataclass(frozen=True)
class PlannedOrder:
    intent_index: int
    intent: TradeIntent
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    limit_price: Decimal
    quantity: Decimal
    gross_notional: Decimal
    fee: Decimal
    cash_effect: Decimal
    limit_gross_notional: Decimal
    worst_case_fee: Decimal
    worst_case_cash_effect: Decimal
    vwap: Decimal
    quantity_reduction_bps: int
    book_timestamp: str
    fills: tuple[PlannedFill, ...]

    def __post_init__(self) -> None:
        if self.intent_index < 1:
            raise ModelError("planned order intent index must be positive")
        if self.token_id != self.intent.instrument_identifier or self.side != self.intent.side:
            raise ModelError("planned order does not match its trade intent")
        if not ZERO < self.limit_price < ONE or not ZERO < self.vwap < ONE:
            raise ModelError("planned prices must be strictly between 0 and 1")
        if self.quantity <= ZERO or self.quantity > self.intent.quantity:
            raise ModelError("planned quantity must be positive and not exceed the intent")
        if self.fee < ZERO or self.worst_case_fee < ZERO:
            raise ModelError("planned fees must not be negative")
        fill_quantity = sum((fill.quantity for fill in self.fills), ZERO)
        fill_gross = sum((fill.gross_notional for fill in self.fills), ZERO)
        fill_fee = sum((fill.fee for fill in self.fills), ZERO)
        if fill_quantity != self.quantity or fill_gross != self.gross_notional:
            raise ModelError("planned fills do not reproduce order quantity/notional")
        if fill_fee != self.fee or self.vwap != self.gross_notional / self.quantity:
            raise ModelError("planned fills do not reproduce order fee/VWAP")
        expected_cash = (
            self.gross_notional + self.fee
            if self.side == "buy"
            else self.gross_notional - self.fee
        )
        if expected_cash != self.cash_effect or expected_cash <= ZERO:
            raise ModelError("planned cash effect is inconsistent")
        if self.limit_gross_notional != self.limit_price * self.quantity:
            raise ModelError("limit gross notional is inconsistent")
        protected = (
            self.limit_gross_notional + self.worst_case_fee
            if self.side == "buy"
            else self.limit_gross_notional - self.worst_case_fee
        )
        if protected != self.worst_case_cash_effect or protected <= ZERO:
            raise ModelError("worst-case cash effect is inconsistent")
        if self.side == "buy" and self.worst_case_fee < self.fee:
            raise ModelError("worst-case buy fee is smaller than the estimated fee")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlannedOrder":
        side = str(raw.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            raise ModelError("planned order side must be buy or sell")
        return cls(
            intent_index=int(raw["intent_index"]),
            intent=TradeIntent.from_dict(raw["intent"]),
            market_id=str(raw["market_id"]),
            token_id=str(raw["token_id"]),
            side=side,  # type: ignore[arg-type]
            limit_price=decimal_value(raw["limit_price"], "limit price", positive=True),
            quantity=decimal_value(raw["quantity"], "planned quantity", positive=True),
            gross_notional=decimal_value(
                raw["gross_notional"], "planned gross notional", positive=True
            ),
            fee=decimal_value(raw.get("fee", "0"), "planned fee"),
            cash_effect=decimal_value(raw["cash_effect"], "planned cash effect", positive=True),
            limit_gross_notional=decimal_value(
                raw["limit_gross_notional"], "limit gross notional", positive=True
            ),
            worst_case_fee=decimal_value(raw["worst_case_fee"], "worst-case fee"),
            worst_case_cash_effect=decimal_value(
                raw["worst_case_cash_effect"], "worst-case cash effect", positive=True
            ),
            vwap=decimal_value(raw["vwap"], "planned vwap", positive=True),
            quantity_reduction_bps=int(raw["quantity_reduction_bps"]),
            book_timestamp=str(raw["book_timestamp"]),
            fills=tuple(PlannedFill.from_dict(item) for item in raw.get("fills", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_index": self.intent_index,
            "intent": self.intent.to_dict(),
            "market_id": self.market_id,
            "token_id": self.token_id,
            "side": self.side,
            "limit_price": decimal_string(self.limit_price),
            "quantity": decimal_string(self.quantity),
            "gross_notional": decimal_string(self.gross_notional),
            "fee": decimal_string(self.fee),
            "cash_effect": decimal_string(self.cash_effect),
            "limit_gross_notional": decimal_string(self.limit_gross_notional),
            "worst_case_fee": decimal_string(self.worst_case_fee),
            "worst_case_cash_effect": decimal_string(self.worst_case_cash_effect),
            "vwap": decimal_string(self.vwap),
            "quantity_reduction_bps": self.quantity_reduction_bps,
            "book_timestamp": self.book_timestamp,
            "fills": [fill.to_dict() for fill in self.fills],
        }


@dataclass(frozen=True)
class SkippedIntent:
    intent_index: int
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
