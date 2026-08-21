"""Live order lifecycle with bounded partial-fill handling."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from typing import Any, Iterable

from .ledger import DynamoLedger, SQLiteLedger
from .models import (
    SHARE_QUANTUM,
    ZERO,
    MarketSnapshot,
    PlannedFill,
    PlannedOrder,
    RiskLimits,
    TradeIntent,
    decimal_string,
    decimal_value,
)
from .planner import build_execution_plan
from .planner import POLYMARKET_MIN_MARKETABLE_BUY_AMOUNT
from .sdk_adapter import (
    LivePolymarketAdapter,
    OrderNotFilledError,
    OrderRejectedError,
    required_exchange_allowance,
)


class ExecutionError(RuntimeError):
    """A known, safely contained execution failure."""


class UncertainExecutionError(ExecutionError):
    """The account may have changed and automated continuation is unsafe."""


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        raw = value.model_dump(mode="json")
        if isinstance(raw, dict):
            return raw
    raise ExecutionError(f"cannot serialize SDK response {type(value).__name__}")


def _position_map(adapter: LivePolymarketAdapter) -> dict[str, Decimal]:
    return {
        str(item["token_id"]): decimal_value(item["size"], "position size")
        for item in adapter.positions()
    }


def _replan_one(
    *,
    adapter: LivePolymarketAdapter,
    original: PlannedOrder,
    limits: RiskLimits,
) -> tuple[PlannedOrder, MarketSnapshot]:
    open_orders = adapter.open_orders()
    if open_orders:
        raise ExecutionError("account acquired an open order after the cancellation sweep")
    market = adapter.market_snapshot(original.intent)
    positions = _position_map(adapter)
    cash = adapter.collateral_balance()
    capped_intent = TradeIntent(
        schema_version=original.intent.schema_version,
        venue=original.intent.venue,
        multi_market_identifier=original.intent.multi_market_identifier,
        instrument_identifier=original.intent.instrument_identifier,
        outcome=original.intent.outcome,
        side=original.intent.side,
        reference_price=original.intent.reference_price,
        notional=(
            original.intent.notional * original.quantity / original.intent.quantity
        ),
        quantity=original.quantity,
        currency=original.intent.currency,
    )
    refreshed = build_execution_plan(
        intents=[capped_intent],
        markets={market.token_id: market},
        positions=positions,
        available_cash=cash,
        limits=limits,
    )
    if not refreshed.orders:
        reason = refreshed.skipped[0].code if refreshed.skipped else "unknown"
        raise ExecutionError(f"intent {original.intent_index} became non-executable: {reason}")
    current = refreshed.orders[0]
    current = replace(current, intent_index=original.intent_index)
    if current.quantity > original.quantity:
        raise UncertainExecutionError("fresh plan would increase the approved quantity")
    if current.side == "buy" and current.limit_price > original.limit_price:
        raise UncertainExecutionError("fresh plan would increase the approved buy price")
    if current.side == "sell" and current.limit_price < original.limit_price:
        raise UncertainExecutionError("fresh plan would decrease the approved sell price")
    if current.side == "buy" and current.worst_case_cash_effect > original.worst_case_cash_effect:
        raise UncertainExecutionError("fresh plan would increase the protected buy cash limit")
    if current.side == "sell" and current.worst_case_cash_effect < original.worst_case_cash_effect:
        raise UncertainExecutionError("fresh plan would decrease protected sell proceeds")
    return current, market


def _assert_allowance(
    adapter: LivePolymarketAdapter,
    order: PlannedOrder,
    market: MarketSnapshot,
) -> None:
    if order.side == "buy":
        allowance = adapter.balance_allowance(asset_type="COLLATERAL")
        required = order.worst_case_cash_effect
    else:
        allowance = adapter.balance_allowance(
            asset_type="CONDITIONAL", token_id=order.token_id
        )
        required = order.quantity
        if decimal_value(allowance["balance"], "conditional balance") < required:
            raise ExecutionError("fresh outcome-token balance is smaller than the sell order")
    if required_exchange_allowance(market, allowance) < required:
        raise ExecutionError(
            "preexisting exchange allowance is insufficient; scheduled execution will not approve"
        )


def _cap_to_signed_market_quantity(
    order: PlannedOrder,
    signed_quantity: Decimal,
    limits: RiskLimits,
) -> PlannedOrder:
    """Reconcile an SDK-rounded FOK share quantity with the protected plan."""

    if signed_quantity > order.quantity:
        raise UncertainExecutionError(
            "signed market order exceeds the protected planned quantity"
        )
    if signed_quantity == order.quantity:
        return order
    if signed_quantity <= ZERO:
        raise ExecutionError("SDK-rounded market order has no executable quantity")
    reduction_bps = int(
        (
            (order.intent.quantity - signed_quantity)
            / order.intent.quantity
            * Decimal("10000")
        ).to_integral_value(rounding=ROUND_UP)
    )
    if reduction_bps > limits.max_quantity_reduction_bps:
        raise ExecutionError(
            "SDK market-order rounding exceeds the configured quantity reduction limit"
        )

    remaining = signed_quantity
    fills: list[PlannedFill] = []
    for fill in order.fills:
        if remaining <= ZERO:
            break
        quantity = min(fill.quantity, remaining)
        fee_per_share = fill.fee / fill.quantity
        fills.append(
            PlannedFill(
                price=fill.price,
                quantity=quantity,
                gross_notional=quantity * fill.price,
                fee=quantity * fee_per_share,
            )
        )
        remaining -= quantity
    if remaining > ZERO:
        raise UncertainExecutionError(
            "SDK-rounded market quantity exceeds the simulated executable book"
        )

    gross = sum((fill.gross_notional for fill in fills), ZERO)
    fee = sum((fill.fee for fill in fills), ZERO)
    limit_gross = signed_quantity * order.limit_price
    maximum_fee_per_share = order.worst_case_fee / order.quantity
    worst_fee = signed_quantity * maximum_fee_per_share
    return replace(
        order,
        quantity=signed_quantity,
        gross_notional=gross,
        fee=fee,
        cash_effect=gross + fee if order.side == "buy" else gross - fee,
        limit_gross_notional=limit_gross,
        worst_case_fee=worst_fee,
        worst_case_cash_effect=(
            limit_gross + worst_fee if order.side == "buy" else limit_gross - worst_fee
        ),
        vwap=gross / signed_quantity,
        quantity_reduction_bps=reduction_bps,
        fills=tuple(fills),
    )


def _trade_ids_from_order(raw: dict[str, Any] | None) -> set[str]:
    if not raw:
        return set()
    values = raw.get("associate_trades", ())
    return {str(value) for value in values if str(value)} if isinstance(values, (list, tuple)) else set()


def _wait_balances(
    *,
    adapter: LivePolymarketAdapter,
    token_id: str,
    before_token: Decimal,
    before_cash: Decimal,
    side: str,
    timeout_seconds: int,
) -> tuple[Decimal, Decimal]:
    deadline = time.monotonic() + timeout_seconds
    latest = (before_token, before_cash)
    while True:
        latest = (adapter.conditional_balance(token_id), adapter.collateral_balance())
        token_changed = latest[0] > before_token if side == "buy" else latest[0] < before_token
        cash_changed = latest[1] < before_cash if side == "buy" else latest[1] > before_cash
        if (token_changed and cash_changed) or time.monotonic() >= deadline:
            return latest
        time.sleep(2)


def _wait_trades(
    *,
    adapter: LivePolymarketAdapter,
    trade_ids: set[str],
    timeout_seconds: int,
) -> tuple[dict[str, Any], ...]:
    if not trade_ids:
        return ()
    deadline = time.monotonic() + timeout_seconds
    latest: tuple[dict[str, Any], ...] = ()
    while True:
        latest = adapter.account_trades(trade_ids)
        statuses = {str(item.get("status", "")) for item in latest}
        if len(latest) == len(trade_ids) and statuses.issubset({"CONFIRMED", "FAILED"}):
            return latest
        if time.monotonic() >= deadline:
            raise UncertainExecutionError("one or more CLOB trades did not reach a terminal state")
        time.sleep(2)


def _verify_fill(
    *,
    order: PlannedOrder,
    before_token: Decimal,
    before_cash: Decimal,
    after_token: Decimal,
    after_cash: Decimal,
    trades: Iterable[dict[str, Any]],
    order_id: str,
    transaction_hashes: Iterable[str],
) -> dict[str, Any]:
    token_delta = after_token - before_token
    cash_delta = after_cash - before_cash
    filled = token_delta if order.side == "buy" else -token_delta
    if filled < ZERO:
        raise UncertainExecutionError("outcome-token balance moved opposite to the submitted order")
    if filled > order.quantity + SHARE_QUANTUM:
        raise UncertainExecutionError("filled quantity exceeds the protected order quantity")

    filtered: list[dict[str, Any]] = []
    trade_quantity = ZERO
    trade_gross = ZERO
    nonterminal: list[str] = []
    failed: list[str] = []
    for trade in trades:
        if str(trade.get("token_id", trade.get("asset_id", ""))) != order.token_id:
            continue
        quantity = decimal_value(trade.get("size"), "trade size", positive=True)
        price = decimal_value(trade.get("price"), "trade price", positive=True)
        status = str(trade.get("status", ""))
        trade_id = str(trade.get("id", ""))
        if status == "FAILED":
            failed.append(trade_id)
            continue
        if status != "CONFIRMED":
            nonterminal.append(trade_id)
        trade_side = str(trade.get("side", "")).lower()
        if trade_side and trade_side != order.side:
            raise UncertainExecutionError("a matching trade has the opposite account side")
        maker_orders = trade.get("maker_orders", ())
        maker_ids = {
            str(item.get("order_id", ""))
            for item in maker_orders
            if isinstance(item, dict)
        } if isinstance(maker_orders, (list, tuple)) else set()
        if str(trade.get("taker_order_id", "")) != order_id and order_id not in maker_ids:
            raise UncertainExecutionError("a matching trade is not linked to the submitted order")
        if order.side == "buy" and price > order.limit_price:
            raise UncertainExecutionError("a buy fill exceeded the signed limit price")
        if order.side == "sell" and price < order.limit_price:
            raise UncertainExecutionError("a sell fill was below the signed limit price")
        trade_quantity += quantity
        trade_gross += quantity * price
        filtered.append(
            {"id": trade_id, "status": status, "price": decimal_string(price), "size": decimal_string(quantity)}
        )
    if nonterminal:
        raise UncertainExecutionError(
            "trade settlement is still non-terminal: " + ", ".join(nonterminal)
        )
    if filled > ZERO and trade_quantity == ZERO:
        raise UncertainExecutionError("account balance changed but no matching CLOB trade was found")
    if abs(trade_quantity - filled) > SHARE_QUANTUM:
        raise UncertainExecutionError("CLOB trade quantity and outcome-token balance delta disagree")

    if order.side == "buy" and cash_delta > ZERO:
        raise UncertainExecutionError("collateral balance increased during a buy")
    if order.side == "sell" and cash_delta < ZERO:
        raise UncertainExecutionError("collateral balance decreased during a sell")
    cash_effect = -cash_delta if order.side == "buy" else cash_delta
    if filled > ZERO and cash_effect <= ZERO:
        raise UncertainExecutionError("a filled order has no matching collateral movement")
    if filled == ZERO and abs(cash_delta) > Decimal("0.01"):
        raise UncertainExecutionError("collateral changed even though the order had no fill")
    protected_for_fill = order.worst_case_cash_effect * filled / order.quantity
    if order.side == "buy" and cash_effect > protected_for_fill + Decimal("0.01"):
        raise UncertainExecutionError("actual buy cash exceeded the protected plan cap")
    if order.side == "sell" and cash_effect + Decimal("0.01") < protected_for_fill:
        raise UncertainExecutionError("actual sell proceeds fell below the protected plan floor")

    if filled == ZERO:
        status = "NO_FILL"
        vwap = ZERO
    elif filled + SHARE_QUANTUM < order.quantity:
        status = "PARTIAL"
        vwap = trade_gross / trade_quantity
    else:
        status = "FULL"
        vwap = trade_gross / trade_quantity
    return {
        "intent_index": order.intent_index,
        "order_id": order_id,
        "market_id": order.market_id,
        "token_id": order.token_id,
        "side": order.side,
        "order_type": "FOK",
        "execution_style": "protected_market",
        "status": status,
        "planned_quantity": decimal_string(order.quantity),
        "filled_quantity": decimal_string(filled),
        "planned_limit_price": decimal_string(order.limit_price),
        "planned_cash_limit": decimal_string(order.worst_case_cash_effect),
        "actual_vwap": decimal_string(vwap),
        "collateral_effect": decimal_string(cash_effect),
        "before_token_balance": decimal_string(before_token),
        "after_token_balance": decimal_string(after_token),
        "before_collateral_balance": decimal_string(before_cash),
        "after_collateral_balance": decimal_string(after_cash),
        "failed_trade_ids": failed,
        "transaction_hashes": list(transaction_hashes),
        "trades": filtered,
    }


def _definitive_no_fill_receipt(
    *,
    adapter: LivePolymarketAdapter,
    order: PlannedOrder,
    before_token: Decimal,
    before_cash: Decimal,
) -> dict[str, Any]:
    """Prove an SDK FOK rejection did not change the account."""

    after_token = adapter.conditional_balance(order.token_id)
    after_cash = adapter.collateral_balance()
    if abs(after_token - before_token) > SHARE_QUANTUM:
        raise UncertainExecutionError(
            "outcome-token balance changed after a definitive FOK no-fill response"
        )
    if abs(after_cash - before_cash) > Decimal("0.01"):
        raise UncertainExecutionError(
            "collateral balance changed after a definitive FOK no-fill response"
        )
    if adapter.open_orders():
        raise UncertainExecutionError(
            "account has an open order after a definitive FOK no-fill response"
        )
    return {
        "intent_index": order.intent_index,
        "order_id": None,
        "market_id": order.market_id,
        "token_id": order.token_id,
        "side": order.side,
        "order_type": "FOK",
        "execution_style": "protected_market",
        "status": "NO_FILL",
        "code": "FOK_NOT_FILLED",
        "message": "protected FOK order was killed without a fill",
        "planned_quantity": decimal_string(order.quantity),
        "filled_quantity": "0",
        "planned_limit_price": decimal_string(order.limit_price),
        "planned_cash_limit": decimal_string(order.worst_case_cash_effect),
        "actual_vwap": "0",
        "collateral_effect": "0",
        "before_token_balance": decimal_string(before_token),
        "after_token_balance": decimal_string(after_token),
        "before_collateral_balance": decimal_string(before_cash),
        "after_collateral_balance": decimal_string(after_cash),
        "failed_trade_ids": [],
        "transaction_hashes": [],
        "trades": [],
    }


def _definitive_rejection_receipt(
    *,
    adapter: LivePolymarketAdapter,
    order: PlannedOrder,
    before_token: Decimal,
    before_cash: Decimal,
) -> dict[str, Any]:
    """Prove a server-side order rejection did not change the account."""

    after_token = adapter.conditional_balance(order.token_id)
    after_cash = adapter.collateral_balance()
    if abs(after_token - before_token) > SHARE_QUANTUM:
        raise UncertainExecutionError(
            "outcome-token balance changed after a definitive CLOB rejection"
        )
    if abs(after_cash - before_cash) > Decimal("0.01"):
        raise UncertainExecutionError(
            "collateral balance changed after a definitive CLOB rejection"
        )
    if adapter.open_orders():
        raise UncertainExecutionError(
            "account has an open order after a definitive CLOB rejection"
        )
    return {
        "intent_index": order.intent_index,
        "order_id": None,
        "market_id": order.market_id,
        "token_id": order.token_id,
        "side": order.side,
        "order_type": "FOK",
        "execution_style": "protected_market",
        "status": "REJECTED",
        "code": "CLOB_REJECTED",
        "message": "CLOB definitively rejected the protected FOK order",
        "planned_quantity": decimal_string(order.quantity),
        "filled_quantity": "0",
        "planned_limit_price": decimal_string(order.limit_price),
        "planned_cash_limit": decimal_string(order.worst_case_cash_effect),
        "actual_vwap": "0",
        "collateral_effect": "0",
        "before_token_balance": decimal_string(before_token),
        "after_token_balance": decimal_string(after_token),
        "before_collateral_balance": decimal_string(before_cash),
        "after_collateral_balance": decimal_string(after_cash),
        "failed_trade_ids": [],
        "transaction_hashes": [],
        "trades": [],
    }


def execute_orders(
    *,
    adapter: LivePolymarketAdapter,
    orders: Iterable[PlannedOrder],
    limits: RiskLimits,
    ledger: DynamoLedger | SQLiteLedger,
    settlement_timeout_seconds: int = 90,
    expected_account: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    pre_execution_cancellation: dict[str, Any] | None = None,
    execution_id: str,
    execution_claim: str,
    planning: dict[str, Any],
) -> dict[str, Any]:
    orders = tuple(orders)
    receipts: list[dict[str, Any]] = []
    uncertain = False
    account_may_have_changed = False
    try:
        health = adapter.health()
        if health["closed_only"] and any(order.side == "buy" for order in orders):
            raise ExecutionError("account entered closed-only mode before submission")
        if expected_account is not None:
            actual = {
                "wallet": adapter.wallet,
                "signer": adapter.signer,
                "wallet_type": adapter.wallet_type,
            }
            if any(
                str(actual.get(key, "")).casefold()
                != str(expected_account.get(key, "")).casefold()
                for key in actual
            ):
                raise ExecutionError("execution credentials differ from the preflight account")
        for original in orders:
            ledger.renew()
            if expires_at is not None and datetime.now(timezone.utc) > expires_at:
                raise ExecutionError("execution plan expired before the next order")
            previous = ledger.get(ledger.order_key(original.intent_index))
            if previous and previous.get("state") == "VERIFIED":
                receipt = json.loads(str(previous["receipt"]))
                receipts.append(receipt)
                if receipt.get("status") not in {"FULL", "NO_FILL"}:
                    raise ExecutionError(
                        f"intent {original.intent_index} previously ended as "
                        f"{receipt.get('status', 'UNKNOWN')}; later orders remain stopped"
                    )
                continue

            if previous and previous.get("state") == "SIGNED":
                raise UncertainExecutionError(
                    "a signed order exists without a durable CLOB response; automated replay "
                    "could duplicate an order"
                )
            if previous and previous.get("state") == "SUBMITTED":
                current = PlannedOrder.from_dict(json.loads(str(previous["plan"])))
                if (
                    current.intent_index != original.intent_index
                    or current.token_id != original.token_id
                    or current.quantity > original.quantity
                ):
                    raise UncertainExecutionError("stored execution plan differs from the approved plan")
                before_token = Decimal(previous["before_token_balance"])
                before_cash = Decimal(previous["before_collateral_balance"])
            elif previous is not None:
                raise UncertainExecutionError(
                    f"intent {original.intent_index} has unsupported ledger state "
                    f"{previous.get('state', '<missing>')}"
                )
            else:
                latest_health = adapter.health()
                if latest_health["closed_only"] and original.side == "buy":
                    raise ExecutionError("account entered closed-only mode before order signing")
                current, market = _replan_one(adapter=adapter, original=original, limits=limits)
                _assert_allowance(adapter, current, market)
                before_token = adapter.conditional_balance(current.token_id)
                before_cash = adapter.collateral_balance()
                newly_signed = adapter.create_market_order(
                    token_id=current.token_id,
                    price_limit=current.limit_price,
                    quantity=current.quantity,
                    side=current.side,
                    max_buy_spend=(
                        current.worst_case_cash_effect if current.side == "buy" else None
                    ),
                )
                current = _cap_to_signed_market_quantity(
                    current,
                    adapter.signed_market_order_quantity(
                        newly_signed, side=current.side
                    ),
                    limits,
                )
                if current.quantity < market.min_order_size:
                    raise ExecutionError(
                        "SDK-rounded market quantity is below the live minimum order size"
                    )
                if (
                    current.side == "buy"
                    and current.limit_gross_notional
                    < POLYMARKET_MIN_MARKETABLE_BUY_AMOUNT
                ):
                    raise ExecutionError(
                        "SDK-rounded marketable BUY amount is below the Polymarket CLOB $1 minimum"
                    )
                ledger.save_signed_order(
                    current.intent_index,
                    newly_signed,
                    current.to_dict(),
                    before_token_balance=before_token,
                    before_collateral_balance=before_cash,
                )

            previous = ledger.get(ledger.order_key(current.intent_index))
            post_response: dict[str, Any]
            response: Any | None = None
            if previous and previous.get("state") == "SUBMITTED":
                post_response = json.loads(str(previous["post_response"]))
                account_may_have_changed = True
            else:
                account_may_have_changed = True
                try:
                    response = adapter.post_order(newly_signed)
                except OrderNotFilledError:
                    receipt = _definitive_no_fill_receipt(
                        adapter=adapter,
                        order=current,
                        before_token=before_token,
                        before_cash=before_cash,
                    )
                    ledger.mark_order_terminal(current.intent_index, receipt)
                    receipts.append(receipt)
                    account_may_have_changed = False
                    continue
                except OrderRejectedError as exc:
                    receipt = _definitive_rejection_receipt(
                        adapter=adapter,
                        order=current,
                        before_token=before_token,
                        before_cash=before_cash,
                    )
                    ledger.mark_order_terminal(current.intent_index, receipt)
                    receipts.append(receipt)
                    account_may_have_changed = False
                    raise ExecutionError(
                        f"intent {current.intent_index} was definitively rejected by the CLOB"
                    ) from exc
                except Exception as exc:
                    raise UncertainExecutionError(
                        "CLOB submission did not return a definitive response"
                    ) from exc
                post_response = _model_dict(response)
                if post_response.get("ok") is not True:
                    account_may_have_changed = False
                    receipt = {
                        "intent_index": current.intent_index,
                        "status": "REJECTED",
                        "code": str(post_response.get("code", "unknown")),
                        "message": str(post_response.get("message", "order rejected")),
                    }
                    ledger.mark_order_terminal(current.intent_index, receipt)
                    receipts.append(receipt)
                    raise ExecutionError(
                        f"intent {current.intent_index} was rejected: {receipt['code']}"
                    )
                ledger.mark_submitted(current.intent_index, post_response)

            order_id = str(post_response.get("order_id", ""))
            if not order_id:
                uncertain = True
                raise UncertainExecutionError("accepted order has no order id")
            trade_ids = {str(item) for item in post_response.get("trade_ids", []) if str(item)}
            order_record = adapter.order(order_id)
            trade_ids.update(_trade_ids_from_order(order_record))
            open_order = adapter.order_if_open(order_id)
            if open_order is not None:
                cancel = adapter.cancel_order(order_id)
                not_canceled = cancel.get("not_canceled", {})
                if order_id in not_canceled and adapter.order_if_open(order_id) is not None:
                    uncertain = True
                    raise UncertainExecutionError(f"could not cancel unfilled order {order_id}")
            if adapter.order_if_open(order_id) is not None:
                uncertain = True
                raise UncertainExecutionError(f"order {order_id} remains open after cancellation")

            transaction_hashes: tuple[str, ...] = ()
            if trade_ids:
                # Reconstructing an AcceptedOrder after process recovery is not
                # necessary: account trades below are independently polled.
                if response is not None and getattr(response, "ok", False):
                    transaction_hashes = adapter.wait_for_settlement(
                        response, timeout_seconds=settlement_timeout_seconds
                    )
            trades = _wait_trades(
                adapter=adapter,
                trade_ids=trade_ids,
                timeout_seconds=settlement_timeout_seconds,
            )
            after_token, after_cash = _wait_balances(
                adapter=adapter,
                token_id=current.token_id,
                before_token=before_token,
                before_cash=before_cash,
                side=current.side,
                timeout_seconds=settlement_timeout_seconds,
            )
            receipt = _verify_fill(
                order=current,
                before_token=before_token,
                before_cash=before_cash,
                after_token=after_token,
                after_cash=after_cash,
                trades=trades,
                order_id=order_id,
                transaction_hashes=transaction_hashes,
            )
            ledger.mark_order_terminal(current.intent_index, receipt)
            receipts.append(receipt)
            account_may_have_changed = False
            if receipt["status"] != "FULL":
                raise ExecutionError(
                    f"intent {current.intent_index} ended as {receipt['status']}; "
                    "remaining orders were stopped"
                )
        warning_receipts = [
            receipt for receipt in receipts if receipt.get("status") != "FULL"
        ]
        summary = {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "COMPLETED_WITH_WARNINGS" if warning_receipts else "COMPLETED",
            "execution_id": execution_id,
            "claim": execution_claim,
            "planning": planning,
            "pre_execution_cancellation": pre_execution_cancellation or {},
            "receipts": receipts,
            "warnings": [
                {
                    "intent_index": receipt.get("intent_index"),
                    "status": receipt.get("status"),
                    "code": receipt.get("code"),
                    "message": receipt.get("message"),
                }
                for receipt in warning_receipts
            ],
        }
        ledger.complete(summary)
        return summary
    except UncertainExecutionError:
        uncertain = True
        summary = {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "UNCERTAIN",
            "execution_id": execution_id,
            "claim": execution_claim,
            "planning": planning,
            "pre_execution_cancellation": pre_execution_cancellation or {},
            "receipts": receipts,
        }
        ledger.fail(summary, uncertain=True)
        raise
    except Exception:
        uncertain = uncertain or account_may_have_changed
        summary = {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "UNCERTAIN" if uncertain else "FAILED",
            "execution_id": execution_id,
            "claim": execution_claim,
            "planning": planning,
            "pre_execution_cancellation": pre_execution_cancellation or {},
            "receipts": receipts,
        }
        ledger.fail(summary, uncertain=uncertain)
        raise


def verify_receipts(
    *, adapter: LivePolymarketAdapter, summary: dict[str, Any]
) -> dict[str, Any]:
    health = adapter.health()
    remaining_open_orders = adapter.open_orders()
    if remaining_open_orders:
        raise UncertainExecutionError(
            "account has open orders after protected market execution"
        )
    cancellations = summary.get("pre_execution_cancellation")
    if not isinstance(cancellations, dict) or cancellations.get(
        "remaining_open_order_count"
    ) != 0:
        raise ExecutionError(
            "execution receipt does not prove the pre-execution cancellation sweep"
        )
    receipts = summary.get("receipts")
    if not isinstance(receipts, list):
        raise ExecutionError("execution summary has no receipts list")
    checks: list[dict[str, Any]] = []
    expected_final_cash: Decimal | None = None
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("status") not in {
            "FULL",
            "NO_FILL",
        }:
            raise ExecutionError(
                "post-execution verification accepts only FULL or definitive NO_FILL receipts"
            )
        status = str(receipt["status"])
        order_id = str(receipt.get("order_id") or "")
        if status == "FULL":
            if not order_id or adapter.order_if_open(order_id) is not None:
                raise UncertainExecutionError(
                    f"order {order_id or '<missing>'} is still open"
                )
        else:
            if receipt.get("code") != "FOK_NOT_FILLED":
                raise ExecutionError("a definitive NO_FILL receipt has no FOK kill proof")
            if order_id:
                raise ExecutionError("a definitive NO_FILL receipt must not contain an order id")
            if decimal_value(receipt.get("filled_quantity"), "no-fill quantity") != ZERO:
                raise ExecutionError("a definitive NO_FILL receipt contains a fill")
            if decimal_value(receipt.get("collateral_effect"), "no-fill cash effect") != ZERO:
                raise ExecutionError("a definitive NO_FILL receipt changed collateral")
            if receipt.get("trades") or receipt.get("transaction_hashes"):
                raise ExecutionError("a definitive NO_FILL receipt contains trade evidence")
            before_token = decimal_value(
                receipt.get("before_token_balance"), "no-fill prior token balance"
            )
            after_token = decimal_value(
                receipt.get("after_token_balance"), "no-fill final token balance"
            )
            before_cash = decimal_value(
                receipt.get("before_collateral_balance"), "no-fill prior collateral balance"
            )
            after_cash = decimal_value(
                receipt.get("after_collateral_balance"), "no-fill final collateral balance"
            )
            if abs(after_token - before_token) > SHARE_QUANTUM:
                raise ExecutionError("a definitive NO_FILL receipt changed token balance")
            if abs(after_cash - before_cash) > Decimal("0.01"):
                raise ExecutionError("a definitive NO_FILL receipt changed collateral balance")
        token_id = str(receipt.get("token_id", ""))
        if not token_id:
            raise ExecutionError("execution receipt is missing its outcome token id")
        expected_token = decimal_value(
            receipt.get("after_token_balance"), "receipt outcome-token balance"
        )
        actual_token = adapter.conditional_balance(token_id)
        if abs(actual_token - expected_token) > SHARE_QUANTUM:
            raise UncertainExecutionError(
                f"post-execution position changed for token {token_id}"
            )
        expected_final_cash = decimal_value(
            receipt.get("after_collateral_balance"), "receipt collateral balance"
        )
        checks.append(
            {
                "order_id": order_id or None,
                "token_id": token_id,
                "status": status,
                "open": False,
                "position_balance": decimal_string(actual_token),
            }
        )
    if expected_final_cash is not None:
        actual_cash = adapter.collateral_balance()
        if abs(actual_cash - expected_final_cash) > Decimal("0.01"):
            raise UncertainExecutionError("post-execution collateral balance changed")
    return {
        "schema_version": "1.0",
        "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "health": health,
        "open_order_count": 0,
        "orders": checks,
    }
