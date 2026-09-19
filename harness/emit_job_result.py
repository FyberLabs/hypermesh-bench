#!/usr/bin/env python3
"""Shape a Path B JobResultBody for the Host agent to POST.

Host already POSTs {passed, image_hash?} on the existing
/api/v1/hypermesh/agent/jobs/{job_id}/result channel. This adapter
does not invent a URL, hash, or tok/s. Unmeasured image_hash stays null.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Locked JobResultRequest fields Host POSTs for path_b (hypermesh-host).
JOB_RESULT_FIELDS = ("passed", "image_hash")

DEFAULT_RAW_REFS = (
    "scorecard.json",
    "llama-bench.json",
    "ttft_hot.json",
    "mem_after_load.json",
    "free-after-load.txt",
    "power.json",
    "identity.json",
    "reliability.json",
    "tegrastats.log",
    "host_probe.json",
    "check_path_b.txt",
)


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip() != "TBD":
        return value.strip()
    return None


def from_scorecard(
    scorecard: dict[str, Any],
    *,
    raw_refs: list[str] | None = None,
    dest_dir: Path | None = None,
) -> dict[str, Any]:
    """Map scorecard → existing JobResultBody. passed is bool (Host lock)."""
    identity = scorecard.get("identity") or {}
    run = scorecard.get("run") or {}
    image_hash = _nonempty_str(identity.get("image_hash"))
    passed = run.get("passed") is True
    body: dict[str, Any] = {
        "passed": passed,
        "image_hash": image_hash,
    }
    # Extra keys are for the Host agent on disk; the POST body stays
    # {passed, image_hash?}. Go unmarshal ignores unknowns.
    refs = list(raw_refs) if raw_refs is not None else list(DEFAULT_RAW_REFS)
    if dest_dir is not None:
        refs = [name for name in refs if (dest_dir / name).exists()]
    body["raw_refs"] = refs
    body["scorecard"] = "scorecard.json"
    body["kind"] = "path_b"
    body["suite_id"] = identity.get("suite_id") or (scorecard.get("identity") or {}).get(
        "prompt_pack_id"
    )
    body["catalog_id"] = run.get("catalog_id") or identity.get("catalog_id")
    body["device_id"] = run.get("device_id")
    body["artifact_hash"] = _nonempty_str(identity.get("artifact_hash"))
    body["post"] = (
        "Host POSTs passed + image_hash on existing "
        "POST /api/v1/hypermesh/agent/jobs/{job_id}/result. "
        "Bench does not POST and does not invent a dashboard URL."
    )
    return body


def write_job_result(path: Path, body: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def emit(
    scorecard: dict[str, Any],
    dest: Path,
    *,
    dest_dir: Path | None = None,
) -> dict[str, Any]:
    body = from_scorecard(scorecard, dest_dir=dest_dir)
    write_job_result(dest, body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scorecard", type=Path, help="Path to scorecard.json")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Default: <scorecard-dir>/job_result.json",
    )
    args = parser.parse_args(argv)
    try:
        scorecard = json.loads(args.scorecard.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not isinstance(scorecard, dict):
        print("error: scorecard must be an object", file=sys.stderr)
        return 1
    dest = args.out or (args.scorecard.parent / "job_result.json")
    body = emit(scorecard, dest, dest_dir=args.scorecard.parent)
    print(dest)
    print(json.dumps({"passed": body["passed"], "image_hash": body["image_hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
