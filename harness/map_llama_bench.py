#!/usr/bin/env python3
"""Map llama-bench JSON onto Path B scorecard fields.

Phase 0: if raw output is missing or a not_run stub, every measured field
stays null. Do not invent tok/s. Do not copy third-party rates.

TODO(phase1): parse real llama-bench --output-format json (pp*/tg* mean±stdev)
and still leave *_after_throttle null until a hot-window soak exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "hypermesh.path_b.scorecard.v0"
NUMBERS_POLICY = (
    "Do not invent tok/s. Leave null until measured on this class + pack + image."
)
PASS_RULE = "sustained_within_band_of_class_envelope; peak_alone_fails"
DEFAULT_CLASS = "fyber-agx-orin-64gb"
DEFAULT_SKU = "NVIDIA Jetson AGX Orin 64GB"


def empty_scorecard(
    *,
    class_id: str = DEFAULT_CLASS,
    sku: str = DEFAULT_SKU,
    catalog_id: str | None = None,
    device_id: str | None = None,
    loader: str = "oci",
    precision: str | None = "Q4_K_M",
    power_mode: str | None = "30W",
    nvpmodel_id: int | None = 2,
    pack_id: str = "thin-v1",
    hot_window_s: int = 600,
    context_fit_no_swap: int | None = 4096,
    skip_reason: str | None = None,
    harness_name: str | None = "llama-bench",
    harness_version: str | None = None,
) -> dict[str, Any]:
    """Return a schema-valid scorecard with unmeasured metrics as null."""
    return {
        "schema": SCHEMA,
        "run": {
            "run_id": None,
            "device_id": device_id,
            "class_id": class_id,
            "catalog_id": catalog_id,
            "passed": None,
            "started_at": None,
            "ended_at": None,
            "skip_reason": skip_reason,
        },
        "host": {
            "class_id": class_id,
            "sku": sku,
            "serial": None,
            "device_id": device_id,
            "jetpack_l4t": None,
            "cuda": None,
            "container_runtime": "nvidia",
            "backend": "cuda-jetson",
            "loader": loader,
            "power_mode": power_mode,
            "nvpmodel_id": nvpmodel_id,
            "jetson_clocks": False,
            "nic": {"iface": "eth0", "speed_mbps": None, "wan_rtt_ms": None},
        },
        "identity": {
            "image_hash": None,
            "artifact_hash": None,
            "prompt_pack_id": pack_id,
            "suite_id": pack_id,
            "catalog_id": catalog_id,
        },
        "memory": {
            "advertised_ram_gib": 64,
            "usable_ram_gib": None,
            "ram_after_load_gib": None,
            "bandwidth_class": "LPDDR5-UMA",
            "precision": precision,
            "context_fit_no_swap": context_fit_no_swap,
            "kv_headroom_gib": None,
            "notes": (
                "Measure free after OS+runtime+weights+KV. "
                "Do not treat cudaMemGetInfo as Tegra allocator truth."
            ),
        },
        "inference_sustained": {
            "prompt_pack": pack_id,
            "batch": 1,
            "hot_window_s": hot_window_s,
            "ttft_ms_p50_after_throttle": None,
            "ttft_ms_p95_after_throttle": None,
            "decode_tok_s_p50_after_throttle": None,
            "decode_tok_s_p95_after_throttle": None,
            "prefill_tok_s_p50": None,
            "peak_vs_sustained": {
                "peak_decode_tok_s": None,
                "sustained_decode_tok_s": None,
                "pass_rule": PASS_RULE,
            },
        },
        "power_thermals": {
            "wall_watts_idle": None,
            "wall_watts_load": None,
            "tokens_per_kwh": None,
            "junction_c_max": None,
            "throttle_events": 0,
            "duty_cycle": None,
        },
        "storage": {
            "nvme_gib": None,
            "artifact_and_image_fit": None,
        },
        "reliability": {
            "reboot_rate_per_24h": None,
            "path_b": "skip" if skip_reason else None,
            "time_to_fail_s": None,
            "unexpected_restart": False,
        },
        "harness": {
            "name": harness_name,
            "version": harness_version,
            "raw_outputs": [
                "llama-bench.json",
                "tegrastats.log",
                "free-after-load.txt",
            ],
        },
        "numbers_policy": NUMBERS_POLICY,
    }


def _rows(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        if raw.get("status") == "not_run":
            return []
        results = raw.get("results")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
        if "test" in raw or "avg_ts" in raw:
            return [raw]
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def map_llama_bench(raw: Any) -> dict[str, Any]:
    """Extract measured llama-bench cells. Unknown / stub → all null.

    TODO(phase1): map pp* → prefill_tok_s_p50 and tg* → a *cold* decode rate.
    TODO(phase1): ttft_ms_*_after_throttle and decode_tok_s_*_after_throttle
    stay null until a hot-window client/soak writes them. Peak-only is not a pass.
    """
    mapped = {
        "prefill_tok_s_p50": None,
        "decode_tok_s_cold": None,
        "ttft_ms_p50_after_throttle": None,
        "decode_tok_s_p50_after_throttle": None,
        "source_tests": [],
    }
    rows = _rows(raw)
    if not rows:
        return mapped

    prefills: list[float] = []
    decodes: list[float] = []
    tests: list[str] = []
    for row in rows:
        test = str(row.get("test") or "")
        avg = row.get("avg_ts")
        if not isinstance(avg, (int, float)):
            continue
        tests.append(test)
        if test.startswith("pp"):
            prefills.append(float(avg))
        elif test.startswith("tg"):
            decodes.append(float(avg))

    mapped["source_tests"] = tests
    if prefills:
        # First pp* cell only. Still not "after throttle".
        mapped["prefill_tok_s_p50"] = prefills[0]
    if decodes:
        mapped["decode_tok_s_cold"] = decodes[0]
    # after_throttle fields remain null on purpose.
    return mapped


def apply_map(scorecard: dict[str, Any], mapped: dict[str, Any]) -> dict[str, Any]:
    inf = scorecard["inference_sustained"]
    # Only fill the cold prefill cell if llama-bench actually ran.
    # Never promote cold decode into decode_tok_s_p50_after_throttle.
    if mapped.get("prefill_tok_s_p50") is not None:
        inf["prefill_tok_s_p50"] = mapped["prefill_tok_s_p50"]
    return scorecard


def load_raw(path: Path) -> Any:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "llama_bench_json",
        nargs="?",
        help="Path to llama-bench --output-format json (optional)",
    )
    parser.add_argument(
        "--emit-empty",
        action="store_true",
        help="Print a schema-valid null scorecard to stdout",
    )
    args = parser.parse_args(argv)

    if args.emit_empty:
        json.dump(empty_scorecard(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    raw = load_raw(Path(args.llama_bench_json)) if args.llama_bench_json else None
    mapped = map_llama_bench(raw)
    json.dump(mapped, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
