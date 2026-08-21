from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "extract_research_handoff_reports.py"
spec = importlib.util.spec_from_file_location("extract_research_handoff_reports", MODULE_PATH)
assert spec is not None and spec.loader is not None
handoff_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff_module)


class ResearchHandoffReportTests(unittest.TestCase):
    def _archive(self, root: Path, *, commit_sha: str = "a" * 40) -> Path:
        report_json = json.dumps({"schema_version": "1.0", "summary": "specific"}).encode()
        manifest = {
            "schema_version": "2.0",
            "repository": "owner/repo",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "commit_sha": commit_sha,
            "report_count": 1,
            "reports": [
                {
                    "adapter": "polymarket",
                    "json_asset": "report-polymarket.json",
                    "markdown_asset": "report-polymarket.md",
                    "sha256": hashlib.sha256(report_json).hexdigest(),
                }
            ],
        }
        archive_path = root / "handoff.tar.gz"
        with tarfile.open(archive_path, mode="w:gz") as archive:
            for name, data in (
                ("release-assets/research-release-manifest.json", json.dumps(manifest).encode()),
                ("release-assets/report-polymarket.json", report_json),
                ("release-assets/report-polymarket.md", b"# specific\n"),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return archive_path

    def test_extracts_only_identity_bound_report_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "reports"
            metadata = handoff_module.extract_reports(
                archive_path=self._archive(root),
                destination=destination,
                expected_repository="owner/repo",
                expected_run_id="123",
                expected_run_attempt="1",
                expected_commit_sha="a" * 40,
            )
            self.assertTrue(metadata["verified"])
            self.assertEqual(metadata["source"], "private_research_handoff")
            self.assertEqual(
                sorted(path.name for path in destination.iterdir()),
                [
                    "report-polymarket.json",
                    "report-polymarket.md",
                    "research-release-manifest.json",
                ],
            )

    def test_rejects_handoff_from_another_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(handoff_module.HandoffError, "commit_sha"):
                handoff_module.extract_reports(
                    archive_path=self._archive(root, commit_sha="b" * 40),
                    destination=root / "reports",
                    expected_repository="owner/repo",
                    expected_run_id="123",
                    expected_run_attempt="1",
                    expected_commit_sha="a" * 40,
                )

    def test_rejects_unsafe_archive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsafe_path = root / "unsafe.tar.gz"
            with tarfile.open(unsafe_path, mode="w:gz") as archive:
                data = b"unsafe"
                member = tarfile.TarInfo("../outside")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            with self.assertRaisesRegex(handoff_module.HandoffError, "unsafe path"):
                handoff_module.extract_reports(
                    archive_path=unsafe_path,
                    destination=root / "reports",
                    expected_repository="owner/repo",
                    expected_run_id="123",
                    expected_run_attempt="1",
                    expected_commit_sha="a" * 40,
                )


if __name__ == "__main__":
    unittest.main()
