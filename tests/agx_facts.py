"""Passing AGX preflight facts for unit tests. Not a live Jetson."""

from __future__ import annotations

import json
from pathlib import Path

KNOWN_DEVICE_ID = "a6400000-0640-4000-8000-000000000001"

PASSING_FACTS = {
    "arch": "aarch64",
    "kernel": "6.8.12-tegra",
    "l4t": "R39.2.1",
    "ubuntu": "24.04.5",
    "cuda": "13.2.1",
    "default_target": "multi-user.target",
    "packages": {"gdm3": True, "nvidia-l4t-3d-core": True},
    "boot_control": True,
    "nvpmodel_id": 2,
    "power_mode": "30W",
    "jetson_clocks_present": True,
    "jetson_clocks": False,
    "tegrastats": True,
    "docker": True,
    "nvidia_runtime": True,
    "wireguard_tools": True,
    "llama_bench": True,
}


class EnrolledHost:
    def __init__(self, directory: Path) -> None:
        import soak_gate

        self._soak_gate = soak_gate
        self._previous_path = soak_gate.DEVICE_JSON
        self._previous_collect = soak_gate.collect_facts
        state = directory / "device.json"
        state.write_text(
            json.dumps(
                {"device_id": KNOWN_DEVICE_ID, "device_secret": "hm_dev_test"}
            ),
            encoding="utf-8",
        )
        soak_gate.DEVICE_JSON = state
        soak_gate.collect_facts = lambda **_kwargs: dict(PASSING_FACTS)

    def close(self) -> None:
        self._soak_gate.DEVICE_JSON = self._previous_path
        self._soak_gate.collect_facts = self._previous_collect
