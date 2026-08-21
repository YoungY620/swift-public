from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "classify_kimi_failure.py"
spec = importlib.util.spec_from_file_location("classify_kimi_failure", MODULE_PATH)
assert spec is not None and spec.loader is not None
classifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classifier)


class KimiFailureClassifierTests(unittest.TestCase):
    def test_usage_limit_is_not_retryable(self) -> None:
        result = classifier.classify_failure(
            "provider.auth_error: 403 You've reached your usage limit for this billing cycle"
        )
        self.assertFalse(result["retryable"])
        self.assertEqual(result["category"], "AUTH_OR_QUOTA")
        self.assertNotIn("403", result["summary"])

    def test_invalid_key_is_not_retryable(self) -> None:
        result = classifier.classify_failure("invalid API key")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["category"], "AUTH_OR_QUOTA")

    def test_transient_provider_error_can_repair(self) -> None:
        result = classifier.classify_failure("provider connection reset by peer")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["category"], "TRANSIENT_OR_AGENT")


if __name__ == "__main__":
    unittest.main()
