#!/usr/bin/env python3
"""Fixture tests for llama-bench / TTFT mapping. No GPU. No invented numbers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
sys.path.insert(0, str(HARNESS))

from map_llama_bench import (  # noqa: E402
    apply_hot,
    apply_map,
    empty_scorecard,
    load_raw,
    map_llama_bench,
    map_ttft_hot,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class MapLlamaBenchTest(unittest.TestCase):
    def test_not_run_stays_null(self) -> None:
        mapped = map_llama_bench({"status": "not_run", "results": []})
        self.assertIsNone(mapped["prefill_tok_s_p50"])
        self.assertIsNone(mapped["decode_tok_s_cold"])
        self.assertIsNone(mapped["decode_tok_s_p50_after_throttle"])

    def test_named_pp_tg_cells(self) -> None:
        raw = load_raw(FIXTURES / "llama-bench-named.json")
        mapped = map_llama_bench(raw)
        self.assertEqual(mapped["prefill_tok_s_p50"], 200.0)
        self.assertEqual(mapped["decode_tok_s_cold"], 40.0)
        self.assertIsNone(mapped["decode_tok_s_p50_after_throttle"])

    def test_n_prompt_n_gen_json(self) -> None:
        raw = load_raw(FIXTURES / "llama-bench-sample.json")
        mapped = map_llama_bench(raw)
        self.assertEqual(mapped["prefill_tok_s_p50"], 210.5)
        self.assertEqual(mapped["decode_tok_s_cold"], 42.0)
        self.assertIsNone(mapped["ttft_ms_p50_after_throttle"])

    def test_apply_map_does_not_promote_peak_to_sustained(self) -> None:
        card = empty_scorecard()
        mapped = map_llama_bench(load_raw(FIXTURES / "llama-bench-named.json"))
        apply_map(card, mapped)
        inf = card["inference_sustained"]
        self.assertEqual(inf["prefill_tok_s_p50"], 200.0)
        self.assertEqual(inf["peak_vs_sustained"]["peak_decode_tok_s"], 40.0)
        self.assertIsNone(inf["decode_tok_s_p50_after_throttle"])
        self.assertIsNone(inf["peak_vs_sustained"]["sustained_decode_tok_s"])

    def test_apply_hot_only_when_measured(self) -> None:
        card = empty_scorecard()
        apply_hot(card, map_ttft_hot({"status": "unavailable", "ttft_ms_p50": None}))
        inf = card["inference_sustained"]
        self.assertIsNone(inf["ttft_ms_p50_after_throttle"])
        apply_hot(card, map_ttft_hot(json.loads((FIXTURES / "ttft_hot-sample.json").read_text())))
        self.assertEqual(inf["ttft_ms_p50_after_throttle"], 120.0)
        self.assertEqual(inf["decode_tok_s_p50_after_throttle"], 38.5)
        self.assertEqual(inf["peak_vs_sustained"]["sustained_decode_tok_s"], 38.5)


if __name__ == "__main__":
    unittest.main()
