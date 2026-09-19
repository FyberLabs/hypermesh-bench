#!/usr/bin/env python3
"""Invoke llama-bench (native or OCI) and write llama-bench.json.

Never invent tok/s. If the binary is missing, write a not_run stub
(or fail when --require-bench). Runtime is --runtime=nvidia only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


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


def pack_bench_args(pack: dict[str, Any]) -> list[str]:
    prefill = pack.get("prefill_tokens") or [512, 2048]
    if isinstance(prefill, list):
        p_arg = ",".join(str(int(x)) for x in prefill)
    else:
        p_arg = str(prefill)
    n_arg = str(int(pack.get("gen_tokens") or 128))
    r_arg = str(int(pack.get("reps") or 5))
    ngl = str(int(pack.get("ngl") or 99))
    args = [
        "-p",
        p_arg,
        "-n",
        n_arg,
        "-r",
        r_arg,
        "-ngl",
        ngl,
        "--output-format",
        "json",
    ]
    if pack.get("flash_attn", True):
        args.extend(["-fa", "1"])
    return args


def find_llama_bench(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() and os.access(explicit, os.X_OK) else None
    env = os.environ.get("LLAMA_BENCH") or os.environ.get("HM_LLAMA_BENCH")
    if env:
        path = Path(env)
        if path.is_file() and os.access(path, os.X_OK):
            return path
    bin_dir = os.environ.get("LLAMA_BIN_DIR") or os.environ.get("HM_LLAMA_BIN_DIR")
    candidates: list[Path] = []
    if bin_dir:
        candidates.append(Path(bin_dir) / "llama-bench")
    which = shutil.which("llama-bench")
    if which:
        candidates.append(Path(which))
    candidates.extend(
        [
            ROOT / ".cache" / "llama.cpp" / "build-sm87" / "bin" / "llama-bench",
            Path("/opt/hypermesh/bin/llama-bench"),
        ]
    )
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def not_run_payload(reason: str) -> dict[str, Any]:
    return {"status": "not_run", "reason": reason, "results": []}


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _parse_bench_stdout(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        if start < 0:
            start = text.find("{")
        if start >= 0:
            try:
                return json.loads(text[start:])
            except json.JSONDecodeError:
                return None
        return None


def run_native(
    binary: Path,
    *,
    model_path: Path,
    pack: dict[str, Any],
    extra_args: list[str] | None = None,
    timeout_s: int = 3600,
) -> tuple[int, Any, str]:
    cmd = [str(binary), "-m", str(model_path), *pack_bench_args(pack)]
    if extra_args:
        cmd.extend(extra_args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, None, str(exc)
    parsed = _parse_bench_stdout(proc.stdout or "")
    err = (proc.stderr or "").strip()
    return proc.returncode, parsed, err


def run_oci(
    image: str,
    *,
    model_path: Path,
    pack: dict[str, Any],
    models_dir: Path,
    extra_args: list[str] | None = None,
    timeout_s: int = 3600,
    require_bench: bool = True,
) -> tuple[int, Any, str]:
    if not shutil.which("docker"):
        return 1, None, "docker not found"
    try:
        rel = model_path.resolve().relative_to(models_dir.resolve())
        container_model = f"/models/{rel.as_posix()}"
    except ValueError:
        container_model = f"/models/{model_path.name}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--runtime=nvidia",
        "--network",
        "host",
        "-v",
        f"{models_dir.resolve()}:/models:ro",
        "-e",
        f"REQUIRE_BENCH={'1' if require_bench else '0'}",
        image,
        "llama-bench",
        "-m",
        container_model,
        *pack_bench_args(pack),
    ]
    if extra_args:
        cmd.extend(extra_args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, None, str(exc)
    parsed = _parse_bench_stdout(proc.stdout or "")
    err = (proc.stderr or "").strip()
    return proc.returncode, parsed, err


def inspect_image_digest(image: str) -> str | None:
    """Return repo@digest from docker inspect, or None. Never invent."""
    if not image or not shutil.which("docker"):
        return None
    try:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", image],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out or out in ("<no value>", "<nil>"):
        return None
    if "@" not in out:
        return None
    return out


def run_llama_bench(
    *,
    out_path: Path,
    pack: dict[str, Any],
    model_path: Path | None = None,
    binary: Path | None = None,
    image: str | None = None,
    models_dir: Path | None = None,
    stub: bool = False,
    require_bench: bool = False,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    if stub:
        payload = not_run_payload("stub; llama-bench not invoked")
        write_json(out_path, payload)
        return payload

    if image:
        if model_path is None or models_dir is None:
            payload = not_run_payload("oci loader needs --model and --models-dir")
            write_json(out_path, payload)
            if require_bench:
                raise SystemExit("llama-bench oci run missing model/models-dir")
            return payload
        code, parsed, err = run_oci(
            image,
            model_path=model_path,
            pack=pack,
            models_dir=models_dir,
            extra_args=extra_args,
            require_bench=require_bench,
        )
        if parsed is not None:
            write_json(out_path, parsed)
            return parsed if isinstance(parsed, dict) else {"results": parsed}
        reason = err or f"oci llama-bench exited {code} with non-JSON stdout"
        payload = not_run_payload(reason)
        write_json(out_path, payload)
        if require_bench:
            raise SystemExit(reason)
        return payload

    bench = find_llama_bench(binary)
    if bench is None or model_path is None or not model_path.is_file():
        reason = (
            "llama-bench binary missing"
            if bench is None
            else f"model file missing: {model_path}"
        )
        payload = not_run_payload(reason)
        write_json(out_path, payload)
        if require_bench:
            raise SystemExit(reason)
        return payload

    code, parsed, err = run_native(
        bench, model_path=model_path, pack=pack, extra_args=extra_args
    )
    if parsed is not None:
        write_json(out_path, parsed)
        return parsed if isinstance(parsed, dict) else {"results": parsed}
    reason = err or f"llama-bench exited {code} with non-JSON stdout"
    payload = not_run_payload(reason)
    write_json(out_path, payload)
    if require_bench:
        raise SystemExit(reason)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "packs" / "thin-v1")
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--binary", type=Path, default=None)
    parser.add_argument("--image", default=os.environ.get("HM_IMAGE_DIGEST") or None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--require-bench", action="store_true")
    args = parser.parse_args(argv)
    pack = load_pack(args.pack)
    models_dir = args.models_dir
    if models_dir is None and args.model is not None:
        models_dir = args.model.parent
    try:
        run_llama_bench(
            out_path=args.out,
            pack=pack,
            model_path=args.model,
            binary=args.binary,
            image=args.image,
            models_dir=models_dir,
            stub=args.stub,
            require_bench=args.require_bench,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
