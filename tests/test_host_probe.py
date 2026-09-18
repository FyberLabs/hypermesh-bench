#!/usr/bin/env python3
"""host_probe: nulls off-box; parse L4T when a fixture file exists."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from host_probe import apply_to_scorecard, collect, probe_l4t  # noqa: E402
from map_llama_bench import empty_scorecard  # noqa: E402


class HostProbeTest(unittest.TestCase):
    def test_missing_tegra_release_is_null(self) -> None:
        self.assertIsNone(probe_l4t(release_path=Path("/no/such/nv_tegra_release")))

    def test_parse_l4t_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nv_tegra_release"
            path.write_text(
                "# R36 (release), REVISION: 4.3, GCID: 0, BOARD: p3701, EABI: aarch64, DATE: fake\n",
                encoding="utf-8",
            )
            self.assertEqual(probe_l4t(release_path=path), "R36.4.3")

    def test_collect_off_box_does_not_invent(self) -> None:
        probe = collect(models_dir=Path("/tmp"), out_dir=Path("/tmp"))
        # CI is not JetPack. These stay null unless the file/binary exists.
        if not Path("/etc/nv_tegra_release").exists():
            self.assertIsNone(probe["jetpack_l4t"])
        self.assertIn("disk_free_gib", probe)
        self.assertNotIn("tok_s", probe)

    def test_apply_leaves_unmeasured_null(self) -> None:
        card = empty_scorecard()
        apply_to_scorecard(card, {"jetpack_l4t": None, "cuda": None, "device_id": "dev-1"})
        self.assertEqual(card["host"]["device_id"], "dev-1")
        self.assertEqual(card["run"]["device_id"], "dev-1")
        self.assertIsNone(card["host"]["jetpack_l4t"])


if __name__ == "__main__":
    unittest.main()
