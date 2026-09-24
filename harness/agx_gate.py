#!/usr/bin/env python3
"""Fail closed unless this process is the AGX soak host.

The checks live in panopticon ``hypermesh_service.agx_preflight`` (the same
module ``lease_harness host`` runs before enroll). This gate does not copy
the pins. A missing module, a failed report, or a non-AGX class stops the
soak. Stub runs do not call it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

AGX_CLASS_IDS = frozenset({"agx-large", "fyber-agx-orin-64gb"})

ReportRunner = Callable[[], dict[str, Any]]


class PreflightClosed(Exception):
    """The soak must not run."""


def assert_agx_class(class_id: str | None) -> None:
    if class_id not in AGX_CLASS_IDS:
        raise PreflightClosed(
            "bench is AGX-only (agx-large / fyber-agx-orin-64gb); "
            f"refusing class {class_id!r}"
        )


def _sibling_preflight() -> Path | None:
    """Panopticon checkout next to this repo, when one is already on disk."""

    path = (
        Path(__file__).resolve().parents[2]
        / "panopticon"
        / "products"
        / "hypermesh"
        / "src"
        / "hypermesh_service"
        / "agx_preflight.py"
    )
    return path if path.is_file() else None


def load_agx_preflight() -> tuple[Callable[[], dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]]:
    try:
        from hypermesh_service.agx_preflight import collect_facts, evaluate
    except ImportError:
        collect_facts = None
        evaluate = None
    if collect_facts is not None and evaluate is not None:
        return collect_facts, evaluate
    path = _sibling_preflight()
    if path is None:
        raise PreflightClosed(
            "AGX preflight failed closed: hypermesh_service.agx_preflight is not available"
        )
    spec = importlib.util.spec_from_file_location("_hm_agx_preflight", path)
    if spec is None or spec.loader is None:
        raise PreflightClosed(
            "AGX preflight failed closed: hypermesh_service.agx_preflight could not load"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect_facts, module.evaluate


def _detail(report: object) -> str:
    if not isinstance(report, dict):
        return "AGX preflight failed closed: report was not JSON"
    failed = [
        str(item.get("name"))
        for item in report.get("checks") or []
        if isinstance(item, dict) and not item.get("ok")
    ]
    if failed:
        return "AGX preflight failed closed: " + ", ".join(failed)
    return "AGX preflight failed closed"


def run_preflight(runner: ReportRunner | None = None) -> dict[str, Any]:
    if runner is not None:
        report = runner()
    else:
        collect_facts, evaluate = load_agx_preflight()
        report = evaluate(collect_facts())
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise PreflightClosed(_detail(report))
    return report


def gate_execute(runner: ReportRunner | None = None) -> dict[str, Any]:
    """Local AGX check. Exit 3 when it does not pass."""

    try:
        return run_preflight(runner)
    except PreflightClosed as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from exc
