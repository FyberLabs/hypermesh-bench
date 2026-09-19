#!/usr/bin/env python3
"""JobResultBody adapter: passed + image_hash only; no invented hash."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from emit_job_result import from_scorecard  # noqa: E402
from map_llama_bench import empty_scorecard  # noqa: E402


class EmitJobResultTest(unittest.TestCase):
    def test_stub_scorecard_false_null_hash(self) -> None:
        card = empty_scorecard(catalog_id="llama-3.1-8b-q4")
        body = from_scorecard(card)
        self.assertIs(body["passed"], False)
        self.assertIsNone(body["image_hash"])
        self.assertEqual(body["kind"], "path_b")
        self.assertIn("passed", body)
        self.assertIn("image_hash", body)

    def test_passed_true_only_when_run_passed(self) -> None:
        card = empty_scorecard()
        card["run"]["passed"] = True
        card["identity"]["image_hash"] = "ghcr.io/fyberlabs/hypermesh-llama@sha256:abc"
        body = from_scorecard(card)
        self.assertIs(body["passed"], True)
        self.assertEqual(
            body["image_hash"],
            "ghcr.io/fyberlabs/hypermesh-llama@sha256:abc",
        )

    def test_tbd_hash_is_not_emitted(self) -> None:
        card = empty_scorecard()
        card["identity"]["image_hash"] = "TBD"
        body = from_scorecard(card)
        self.assertIsNone(body["image_hash"])


if __name__ == "__main__":
    unittest.main()
