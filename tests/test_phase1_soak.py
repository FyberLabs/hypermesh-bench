#!/usr/bin/env python3
"""phase1_soak --stub dry-run + validation skip. No GPU."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
sys.path.insert(0, str(HARNESS))

from check_scorecard import check_file, load_json  # noqa: E402
from phase1_soak import EXIT_SETUP, EXIT_SKIP, main as phase1_main  # noqa: E402


class Phase1SoakTest(unittest.TestCase):
    def test_stub_writes_schema_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = phase1_main(
                [
                    "--stub",
                    "--model-id",
                    "llama-3.1-8b-q4",
                    "--pack",
                    str(ROOT / "packs" / "thin-v1"),
                    "--out",
                    str(out),
                    "--models-dir",
                    str(Path(tmp) / "models"),
                    "--device-id",
                    "f6124d28-772c-4f1f-8e03-1a7a17724381",
                    "--loader",
                    "gguf",
                ]
            )
            self.assertEqual(code, 0)
            dest = out / "llama-3.1-8b-q4"
            scorecard_path = dest / "scorecard.json"
            job_path = dest / "job_result.json"
            self.assertTrue(scorecard_path.is_file())
            self.assertTrue(job_path.is_file())
            for name in (
                "host_probe.json",
                "llama-bench.json",
                "ttft_hot.json",
                "identity.json",
                "mem_after_load.json",
                "power.json",
                "reliability.json",
                "check_path_b.txt",
            ):
                self.assertTrue((dest / name).is_file(), name)

            schema = load_json(ROOT / "schemas" / "scorecard.schema.json")
            errors = check_file(scorecard_path, schema, path_b=False)
            self.assertEqual(errors, [])

            card = json.loads(scorecard_path.read_text(encoding="utf-8"))
            inf = card["inference_sustained"]
            self.assertIsNone(inf["ttft_ms_p50_after_throttle"])
            self.assertIsNone(inf["decode_tok_s_p50_after_throttle"])
            self.assertIsNone(inf["prefill_tok_s_p50"])
            self.assertIsNone(card["identity"]["image_hash"])
            self.assertIsNone(card["identity"]["artifact_hash"])
            self.assertIsNot(card["run"]["passed"], True)
            self.assertEqual(card["run"]["device_id"], "f6124d28-772c-4f1f-8e03-1a7a17724381")

            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertIs(job["passed"], False)
            self.assertIsNone(job["image_hash"])
            self.assertIn("passed", job)
            self.assertIn("image_hash", job)

            power = json.loads((dest / "power.json").read_text(encoding="utf-8"))
            self.assertIsNone(power["idle_w"])
            self.assertIsNone(power["load_w"])

            ttft = json.loads((dest / "ttft_hot.json").read_text(encoding="utf-8"))
            self.assertIsNone(ttft["ttft_ms_p50"])

    def test_validation_denied_exits_4(self) -> None:
        env = os.environ.copy()
        env["HM_VALIDATION_WINDOW"] = "denied"
        proc = subprocess.run(
            [
                sys.executable,
                str(HARNESS / "phase1_soak.py"),
                "--stub",
                "--out",
                "/tmp/hm-skip-should-not-write",
            ],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, EXIT_SKIP)

    def test_lease_stop_is_rejected(self) -> None:
        code = phase1_main(["--stub", "--job-kind", "lease_stop", "--out", "/tmp/x"])
        self.assertEqual(code, EXIT_SETUP)

    def test_execute_fails_closed_on_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = phase1_main(["--out", tmp])
        self.assertEqual(code, EXIT_SETUP)

    def test_script_without_stub_fails_closed(self) -> None:
        script = ROOT / "scripts" / "phase1_soak.sh"
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [str(script), "--out", tmp],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, EXIT_SETUP)
        self.assertIn("preflight", (proc.stderr + proc.stdout).lower())


if __name__ == "__main__":
    unittest.main()
