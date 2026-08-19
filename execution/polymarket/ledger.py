"""DynamoDB-backed idempotency and daily-risk ledger.

An ephemeral runner is intentionally stateless.  The tiny DynamoDB table is the
only durable execution state: it prevents a GitHub rerun from placing the same
logical batch twice and preserves signed order payloads across the narrow crash
window between signing and receiving a CLOB response.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    """Raised when an execution lease or idempotency invariant fails."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def signed_order_payload(order: Any) -> dict[str, Any]:
    raw = asdict(order)
    return {str(key): value for key, value in raw.items()}


def restore_signed_order(payload: dict[str, Any]) -> Any:
    try:
        from polymarket import SignedOrder
    except ImportError as exc:  # pragma: no cover - deployment configuration error.
        raise LedgerError("polymarket-client is required to restore a signed order") from exc
    return SignedOrder(**payload)


class DynamoLedger:
    def __init__(self, *, table_name: str, execution_id: str, lease_owner: str) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - deployment configuration error.
            raise LedgerError("boto3 is required for the execution ledger") from exc
        self.table_name = table_name
        self.execution_id = execution_id
        self.lease_owner = lease_owner
        self.client = boto3.client("dynamodb")

    @property
    def run_key(self) -> str:
        return f"RUN#{self.execution_id}"

    def order_key(self, intent_index: int) -> str:
        return f"ORDER#{self.execution_id}#{intent_index:04d}"

    def get(self, key: str) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"pk": {"S": key}},
            ConsistentRead=True,
        )
        raw = response.get("Item")
        if not isinstance(raw, dict):
            return None
        result: dict[str, Any] = {}
        for name, value in raw.items():
            if "S" in value:
                result[name] = value["S"]
            elif "N" in value:
                result[name] = Decimal(value["N"])
            elif "BOOL" in value:
                result[name] = value["BOOL"]
        return result

    def claim_run(
        self,
        *,
        artifact_sha256: str,
        planned_notional: Decimal,
        daily_notional_limit: Decimal,
        lease_seconds: int = 900,
    ) -> str:
        now = int(time.time())
        existing = self.get(self.run_key)
        if existing is not None:
            if existing.get("artifact_sha256") != artifact_sha256:
                raise LedgerError("execution id is already bound to another artifact digest")
            if existing.get("state") == "COMPLETED":
                return "completed"
            if existing.get("state") == "UNCERTAIN":
                raise LedgerError(
                    "execution is UNCERTAIN and requires manual reconciliation before retry"
                )
            lease_until = int(existing.get("lease_until", 0))
            if lease_until > now and existing.get("lease_owner") != self.lease_owner:
                raise LedgerError("another runner holds the unexpired execution lease")
            try:
                self.client.update_item(
                    TableName=self.table_name,
                    Key={"pk": {"S": self.run_key}},
                    UpdateExpression=(
                        "SET lease_owner = :owner, lease_until = :until, #state = :state, "
                        "updated_at = :now"
                    ),
                    ConditionExpression="artifact_sha256 = :digest AND lease_until <= :now",
                    ExpressionAttributeNames={"#state": "state"},
                    ExpressionAttributeValues={
                        ":owner": {"S": self.lease_owner},
                        ":until": {"N": str(now + lease_seconds)},
                        ":state": {"S": "IN_PROGRESS"},
                        ":now": {"N": str(now)},
                        ":digest": {"S": artifact_sha256},
                    },
                )
            except Exception as exc:
                raise LedgerError(f"could not recover the expired execution lease: {exc}") from exc
            return "resumed"

        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if planned_notional > daily_notional_limit:
            raise LedgerError("planned notional exceeds the full UTC daily limit")
        remaining_before_reservation = daily_notional_limit - planned_notional
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "pk": {"S": self.run_key},
                                "artifact_sha256": {"S": artifact_sha256},
                                "state": {"S": "IN_PROGRESS"},
                                "lease_owner": {"S": self.lease_owner},
                                "lease_until": {"N": str(now + lease_seconds)},
                                "planned_notional": {"N": str(planned_notional)},
                                "created_at": {"N": str(now)},
                                "updated_at": {"N": str(now)},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": {"pk": {"S": f"DAY#{day}"}},
                            "UpdateExpression": (
                                "SET updated_at = :now ADD reserved_notional :amount"
                            ),
                            "ConditionExpression": (
                                "attribute_not_exists(reserved_notional) OR "
                                "reserved_notional <= :remaining"
                            ),
                            "ExpressionAttributeValues": {
                                ":amount": {"N": str(planned_notional)},
                                ":remaining": {"N": str(remaining_before_reservation)},
                                ":now": {"N": str(now)},
                            },
                        }
                    },
                ]
            )
        except Exception as exc:
            raise LedgerError(f"execution claim or daily notional reservation failed: {exc}") from exc
        return "new"

    def renew(self, lease_seconds: int = 900) -> None:
        now = int(time.time())
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"pk": {"S": self.run_key}},
                UpdateExpression="SET lease_until = :until, updated_at = :now",
                ConditionExpression="lease_owner = :owner AND #state = :state",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":owner": {"S": self.lease_owner},
                    ":state": {"S": "IN_PROGRESS"},
                    ":until": {"N": str(now + lease_seconds)},
                    ":now": {"N": str(now)},
                },
            )
        except Exception as exc:
            raise LedgerError(f"execution lease renewal failed: {exc}") from exc

    def save_signed_order(
        self,
        intent_index: int,
        order: Any,
        plan: dict[str, Any],
        *,
        before_token_balance: Decimal,
        before_collateral_balance: Decimal,
    ) -> dict[str, Any]:
        payload = signed_order_payload(order)
        serialized = canonical_json(payload)
        digest = payload_digest(payload)
        serialized_plan = canonical_json(plan)
        plan_digest = payload_digest(plan)
        now = int(time.time())
        key = self.order_key(intent_index)
        existing = self.get(key)
        if existing is not None:
            if existing.get("plan_digest") != plan_digest:
                raise LedgerError(f"intent {intent_index} is already bound to another plan")
            stored = json.loads(str(existing.get("signed_order", "{}")))
            if payload_digest(stored) != str(existing.get("signed_order_digest", "")):
                raise LedgerError(f"intent {intent_index} stored signed order failed its digest")
            return stored
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={
                    "pk": {"S": key},
                    "run_key": {"S": self.run_key},
                    "intent_index": {"N": str(intent_index)},
                    "state": {"S": "SIGNED"},
                    "plan_digest": {"S": plan_digest},
                    "plan": {"S": serialized_plan},
                    "signed_order": {"S": serialized},
                    "signed_order_digest": {"S": digest},
                    "before_token_balance": {"N": str(before_token_balance)},
                    "before_collateral_balance": {"N": str(before_collateral_balance)},
                    "updated_at": {"N": str(now)},
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except Exception as exc:
            raise LedgerError(f"could not persist signed order for intent {intent_index}: {exc}") from exc
        return payload

    def mark_submitted(self, intent_index: int, response: dict[str, Any]) -> None:
        order_id = str(response.get("order_id", ""))
        if not order_id:
            raise LedgerError("accepted CLOB response has no order id")
        self._update_order(
            intent_index,
            "SUBMITTED",
            {
                "order_id": order_id,
                "post_response": canonical_json(response),
            },
            expected_states=("SIGNED",),
        )

    def mark_order_terminal(self, intent_index: int, receipt: dict[str, Any]) -> None:
        self._update_order(
            intent_index,
            "VERIFIED",
            {"receipt": canonical_json(receipt)},
            expected_states=("SIGNED", "SUBMITTED"),
        )

    def _update_order(
        self,
        intent_index: int,
        state: str,
        values: dict[str, str],
        *,
        expected_states: tuple[str, ...],
    ) -> None:
        names = {"#state": "state"}
        expressions = ["#state = :state", "updated_at = :now"]
        attributes: dict[str, dict[str, str]] = {
            ":state": {"S": state},
            ":now": {"N": str(int(time.time()))},
        }
        for index, (name, value) in enumerate(values.items()):
            alias = f"#v{index}"
            token = f":v{index}"
            names[alias] = name
            attributes[token] = {"S": value}
            expressions.append(f"{alias} = {token}")
        expected_tokens: list[str] = []
        for index, expected in enumerate(expected_states):
            token = f":expected{index}"
            attributes[token] = {"S": expected}
            expected_tokens.append(token)
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"pk": {"S": self.order_key(intent_index)}},
                UpdateExpression="SET " + ", ".join(expressions),
                ConditionExpression=(
                    "attribute_exists(pk) AND #state IN (" + ", ".join(expected_tokens) + ")"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=attributes,
            )
        except Exception as exc:
            raise LedgerError(f"could not persist {state} for intent {intent_index}: {exc}") from exc

    def complete(self, summary: dict[str, Any]) -> None:
        self._finish("COMPLETED", summary)

    def fail(self, summary: dict[str, Any], *, uncertain: bool) -> None:
        self._finish("UNCERTAIN" if uncertain else "FAILED", summary)

    def _finish(self, state: str, summary: dict[str, Any]) -> None:
        now = int(time.time())
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"pk": {"S": self.run_key}},
                UpdateExpression=(
                    "SET #state = :state, summary = :summary, updated_at = :now, "
                    "lease_until = :now"
                ),
                ConditionExpression="lease_owner = :owner",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":state": {"S": state},
                    ":summary": {"S": canonical_json(summary)},
                    ":now": {"N": str(now)},
                    ":owner": {"S": self.lease_owner},
                },
            )
        except Exception as exc:
            raise LedgerError(f"could not finalize execution ledger as {state}: {exc}") from exc


class SQLiteLedger:
    """Persistent single-host ledger for a dedicated long-running executor."""

    def __init__(self, *, path: Path, execution_id: str, lease_owner: str) -> None:
        self.path = path
        self.execution_id = execution_id
        self.lease_owner = lease_owner
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise LedgerError(f"could not open local execution ledger: {exc}") from exc
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise LedgerError("local execution ledger is not a regular file")
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise LedgerError("local execution ledger must use mode 0600")
        finally:
            os.close(descriptor)
        try:
            self.connection = sqlite3.connect(path, timeout=30, isolation_level=None)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA busy_timeout=30000")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS ledger_entries "
                "(pk TEXT PRIMARY KEY NOT NULL, document TEXT NOT NULL)"
            )
        except sqlite3.Error as exc:
            raise LedgerError(f"could not initialize local execution ledger: {exc}") from exc

    @property
    def run_key(self) -> str:
        return f"RUN#{self.execution_id}"

    def order_key(self, intent_index: int) -> str:
        return f"ORDER#{self.execution_id}#{intent_index:04d}"

    @staticmethod
    def _load(cursor: sqlite3.Cursor, key: str) -> dict[str, Any] | None:
        row = cursor.execute(
            "SELECT document FROM ledger_entries WHERE pk = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row[0]))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"local ledger entry {key} is corrupt") from exc
        if not isinstance(value, dict):
            raise LedgerError(f"local ledger entry {key} is not an object")
        return value

    @staticmethod
    def _store(cursor: sqlite3.Cursor, key: str, value: dict[str, Any]) -> None:
        cursor.execute(
            "INSERT INTO ledger_entries(pk, document) VALUES(?, ?) "
            "ON CONFLICT(pk) DO UPDATE SET document = excluded.document",
            (key, canonical_json(value)),
        )

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            return self._load(self.connection.cursor(), key)
        except sqlite3.Error as exc:
            raise LedgerError(f"could not read local ledger entry {key}: {exc}") from exc

    def claim_run(
        self,
        *,
        artifact_sha256: str,
        planned_notional: Decimal,
        daily_notional_limit: Decimal,
        lease_seconds: int = 900,
    ) -> str:
        now = int(time.time())
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._load(cursor, self.run_key)
            if existing is not None:
                if existing.get("artifact_sha256") != artifact_sha256:
                    raise LedgerError("execution id is already bound to another artifact digest")
                if existing.get("state") == "COMPLETED":
                    self.connection.commit()
                    return "completed"
                if existing.get("state") == "UNCERTAIN":
                    raise LedgerError(
                        "execution is UNCERTAIN and requires manual reconciliation before retry"
                    )
                lease_until = int(existing.get("lease_until", 0))
                if lease_until > now and existing.get("lease_owner") != self.lease_owner:
                    raise LedgerError("another runner holds the unexpired execution lease")
                existing.update(
                    {
                        "state": "IN_PROGRESS",
                        "lease_owner": self.lease_owner,
                        "lease_until": now + lease_seconds,
                        "updated_at": now,
                    }
                )
                self._store(cursor, self.run_key, existing)
                self.connection.commit()
                return "resumed"

            if planned_notional > daily_notional_limit:
                raise LedgerError("planned notional exceeds the full UTC daily limit")
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            day_key = f"DAY#{day}"
            day_entry = self._load(cursor, day_key) or {"reserved_notional": "0"}
            reserved = Decimal(str(day_entry.get("reserved_notional", "0")))
            if reserved + planned_notional > daily_notional_limit:
                raise LedgerError("daily notional reservation would exceed the UTC limit")
            day_entry.update(
                {
                    "reserved_notional": str(reserved + planned_notional),
                    "updated_at": now,
                }
            )
            self._store(cursor, day_key, day_entry)
            self._store(
                cursor,
                self.run_key,
                {
                    "artifact_sha256": artifact_sha256,
                    "state": "IN_PROGRESS",
                    "lease_owner": self.lease_owner,
                    "lease_until": now + lease_seconds,
                    "planned_notional": str(planned_notional),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            self.connection.commit()
            return "new"
        except LedgerError:
            self.connection.rollback()
            raise
        except (sqlite3.Error, ValueError) as exc:
            self.connection.rollback()
            raise LedgerError(f"local execution claim failed: {exc}") from exc

    def renew(self, lease_seconds: int = 900) -> None:
        now = int(time.time())
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._load(cursor, self.run_key)
            if (
                existing is None
                or existing.get("lease_owner") != self.lease_owner
                or existing.get("state") != "IN_PROGRESS"
            ):
                raise LedgerError("local execution lease is not owned by this runner")
            existing.update({"lease_until": now + lease_seconds, "updated_at": now})
            self._store(cursor, self.run_key, existing)
            self.connection.commit()
        except LedgerError:
            self.connection.rollback()
            raise
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise LedgerError(f"could not renew local execution lease: {exc}") from exc

    def save_signed_order(
        self,
        intent_index: int,
        order: Any,
        plan: dict[str, Any],
        *,
        before_token_balance: Decimal,
        before_collateral_balance: Decimal,
    ) -> dict[str, Any]:
        payload = signed_order_payload(order)
        plan_digest = payload_digest(plan)
        key = self.order_key(intent_index)
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._load(cursor, key)
            if existing is not None:
                if existing.get("plan_digest") != plan_digest:
                    raise LedgerError(f"intent {intent_index} is already bound to another plan")
                stored = json.loads(str(existing.get("signed_order", "{}")))
                if payload_digest(stored) != str(existing.get("signed_order_digest", "")):
                    raise LedgerError(
                        f"intent {intent_index} stored signed order failed its digest"
                    )
                self.connection.commit()
                return stored
            now = int(time.time())
            self._store(
                cursor,
                key,
                {
                    "run_key": self.run_key,
                    "intent_index": intent_index,
                    "state": "SIGNED",
                    "plan_digest": plan_digest,
                    "plan": canonical_json(plan),
                    "signed_order": canonical_json(payload),
                    "signed_order_digest": payload_digest(payload),
                    "before_token_balance": str(before_token_balance),
                    "before_collateral_balance": str(before_collateral_balance),
                    "updated_at": now,
                },
            )
            self.connection.commit()
            return payload
        except LedgerError:
            self.connection.rollback()
            raise
        except (json.JSONDecodeError, sqlite3.Error) as exc:
            self.connection.rollback()
            raise LedgerError(f"could not persist local signed order {intent_index}: {exc}") from exc

    def mark_submitted(self, intent_index: int, response: dict[str, Any]) -> None:
        order_id = str(response.get("order_id", ""))
        if not order_id:
            raise LedgerError("accepted CLOB response has no order id")
        self._update_order(
            intent_index,
            "SUBMITTED",
            {"order_id": order_id, "post_response": canonical_json(response)},
            expected_states=("SIGNED",),
        )

    def mark_order_terminal(self, intent_index: int, receipt: dict[str, Any]) -> None:
        self._update_order(
            intent_index,
            "VERIFIED",
            {"receipt": canonical_json(receipt)},
            expected_states=("SIGNED", "SUBMITTED"),
        )

    def _update_order(
        self,
        intent_index: int,
        state: str,
        values: dict[str, str],
        *,
        expected_states: tuple[str, ...],
    ) -> None:
        key = self.order_key(intent_index)
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._load(cursor, key)
            if existing is None or existing.get("state") not in expected_states:
                raise LedgerError(
                    f"local order {intent_index} is not in an expected state"
                )
            existing.update(values)
            existing.update({"state": state, "updated_at": int(time.time())})
            self._store(cursor, key, existing)
            self.connection.commit()
        except LedgerError:
            self.connection.rollback()
            raise
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise LedgerError(f"could not persist local {state} for intent {intent_index}: {exc}") from exc

    def complete(self, summary: dict[str, Any]) -> None:
        self._finish("COMPLETED", summary)

    def fail(self, summary: dict[str, Any], *, uncertain: bool) -> None:
        self._finish("UNCERTAIN" if uncertain else "FAILED", summary)

    def _finish(self, state: str, summary: dict[str, Any]) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._load(cursor, self.run_key)
            if existing is None or existing.get("lease_owner") != self.lease_owner:
                raise LedgerError("local execution run is not owned by this runner")
            now = int(time.time())
            existing.update(
                {
                    "state": state,
                    "summary": canonical_json(summary),
                    "updated_at": now,
                    "lease_until": now,
                }
            )
            self._store(cursor, self.run_key, existing)
            self.connection.commit()
        except LedgerError:
            self.connection.rollback()
            raise
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise LedgerError(f"could not finalize local execution as {state}: {exc}") from exc
