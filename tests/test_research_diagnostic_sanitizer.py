from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "sanitize_research_diagnostics.py"
spec = importlib.util.spec_from_file_location("sanitize_research_diagnostics", MODULE_PATH)
assert spec is not None and spec.loader is not None
sanitizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sanitizer)


class ResearchDiagnosticSanitizerTests(unittest.TestCase):
    def test_redacts_exact_and_credential_shaped_values_then_reports_counts(self) -> None:
        root = Path(tempfile.mkdtemp())
        secret = b"protected-model-value"
        signature = b"0x" + b"ab" * 65
        private_key = (
            b"-----BEGIN PRIVATE KEY-----\nprotected body\n"
            b"-----END PRIVATE KEY-----"
        )
        source = root / "wire.jsonl"
        source.write_bytes(
            b"prefix "
            + secret
            + b" Authorization: Bearer protected-token password=protected-value "
            + signature
            + b" "
            + private_key
        )
        report_path = root / "redaction-report.json"
        report = sanitizer.sanitize_tree(root, [secret], report_path)
        sanitized = source.read_bytes()
        self.assertNotIn(secret, sanitized)
        self.assertNotIn(signature, sanitized)
        self.assertNotIn(b"protected-token", sanitized)
        self.assertNotIn(b"protected-value", sanitized)
        self.assertNotIn(b"BEGIN PRIVATE KEY", sanitized)
        self.assertTrue(report["secret_scan_passed"])
        self.assertEqual(report["sanitized_file_count"], 1)
        persisted = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted, report)
        self.assertNotIn(secret.decode(), report_path.read_text(encoding="utf-8"))

    def test_rejects_symlinks(self) -> None:
        root = Path(tempfile.mkdtemp())
        target = root / "target.log"
        target.write_text("safe", encoding="utf-8")
        (root / "link.log").symlink_to(target)
        with self.assertRaisesRegex(sanitizer.SanitizationError, "non-regular"):
            sanitizer.sanitize_tree(root, [b"protected"], root / "report.json")


if __name__ == "__main__":
    unittest.main()
