#!/usr/bin/env python3
"""Validate a Path B scorecard against the JSON Schema.

Default: schema only (Phase 0 null scorecards must pass).
--path-b: enroll rules from docs/SCORECARD.md (empty image_hash fails, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "scorecard.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_schema(scorecard: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    if jsonschema is None:
        return ["jsonschema is not installed; pip install -r requirements.txt"]
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(scorecard)]


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != "" and value != "TBD"


def validate_path_b(scorecard: dict[str, Any]) -> list[str]:
    """Enroll / Path B rules. Does not invent a tok/s threshold."""
    errors: list[str] = []
    identity = scorecard.get("identity") or {}
    inference = scorecard.get("inference_sustained") or {}
    peak = inference.get("peak_vs_sustained") or {}
    reliability = scorecard.get("reliability") or {}
    host = scorecard.get("host") or {}

    if not _nonempty_str(identity.get("image_hash")):
        errors.append("identity.image_hash must be non-empty for Path B (no fake hash)")
    if host.get("loader") == "gguf" and not _nonempty_str(identity.get("artifact_hash")):
        errors.append("identity.artifact_hash must be non-empty for loader=gguf")
    if host.get("loader") == "oci" and _nonempty_str(identity.get("image_hash")):
        digest = identity["image_hash"]
        if "@" not in digest:
            errors.append("identity.image_hash for oci must be repo@digest, not a bare sha256")

    if host.get("power_mode") in (None, ""):
        errors.append("host.power_mode must be recorded")
    if host.get("nvpmodel_id") is None:
        errors.append("host.nvpmodel_id must be recorded")

    ttft = inference.get("ttft_ms_p50_after_throttle")
    decode = inference.get("decode_tok_s_p50_after_throttle")
    if ttft is None or decode is None:
        errors.append(
            "sustained ttft_ms_p50_after_throttle and decode_tok_s_p50_after_throttle "
            "must be measured after the hot window"
        )

    if peak.get("peak_decode_tok_s") is not None and peak.get("sustained_decode_tok_s") is None:
        errors.append("peak-only submission fails Path B")

    if reliability.get("unexpected_restart") is True:
        errors.append("unexpected reboot during soak fails Path B")

    return errors


def emit_example() -> dict[str, Any]:
    # Local import keeps check_scorecard usable if map_llama_bench is missing.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from map_llama_bench import empty_scorecard

    return empty_scorecard()


def check_file(path: Path, schema: dict[str, Any], path_b: bool) -> list[str]:
    try:
        scorecard = load_json(path)
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    if not isinstance(scorecard, dict):
        return [f"{path}: scorecard must be an object"]
    errors = [f"{path}: {e}" for e in validate_schema(scorecard, schema)]
    if path_b:
        errors.extend(f"{path}: {e}" for e in validate_path_b(scorecard))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scorecards", nargs="*", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--path-b",
        action="store_true",
        help="Apply Path B enroll rules (Phase 0 stubs will fail this on purpose)",
    )
    parser.add_argument(
        "--emit-example",
        action="store_true",
        help="Print a schema-valid null scorecard and exit",
    )
    args = parser.parse_args(argv)

    if args.emit_example:
        json.dump(emit_example(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    schema = load_json(args.schema)
    if not args.scorecards:
        if sys.stdin.isatty():
            parser.error("pass scorecard paths, stdin, or --emit-example")
        try:
            scorecard = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            print(f"stdin: invalid JSON: {exc}", file=sys.stderr)
            return 1
        errors = validate_schema(scorecard, schema)
        if args.path_b:
            errors.extend(validate_path_b(scorecard))
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print("ok (stdin)")
        return 0

    all_errors: list[str] = []
    for path in args.scorecards:
        all_errors.extend(check_file(path, schema, args.path_b))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        return 1
    print(f"ok ({len(args.scorecards)} scorecard(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
