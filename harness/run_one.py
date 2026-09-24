#!/usr/bin/env python3
"""Run one model from the AGX batch manifest and write a scorecard.

--stub (default, CI): write a schema-valid scorecard with null measurements.
--execute: AGX preflight and the enrolled known host, then llama-bench.
A failed preflight does not start the bench. Never invent tok/s.
"""

from __future__ import annotations

import argparse
import json
import os
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

from host_probe import apply_to_scorecard, collect as collect_host  # noqa: E402
from soak_gate import SoakRefused, apply_probe_facts, assert_ready_for_soak  # noqa: E402
from map_llama_bench import (  # noqa: E402
    apply_hot,
    apply_map,
    empty_scorecard,
    load_raw,
    map_llama_bench,
    map_ttft_hot,
)
from run_llama_bench import run_llama_bench  # noqa: E402
from ttft_client import load_ttft  # noqa: E402


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
                "reason": "stub; llama-bench not invoked",
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


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def assemble_scorecard(
    *,
    dest: Path,
    manifest: dict[str, Any],
    pack: dict[str, Any],
    model: dict[str, Any],
    chosen_id: str,
    skip_reason: str | None,
    device_id: str | None,
    loader: str | None,
    artifact_hash: str | None,
    image_hash: str | None,
    host_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = load_raw(dest / "llama-bench.json")
    mapped = map_llama_bench(raw)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog = model.get("catalog_id")
    if catalog == "null":
        catalog = None
    resolved_loader = loader or model.get("loader") or pack.get("loader_default") or "oci"
    scorecard = empty_scorecard(
        class_id=manifest.get("class_id") or pack.get("class_id") or "fyber-agx-orin-64gb",
        sku=manifest.get("sku") or "NVIDIA Jetson AGX Orin 64GB",
        catalog_id=catalog,
        device_id=device_id,
        loader=resolved_loader,
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
    scorecard["harness"]["raw_outputs"] = [
        "llama-bench.json",
        "ttft_hot.json",
        "mem_after_load.json",
        "free-after-load.txt",
        "power.json",
        "identity.json",
        "reliability.json",
        "tegrastats.log",
        "host_probe.json",
        "job_result.json",
        "check_path_b.txt",
    ]
    apply_map(scorecard, mapped)

    ttft = load_ttft(dest / "ttft_hot.json")
    apply_hot(scorecard, map_ttft_hot(ttft))

    identity_file = _load_json(dest / "identity.json") or {}
    art = artifact_hash or identity_file.get("artifact_hash")
    img = image_hash or identity_file.get("image_hash")
    if isinstance(art, str) and art.strip() and art != "TBD":
        scorecard["identity"]["artifact_hash"] = art.strip()
    if isinstance(img, str) and img.strip() and img != "TBD":
        scorecard["identity"]["image_hash"] = img.strip()

    mem_file = _load_json(dest / "mem_after_load.json") or {}
    memory = scorecard["memory"]
    for key in ("usable_ram_gib", "ram_after_load_gib", "kv_headroom_gib"):
        val = mem_file.get(key)
        if isinstance(val, (int, float)):
            memory[key] = float(val)

    power_file = _load_json(dest / "power.json") or {}
    thermals = scorecard["power_thermals"]
    # Only wall_meter may fill wall_watts_*. tegrastats_proxy stays null.
    if power_file.get("method") == "wall_meter":
        for src, dest_key in (("idle_w", "wall_watts_idle"), ("load_w", "wall_watts_load")):
            val = power_file.get(src)
            if isinstance(val, (int, float)):
                thermals[dest_key] = float(val)
    if isinstance(power_file.get("junction_c_max"), (int, float)):
        thermals["junction_c_max"] = float(power_file["junction_c_max"])

    rel_file = _load_json(dest / "reliability.json") or {}
    reliability = scorecard["reliability"]
    if rel_file.get("unexpected_restart") is True:
        reliability["unexpected_restart"] = True
    elif rel_file.get("unexpected_restart") is False:
        reliability["unexpected_restart"] = False
    if isinstance(rel_file.get("reboot_rate_per_24h"), (int, float)):
        reliability["reboot_rate_per_24h"] = float(rel_file["reboot_rate_per_24h"])
    if rel_file.get("path_b") in ("pass", "fail", "skip"):
        reliability["path_b"] = rel_file["path_b"]

    probe = host_probe or _load_json(dest / "host_probe.json")
    if probe:
        apply_to_scorecard(scorecard, probe)

    return scorecard


def run_one(
    *,
    manifest_path: Path,
    pack_dir: Path,
    model_id: str | None,
    out_dir: Path,
    skip_reason: str | None = None,
    stub: bool = True,
    require_bench: bool = False,
    model_path: Path | None = None,
    models_dir: Path | None = None,
    image: str | None = None,
    binary: Path | None = None,
    device_id: str | None = None,
    loader: str | None = None,
    artifact_hash: str | None = None,
    image_hash: str | None = None,
    host_probe: dict[str, Any] | None = None,
) -> Path:
    manifest = load_yaml(manifest_path)
    pack = load_pack(pack_dir)
    chosen_id = model_id or manifest.get("first_model_id") or "llama-3.1-8b-q4"
    model = find_model(manifest, chosen_id)

    ready: dict[str, Any] | None = None
    if not stub:
        ready = assert_ready_for_soak(device_id)
        device_id = str(ready["device_id"])

    dest = out_dir / chosen_id
    dest.mkdir(parents=True, exist_ok=True)

    if stub:
        if not (dest / "llama-bench.json").exists():
            write_placeholders(dest)
    else:
        run_llama_bench(
            out_path=dest / "llama-bench.json",
            pack=pack,
            model_path=model_path,
            binary=binary,
            image=image if ((loader or model.get("loader")) == "oci" and image) else None,
            models_dir=models_dir,
            stub=False,
            require_bench=require_bench,
        )

    probe_path = dest / "host_probe.json"
    if host_probe is None and probe_path.is_file() is False:
        host_probe = collect_host(
            models_dir=models_dir,
            out_dir=dest,
            device_id=device_id,
            class_id=manifest.get("class_id") or "fyber-agx-orin-64gb",
        )
    if ready is not None:
        if host_probe is None:
            host_probe = _load_json(probe_path) or {}
        apply_probe_facts(host_probe, ready["facts"], device_id or "")
        host_probe["preflight"] = ready["preflight"]
    if host_probe is not None and (ready is not None or probe_path.is_file() is False):
        probe_path.write_text(json.dumps(host_probe, indent=2) + "\n", encoding="utf-8")

    scorecard = assemble_scorecard(
        dest=dest,
        manifest=manifest,
        pack=pack,
        model=model,
        chosen_id=chosen_id,
        skip_reason=skip_reason,
        device_id=device_id,
        loader=loader,
        artifact_hash=artifact_hash,
        image_hash=image_hash,
        host_probe=host_probe,
    )

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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--stub",
        action="store_true",
        help="Do not invoke llama-bench (CI default)",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Run llama-bench when the binary or OCI image exists",
    )
    parser.add_argument("--require-bench", action="store_true")
    parser.add_argument("--model", type=Path, default=None, help="Path to GGUF")
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--image", default=os.environ.get("HM_IMAGE_DIGEST") or None)
    parser.add_argument("--binary", type=Path, default=None)
    parser.add_argument(
        "--device-id",
        default=os.environ.get("HM_DEVICE_ID") or None,
        help="Must be the enrolled known host on --execute. HM_DEVICE_ID alone does not enroll",
    )
    parser.add_argument("--loader", choices=("gguf", "oci"), default=None)
    parser.add_argument("--artifact-hash", default=None)
    parser.add_argument("--image-hash", default=None)
    args = parser.parse_args(argv)

    # CI and existing callers omit both flags → stub.
    stub = not args.execute
    if args.stub:
        stub = True

    try:
        path = run_one(
            manifest_path=args.manifest,
            pack_dir=args.pack,
            model_id=args.model_id,
            out_dir=args.out,
            skip_reason=args.skip_reason,
            stub=stub,
            require_bench=args.require_bench,
            model_path=args.model,
            models_dir=args.models_dir,
            image=args.image,
            binary=args.binary,
            device_id=args.device_id,
            loader=args.loader,
            artifact_hash=args.artifact_hash,
            image_hash=args.image_hash,
        )
    except SoakRefused as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
