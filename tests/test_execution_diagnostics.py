from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_polymarket_execution_diagnostics.py"
VERIFIER = ROOT / "scripts" / "verify_polymarket_execution_diagnostics.py"


class ExecutionDiagnosticTests(unittest.TestCase):
    def fixture(self) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp())
        runtime = root / "runtime"
        runtime.mkdir()
        secret_file = root / "secrets.json"
        secret_file.write_text(
            json.dumps(
                {
                    "signer_private_key": "0x" + "a1" * 32,
                    "clob_api_key": "diagnostic-test-api-key",
                    "clob_api_secret": "diagnostic-test-api-secret",
                    "clob_api_passphrase": "diagnostic-test-passphrase",
                }
            ),
            encoding="utf-8",
        )
        intent_one = {
            "schema_version": "1.0",
            "venue": "polymarket",
            "multi_market_identifier": "0x" + "a" * 64,
            "instrument_identifier": "111",
            "outcome": "yes",
            "side": "sell",
            "reference_price": "0.5",
            "notional": "5",
            "quantity": "10",
            "currency": "pUSD",
        }
        intent_two = {**intent_one, "instrument_identifier": "222", "side": "buy"}
        (runtime / "trade-intents.jsonl").write_text(
            "\n".join(json.dumps(item) for item in (intent_one, intent_two)) + "\n",
            encoding="utf-8",
        )
        (runtime / "validated-intents.jsonl").write_text(
            (runtime / "trade-intents.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        plan = {
            "schema_version": "1.0",
            "execution_id": "execution-1",
            "created_at": "2026-08-20T00:00:00Z",
            "orders": [
                {
                    "intent_index": 1,
                    "intent": intent_one,
                    "token_id": "111",
                    "side": "sell",
                }
            ],
            "skipped": [
                {
                    "intent_index": 2,
                    "code": "insufficient_cash",
                    "message": "reserve protected",
                    "intent": intent_two,
                }
            ],
        }
        (runtime / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        receipt = {
            "schema_version": "1.0",
            "execution_id": "execution-1",
            "status": "COMPLETED",
            "receipts": [
                {
                    "intent_index": 1,
                    "status": "FULL",
                    "side": "sell",
                    "token_id": "111",
                    "order_id": "order-1",
                }
            ],
        }
        (runtime / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        (runtime / "preflight.json").write_text(
            json.dumps({"open_order_count": 0}), encoding="utf-8"
        )
        (runtime / "verification.json").write_text(
            json.dumps({"open_order_count": 0, "orders": []}), encoding="utf-8"
        )
        (runtime / "phase-events.ndjson").write_text(
            json.dumps(
                {
                    "phase": "execute",
                    "started_at": "2026-08-20T00:00:00Z",
                    "completed_at": "2026-08-20T00:00:01Z",
                    "exit_code": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        ledger = root / "ledger.sqlite3"
        connection = sqlite3.connect(ledger)
        connection.execute("CREATE TABLE ledger_entries (pk TEXT PRIMARY KEY, document TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO ledger_entries VALUES (?, ?)",
            (
                "RUN#execution-1",
                json.dumps(
                    {
                        "state": "COMPLETED",
                        "artifact_sha256": "b" * 64,
                        "planned_notional": "5",
                        "summary": json.dumps(receipt),
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO ledger_entries VALUES (?, ?)",
            (
                "ORDER#execution-1#0001",
                json.dumps(
                    {
                        "intent_index": 1,
                        "state": "VERIFIED",
                        "reconciliation_observation": json.dumps(
                            {
                                "order_id": "order-1",
                                "order_status": "MATCHED",
                                "matching_trades": [{"id": "trade-1"}],
                            }
                        ),
                        "signed_order": "must-never-be-exported",
                        "signed_order_digest": "c" * 64,
                        "receipt": json.dumps(receipt["receipts"][0]),
                    }
                ),
            ),
        )
        connection.commit()
        connection.close()
        return runtime, ledger, secret_file

    def build_command(
        self, runtime: Path, ledger: Path, secret_file: Path
    ) -> list[str]:
        output = runtime / "diagnostics-package"
        archive = runtime / "execution-diagnostics.tar.gz"
        return [
            sys.executable,
            str(BUILDER),
            "--runtime-dir",
            str(runtime),
            "--ledger-path",
            str(ledger),
            "--secret-file",
            str(secret_file),
            "--execution-id",
            "execution-1",
            "--repository",
            "owner/private-fork",
            "--run-id",
            "123",
            "--run-attempt",
            "1",
            "--commit-sha",
            "d" * 40,
            "--trade-intents-sha256",
            "b" * 64,
            "--output-directory",
            str(output),
            "--archive",
            str(archive),
        ]

    def build(self, runtime: Path, ledger: Path, secret_file: Path) -> tuple[Path, Path]:
        output = runtime / "diagnostics-package"
        archive = runtime / "execution-diagnostics.tar.gz"
        subprocess.run(
            self.build_command(runtime, ledger, secret_file),
            check=True,
            capture_output=True,
            text=True,
        )
        return output, archive

    def test_builds_traceable_secret_free_bundle_and_verifies_it(self) -> None:
        runtime, ledger, secret_file = self.fixture()
        output, archive = self.build(runtime, ledger, secret_file)
        summary = json.loads((output / "diagnostic-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["source_intent_count"], 2)
        self.assertEqual(summary["planned_order_count"], 1)
        self.assertEqual(summary["skipped_intent_count"], 1)
        self.assertEqual(summary["intent_trace"][1]["skip_code"], "insufficient_cash")
        ledger_snapshot = json.loads((output / "ledger-snapshot.json").read_text(encoding="utf-8"))
        serialized_ledger = json.dumps(ledger_snapshot)
        self.assertNotIn("must-never-be-exported", serialized_ledger)
        self.assertNotIn('"signed_order":', serialized_ledger)
        self.assertIn("signed_order_digest", serialized_ledger)
        self.assertEqual(
            ledger_snapshot["orders"][0]["reconciliation_observation"][
                "matching_trades"
            ][0]["id"],
            "trade-1",
        )
        all_bytes = b"".join(path.read_bytes() for path in output.iterdir() if path.is_file())
        self.assertNotIn(b"diagnostic-test-api-secret", all_bytes)

        checksum = runtime / "execution-diagnostics.sha256"
        checksum.write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  execution-diagnostics.tar.gz\n",
            encoding="utf-8",
        )
        destination = runtime / "verified"
        verification = runtime / "verification-result.json"
        subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--archive",
                str(archive),
                "--checksum",
                str(checksum),
                "--summary",
                str(output / "diagnostic-summary.json"),
                "--manifest",
                str(output / "diagnostic-manifest.json"),
                "--expected-run-id",
                "123",
                "--destination",
                str(destination),
                "--output",
                str(verification),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(json.loads(verification.read_text(encoding="utf-8"))["artifact_verified"])

    def test_fails_closed_if_a_known_credential_leaks_into_a_log(self) -> None:
        runtime, ledger, secret_file = self.fixture()
        (runtime / "execute-command.log").write_text(
            "diagnostic-test-api-secret", encoding="utf-8"
        )
        completed = subprocess.run(
            self.build_command(runtime, ledger, secret_file),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("credential value detected", completed.stderr)

    def test_fails_closed_if_raw_signature_or_labeled_secret_enters_a_log(self) -> None:
        protected_values = (
            "signed_order=0x" + "ab" * 65,
            'response={"seed_phrase":"one two three four five six seven eight nine ten eleven twelve"}',
            "Cookie: session=protected-value",
        )
        for protected_value in protected_values:
            with self.subTest(protected_value=protected_value[:24]):
                runtime, ledger, secret_file = self.fixture()
                (runtime / "execute-command.log").write_text(
                    protected_value, encoding="utf-8"
                )
                completed = subprocess.run(
                    self.build_command(runtime, ledger, secret_file),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("secret-like material detected", completed.stderr)


if __name__ == "__main__":
    unittest.main()
