#!/usr/bin/env python3
"""AGX soak gate. No claim that this machine is the Jetson."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from agx_gate import (  # noqa: E402
    PreflightClosed,
    assert_agx_class,
    run_preflight,
)


class AgxGateTest(unittest.TestCase):
    def test_only_agx_classes_pass(self) -> None:
        assert_agx_class("agx-large")
        assert_agx_class("fyber-agx-orin-64gb")
        with self.assertRaises(PreflightClosed):
            assert_agx_class("thor")
        with self.assertRaises(PreflightClosed):
            assert_agx_class("nx-volume")
        with self.assertRaises(PreflightClosed):
            assert_agx_class(None)

    def test_runner_must_report_ok(self) -> None:
        self.assertTrue(run_preflight(lambda: {"ok": True})["ok"])
        with self.assertRaises(PreflightClosed) as raised:
            run_preflight(lambda: {"ok": False, "checks": [{"name": "cuda", "ok": False}]})
        self.assertIn("cuda", str(raised.exception))

    def test_this_process_is_not_the_agx(self) -> None:
        sibling = (
            Path(__file__).resolve().parents[2]
            / "panopticon"
            / "products"
            / "hypermesh"
            / "src"
            / "hypermesh_service"
            / "agx_preflight.py"
        )
        if not sibling.is_file():
            with self.assertRaises(PreflightClosed) as raised:
                run_preflight()
            self.assertIn("not available", str(raised.exception))
            return
        with self.assertRaises(PreflightClosed) as raised:
            run_preflight()
        self.assertIn("failed closed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
