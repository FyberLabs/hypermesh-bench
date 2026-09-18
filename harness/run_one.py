#!/usr/bin/env python3
"""Run one model from the AGX batch manifest and write a scorecard.

Phase 0 stub: does not invoke llama-bench or invent tok/s. Writes a
schema-valid scorecard with null measurements plus TODO raw placeholders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

from map_llama_bench import apply_map, empty_scorecard, load_raw, map_llama_bench  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML is not installed; pip install -r requirements.txt")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def load_pack(pack_dir: Path) -> dict[str, Any]:
    pack_yaml = pack_dir / "pack.yaml"
    if not pack_yaml.exists():
        raise SystemExit(f"pack.yaml not found in {pack_dir}")
    return load_yaml(pack_yaml)


def find_model(manifest: dict[str, Any], model_id: str) -> dict[str, Any]:
    for row in manifest.get("models") or []:
        if row.get("id") == model_id:
            return row
    raise SystemExit(f"model id not in manifest: {model_id}")


def write_placeholders(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "llama-bench.json").write_text(
        json.dumps(
            {
                "status": "not_run",
                "reason": "Phase 0 harness stub; llama-bench not invoked",
                "results": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "free-after-load.txt").write_text(
        "TODO(phase1): capture `free -b` after weights+KV load. usable_ram_gib stays null.\n",
        encoding="utf-8",
    )
    (out_dir / "identity.json").write_text(
        json.dumps(
            {
                "image_hash": None,
                "artifact_hash": None,
                "note": (
                    "artifact_hash stays null until the pinned file is pulled and "
                    "verified on the host; oci image_hash is repo@digest"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "TODO.md").write_text(
        "\n".join(
            [
                "# Phase 1 TODOs for this model",
                "",
                "- Pull the pinned sha256 (pull-gguf.sh --model-id …) and verify at soak",
                "- nvpmodel -m 2 (30W); record jetson_clocks",
                "- Load weights; write mem_after_load.json from free/tegrastats",
                "- Hot window (pack hot_window_s) then llama-bench JSON",
                "- TTFT client after throttle → ttft_hot.json",
                "- Wall meter → power.json; do not fill wall_watts_* from tegrastats",
                "- Map raw outputs; leave unknown fields null",
                "- Never copy blog tok/s or TOPS into inference_sustained",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_one(
    *,
    manifest_path: Path,
    pack_dir: Path,
    model_id: str | None,
    out_dir: Path,
    skip_reason: str | None = None,
) -> Path:
    manifest = load_yaml(manifest_path)
    pack = load_pack(pack_dir)
    chosen_id = model_id or manifest.get("first_model_id") or "llama-3.1-8b-q4"
    model = find_model(manifest, chosen_id)

    dest = out_dir / chosen_id
    write_placeholders(dest)

    raw = load_raw(dest / "llama-bench.json")
    mapped = map_llama_bench(raw)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog = model.get("catalog_id")
    if catalog == "null":
        catalog = None
    scorecard = empty_scorecard(
        class_id=manifest.get("class_id") or pack.get("class_id") or "fyber-agx-orin-64gb",
        sku=manifest.get("sku") or "NVIDIA Jetson AGX Orin 64GB",
        catalog_id=catalog,
        loader=model.get("loader") or pack.get("loader_default") or "oci",
        precision=model.get("quant") or manifest.get("default_quant"),
        power_mode=manifest.get("power_profile") or pack.get("power_mode") or "30W",
        nvpmodel_id=manifest.get("nvpmodel_id") or pack.get("nvpmodel_id") or 2,
        pack_id=pack.get("pack_id") or "thin-v1",
        hot_window_s=int(pack.get("hot_window_s") or 600),
        context_fit_no_swap=int(
            pack.get("context_fit_default") or manifest.get("default_ctx") or 4096
        ),
        skip_reason=skip_reason,
        harness_name="llama-bench",
        harness_version=None,
    )
    scorecard["run"]["started_at"] = now
    scorecard["run"]["ended_at"] = now
    apply_map(scorecard, mapped)

    dest_scorecard = dest / "scorecard.json"
    dest_scorecard.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    return dest_scorecard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "models" / "agx64-batch12.yaml",
    )
    parser.add_argument("--pack", type=Path, default=ROOT / "packs" / "thin-v1")
    parser.add_argument(
        "--model-id",
        default=None,
        help="Default: manifest first_model_id (llama-3.1-8b-q4)",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--skip-reason",
        default=None,
        help="Record a skip (e.g. skipped:uma) without inventing metrics",
    )
    args = parser.parse_args(argv)

    path = run_one(
        manifest_path=args.manifest,
        pack_dir=args.pack,
        model_id=args.model_id,
        out_dir=args.out,
        skip_reason=args.skip_reason,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
