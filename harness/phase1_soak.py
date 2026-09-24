#!/usr/bin/env python3
"""Host-job-shaped Phase 1 thin-v1 soak driver for llama-3.1-8b-q4.

Host path_b entrypoint (run only after Host pulls kind=path_b thin-v1).
Emits scorecard.json + job_result.json (passed, image_hash) for the
existing job-result channel. Does not POST the plane override, lease_stop,
or /jobs. Does not invent tok/s, watts, or hashes. Unmeasured fields stay
null.

Exit codes:
  0  scorecard written; --path-b green (or stub dry-run without --require-path-b)
  2  scorecard written; Path B enroll rules red
  3  setup / disk / pin / wrong job kind
  4  skipped (no validation window / preempt denied)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

from check_scorecard import check_file, load_json as load_schema_json  # noqa: E402
from emit_job_result import emit as emit_job_result  # noqa: E402
from host_probe import collect as collect_host, write_probe  # noqa: E402
from soak_gate import SoakRefused, apply_probe_facts, assert_ready_for_soak  # noqa: E402
from run_llama_bench import inspect_image_digest  # noqa: E402
from run_one import find_model, load_pack, load_yaml, run_one  # noqa: E402
from ttft_client import run_ttft  # noqa: E402

EXIT_OK = 0
EXIT_PATH_B_FAIL = 2
EXIT_SETUP = 3
EXIT_SKIP = 4

DEFAULT_MODEL_ID = "llama-3.1-8b-q4"
DEFAULT_CLASS_ID = "fyber-agx-orin-64gb"
DEFAULT_SUITE = "thin-v1"
HEADROOM_GIB = 2.0
BYTES_PER_GIB = 1024**3
# AGX64-1 disk note: prefer thin runtime + one GGUF under ~9.9 GiB free.
THIN_DISK_NOTE_GIB = 9.9

DENIED_WINDOW = {"0", "false", "denied", "no_hold", "closed", "none", "no"}
TRUTHY = {"1", "true", "yes", "on"}


def _env(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def validation_denied() -> tuple[bool, str]:
    """Consume-only: Host/Panopticon said no window. Do not invent a CP API."""
    denied_flag = (_env("HM_VALIDATION_DENIED") or "").lower()
    if denied_flag in TRUTHY:
        return True, "HM_VALIDATION_DENIED"
    window = (_env("HM_VALIDATION_WINDOW") or "").lower()
    if window in DENIED_WINDOW:
        return True, f"HM_VALIDATION_WINDOW={window}"
    return False, ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model_file(model: dict[str, Any], models_dir: Path) -> Path:
    name = model.get("file") or ""
    return models_dir / name


def disk_gate(
    models_dir: Path,
    model: dict[str, Any],
    *,
    dest_exists: bool,
    headroom_gib: float = HEADROOM_GIB,
) -> tuple[bool, str]:
    size = model.get("size_bytes")
    if not isinstance(size, int) or size <= 0:
        return False, "pin size_bytes missing; refuse unmeasured disk need"
    try:
        usage = shutil.disk_usage(models_dir if models_dir.exists() else models_dir.parent)
    except OSError as exc:
        return False, f"disk_usage failed: {exc}"
    need = size if dest_exists else size + int(headroom_gib * BYTES_PER_GIB)
    free = int(usage.free)
    free_gib = free / BYTES_PER_GIB
    need_gib = need / BYTES_PER_GIB
    if free < need:
        return False, (
            f"disk gate: {free_gib:.2f} GiB free on {models_dir}; "
            f"need {need_gib:.2f} GiB (pin {size} B + {headroom_gib:g} GiB headroom). "
            f"Prefer thin OCI runtime + one GGUF pull; do not bake+duplicate "
            f"under ~{THIN_DISK_NOTE_GIB} GiB free."
        )
    return True, (
        f"disk gate ok: {free_gib:.2f} GiB free, need {need_gib:.2f} GiB "
        f"(headroom {headroom_gib:g} GiB)"
    )


def pull_gguf(model_id: str, models_dir: Path, manifest: Path) -> int:
    script = ROOT / "recipes" / "gguf" / "pull-gguf.sh"
    proc = subprocess.run(
        [str(script), "--model-id", model_id, "--manifest", str(manifest), "--out", str(models_dir)],
        check=False,
    )
    return proc.returncode


def write_identity(
    dest: Path,
    *,
    artifact_hash: str | None,
    image_hash: str | None,
    loader: str,
) -> dict[str, Any]:
    body = {
        "image_hash": image_hash,
        "artifact_hash": artifact_hash,
        "loader": loader,
        "note": (
            "artifact_hash is the verified GGUF sha256. "
            "image_hash is repo@digest only when an OCI image was inspected. "
            "Do not invent either. Host may fill job_result image_hash from "
            "host telem when loader=gguf; bench does not mint that hash."
        ),
    }
    (dest / "identity.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def write_mem(dest: Path, *, after_load: bool) -> dict[str, Any]:
    meminfo = Path("/proc/meminfo")
    available: int | None = None
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    available = int(parts[1]) * 1024
                break
    except OSError:
        available = None
    free_txt = dest / "free-after-load.txt"
    code, out = 0, ""
    try:
        proc = subprocess.run(["free", "-b"], capture_output=True, text=True, check=False)
        code, out = proc.returncode, proc.stdout or ""
    except OSError:
        code, out = 127, ""
    if code == 0 and out:
        free_txt.write_text(out, encoding="utf-8")
    else:
        free_txt.write_text(
            "free -b unavailable; usable_ram_gib stays null until measured after load.\n",
            encoding="utf-8",
        )
    usable = None
    ram_after = None
    if after_load and available is not None:
        usable = round(available / BYTES_PER_GIB, 3)
        ram_after = usable
    body = {
        "usable_ram_gib": usable,
        "ram_after_load_gib": ram_after,
        "mem_available_bytes": available,
        "after_load": after_load,
        "source": "free -b / MemAvailable",
        "note": (
            "usable_ram_gib stays null until weights+KV were loaded. "
            "Do not treat cudaMemGetInfo as Tegra allocator truth."
        ),
    }
    (dest / "mem_after_load.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )
    return body


def write_power(dest: Path, *, stub: bool) -> dict[str, Any]:
    script = ROOT / "recipes" / "power" / "sample-tegrastats.sh"
    if not stub and script.is_file() and shutil.which("tegrastats"):
        subprocess.run([str(script), str(dest), "15", "1000"], check=False)
        existing = dest / "power.json"
        if existing.exists():
            try:
                data = json.loads(existing.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
    body = {
        "method": "unavailable",
        "note": (
            "No wall meter attached and tegrastats not sampled. "
            "wall_watts_* stay null. Do not invent watts."
        ),
        "idle_w": None,
        "load_w": None,
        "junction_c_max": None,
        "log": None,
    }
    (dest / "power.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    log = dest / "tegrastats.log"
    if not log.exists():
        log.write_text("tegrastats not sampled\n", encoding="utf-8")
    return body


def write_reliability(dest: Path) -> dict[str, Any]:
    body = {
        "unexpected_restart": False,
        "oom": False,
        "reboot_rate_per_24h": None,
        "path_b": None,
        "note": "reboot_rate stays null until a soak supervisor measures it",
    }
    (dest / "reliability.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )
    return body


def run_path_b_check(scorecard_path: Path, dest: Path) -> tuple[bool, list[str]]:
    schema = load_schema_json(ROOT / "schemas" / "scorecard.schema.json")
    errors = check_file(scorecard_path, schema, path_b=True)
    text = (
        "ok: path-b green\n"
        if not errors
        else "path-b red\n" + "\n".join(errors) + "\n"
    )
    (dest / "check_path_b.txt").write_text(text, encoding="utf-8")
    return (not errors), errors


def maybe_set_nvpmodel(nvpmodel_id: int, *, stub: bool) -> None:
    if stub:
        return
    if shutil.which("nvpmodel") is None:
        return
    subprocess.run(["nvpmodel", "-m", str(nvpmodel_id)], check=False)


def run_soak(args: argparse.Namespace) -> int:
    kind = args.job_kind or _env("HM_JOB_KIND") or "path_b"
    if kind != "path_b":
        print(
            f"error: this entrypoint is Host path_b only (got kind={kind!r}). "
            "Not lease_stop / chat.",
            file=sys.stderr,
        )
        return EXIT_SETUP

    denied, why = validation_denied()
    if denied:
        print(f"skip: no validation window ({why})", file=sys.stderr)
        return EXIT_SKIP

    suite = args.suite_id or _env("HM_SUITE_ID") or DEFAULT_SUITE
    if suite not in (DEFAULT_SUITE, "", "packs/thin-v1"):
        # Allow a pack path that ends with thin-v1.
        if Path(suite).name not in (DEFAULT_SUITE,) and not str(suite).endswith("thin-v1"):
            print(f"error: unknown suite_id {suite!r}; only thin-v1", file=sys.stderr)
            return EXIT_SETUP

    model_id = args.model_id or _env("HM_CATALOG_ID") or DEFAULT_MODEL_ID
    class_id = args.class_id or _env("HM_CLASS_ID") or DEFAULT_CLASS_ID
    device_id = args.device_id or _env("HM_DEVICE_ID")
    loader = args.loader or _env("HM_LOADER") or "gguf"
    if loader not in ("gguf", "oci"):
        print(f"error: loader must be gguf or oci (got {loader!r})", file=sys.stderr)
        return EXIT_SETUP

    models_dir = Path(
        args.models_dir or _env("HM_MODELS_DIR") or "/var/lib/hypermesh/models"
    )
    out_root = Path(args.out or _env("HM_OUT_DIR") or _default_out())
    pack_dir = Path(args.pack)
    manifest_path = Path(args.manifest)
    image = args.image or _env("HM_IMAGE_DIGEST") or None
    require_path_b = args.require_path_b or (_env("HM_REQUIRE_PATH_B") or "").lower() in TRUTHY
    stub = args.stub
    skip_pull = args.skip_pull or stub
    skip_hot = args.skip_hot_window or stub

    if not manifest_path.is_file():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return EXIT_SETUP
    if not (pack_dir / "pack.yaml").is_file():
        print(f"error: pack.yaml not found in {pack_dir}", file=sys.stderr)
        return EXIT_SETUP

    manifest = load_yaml(manifest_path)
    pack = load_pack(pack_dir)
    try:
        model = find_model(manifest, model_id)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return EXIT_SETUP

    pin = model.get("sha256")
    if not isinstance(pin, str) or pin in ("TBD", "<pin>", "") or len(pin) != 64:
        print(f"error: refuse unpinned sha256 for {model_id}: {pin!r}", file=sys.stderr)
        return EXIT_SETUP

    ready = None
    if not stub:
        try:
            ready = assert_ready_for_soak(device_id)
        except SoakRefused as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_SETUP
        device_id = str(ready["device_id"])

    dest = out_root / model_id
    dest.mkdir(parents=True, exist_ok=True)

    probe = collect_host(
        models_dir=models_dir,
        out_dir=dest,
        device_id=device_id,
        class_id=class_id,
    )
    if ready is not None:
        apply_probe_facts(probe, ready["facts"], device_id)
        probe["preflight"] = ready["preflight"]
    write_probe(dest / "host_probe.json", probe)

    model_path = resolve_model_file(model, models_dir)
    dest_exists = model_path.is_file()
    if dest_exists:
        try:
            dest_exists = sha256_file(model_path).lower() == pin.lower()
        except OSError:
            dest_exists = False

    if not stub:
        models_dir.mkdir(parents=True, exist_ok=True)
        ok, msg = disk_gate(models_dir, model, dest_exists=dest_exists)
        print(msg)
        if not ok:
            return EXIT_SETUP
        maybe_set_nvpmodel(int(pack.get("nvpmodel_id") or 2), stub=False)
        if not skip_pull and not dest_exists:
            code = pull_gguf(model_id, models_dir, manifest_path)
            if code != 0:
                print("error: pull-gguf.sh failed", file=sys.stderr)
                return EXIT_SETUP
            dest_exists = model_path.is_file()
        if dest_exists:
            actual = sha256_file(model_path)
            if actual.lower() != pin.lower():
                print(
                    f"error: sha256 mismatch {model_path}: expected {pin} got {actual}",
                    file=sys.stderr,
                )
                return EXIT_SETUP
            artifact_hash = actual
        else:
            artifact_hash = None
            if args.require_model:
                print(f"error: model file missing: {model_path}", file=sys.stderr)
                return EXIT_SETUP
    else:
        artifact_hash = None
        print("stub: skip pull, nvpmodel set, and disk fail")

    image_hash = None
    if loader == "oci" and image:
        image_hash = inspect_image_digest(image)
        if image_hash is None and "@" in image:
            # Caller already passed repo@digest; record only that measured pin.
            image_hash = image if image.startswith("sha256:") is False else None
            if image.count("@") == 1 and not image.split("@", 1)[1].startswith("TBD"):
                image_hash = image

    write_identity(dest, artifact_hash=artifact_hash, image_hash=image_hash, loader=loader)

    try:
        scorecard_path = run_one(
            manifest_path=manifest_path,
            pack_dir=pack_dir,
            model_id=model_id,
            out_dir=out_root,
            stub=stub,
            require_bench=args.require_bench and not stub,
            model_path=model_path if model_path.is_file() else None,
            models_dir=models_dir,
            image=image if loader == "oci" else None,
            device_id=device_id,
            loader=loader,
            artifact_hash=artifact_hash,
            image_hash=image_hash,
            host_probe=probe,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return EXIT_SETUP

    bench_raw = {}
    try:
        bench_raw = json.loads((dest / "llama-bench.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        bench_raw = {}
    bench_ran = isinstance(bench_raw, (dict, list)) and (
        (isinstance(bench_raw, dict) and bench_raw.get("status") not in ("not_run", "unavailable"))
        or isinstance(bench_raw, list)
    )
    write_mem(dest, after_load=bool(bench_ran) and not stub)
    write_power(dest, stub=stub)
    write_reliability(dest)

    hot_s = 0 if skip_hot else int(args.hot_window_s if args.hot_window_s is not None else pack.get("hot_window_s") or 0)
    endpoint = args.completions_url or _env("HM_COMPLETIONS_URL") or "http://127.0.0.1:8080/v1/chat/completions"
    run_ttft(
        out_path=dest / "ttft_hot.json",
        endpoint=endpoint,
        hot_window_s=hot_s,
        reps=int(pack.get("reps") or 5),
        max_tokens=int(pack.get("gen_tokens") or 128),
        stub=stub,
        skip=skip_hot and not stub,
    )

    # Re-assemble so ttft / mem / identity land on the scorecard.
    scorecard_path = run_one(
        manifest_path=manifest_path,
        pack_dir=pack_dir,
        model_id=model_id,
        out_dir=out_root,
        stub=True,  # raws already written; do not re-invoke bench
        device_id=device_id,
        loader=loader,
        artifact_hash=artifact_hash,
        image_hash=image_hash,
        host_probe=probe,
    )

    schema_errors = check_file(
        scorecard_path,
        load_schema_json(ROOT / "schemas" / "scorecard.schema.json"),
        path_b=False,
    )
    if schema_errors:
        print("error: scorecard failed schema", file=sys.stderr)
        print("\n".join(schema_errors), file=sys.stderr)
        return EXIT_SETUP

    path_b_ok, path_b_errors = run_path_b_check(scorecard_path, dest)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["run"]["ended_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scorecard["run"]["passed"] = True if path_b_ok else False
    scorecard["reliability"]["path_b"] = "pass" if path_b_ok else "fail"
    if args.skip_reason:
        scorecard["run"]["skip_reason"] = args.skip_reason
    scorecard_path.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")

    body = emit_job_result(scorecard, dest / "job_result.json", dest_dir=dest)
    print(scorecard_path)
    print(dest / "job_result.json")
    print(json.dumps({"passed": body["passed"], "image_hash": body["image_hash"]}))
    if path_b_ok:
        return EXIT_OK
    if require_path_b:
        print("path-b red (--require-path-b)", file=sys.stderr)
        print("\n".join(path_b_errors), file=sys.stderr)
        return EXIT_PATH_B_FAIL
    if stub:
        # Dry-run produced a schema-valid bundle with nulls. Not a Path B pass.
        return EXIT_OK
    print("path-b red", file=sys.stderr)
    print("\n".join(path_b_errors), file=sys.stderr)
    return EXIT_PATH_B_FAIL


def _default_out() -> str:
    host = os.uname().nodename if hasattr(os, "uname") else "host"
    day = datetime.now().strftime("%Y%m%d")
    return str(ROOT / "out" / f"{host}-{day}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase1_soak",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model-id", default=None, help=f"Default: {DEFAULT_MODEL_ID}")
    parser.add_argument("--pack", type=Path, default=ROOT / "packs" / "thin-v1")
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "models" / "agx64-batch12.yaml"
    )
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--class-id", default=None)
    parser.add_argument("--suite-id", default=None)
    parser.add_argument("--job-kind", default=None, help="Must be path_b when set")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--loader", choices=("gguf", "oci"), default=None)
    parser.add_argument("--image", default=None, help="OCI image ref (loader=oci)")
    parser.add_argument("--completions-url", default=None)
    parser.add_argument("--hot-window-s", type=int, default=None)
    parser.add_argument("--skip-hot-window", action="store_true")
    parser.add_argument("--skip-pull", action="store_true")
    parser.add_argument("--stub", action="store_true", help="CI / dry-run; no GPU")
    parser.add_argument("--require-bench", action="store_true")
    parser.add_argument("--require-model", action="store_true")
    parser.add_argument(
        "--require-path-b",
        action="store_true",
        help="Exit 2 if check_scorecard --path-b is red",
    )
    parser.add_argument("--skip-reason", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_soak(args)


if __name__ == "__main__":
    raise SystemExit(main())
