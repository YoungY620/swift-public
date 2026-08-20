from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_research_iteration.py"
SPEC = importlib.util.spec_from_file_location("validate_research_iteration", MODULE_PATH)
assert SPEC and SPEC.loader
iteration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(iteration)


class ResearchIterationTests(unittest.TestCase):
    def arguments(self, root: Path, enabled: str = "polymarket,kalshi") -> argparse.Namespace:
        reports_root = root / "archive"
        reports_root.mkdir()
        baseline = root / "baseline.txt"
        baseline.write_text("", encoding="utf-8")
        trade_intents = root / "trade-intents.jsonl"
        trade_intents.write_text("", encoding="utf-8")
        placeholders = {}
        for name in ("trade_schema", "account_snapshots_manifest", "adapter_registry"):
            path = root / f"{name}.json"
            path.write_text("{}\n", encoding="utf-8")
            placeholders[name] = path
        return argparse.Namespace(
            reports_root=reports_root,
            baseline_reports=baseline,
            enabled_venues=enabled,
            trade_intents=trade_intents,
            trade_schema=placeholders["trade_schema"],
            account_snapshots_manifest=placeholders["account_snapshots_manifest"],
            adapter_registry=placeholders["adapter_registry"],
            current_reports_manifest=root / "current-reports.json",
            report_validation=root / "report-validation.json",
            validation_summary=root / "validation-summary.json",
            report_tool=ROOT / "skills" / "forecast-edge" / "scripts" / "report_tool.py",
            trade_validator=ROOT / "scripts" / "validate_trade_intents.py",
        )

    @staticmethod
    def add_report(root: Path, directory: str, adapter: str) -> Path:
        report_dir = root / "reports" / directory
        report_dir.mkdir(parents=True)
        report = report_dir / "report.json"
        report.write_text(
            json.dumps(
                {
                    "schema_version": "3.0",
                    "run": {"account": {"adapter": adapter}},
                }
            ),
            encoding="utf-8",
        )
        # report_tool.py archive always renders report.md next to report.json;
        # fixtures must mirror that delivery contract.
        (report_dir / "report.md").write_text("# report\n", encoding="utf-8")
        return report

    def test_accepts_exactly_one_report_for_each_enabled_venue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments(Path(temporary))
            self.add_report(args.reports_root, "run-poly", "polymarket")
            self.add_report(args.reports_root, "run-kalshi", "kalshi")

            def fake_run(command: list[str], label: str) -> str:
                if label == "trade-intent validation":
                    self.assertEqual(command.count("--report"), 2)
                    return '{"items": 0, "venues": {}}\n'
                return '{"valid": true}\n'

            with patch.object(iteration, "run_checked", side_effect=fake_run):
                summary = iteration.validate(args)

            self.assertEqual(summary, {"items": 0, "venues": {}})
            manifest = json.loads(args.current_reports_manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                {row["adapter"] for row in manifest["reports"]},
                {"polymarket", "kalshi"},
            )

    def test_rejects_duplicate_report_for_one_venue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments(Path(temporary))
            self.add_report(args.reports_root, "run-a", "polymarket")
            self.add_report(args.reports_root, "run-b", "polymarket")

            with self.assertRaisesRegex(iteration.IterationError, "multiple current reports"):
                iteration.validate(args)

    def test_rejects_missing_venue_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments(Path(temporary))
            self.add_report(args.reports_root, "run-poly", "polymarket")

            with self.assertRaisesRegex(iteration.IterationError, "expected 2"):
                iteration.validate(args)

    def test_rejects_report_without_sibling_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments(Path(temporary), enabled="polymarket")
            report = self.add_report(args.reports_root, "run-poly", "polymarket")
            (report.parent / "report.md").unlink()

            with self.assertRaisesRegex(iteration.IterationError, "sibling report.md"):
                iteration.validate(args)

    def test_snapshot_bundle_digest_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "polymarket.json"
            snapshot.write_text('{"adapter":"polymarket"}\n', encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "snapshots": {"polymarket": str(snapshot)},
                    }
                ),
                encoding="utf-8",
            )
            expected = iteration.account_snapshot_bundle_sha256(manifest)
            snapshot.write_text('{"adapter":"tampered"}\n', encoding="utf-8")

            self.assertNotEqual(iteration.account_snapshot_bundle_sha256(manifest), expected)


if __name__ == "__main__":
    unittest.main()
