from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.polymarket.sdk_adapter import (
    AdapterError,
    Credentials,
    SIGNATURE_TYPE_BY_WALLET,
)
from scripts.bootstrap_polymarket_credentials import _write_exclusive


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execute-polymarket.yml"
WRAPPER = ROOT / "scripts" / "run_with_polymarket_secrets.py"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_polymarket_credentials.py"
EXECUTION_CLI = ROOT / "scripts" / "polymarket_execution.py"

CANONICAL = {
    "POLYMARKET_SIGNER_PRIVATE_KEY": "signer-secret",
    "POLYMARKET_ACCOUNT_WALLET": "0x" + "1" * 40,
    "POLYMARKET_CLOB_API_KEY": "clob-key",
    "POLYMARKET_CLOB_API_SECRET": "clob-secret",
    "POLYMARKET_CLOB_API_PASSPHRASE": "clob-passphrase",
    "POLYMARKET_EXPECTED_WALLET_TYPE": "POLY_PROXY",
}

class CredentialConfigurationTests(unittest.TestCase):
    def test_canonical_environment_supports_explicit_wallet_type(self) -> None:
        with patch.dict(os.environ, CANONICAL, clear=True):
            credentials = Credentials.from_env()
        self.assertEqual(credentials.account_wallet, CANONICAL["POLYMARKET_ACCOUNT_WALLET"])
        self.assertEqual(credentials.expected_wallet_type, "POLY_PROXY")
        self.assertEqual(credentials.clob_api_key, "clob-key")

    def test_wallet_type_is_case_normalized(self) -> None:
        environment = {**CANONICAL, "POLYMARKET_EXPECTED_WALLET_TYPE": "deposit_wallet"}
        with patch.dict(os.environ, environment, clear=True):
            credentials = Credentials.from_env()
        self.assertEqual(credentials.expected_wallet_type, "DEPOSIT_WALLET")

    def test_missing_canonical_environment_names_are_rejected(self) -> None:
        incomplete = dict(CANONICAL)
        incomplete.pop("POLYMARKET_SIGNER_PRIVATE_KEY")
        with patch.dict(os.environ, incomplete, clear=True):
            with self.assertRaisesRegex(AdapterError, "missing required secret/config"):
                Credentials.from_env()

    def test_unknown_wallet_type_is_rejected(self) -> None:
        environment = {**CANONICAL, "POLYMARKET_EXPECTED_WALLET_TYPE": "GOOGLE"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(AdapterError, "must be one of"):
                Credentials.from_env()

    def test_credentials_repr_redacts_all_secret_values(self) -> None:
        with patch.dict(os.environ, CANONICAL, clear=True):
            rendered = repr(Credentials.from_env())
        for secret in ("signer-secret", "clob-key", "clob-secret", "clob-passphrase"):
            self.assertNotIn(secret, rendered)

    def test_all_official_wallet_types_have_the_expected_signature_type(self) -> None:
        self.assertEqual(
            SIGNATURE_TYPE_BY_WALLET,
            {
                "EOA": 0,
                "POLY_PROXY": 1,
                "GNOSIS_SAFE": 2,
                "DEPOSIT_WALLET": 3,
            },
        )


class SecretWrapperTests(unittest.TestCase):
    def run_wrapper(self, document: dict[str, str]) -> subprocess.CompletedProcess[str]:
        directory = Path(tempfile.mkdtemp())
        path = directory / "secrets.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(0o600)
        probe = (
            "import json,os; "
            "print(json.dumps({k:os.environ[k] for k in "
            "['POLYMARKET_ACCOUNT_WALLET','POLYMARKET_EXPECTED_WALLET_TYPE']}))"
        )
        return subprocess.run(
            [sys.executable, str(WRAPPER), str(path), sys.executable, "-c", probe],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_wrapper_accepts_canonical_secret_document(self) -> None:
        result = self.run_wrapper(CANONICAL)
        self.assertEqual(result.returncode, 0, result.stderr)
        public = json.loads(result.stdout)
        self.assertEqual(public["POLYMARKET_EXPECTED_WALLET_TYPE"], "POLY_PROXY")
        self.assertNotIn("signer-secret", result.stdout)

    def test_wrapper_rejects_mixed_or_extra_key_sets(self) -> None:
        result = self.run_wrapper({**CANONICAL, "EXTRA": "unexpected"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("unexpected key set", result.stderr)

class BootstrapFileTests(unittest.TestCase):
    def test_bootstrap_file_is_mode_0600_and_never_overwrites(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "credentials.json"
        _write_exclusive(path, {"secret": "first"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            _write_exclusive(path, {"secret": "second"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"secret": "first"})

    def test_entrypoints_work_outside_the_repository_working_directory(self) -> None:
        outside = tempfile.mkdtemp()
        for script in (BOOTSTRAP, EXECUTION_CLI):
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=outside,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class ExecutionWorkflowAuthenticationTests(unittest.TestCase):
    def test_workflow_uses_only_server_local_polymarket_credentials(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for name in CANONICAL:
            self.assertNotIn(f"secrets.{name}", workflow)
        self.assertIn(
            "SERVER_SECRET_PATH: /home/ubuntu/poly-edge-live/runtime/secrets.json",
            workflow,
        )
        self.assertIn("scripts/run_with_polymarket_secrets.py", workflow)


if __name__ == "__main__":
    unittest.main()
