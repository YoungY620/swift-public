from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "automation" / "run_scheduled_scan.py"
SPEC = importlib.util.spec_from_file_location("run_scheduled_scan", MODULE_PATH)
assert SPEC and SPEC.loader
scheduled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduled)


class ScheduledScanTests(unittest.TestCase):
    def test_render_requires_known_placeholders(self) -> None:
        template = " ".join("{{" + name + "}}" for name in scheduled.PLACEHOLDERS)
        values = {name: name.lower() for name in scheduled.PLACEHOLDERS}
        rendered = scheduled.render_prompt(template, values)
        self.assertNotIn("{{", rendered)

        with self.assertRaises(scheduled.ScheduleError):
            scheduled.render_prompt(template + " {{UNKNOWN}}", values)

    def test_runner_receives_private_temporary_prompt_and_it_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "runner.py"
            result = root / "result.json"
            helper.write_text(
                "import json, pathlib, sys\n"
                "p = pathlib.Path(sys.argv[-1])\n"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({'path': str(p), 'text': p.read_text()}))\n",
                encoding="utf-8",
            )
            code = scheduled.run_agent(f"{sys.executable} {helper} {result}", "private prompt")
            self.assertEqual(code, 0)
            captured = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(captured["text"], "private prompt")
            self.assertFalse(Path(captured["path"]).exists())

    def test_main_dispatches_minimal_prompt_without_public_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "runner.py"
            result = root / "prompt.txt"
            helper.write_text(
                "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text())\n",
                encoding="utf-8",
            )
            runner = f"{sys.executable} {helper} {result}"
            with patch.dict(os.environ, {"FORECAST_EDGE_RUNNER": runner}, clear=False):
                code = scheduled.main(
                    [
                        "--repository-root",
                        str(ROOT),
                        "--account",
                        "https://polymarket.com/@public-profile",
                        "--output-root",
                        str(root / "reports"),
                    ]
                )
            self.assertEqual(code, 0)
            prompt = result.read_text(encoding="utf-8")
            self.assertIn("https://polymarket.com/@public-profile", prompt)
            self.assertIn("DISABLED", prompt)
            self.assertNotIn("{{", prompt)

    def test_account_must_be_a_polymarket_profile(self) -> None:
        self.assertEqual(
            scheduled.validate_account_profile("https://polymarket.com/@public-profile"),
            "https://polymarket.com/@public-profile",
        )
        with self.assertRaisesRegex(scheduled.ScheduleError, "profile URL"):
            scheduled.validate_account_profile("https://example.org/@public-profile")


if __name__ == "__main__":
    unittest.main()
