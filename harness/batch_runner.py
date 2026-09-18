#!/usr/bin/env python3
"""Run the AGX batch manifest. Never fill tok/s from TOPS or blogs.

Phase 0: writes a null scorecard per model and a batch-summary.json.
70B (skip_on_oom) is recorded as a conditional row, not auto-failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

from run_one import run_one  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML is not installed; pip install -r requirements.txt")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def sort_models(models: list[dict[str, Any]], first_model_id: str | None) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, int, str]:
        priority = int(row.get("priority") or 99)
        lead = 0 if first_model_id and row.get("id") == first_model_id else 1
        return (lead, priority, str(row.get("id") or ""))

    return sorted(models, key=key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "models" / "agx64-batch12.yaml",
    )
    parser.add_argument("--pack", type=Path, default=ROOT / "packs" / "thin-v1")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated model ids. Default: all rows except fit=no",
    )
    parser.add_argument(
        "--first-only",
        action="store_true",
        help="Run only first_model_id (Llama 3.1 8B Q4_K_M)",
    )
    args = parser.parse_args(argv)

    manifest = load_yaml(args.manifest)
    models = list(manifest.get("models") or [])
    first_id = manifest.get("first_model_id") or "llama-3.1-8b-q4"
    models = sort_models(models, first_id)

    if args.first_only:
        models = [m for m in models if m.get("id") == first_id]
    elif args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        models = [m for m in models if m.get("id") in wanted]

    args.out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "class_id": manifest.get("class_id"),
        "power_profile": manifest.get("power_profile") or "30W",
        "nvpmodel_id": manifest.get("nvpmodel_id") or 2,
        "numbers_policy": (
            "Do not invent tok/s. Leave null until measured on this class + pack + image."
        ),
        "phase": 0,
        "results": [],
    }

    for row in models:
        model_id = row.get("id")
        fit = row.get("fit")
        if fit == "no":
            summary["results"].append(
                {
                    "id": model_id,
                    "status": "skip",
                    "reason": "est_fit=no",
                    "scorecard": None,
                }
            )
            continue

        skip_reason = None
        status = "stub"
        # Phase 0 never loads weights, so OOM cannot be observed.
        # skip_on_oom is recorded so Phase 2 does not auto-fail 70B on UMA.
        if row.get("skip_on_oom"):
            skip_reason = None
            status = "stub_conditional"

        try:
            scorecard_path = run_one(
                manifest_path=args.manifest,
                pack_dir=args.pack,
                model_id=model_id,
                out_dir=args.out,
                skip_reason=skip_reason,
            )
            summary["results"].append(
                {
                    "id": model_id,
                    "status": status,
                    "reason": (
                        "phase0_null_scorecard; skip_on_oom"
                        if row.get("skip_on_oom")
                        else "phase0_null_scorecard"
                    ),
                    "fit": fit,
                    "catalog_id": row.get("catalog_id"),
                    "sha256": row.get("sha256"),
                    "scorecard": str(scorecard_path),
                }
            )
        except Exception as exc:  # noqa: BLE001 — batch must continue
            summary["results"].append(
                {
                    "id": model_id,
                    "status": "error",
                    "reason": str(exc),
                    "scorecard": None,
                }
            )

    summary_path = args.out / "batch-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
