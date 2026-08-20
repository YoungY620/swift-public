from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_public_mirror.py"
SPEC = importlib.util.spec_from_file_location("build_public_mirror", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PUBLIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLIC)


class PublicMirrorTests(unittest.TestCase):
    def test_keeps_workflows_and_redacts_deployment_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "example.yml").write_text(
                "runs-on: eu-north-1\npath: /home/ubuntu/poly-edge-live\n",
                encoding="utf-8",
            )
            PUBLIC._sanitize_text(root)
            self.assertTrue(workflows.exists())
            text = (workflows / "example.yml").read_text(encoding="utf-8")
            self.assertIn("deployment-region", text)
            self.assertIn("/srv/poly-edge", text)

    def test_rejects_operator_identity_and_allows_public_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "safe.md").write_text(
                "https://polymarket.com/@public-user\n"
                "0x1111111111111111111111111111111111111111\n"
                "secret@example.com\n",
                encoding="utf-8",
            )
            PUBLIC._scan_public_tree(root)
            (root / "unsafe.md").write_text(
                "https://polymarket.com/@operator-account\n",
                encoding="utf-8",
            )
            with self.assertRaises(PUBLIC.PublicMirrorError):
                PUBLIC._scan_public_tree(root)

    def test_decimal_precision_is_not_mistaken_for_an_aws_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "math.py").write_text(
                'value = Decimal("1.000000000000")\n', encoding="utf-8"
            )
            PUBLIC._scan_public_tree(root)
            (root / "config.txt").write_text(
                "AWS account id: 123456789012\n", encoding="utf-8"
            )
            with self.assertRaises(PUBLIC.PublicMirrorError):
                PUBLIC._scan_public_tree(root)


if __name__ == "__main__":
    unittest.main()
