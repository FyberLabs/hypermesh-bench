#!/usr/bin/env python3
"""TTFT client: unavailable / stub leave metrics null. No invented tok/s."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from ttft_client import percentile, run_ttft  # noqa: E402


class TtftClientTest(unittest.TestCase):
    def test_percentile_empty_is_none(self) -> None:
        self.assertIsNone(percentile([], 50))

    def test_percentile_single(self) -> None:
        self.assertEqual(percentile([10.0], 95), 10.0)

    def test_stub_writes_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ttft_hot.json"
            payload = run_ttft(out_path=dest, stub=True, hot_window_s=600)
            self.assertEqual(payload["status"], "not_run")
            self.assertIsNone(payload["ttft_ms_p50"])
            self.assertIsNone(payload["decode_tok_s_p50"])
            written = json.loads(dest.read_text(encoding="utf-8"))
            self.assertIsNone(written["ttft_ms_p95"])

    def test_unavailable_endpoint_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ttft_hot.json"
            payload = run_ttft(
                out_path=dest,
                endpoint="http://127.0.0.1:1/v1/chat/completions",
                hot_window_s=0,
                reps=1,
                timeout_s=0.2,
            )
            self.assertEqual(payload["status"], "unavailable")
            self.assertIsNone(payload["ttft_ms_p50"])
            self.assertIsNone(payload["decode_tok_s_p50"])
            self.assertEqual(payload["n_samples"], 0)


if __name__ == "__main__":
    unittest.main()
