from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OcmemogContinuityBenchmarkTests(unittest.TestCase):
    def test_benchmark_fixture_meets_continuity_bar(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "ocmemog-continuity-benchmark.py"
        fixture_path = repo_root / "tests" / "fixtures" / "continuity_benchmark.json"
        with tempfile.TemporaryDirectory() as tempdir:
            report_path = Path(tempdir) / "continuity-report.json"
            proc = subprocess.run(
                [sys.executable, str(script_path), "--fixture", str(fixture_path), "--report", str(report_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                self.fail(f"benchmark failed with code {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["overall_score"], report["continuity_bar"])
        self.assertEqual(report["scenario_count"], 2)
        self.assertTrue(all(item["ok"] for item in report["scenarios"]))


if __name__ == "__main__":
    unittest.main()
