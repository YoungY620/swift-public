"""Live order lifecycle with bounded partial-fill handling."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from .ledger import DynamoLedger, SQLiteLedger
from .models import (
    SHARE_QUANTUM,
    ZERO,
    MarketSnapshot,
    PlannedOrder,
    RiskLimits,
    TradeIntent,
    decimal_string,
    decimal_value,
)
from .planner import build_execution_plan
from .sdk_adapter import (
    LivePolymarketAdapter,
    open_buy_order_reserve,
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
    if any(str(order.get("token_id", "")) == original.token_id for order in open_orders):
        raise ExecutionError("account acquired a target-token open order after preflight")
    market = adapter.market_snapshot(original.intent)
    positions = _position_map(adapter)
    cash = max(ZERO, adapter.collateral_balance() - open_buy_order_reserve(open_orders))
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
        "status": status,
        "planned_quantity": decimal_string(order.quantity),
        "filled_quantity": decimal_string(filled),
        "planned_limit_price": decimal_string(order.limit_price),
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


def execute_orders(
    *,
    adapter: LivePolymarketAdapter,
    orders: Iterable[PlannedOrder],
    limits: RiskLimits,
    ledger: DynamoLedger | SQLiteLedger,
    settlement_timeout_seconds: int = 90,
    expected_account: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
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
                if receipt.get("status") != "FULL":
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
                newly_signed = adapter.create_limit_order(
                    token_id=current.token_id,
                    price=current.limit_price,
                    quantity=current.quantity,
                    side=current.side,
                    expiration_epoch=int(time.time()) + limits.order_expiration_seconds,
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
        summary = {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "COMPLETED",
            "receipts": receipts,
        }
        ledger.complete(summary)
        return summary
    except UncertainExecutionError:
        uncertain = True
        summary = {
            "schema_version": "1.0",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "UNCERTAIN",
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
            "receipts": receipts,
        }
        ledger.fail(summary, uncertain=uncertain)
        raise


def verify_receipts(
    *, adapter: LivePolymarketAdapter, summary: dict[str, Any]
) -> dict[str, Any]:
    health = adapter.health()
    receipts = summary.get("receipts")
    if not isinstance(receipts, list):
        raise ExecutionError("execution summary has no receipts list")
    checks: list[dict[str, Any]] = []
    expected_final_cash: Decimal | None = None
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("status") != "FULL":
            raise ExecutionError("post-execution verification requires every receipt to be FULL")
        order_id = str(receipt.get("order_id", ""))
        if not order_id or adapter.order_if_open(order_id) is not None:
            raise UncertainExecutionError(f"order {order_id or '<missing>'} is still open")
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
                "order_id": order_id,
                "token_id": token_id,
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
        "orders": checks,
    }
