#!/usr/bin/env python3
"""Lint the AGX batch-12 pins and Product-2 catalog.

Refuses TBD hashes, invented tok/s on catalog scorecards, and a sellable
row that is not status=certified. Does not download weights.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MEASURED_KEYS = (
    "usable_ram_gib",
    "ram_after_load_gib",
    "context_fit_no_swap",
    "ttft_ms_p50_after_throttle",
    "ttft_ms_p95_after_throttle",
    "decode_tok_s_p50_after_throttle",
    "decode_tok_s_p95_after_throttle",
    "prefill_tok_s_p50",
    "wall_watts_idle",
    "wall_watts_load",
    "tokens_per_kwh",
    "image_hash",
    "artifact_hash",
    "passed",
)


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML is not installed; pip install -r requirements.txt")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def check_batch(path: Path) -> list[str]:
    errors: list[str] = []
    manifest = load_yaml(path)
    models = list(manifest.get("models") or [])
    if len(models) != 12:
        errors.append(f"{path}: expected 12 models, got {len(models)}")
    seen: set[str] = set()
    for row in models:
        mid = row.get("id")
        if not mid:
            errors.append(f"{path}: model row missing id")
            continue
        if mid in seen:
            errors.append(f"{path}: duplicate model id {mid}")
        seen.add(mid)
        sha = row.get("sha256")
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            errors.append(f"{path}: {mid}: sha256 must be 64 hex (got {sha!r})")
        if sha in ("TBD", "<pin>", None, ""):
            errors.append(f"{path}: {mid}: sha256 is unpinned")
        if not row.get("url"):
            errors.append(f"{path}: {mid}: missing url")
        if not row.get("file"):
            errors.append(f"{path}: {mid}: missing file")
        size = row.get("size_bytes")
        if size is not None and (not isinstance(size, int) or size <= 0):
            errors.append(f"{path}: {mid}: size_bytes must be a positive int")
    first = manifest.get("first_model_id")
    if first != "llama-3.1-8b-q4":
        errors.append(f"{path}: first_model_id must be llama-3.1-8b-q4")
    return errors


def check_catalog(path: Path, batch: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    catalog = load_yaml(path)
    if catalog.get("class_id") != "fyber-agx-orin-64gb":
        errors.append(f"{path}: class_id must be fyber-agx-orin-64gb")
    if catalog.get("product") != 2:
        errors.append(f"{path}: product must be 2 (Full Model catalog)")
    sell = str(catalog.get("sell_rule") or "").lower()
    if "certified" not in sell:
        errors.append(f"{path}: sell_rule must require status=certified")
    rows = list(catalog.get("rows") or [])
    if len(rows) != 12:
        errors.append(f"{path}: expected 12 catalog rows, got {len(rows)}")
    if catalog.get("first_catalog_id") != "llama-3.1-8b-q4":
        errors.append(f"{path}: first_catalog_id must be llama-3.1-8b-q4")
    if not rows:
        return errors

    first = rows[0]
    if first.get("catalog_id") != "llama-3.1-8b-q4":
        errors.append(f"{path}: first row must be llama-3.1-8b-q4")
    if first.get("status") != "soak_pending":
        errors.append(f"{path}: llama-3.1-8b-q4 status must be soak_pending until soak")
    if first.get("certified") is True:
        errors.append(f"{path}: llama-3.1-8b-q4 must not be certified before soak")
    if first.get("visibility") == "listed":
        errors.append(f"{path}: listed visibility only after certified")
    if first.get("advertised_context") != 4096:
        errors.append(f"{path}: llama-3.1-8b-q4 advertised_context must start at 4096")
    notes = str(first.get("advertised_context_notes") or "")
    if "usable_ram" not in notes:
        errors.append(f"{path}: llama-3.1-8b-q4 must document usable_ram for ctx")
    power = first.get("power_profile") or {}
    if power.get("nvpmodel_id") != 2 or power.get("watts") != 30:
        errors.append(f"{path}: llama-3.1-8b-q4 power_profile must be 30W / nvpmodel 2")
    loaders = first.get("loaders") or []
    if "oci" not in loaders or "gguf" not in loaders:
        errors.append(f"{path}: llama-3.1-8b-q4 loaders must include oci and gguf")
    if first.get("backend") != "cuda-jetson":
        errors.append(f"{path}: llama-3.1-8b-q4 backend must be cuda-jetson")

    batch_by_id: dict[str, dict[str, Any]] = {}
    if batch:
        for row in batch.get("models") or []:
            if row.get("id"):
                batch_by_id[str(row["id"])] = row

    seen: set[str] = set()
    for row in rows:
        cid = row.get("catalog_id")
        if not cid:
            errors.append(f"{path}: row missing catalog_id")
            continue
        if cid in seen:
            errors.append(f"{path}: duplicate catalog_id {cid}")
        seen.add(cid)
        status = row.get("status")
        if status not in ("soak_pending", "certified", "rejected", "candidate"):
            errors.append(f"{path}: {cid}: invalid status {status!r}")
        if status != "certified" and row.get("visibility") == "listed":
            errors.append(f"{path}: {cid}: must not be listed unless certified")
        if status != "certified" and row.get("certified") is True:
            errors.append(f"{path}: {cid}: certified=true requires status=certified")
        artifact = row.get("artifact") or {}
        sha = artifact.get("sha256")
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            errors.append(f"{path}: {cid}: artifact.sha256 must be 64 hex")
        scorecard = row.get("scorecard") or {}
        for key in MEASURED_KEYS:
            if key in scorecard and scorecard[key] is not None:
                errors.append(
                    f"{path}: {cid}: scorecard.{key} must be null until measured "
                    f"(got {scorecard[key]!r})"
                )
        if cid in batch_by_id:
            bsha = batch_by_id[cid].get("sha256")
            if bsha and sha and bsha != sha:
                errors.append(f"{path}: {cid}: catalog sha256 != batch-12 pin")
        if cid != "llama-3.1-8b-q4" and status == "soak_pending":
            errors.append(
                f"{path}: {cid}: only llama-3.1-8b-q4 is soak_pending; others are candidate"
            )
        if cid != "llama-3.1-8b-q4" and status not in ("candidate", "certified", "rejected"):
            errors.append(f"{path}: {cid}: expected candidate until soaked")

    if "llama-3.1-8b-q4" not in seen:
        errors.append(f"{path}: missing required catalog_id llama-3.1-8b-q4")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "models" / "agx64-batch12.yaml",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "catalog" / "agx64.yaml",
    )
    args = parser.parse_args(argv)

    errors = check_batch(args.manifest)
    batch = load_yaml(args.manifest) if args.manifest.is_file() else None
    errors.extend(check_catalog(args.catalog, batch))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("ok: batch-12 pins + catalog/agx64.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
