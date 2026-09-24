#!/usr/bin/env python3
"""run_one --execute uses a real binary when present; --stub stays null."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agx_facts import EnrolledHost  # noqa: E402
from run_one import run_one  # noqa: E402
from soak_gate import SoakRefused  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "llama-bench-sample.json"


class RunOneExecuteTest(unittest.TestCase):
    def test_execute_refuses_when_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            with self.assertRaises(SoakRefused):
                run_one(
                    manifest_path=ROOT / "models" / "agx64-batch12.yaml",
                    pack_dir=ROOT / "packs" / "thin-v1",
                    model_id="llama-3.1-8b-q4",
                    out_dir=out,
                    stub=False,
                    loader="gguf",
                    device_id="dev-not-enrolled",
                )
            self.assertFalse((out / "llama-3.1-8b-q4" / "llama-bench.json").exists())

    def test_execute_with_fake_binary_maps_prefill_not_sustained(self) -> None:
        fixture = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            enrolled = EnrolledHost(Path(tmp))
            try:
                self._execute_maps(tmp, fixture)
            finally:
                enrolled.close()

    def _execute_maps(self, tmp: str, fixture: str) -> None:
            fake = Path(tmp) / "llama-bench"
            fake.write_text(
                "#!/bin/sh\ncat <<'EOF'\n" + fixture + "\nEOF\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            model = Path(tmp) / "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
            model.write_bytes(b"not-a-real-gguf")
            out = Path(tmp) / "out"
            path = run_one(
                manifest_path=ROOT / "models" / "agx64-batch12.yaml",
                pack_dir=ROOT / "packs" / "thin-v1",
                model_id="llama-3.1-8b-q4",
                out_dir=out,
                stub=False,
                model_path=model,
                models_dir=Path(tmp),
                binary=fake,
                loader="gguf",
            )
            card = json.loads(path.read_text(encoding="utf-8"))
            inf = card["inference_sustained"]
            self.assertEqual(inf["prefill_tok_s_p50"], 210.5)
            self.assertEqual(inf["peak_vs_sustained"]["peak_decode_tok_s"], 42.0)
            self.assertIsNone(inf["decode_tok_s_p50_after_throttle"])
            self.assertIsNone(inf["ttft_ms_p50_after_throttle"])
            self.assertIsNot(card["run"]["passed"], True)
            self.assertEqual(card["host"]["jetpack_l4t"], "R39.2.1")
            self.assertEqual(card["host"]["cuda"], "13.2.1")
            self.assertEqual(card["host"]["nvpmodel_id"], 2)
            self.assertIs(card["host"]["jetson_clocks"], False)
            self.assertEqual(card["run"]["device_id"], "a6400000-0640-4000-8000-000000000001")

    def test_execute_without_binary_writes_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enrolled = EnrolledHost(Path(tmp))
            try:
                self._execute_without_binary(tmp)
            finally:
                enrolled.close()

    def _execute_without_binary(self, tmp: str) -> None:
            env_bin = os.environ.pop("LLAMA_BENCH", None)
            env_dir = os.environ.pop("LLAMA_BIN_DIR", None)
            try:
                path = run_one(
                    manifest_path=ROOT / "models" / "agx64-batch12.yaml",
                    pack_dir=ROOT / "packs" / "thin-v1",
                    model_id="llama-3.1-8b-q4",
                    out_dir=Path(tmp) / "out",
                    stub=False,
                    loader="gguf",
                )
            finally:
                if env_bin is not None:
                    os.environ["LLAMA_BENCH"] = env_bin
                if env_dir is not None:
                    os.environ["LLAMA_BIN_DIR"] = env_dir
            dest = Path(tmp) / "out" / "llama-3.1-8b-q4"
            raw = json.loads((dest / "llama-bench.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["status"], "not_run")
            card = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(card["inference_sustained"]["prefill_tok_s_p50"])


if __name__ == "__main__":
    unittest.main()
