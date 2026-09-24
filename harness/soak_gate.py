"""Fail closed before a real AGX soak.

The soak host is the known-host list shared with panopticon
``seed.KNOWN_HOSTS`` and infra ``config/hypermesh-known-hosts.yaml``.
Today that row is AGX64-1. Enrollment is ``/var/lib/hypermesh/device.json``
written by that enroll path. ``HM_DEVICE_ID`` does not enroll a host.

``harness/agx_preflight.py`` is the same check as panopticon
``agx_preflight.py`` and infra ``scripts/agx-preflight.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agx_preflight import collect_facts, evaluate

# panopticon seed.FYBER_AGX_* and infra config/hypermesh-known-hosts.yaml.
KNOWN_DEVICE_ID = "a6400000-0640-4000-8000-000000000001"
KNOWN_SERIAL = "FYBER-AGX-ORIN-64-001"
KNOWN_LABEL = "AGX64-1"
KNOWN_CLASS = "agx-large"
BENCH_CLASS = "fyber-agx-orin-64gb"
# Fit may record nx-volume and thor. Neither soaks until that hardware arrives.
SOAK_PLANES = frozenset({KNOWN_CLASS})
DEVICE_JSON = Path("/var/lib/hypermesh/device.json")


class SoakRefused(Exception):
    """Preflight failed or the box is not the enrolled known host."""


def read_enrolled_device(path: Path | None = None) -> str | None:
    """Return the known device id when enroll state is that host."""

    state = path or DEVICE_JSON
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    device_id = str(data.get("device_id") or "").strip()
    secret = data.get("device_secret")
    if device_id != KNOWN_DEVICE_ID:
        return None
    if not isinstance(secret, str) or not secret.strip():
        return None
    return device_id


def assert_ready_for_soak(
    device_id: str | None = None,
    *,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Run AGX preflight and require the enrolled known host.

    Always runs preflight. A failure raises ``SoakRefused`` and must not
    start llama-bench. ``device_id`` may be omitted. A different id refuses.
    """

    facts = collect_facts()
    report = evaluate(facts)
    state = state_path or DEVICE_JSON
    enrolled = read_enrolled_device(state)
    problems: list[str] = []
    if report.get("ok") is not True:
        failed = [
            str(item.get("name"))
            for item in report.get("checks") or []
            if isinstance(item, dict) and not item.get("ok")
        ]
        detail = ", ".join(name for name in failed if name) or "no checks"
        problems.append(f"AGX preflight failed closed: {detail}")
    if enrolled is None:
        problems.append(
            "soak refused: "
            f"{state} is not enrolled known host {KNOWN_LABEL} "
            f"({KNOWN_DEVICE_ID})"
        )
    else:
        requested = (device_id or "").strip()
        if requested and requested != enrolled:
            problems.append(
                "soak refused: device id is not enrolled known host "
                f"{KNOWN_LABEL} ({KNOWN_DEVICE_ID})"
            )
    if problems:
        raise SoakRefused("\n".join(problems))
    return {"device_id": enrolled, "facts": facts, "preflight": report}


def apply_probe_facts(
    probe: dict[str, Any],
    facts: dict[str, Any],
    device_id: str,
) -> dict[str, Any]:
    """Fill host fields from a preflight that already passed."""

    probe["device_id"] = device_id
    probe["class_id"] = BENCH_CLASS
    probe["jetpack_l4t"] = facts.get("l4t")
    probe["cuda"] = facts.get("cuda")
    probe["nvpmodel_id"] = facts.get("nvpmodel_id")
    probe["power_mode"] = facts.get("power_mode")
    clocks = facts.get("jetson_clocks")
    if isinstance(clocks, bool):
        probe["jetson_clocks"] = clocks
    return probe
