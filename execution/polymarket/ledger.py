"""Small durable state machine for the dedicated EC2 executor."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


class LedgerError(RuntimeError):
    """Raised when an execution lease or idempotency invariant fails."""


class UnresolvedExecutionsError(LedgerError):
    """A prior batch must be reconciled before a new batch can start."""

    def __init__(self, execution_ids: list[str]) -> None:
        self.execution_ids = tuple(execution_ids)
        visible = ", ".join(execution_ids[:3])
        if len(execution_ids) > 3:
            visible += f", and {len(execution_ids) - 3} more"
        super().__init__(
            "unresolved prior execution prevents a new batch: " + visible
        )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ExecutionLedger:
    """One SQLite ledger shared by every run on the dedicated EC2 host.

    ``SIGNED`` means a POST may or may not have happened, ``SUBMITTED`` has a
    durable CLOB order id, and ``VERIFIED`` has a terminal receipt.  Only a
    submitted order is automatically reconcilable: replaying a merely signed
    order could duplicate it.
    """

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
            raise LedgerError(f"could not open execution ledger: {exc}") from exc
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise LedgerError("execution ledger is not a regular file")
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise LedgerError("execution ledger must use mode 0600")
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
            raise LedgerError(f"could not initialize execution ledger: {exc}") from exc

    @property
    def run_key(self) -> str:
        return f"RUN#{self.execution_id}"

    def order_key(self, intent_index: int) -> str:
        return f"ORDER#{self.execution_id}#{intent_index:04d}"

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            yield cursor
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

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
            raise LedgerError(f"ledger entry {key} is corrupt") from exc
        if not isinstance(value, dict):
            raise LedgerError(f"ledger entry {key} is not an object")
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
            raise LedgerError(f"could not read ledger entry {key}: {exc}") from exc

    def claim_run(
        self,
        *,
        artifact_sha256: str,
        planned_notional: Decimal,
        daily_notional_limit: Decimal,
        execution_plan: dict[str, Any] | None = None,
        lease_seconds: int = 900,
    ) -> str:
        """Return ``new``, ``resumed``, ``reconcile``, or ``completed``."""

        now = int(time.time())
        try:
            with self._transaction() as cursor:
                existing = self._load(cursor, self.run_key)
                if existing is not None:
                    if existing.get("artifact_sha256") != artifact_sha256:
                        raise LedgerError(
                            "execution id is already bound to another artifact digest"
                        )
                    if existing.get("state") == "COMPLETED":
                        return "completed"
                    if execution_plan is not None and not existing.get("execution_plan"):
                        raise LedgerError("existing execution has no durable execution plan")
                    lease_until = int(existing.get("lease_until", 0))
                    if lease_until > now and existing.get("lease_owner") != self.lease_owner:
                        raise LedgerError("another runner holds the execution lease")
                    reconciliation_only = bool(
                        existing.get("reconciliation_only")
                        or existing.get("state") == "UNCERTAIN"
                    )
                    claim = "reconcile" if reconciliation_only else "resumed"
                    existing.update(
                        {
                            "state": "IN_PROGRESS",
                            "reconciliation_only": reconciliation_only,
                            "lease_owner": self.lease_owner,
                            "lease_until": now + lease_seconds,
                            "updated_at": now,
                        }
                    )
                    self._store(cursor, self.run_key, existing)
                    return claim

                unresolved = []
                for (key,) in cursor.execute(
                    "SELECT pk FROM ledger_entries "
                    "WHERE pk LIKE 'RUN#%' AND pk != ? ORDER BY pk",
                    (self.run_key,),
                ):
                    prior = self._load(cursor, str(key)) or {}
                    state = prior.get("state")
                    safely_terminal = state == "COMPLETED" or (
                        state == "FAILED" and not prior.get("reconciliation_only")
                    )
                    if not safely_terminal:
                        unresolved.append(str(key).removeprefix("RUN#"))
                if unresolved:
                    raise UnresolvedExecutionsError(unresolved)

                if planned_notional > daily_notional_limit:
                    raise LedgerError("planned notional exceeds the UTC daily limit")
                day_key = f"DAY#{time.strftime('%Y-%m-%d', time.gmtime(now))}"
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
                run = {
                    "artifact_sha256": artifact_sha256,
                    "state": "IN_PROGRESS",
                    "reconciliation_only": False,
                    "lease_owner": self.lease_owner,
                    "lease_until": now + lease_seconds,
                    "planned_notional": str(planned_notional),
                    "created_at": now,
                    "updated_at": now,
                }
                if execution_plan is not None:
                    run.update(
                        {
                            "execution_plan": canonical_json(execution_plan),
                            "execution_plan_digest": payload_digest(execution_plan),
                        }
                    )
                self._store(
                    cursor,
                    self.run_key,
                    run,
                )
                return "new"
        except LedgerError:
            raise
        except (sqlite3.Error, ValueError) as exc:
            raise LedgerError(f"execution claim failed: {exc}") from exc

    def load_execution_plan(self) -> dict[str, Any]:
        run = self.get(self.run_key) or {}
        try:
            plan = json.loads(str(run["execution_plan"]))
        except (KeyError, json.JSONDecodeError) as exc:
            raise LedgerError("execution has no readable durable plan") from exc
        if not isinstance(plan, dict) or payload_digest(plan) != run.get(
            "execution_plan_digest"
        ):
            raise LedgerError("durable execution plan failed its digest")
        return plan

    def renew(self, lease_seconds: int = 900) -> None:
        now = int(time.time())
        try:
            with self._transaction() as cursor:
                existing = self._load(cursor, self.run_key)
                if (
                    existing is None
                    or existing.get("lease_owner") != self.lease_owner
                    or existing.get("state") != "IN_PROGRESS"
                ):
                    raise LedgerError("execution lease is not owned by this runner")
                existing.update({"lease_until": now + lease_seconds, "updated_at": now})
                self._store(cursor, self.run_key, existing)
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerError(f"could not renew execution lease: {exc}") from exc

    def save_signed_order(
        self,
        intent_index: int,
        order: Any,
        plan: dict[str, Any],
        *,
        before_token_balance: Decimal,
        before_collateral_balance: Decimal,
    ) -> dict[str, Any]:
        payload = {str(key): value for key, value in asdict(order).items()}
        key = self.order_key(intent_index)
        try:
            with self._transaction() as cursor:
                existing = self._load(cursor, key)
                if existing is not None:
                    if existing.get("plan_digest") != payload_digest(plan):
                        raise LedgerError(
                            f"intent {intent_index} is already bound to another plan"
                        )
                    stored = json.loads(str(existing.get("signed_order", "{}")))
                    if payload_digest(stored) != existing.get("signed_order_digest"):
                        raise LedgerError(
                            f"intent {intent_index} stored signed order failed its digest"
                        )
                    return stored
                self._store(
                    cursor,
                    key,
                    {
                        "run_key": self.run_key,
                        "intent_index": intent_index,
                        "state": "SIGNED",
                        "plan_digest": payload_digest(plan),
                        "plan": canonical_json(plan),
                        "signed_order": canonical_json(payload),
                        "signed_order_digest": payload_digest(payload),
                        "before_token_balance": str(before_token_balance),
                        "before_collateral_balance": str(before_collateral_balance),
                        "updated_at": int(time.time()),
                    },
                )
                return payload
        except LedgerError:
            raise
        except (json.JSONDecodeError, sqlite3.Error) as exc:
            raise LedgerError(f"could not persist signed order {intent_index}: {exc}") from exc

    def mark_submitted(self, intent_index: int, response: dict[str, Any]) -> int:
        order_id = str(response.get("order_id", ""))
        if not order_id:
            raise LedgerError("accepted CLOB response has no order id")
        submitted_at = int(time.time())
        self._update_order(
            intent_index,
            "SUBMITTED",
            {
                "order_id": order_id,
                "post_response": canonical_json(response),
                "submitted_at": submitted_at,
            },
            expected_states=("SIGNED",),
        )
        return submitted_at

    def mark_order_terminal(self, intent_index: int, receipt: dict[str, Any]) -> None:
        self._update_order(
            intent_index,
            "VERIFIED",
            {"receipt": canonical_json(receipt)},
            expected_states=("SIGNED", "SUBMITTED"),
        )

    def record_reconciliation_observation(
        self, intent_index: int, observation: dict[str, Any]
    ) -> None:
        """Keep the latest secret-free evidence for a submitted order."""

        self._update_order(
            intent_index,
            "SUBMITTED",
            {"reconciliation_observation": canonical_json(observation)},
            expected_states=("SUBMITTED",),
        )

    def _update_order(
        self,
        intent_index: int,
        state: str,
        values: dict[str, Any],
        *,
        expected_states: tuple[str, ...],
    ) -> None:
        key = self.order_key(intent_index)
        try:
            with self._transaction() as cursor:
                existing = self._load(cursor, key)
                if existing is None or existing.get("state") not in expected_states:
                    raise LedgerError(f"order {intent_index} is not in an expected state")
                existing.update(values)
                existing.update({"state": state, "updated_at": int(time.time())})
                self._store(cursor, key, existing)
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerError(f"could not persist {state} for intent {intent_index}: {exc}") from exc

    def complete(self, summary: dict[str, Any]) -> None:
        self._finish("COMPLETED", summary)

    def fail(self, summary: dict[str, Any], *, uncertain: bool) -> None:
        self._finish("UNCERTAIN" if uncertain else "FAILED", summary)

    def _finish(self, state: str, summary: dict[str, Any]) -> None:
        try:
            with self._transaction() as cursor:
                existing = self._load(cursor, self.run_key)
                if existing is None or existing.get("lease_owner") != self.lease_owner:
                    raise LedgerError("execution run is not owned by this runner")
                now = int(time.time())
                existing.update(
                    {
                        "state": state,
                        "reconciliation_only": state == "UNCERTAIN",
                        "summary": canonical_json(summary),
                        "updated_at": now,
                        "lease_until": now,
                    }
                )
                self._store(cursor, self.run_key, existing)
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerError(f"could not finalize execution as {state}: {exc}") from exc
