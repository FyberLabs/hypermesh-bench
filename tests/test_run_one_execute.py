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

from agx_gate import PreflightClosed  # noqa: E402
from run_one import run_one  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "llama-bench-sample.json"


def _preflight_ok() -> dict:
    return {"ok": True, "checks": []}


class RunOneExecuteTest(unittest.TestCase):
    def test_execute_with_fake_binary_maps_prefill_not_sustained(self) -> None:
        fixture = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
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
                preflight=_preflight_ok,
            )
            card = json.loads(path.read_text(encoding="utf-8"))
            inf = card["inference_sustained"]
            self.assertEqual(inf["prefill_tok_s_p50"], 210.5)
            self.assertEqual(inf["peak_vs_sustained"]["peak_decode_tok_s"], 42.0)
            self.assertIsNone(inf["decode_tok_s_p50_after_throttle"])
            self.assertIsNone(inf["ttft_ms_p50_after_throttle"])
            self.assertIsNot(card["run"]["passed"], True)

    def test_execute_without_binary_writes_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
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
                    preflight=_preflight_ok,
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

    def test_execute_stops_when_preflight_fails(self) -> None:
        def failed() -> dict:
            return {"ok": False, "checks": [{"name": "arch", "ok": False}]}

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PreflightClosed):
                run_one(
                    manifest_path=ROOT / "models" / "agx64-batch12.yaml",
                    pack_dir=ROOT / "packs" / "thin-v1",
                    model_id="llama-3.1-8b-q4",
                    out_dir=Path(tmp) / "out",
                    stub=False,
                    loader="gguf",
                    preflight=failed,
                )
            self.assertFalse((Path(tmp) / "out" / "llama-3.1-8b-q4" / "llama-bench.json").exists())

    def test_stub_does_not_call_preflight(self) -> None:
        called: list[int] = []

        def failed() -> dict:
            called.append(1)
            return {"ok": False}

        with tempfile.TemporaryDirectory() as tmp:
            run_one(
                manifest_path=ROOT / "models" / "agx64-batch12.yaml",
                pack_dir=ROOT / "packs" / "thin-v1",
                model_id="llama-3.1-8b-q4",
                out_dir=Path(tmp) / "out",
                stub=True,
                preflight=failed,
            )
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
