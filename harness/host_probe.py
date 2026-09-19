#!/usr/bin/env python3
"""Probe JetPack/L4T host facts for a Path B scorecard.

Reads only what is on this box. Unmeasured fields stay null. Does not
invent JetPack strings, CUDA versions, watts, or hashes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

NV_TEGRA_RELEASE = Path("/etc/nv_tegra_release")
CUDA_VERSION_TXT = Path("/usr/local/cuda/version.txt")
CUDA_VERSION_JSON = Path("/usr/local/cuda/version.json")
SERIAL_DT = Path("/proc/device-tree/serial-number")
MEMINFO = Path("/proc/meminfo")

BYTES_PER_GIB = 1024**3


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, "", ""
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def probe_l4t(*, release_path: Path = NV_TEGRA_RELEASE) -> str | None:
    """Parse /etc/nv_tegra_release into an Rxx.y.z label, or None."""
    text = _read_text(release_path)
    if not text:
        return None
    first = text.splitlines()[0].strip()
    release = re.search(r"R(\d+)", first)
    revision = re.search(r"REVISION:\s*([0-9.]+)", first)
    if release and revision:
        return f"R{release.group(1)}.{revision.group(1)}"
    return first or None


def probe_cuda() -> str | None:
    code, out, _ = _run(["nvcc", "--version"])
    if code == 0 and out:
        match = re.search(r"release\s+([0-9.]+)", out)
        if match:
            return match.group(1)
        match = re.search(r"V([0-9.]+)", out)
        if match:
            return match.group(1)
    if CUDA_VERSION_JSON.is_file():
        try:
            data = json.loads(CUDA_VERSION_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            cuda = data.get("cuda")
            if isinstance(cuda, dict) and cuda.get("version"):
                return str(cuda["version"])
            if data.get("version"):
                return str(data["version"])
    txt = _read_text(CUDA_VERSION_TXT)
    if txt:
        match = re.search(r"CUDA Version\s+([0-9.]+)", txt, re.I)
        if match:
            return match.group(1)
        return txt.splitlines()[0].strip() or None
    return None


def probe_nvpmodel() -> tuple[int | None, str | None, str | None]:
    """Return (nvpmodel_id, power_mode_label, raw_query)."""
    code, out, err = _run(["nvpmodel", "-q"])
    raw = (out or err).strip() or None
    if code != 0 or not raw:
        return None, None, raw
    nvp_id: int | None = None
    label: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        mode = re.search(r"NV Power Mode:\s*(\S+)", line, re.I)
        if mode:
            token = mode.group(1)
            label = token
            watts = re.search(r"(\d+)\s*W", token, re.I)
            if watts:
                label = f"{watts.group(1)}W"
            continue
        if re.fullmatch(r"-?\d+", line):
            nvp_id = int(line)
    if label and label.upper().startswith("MODE_"):
        rest = label.split("_", 1)[1]
        watts = re.search(r"(\d+)", rest)
        if watts:
            label = f"{watts.group(1)}W"
    return nvp_id, label, raw


def probe_jetson_clocks() -> bool | None:
    """True/False only when the host can say; otherwise null."""
    code, out, err = _run(["jetson_clocks", "--show"])
    text = f"{out}\n{err}".strip()
    if code == 127 or not text:
        if shutil.which("jetson_clocks") is None:
            return None
        return None
    lowered = text.lower()
    if "already running" in lowered or "jetson_clocks is running" in lowered:
        return True
    if "not running" in lowered or "inactive" in lowered:
        return False
    # --show dumps current clocks; do not guess enabled vs stock.
    return None


def probe_disk(path: Path) -> dict[str, Any]:
    target = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return {
            "path": str(path),
            "disk_free_bytes": None,
            "disk_free_gib": None,
            "disk_total_bytes": None,
        }
    return {
        "path": str(path),
        "disk_free_bytes": int(usage.free),
        "disk_free_gib": round(usage.free / BYTES_PER_GIB, 3),
        "disk_total_bytes": int(usage.total),
    }


def probe_meminfo(*, meminfo_path: Path = MEMINFO) -> dict[str, Any]:
    text = _read_text(meminfo_path)
    available: int | None = None
    total: int | None = None
    if text:
        for line in text.splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    available = int(parts[1]) * 1024
            elif line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    total = int(parts[1]) * 1024
    return {
        "mem_available_bytes": available,
        "mem_total_bytes": total,
        "mem_available_gib": (
            round(available / BYTES_PER_GIB, 3) if available is not None else None
        ),
    }


def probe_serial(*, serial_path: Path = SERIAL_DT) -> str | None:
    raw = _read_text(serial_path)
    if not raw:
        return None
    return raw.replace("\x00", "").strip() or None


def collect(
    *,
    models_dir: Path | None = None,
    out_dir: Path | None = None,
    device_id: str | None = None,
    class_id: str = "fyber-agx-orin-64gb",
) -> dict[str, Any]:
    nvp_id, power_mode, nvp_raw = probe_nvpmodel()
    models_disk = probe_disk(models_dir) if models_dir is not None else None
    out_disk = probe_disk(out_dir) if out_dir is not None else None
    mem = probe_meminfo()
    l4t = probe_l4t()
    cuda = probe_cuda()
    clocks = probe_jetson_clocks()
    serial = probe_serial()
    free_bytes = None
    if models_disk and models_disk.get("disk_free_bytes") is not None:
        free_bytes = models_disk["disk_free_bytes"]
    elif out_disk and out_disk.get("disk_free_bytes") is not None:
        free_bytes = out_disk["disk_free_bytes"]
    return {
        "class_id": class_id,
        "device_id": device_id,
        "serial": serial,
        "jetpack_l4t": l4t,
        "cuda": cuda,
        "nvpmodel_id": nvp_id,
        "power_mode": power_mode,
        "jetson_clocks": clocks,
        "disk_free_bytes": free_bytes,
        "disk_free_gib": (
            round(free_bytes / BYTES_PER_GIB, 3) if free_bytes is not None else None
        ),
        "models_dir": models_disk,
        "out_dir": out_disk,
        "memory": mem,
        "nvpmodel_raw": nvp_raw,
        "hostname": os.uname().nodename if hasattr(os, "uname") else None,
    }


def apply_to_scorecard(scorecard: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    host = scorecard.setdefault("host", {})
    run = scorecard.setdefault("run", {})
    if probe.get("jetpack_l4t") is not None:
        host["jetpack_l4t"] = probe["jetpack_l4t"]
    if probe.get("cuda") is not None:
        host["cuda"] = probe["cuda"]
    if probe.get("power_mode") is not None:
        host["power_mode"] = probe["power_mode"]
    if probe.get("nvpmodel_id") is not None:
        host["nvpmodel_id"] = probe["nvpmodel_id"]
    if probe.get("jetson_clocks") is not None:
        host["jetson_clocks"] = probe["jetson_clocks"]
    if probe.get("serial") is not None:
        host["serial"] = probe["serial"]
    if probe.get("device_id") is not None:
        host["device_id"] = probe["device_id"]
        run["device_id"] = probe["device_id"]
    mem = probe.get("memory") or {}
    storage = scorecard.setdefault("storage", {})
    models_disk = probe.get("models_dir") or {}
    if models_disk.get("disk_total_bytes") is not None:
        storage["nvme_gib"] = round(models_disk["disk_total_bytes"] / BYTES_PER_GIB, 3)
    # usable_ram stays null until after weights+KV; probe only records host facts.
    _ = mem
    return scorecard


def write_probe(path: Path, probe: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--device-id", default=os.environ.get("HM_DEVICE_ID") or None)
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write host_probe.json here (default: stdout)",
    )
    args = parser.parse_args(argv)
    probe = collect(
        models_dir=args.models_dir,
        out_dir=args.out_dir,
        device_id=args.device_id,
    )
    if args.write:
        write_probe(args.write, probe)
        print(args.write)
        return 0
    json.dump(probe, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
