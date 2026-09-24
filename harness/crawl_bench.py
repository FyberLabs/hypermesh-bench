#!/usr/bin/env python3
"""Crawl allowlisted GGUF depots, record candidates, fit, and bench.

Metadata only. This does not download weights, POST to the plane, or invent
tok/s. Hugging Face is the only download host the host agent allowlists
(hypermesh-host internal/pull/trust.go). Publishers match that allowlist and
AIuditor crawl.GGUF_PUBLISHER_ALLOWLIST.

Fit numbers match AIuditor fit.py: file size + 1.5 GiB KV headroom against
class RAM after OS reserve. The bench pack in this repo is thin-v1 on
fyber-agx-orin-64gb (plane class agx-large). nx-volume and thor are fit-only.
They are not soak hardware.

``--execute`` soaks only the enrolled known host (AGX64-1) after AGX
preflight. ``HM_DEVICE_ID`` does not enroll a host and does not start a soak.
Stub mode does not call preflight and does not start llama-bench.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

from soak_gate import (  # noqa: E402
    KNOWN_CLASS,
    KNOWN_DEVICE_ID,
    KNOWN_LABEL,
    SOAK_PLANES,
    SoakRefused,
    assert_ready_for_soak,
)

DEPOT_HUGGINGFACE = "huggingface"
HF_API = "https://huggingface.co/api/models"
HF_HOSTS = frozenset({"huggingface.co", "hf.co"})

# Mirrors hypermesh-host internal/pull/trust.go and AIuditor crawl.py.
GGUF_PUBLISHER_ALLOWLIST = frozenset(
    {
        "bartowski",
        "thebloke",
        "quantfactory",
        "ggml-org",
        "lmstudio-community",
        "fyberlabs",
    }
)

KV_HEADROOM_GIB = 1.5
PREFERRED_QUANT = "Q4_K_M"
GIB = 1024**3

# Underscore-separated quant tokens (Q4_K_M, IQ2_XXS). BF16 before F16 so
# the shorter token does not win inside BF16.
_QUANT_RE = re.compile(
    r"(?i)(IQ[1-4](?:_[A-Z0-9]+)*|Q[2-8](?:_[A-Z0-9]+)*|BF16|F16|F32)"
)
_SHARD_RE = re.compile(r"(?i)(?:^|[.\-_])\d+-of-\d+(?:[.\-_]|\.|$)")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ClassBudget:
    plane_class_id: str
    bench_class_id: str
    memory_gib: float
    os_reserve_gib: float
    pack_rel: str | None
    sku: str
    backend: str | None
    power_profile: str | None
    nvpmodel_id: int | None

    @property
    def usable_gib(self) -> float:
        return max(0.0, self.memory_gib - self.os_reserve_gib)


# Budgets match aiuditor_service.fit. Bench class ids are this repo's scorecard
# ids. Only agx-large has a pack here (packs/thin-v1).
CLASS_BUDGETS: dict[str, ClassBudget] = {
    "nx-volume": ClassBudget(
        plane_class_id="nx-volume",
        bench_class_id="nx-volume",
        memory_gib=16.0,
        os_reserve_gib=2.0,
        pack_rel=None,
        sku="Orin NX 16GB",
        backend=None,
        power_profile=None,
        nvpmodel_id=None,
    ),
    "agx-large": ClassBudget(
        plane_class_id="agx-large",
        bench_class_id="fyber-agx-orin-64gb",
        memory_gib=64.0,
        os_reserve_gib=4.0,
        pack_rel="packs/thin-v1",
        sku="NVIDIA Jetson AGX Orin 64GB",
        backend="cuda-jetson",
        power_profile="30W",
        nvpmodel_id=2,
    ),
    "thor": ClassBudget(
        plane_class_id="thor",
        bench_class_id="thor",
        memory_gib=128.0,
        os_reserve_gib=6.0,
        pack_rel=None,
        sku="AGX Thor 128GB",
        backend=None,
        power_profile=None,
        nvpmodel_id=None,
    ),
}

CLASS_ALIASES = {
    "nx-volume": "nx-volume",
    "agx-large": "agx-large",
    "fyber-agx-orin-64gb": "agx-large",
    "thor": "thor",
}

# Prefer the class that has a bench pack when several classes fit.
_BENCH_CLASS_ORDER = ("agx-large", "nx-volume", "thor")


class CrawlError(Exception):
    pass


class JsonFetch(Protocol):
    def get_json(self, url: str) -> Any: ...


@dataclass(frozen=True)
class Artifact:
    depot: str
    publisher: str
    repo: str
    revision: str
    file_name: str
    size_bytes: int
    sha256: str | None
    quant: str | None
    license: str | None
    last_modified: str | None
    shard: bool

    @property
    def source_ref(self) -> str:
        return f"{self.repo}@{self.revision}/{self.file_name}"

    @property
    def url(self) -> str:
        return hf_resolve_url(self.repo, self.revision, self.file_name)

    @property
    def catalog_id(self) -> str:
        return make_catalog_id(self.publisher, self.file_name, self.sha256)


@dataclass(frozen=True)
class LabNode:
    device_id: str
    class_id: str
    label: str | None = None
    lab: bool = False
    enrolled: bool = False
    path_b: str = ""
    schedule_hold: bool = True
    created_at: str = ""


@dataclass(frozen=True)
class FitRow:
    plane_class_id: str
    bench_class_id: str
    fits: bool
    usable_gib: float
    needed_gib: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.plane_class_id,
            "bench_class_id": self.bench_class_id,
            "fits": self.fits,
            "usable_gib": self.usable_gib,
            "needed_gib": self.needed_gib,
        }


def plane_class(class_id: str | None) -> str | None:
    if not class_id:
        return None
    return CLASS_ALIASES.get(class_id.strip())


def sha256_hex(oid: object) -> str | None:
    text = str(oid or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:") :]
    if _SHA_RE.fullmatch(text):
        return text
    return None


def make_catalog_id(publisher: str, file_name: str, digest: str | None) -> str:
    stem = file_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    raw = f"hf-{publisher}-{stem}".lower()
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._") or "hf-artifact"
    if len(cleaned) <= 64:
        return cleaned
    suffix = (digest or "nosha")[:8]
    return f"{cleaned[:55].rstrip('-._')}-{suffix}"


def hf_models_url(publisher: str, limit: int) -> str:
    return (
        f"{HF_API}?author={quote(publisher)}"
        f"&sort=lastModified&direction=-1&limit={int(limit)}"
    )


def hf_tree_url(repo: str, revision: str) -> str:
    return f"{HF_API}/{quote(repo, safe='/')}/tree/{quote(revision, safe='')}?recursive=true"


def hf_resolve_url(repo: str, revision: str, file_name: str) -> str:
    file_path = "/".join(quote(part) for part in file_name.split("/"))
    return (
        f"https://huggingface.co/{quote(repo, safe='/')}/resolve/"
        f"{quote(revision, safe='')}/{file_path}"
    )


def _quant(file_name: str) -> str | None:
    match = _QUANT_RE.search(file_name)
    return match.group(1).upper() if match else None


def _is_shard(file_name: str) -> bool:
    return _SHARD_RE.search(file_name) is not None


def _license_from_tags(tags: object) -> str | None:
    if not isinstance(tags, list):
        return None
    for tag in tags:
        text = str(tag).strip()
        if text.lower().startswith("license:"):
            value = text.split(":", 1)[1].strip().lower()
            return value[:64] or None
    return None


def _looks_like_gguf_repo(model: dict[str, Any]) -> bool:
    tags = [str(tag).lower() for tag in (model.get("tags") or [])]
    if "gguf" in tags:
        return True
    model_id = str(model.get("id") or model.get("modelId") or "").lower()
    return model_id.endswith("-gguf") or model_id.endswith("_gguf")


def _publisher_of(repo: str) -> str:
    return (repo.split("/", 1)[0] or "").strip().lower()


def discover_huggingface(
    fetch: JsonFetch,
    publishers: tuple[str, ...],
    *,
    repo_limit: int,
    revision: str = "main",
    errors: list[str] | None = None,
) -> list[Artifact]:
    """List newest GGUF files for allowlisted publishers. No blob GET."""

    found: list[Artifact] = []
    seen: set[str] = set()
    for publisher in publishers:
        if publisher not in GGUF_PUBLISHER_ALLOWLIST:
            if errors is not None:
                errors.append(f"untrusted publisher {publisher}")
            continue
        try:
            body = fetch.get_json(hf_models_url(publisher, repo_limit))
        except CrawlError as exc:
            if errors is not None:
                errors.append(str(exc))
            continue
        if not isinstance(body, list):
            if errors is not None:
                errors.append(f"{publisher}: models API did not return a list")
            continue
        repos = 0
        for model in body:
            if repos >= repo_limit:
                break
            if not isinstance(model, dict) or model.get("private") is True:
                continue
            repo = str(model.get("id") or model.get("modelId") or "").strip()
            if _publisher_of(repo) != publisher or not _looks_like_gguf_repo(model):
                continue
            repos += 1
            try:
                tree = fetch.get_json(hf_tree_url(repo, revision))
            except CrawlError as exc:
                if errors is not None:
                    errors.append(str(exc))
                continue
            if not isinstance(tree, list):
                if errors is not None:
                    errors.append(f"{repo}: tree API did not return a list")
                continue
            license_name = _license_from_tags(model.get("tags"))
            modified = model.get("lastModified")
            last_modified = str(modified) if modified else None
            for entry in tree:
                artifact = _artifact_from_tree(
                    publisher=publisher,
                    repo=repo,
                    revision=revision,
                    entry=entry,
                    license_name=license_name,
                    last_modified=last_modified,
                )
                if artifact is None or artifact.source_ref in seen:
                    continue
                seen.add(artifact.source_ref)
                found.append(artifact)
    found.sort(key=lambda row: (row.last_modified or "", row.source_ref), reverse=True)
    return found


def _artifact_from_tree(
    *,
    publisher: str,
    repo: str,
    revision: str,
    entry: object,
    license_name: str | None,
    last_modified: str | None,
) -> Artifact | None:
    if not isinstance(entry, dict):
        return None
    path = str(entry.get("path") or "")
    if not path.lower().endswith(".gguf"):
        return None
    if entry.get("type") not in (None, "file"):
        return None
    try:
        size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else {}
    digest = sha256_hex(lfs.get("oid"))
    return Artifact(
        depot=DEPOT_HUGGINGFACE,
        publisher=publisher,
        repo=repo,
        revision=revision,
        file_name=path,
        size_bytes=size,
        sha256=digest,
        quant=_quant(path),
        license=license_name,
        last_modified=last_modified,
        shard=_is_shard(path),
    )


def fits_class(plane: str, size_bytes: int) -> FitRow | None:
    budget = CLASS_BUDGETS.get(plane)
    if budget is None:
        return None
    if size_bytes < 0:
        size_bytes = 0
    needed = (size_bytes / GIB) + KV_HEADROOM_GIB
    usable = budget.usable_gib
    return FitRow(
        plane_class_id=budget.plane_class_id,
        bench_class_id=budget.bench_class_id,
        fits=needed <= usable + 1e-9,
        usable_gib=round(usable, 4),
        needed_gib=round(needed, 4),
    )


def classes_that_fit(size_bytes: int) -> list[FitRow]:
    rows: list[FitRow] = []
    for plane in _BENCH_CLASS_ORDER:
        row = fits_class(plane, size_bytes)
        if row is not None and row.fits:
            rows.append(row)
    return rows


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML is not installed; pip install -r requirements.txt")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def load_nodes(path: Path) -> list[LabNode]:
    data = load_yaml(path)
    raw = data.get("nodes")
    if not isinstance(raw, list):
        raise SystemExit(f"{path}: expected a nodes list")
    nodes: list[LabNode] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        device_id = str(row.get("device_id") or "").strip()
        class_id = str(row.get("class_id") or "").strip()
        if not device_id or not class_id:
            continue
        created = row.get("created_at")
        label = row.get("label")
        nodes.append(
            LabNode(
                device_id=device_id,
                class_id=class_id,
                label=str(label).strip() if label else None,
                lab=_as_bool(row.get("lab"), False),
                enrolled=_as_bool(row.get("enrolled"), False),
                path_b=str(row.get("path_b") or "").strip().lower(),
                schedule_hold=_as_bool(row.get("schedule_hold"), True),
                created_at=str(created).strip() if created else "",
            )
        )
    return nodes


def node_from_env(env: dict[str, str] | None = None) -> LabNode | None:
    """HM_DEVICE_ID does not enroll a host and does not start a soak."""

    del env
    return None


def resolve_nodes(path: Path | None, env: dict[str, str] | None = None) -> list[LabNode]:
    source = env if env is not None else os.environ
    if path is not None:
        return load_nodes(path)
    env_path = (source.get("HM_LAB_NODES") or "").strip()
    if env_path:
        return load_nodes(Path(env_path))
    default = ROOT / "nodes" / "lab.yaml"
    if default.is_file():
        return load_nodes(default)
    return []


def is_ready(node: LabNode, plane: str) -> bool:
    return (
        node.lab
        and node.enrolled
        and node.path_b == "green"
        and not node.schedule_hold
        and bool(node.device_id)
        and plane_class(node.class_id) == plane
    )


def select_lab_node(nodes: list[LabNode], plane: str) -> LabNode | None:
    ready = [node for node in nodes if is_ready(node, plane)]
    ready.sort(key=lambda node: (node.created_at, node.label or "", node.device_id))
    return ready[0] if ready else None


def select_bench_target(
    fits: list[FitRow], nodes: list[LabNode]
) -> tuple[FitRow, LabNode] | None:
    by_class = {row.plane_class_id: row for row in fits}
    # Only the AGX pack soaks. nx-volume and thor stay fit rows.
    for plane in ("agx-large",):
        row = by_class.get(plane)
        budget = CLASS_BUDGETS.get(plane)
        if row is None or budget is None or not budget.pack_rel:
            continue
        node = select_lab_node(nodes, plane)
        if node is not None:
            return row, node
    return None


def collect_sha256(node: object, into: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sha256":
                digest = sha256_hex(value)
                if digest:
                    into.add(digest)
            else:
                collect_sha256(value, into)
    elif isinstance(node, list):
        for item in node:
            collect_sha256(item, into)


def known_sha256_from(paths: list[Path]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        collect_sha256(load_yaml(path), found)
    return found


def default_known_paths() -> list[Path]:
    paths = sorted((ROOT / "models").glob("*.yaml"))
    paths.extend(sorted((ROOT / "catalog").glob("*.yaml")))
    return paths


def _preferred_rank(artifact: Artifact) -> tuple[int, int, str]:
    exact = 0 if artifact.file_name.lower().endswith("q4_k_m.gguf") else 1
    return (exact, artifact.size_bytes, artifact.file_name)


def _stamp(now: datetime | None) -> str:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preliminary_status(artifact: Artifact, known: set[str], fits: list[FitRow]) -> str:
    if artifact.shard:
        return "shard"
    if artifact.sha256 and artifact.sha256 in known:
        return "known"
    if not artifact.sha256:
        return "no_hash"
    if not fits:
        return "dropped"
    return "pending"


def _pack_classes(fits: list[FitRow]) -> list[str]:
    out: list[str] = []
    for row in fits:
        budget = CLASS_BUDGETS.get(row.plane_class_id)
        if budget is not None and budget.pack_rel:
            out.append(row.plane_class_id)
    return out


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict) and row.get("source_ref"):
            rows[str(row["source_ref"])] = row
    return rows


def write_ledger(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(rows[key], sort_keys=True, separators=(",", ":"))
        for key in sorted(rows)
    ]
    path.write_text(("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8")


def _write_manifest(
    path: Path,
    artifact: Artifact,
    budget: ClassBudget,
) -> None:
    if yaml is None:
        raise SystemExit("PyYAML is not installed; pip install -r requirements.txt")
    model_id = artifact.catalog_id
    doc = {
        "class_id": budget.bench_class_id,
        "sku": budget.sku,
        "backend": budget.backend,
        "default_ctx": 4096,
        "default_quant": artifact.quant or PREFERRED_QUANT,
        "power_profile": budget.power_profile,
        "nvpmodel_id": budget.nvpmodel_id,
        "first_model_id": model_id,
        "notes": (
            "Generated crawl candidate. sha256 is the Hugging Face LFS oid, "
            "not a host-verified artifact_hash. Do not invent tok/s."
        ),
        "models": [
            {
                "id": model_id,
                "name": artifact.file_name.rsplit("/", 1)[-1],
                "quant": artifact.quant,
                "loader": "gguf",
                "loaders": ["gguf"],
                "hf": artifact.repo,
                "file": artifact.file_name,
                "url": artifact.url,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "fit": "likely",
                "catalog_id": model_id,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def bench_artifact(
    artifact: Artifact,
    *,
    budget: ClassBudget,
    node: LabNode,
    out_dir: Path,
    execute: bool,
    pack_root: Path,
) -> dict[str, Any]:
    """Call run_one. Stub leaves metrics null. Execute does not download weights."""

    from run_one import run_one

    if budget.plane_class_id not in SOAK_PLANES:
        return {"mode": "skipped", "reason": "only the AGX soaks"}
    if execute and node.device_id != KNOWN_DEVICE_ID:
        raise SoakRefused(
            "soak refused: only the enrolled AGX known host can execute a soak"
        )
    if not budget.pack_rel:
        return {"mode": "skipped", "reason": "no bench pack"}
    pack_dir = pack_root / budget.pack_rel
    manifest_path = out_dir / "manifests" / f"{artifact.catalog_id}.yaml"
    _write_manifest(manifest_path, artifact, budget)
    scorecard = run_one(
        manifest_path=manifest_path,
        pack_dir=pack_dir,
        model_id=artifact.catalog_id,
        out_dir=out_dir,
        stub=not execute,
        device_id=node.device_id,
        loader="gguf",
    )
    bench_status = None
    raw_path = scorecard.parent / "llama-bench.json"
    if raw_path.is_file():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict):
            bench_status = raw.get("status")
    return {
        "mode": "execute" if execute else "stub",
        "device_id": node.device_id,
        "node_label": node.label,
        "class_id": budget.bench_class_id,
        "plane_class_id": budget.plane_class_id,
        "scorecard": str(scorecard),
        "llama_bench_status": bench_status,
    }


def _keep_prior_bench(
    existing: dict[str, Any] | None,
    digest: str | None,
    execute: bool,
) -> bool:
    if not existing or not digest or existing.get("sha256") != digest:
        return False
    if existing.get("status") != "benched":
        return False
    prior = existing.get("bench") if isinstance(existing.get("bench"), dict) else {}
    if execute and prior.get("mode") != "execute":
        return False
    return True


def run_crawl(
    *,
    fetch: JsonFetch,
    publishers: tuple[str, ...],
    repo_limit: int,
    revision: str,
    known_sha256: set[str],
    nodes: list[LabNode],
    bench_limit: int,
    execute: bool,
    out_dir: Path,
    ledger_path: Path,
    pack_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    artifacts = discover_huggingface(
        fetch,
        publishers,
        repo_limit=repo_limit,
        revision=revision,
        errors=errors,
    )
    existing = load_ledger(ledger_path)
    seen_at = _stamp(now)
    pack_base = pack_root or ROOT
    prepared: list[dict[str, Any]] = []
    for artifact in artifacts:
        fits = classes_that_fit(artifact.size_bytes)
        status = _preliminary_status(artifact, known_sha256, fits)
        prior = existing.get(artifact.source_ref)
        hash_changed = bool(
            prior
            and artifact.sha256
            and prior.get("sha256")
            and prior.get("sha256") != artifact.sha256
        )
        prepared.append(
            {
                "artifact": artifact,
                "fits": fits,
                "status": status,
                "hash_changed": hash_changed,
                "prior": prior,
            }
        )

    # One preferred Q4_K_M file per repo, newest repo first.
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for row in prepared:
        if row["status"] != "pending":
            continue
        artifact = row["artifact"]
        if artifact.quant != PREFERRED_QUANT:
            continue
        by_repo.setdefault(artifact.repo, []).append(row)
    repo_order = sorted(
        by_repo,
        key=lambda repo: by_repo[repo][0]["artifact"].last_modified or "",
        reverse=True,
    )
    slots = 0
    for repo in repo_order:
        if slots >= bench_limit:
            break
        group = sorted(by_repo[repo], key=lambda row: _preferred_rank(row["artifact"]))
        pick = group[0]
        artifact = pick["artifact"]
        if _keep_prior_bench(pick["prior"], artifact.sha256, execute):
            pick["status"] = "benched"
            pick["bench"] = pick["prior"].get("bench")
            continue
        target = select_bench_target(pick["fits"], nodes)
        if target is None:
            if _pack_classes(pick["fits"]):
                pick["status"] = "skipped_no_node"
            else:
                pick["status"] = "skipped_no_pack"
            continue
        fit_row, node = target
        budget = CLASS_BUDGETS[fit_row.plane_class_id]
        pick["status"] = "benched"
        pick["bench"] = bench_artifact(
            artifact,
            budget=budget,
            node=node,
            out_dir=out_dir,
            execute=execute,
            pack_root=pack_base,
        )
        slots += 1

    rows: dict[str, dict[str, Any]] = dict(existing)
    counts = {
        "crawled_files": len(prepared),
        "known": 0,
        "shard": 0,
        "no_hash": 0,
        "dropped": 0,
        "candidate": 0,
        "benched": 0,
        "skipped_no_node": 0,
        "skipped_no_pack": 0,
    }
    results: list[dict[str, Any]] = []
    for row in prepared:
        artifact: Artifact = row["artifact"]
        status = row["status"]
        if status == "pending":
            if _pack_classes(row["fits"]):
                status = "candidate"
            elif row["fits"]:
                status = "skipped_no_pack"
            else:
                status = "dropped"
        counts[status] = counts.get(status, 0) + 1
        record = {
            "depot": artifact.depot,
            "publisher": artifact.publisher,
            "repo": artifact.repo,
            "revision": artifact.revision,
            "file_name": artifact.file_name,
            "source_ref": artifact.source_ref,
            "url": artifact.url,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "quant": artifact.quant,
            "license": artifact.license,
            "last_modified": artifact.last_modified,
            "shard": artifact.shard,
            "catalog_id": artifact.catalog_id,
            "seen_at": seen_at,
            "status": status,
            "hash_changed": row["hash_changed"],
            "fit": [fit.as_dict() for fit in row["fits"]],
            "bench": row.get("bench"),
        }
        if status == "benched" and record["bench"] is None and row.get("prior"):
            record["bench"] = row["prior"].get("bench")
        rows[artifact.source_ref] = record
        if status in {"benched", "skipped_no_node", "skipped_no_pack"}:
            results.append(
                {
                    "source_ref": artifact.source_ref,
                    "status": status,
                    "sha256": artifact.sha256,
                    "bench": record["bench"],
                }
            )

    write_ledger(ledger_path, rows)
    summary = {
        "depot": DEPOT_HUGGINGFACE,
        "publishers": list(publishers),
        "revision": revision,
        "repo_limit": repo_limit,
        "bench_limit": bench_limit,
        "bench_mode": "execute" if execute else "stub",
        "nodes_configured": len(nodes),
        "errors": errors,
        **counts,
        "results": results,
    }
    return summary


class UrlFetcher:
    """GET JSON. Refuses redirects so a token cannot leave Hugging Face."""

    def __init__(self, token: str | None = None, timeout: float = 30.0) -> None:
        self._token = token or None
        self._timeout = timeout

    def get_json(self, url: str) -> Any:
        headers = {
            "User-Agent": "hypermesh-bench-crawl/0",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(_RefuseRedirect())
        try:
            with opener.open(request, timeout=self._timeout) as response:
                host = (response.geturl() or url).split("/")[2].split(":")[0].lower()
                if host not in HF_HOSTS:
                    raise CrawlError(f"refusing response host {host}")
                payload = response.read().decode("utf-8")
        except CrawlError:
            raise
        except urllib.error.HTTPError as exc:
            raise CrawlError(f"{url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise CrawlError(f"{url}: {exc.reason}") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise CrawlError(f"{url}: invalid JSON") from exc


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise CrawlError(f"refusing redirect to {newurl}")


def _token_from_env(env: dict[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = (source.get(key) or "").strip()
        if value:
            return value
    return None


def _publishers_from_arg(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return tuple(sorted(GGUF_PUBLISHER_ALLOWLIST))
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "out" / "crawl" / "candidates.jsonl",
        help="Candidate ledger (JSONL). Default: out/crawl/candidates.jsonl",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "out" / "crawl" / "summary.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "crawl" / "bench",
        help="Scorecard directory for models this run benches",
    )
    parser.add_argument("--repo-limit", type=int, default=3)
    parser.add_argument(
        "--bench-limit",
        type=int,
        default=1,
        help="How many new Q4_K_M files to pass to run_one (0 records only)",
    )
    parser.add_argument(
        "--publishers",
        default=None,
        help="Comma-separated allowlisted HF publishers. Default: the full allowlist",
    )
    parser.add_argument("--revision", default="main")
    parser.add_argument(
        "--nodes",
        type=Path,
        default=None,
        help="Lab node list for stub runs. Default: HM_LAB_NODES, else nodes/lab.yaml. "
        "--execute ignores this and uses the enrolled known host",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Soak the enrolled known host after AGX preflight. "
        "Does not download weights. Default is --stub",
    )
    args = parser.parse_args(argv)
    if args.repo_limit < 1:
        raise SystemExit("--repo-limit must be >= 1")
    if args.bench_limit < 0:
        raise SystemExit("--bench-limit must be >= 0")

    publishers = _publishers_from_arg(args.publishers)
    if args.execute:
        try:
            ready = assert_ready_for_soak(None)
        except SoakRefused as exc:
            print(str(exc), file=sys.stderr)
            return 3
        nodes = [
            LabNode(
                device_id=str(ready["device_id"]),
                class_id=KNOWN_CLASS,
                label=KNOWN_LABEL,
                lab=True,
                enrolled=True,
                path_b="green",
                schedule_hold=False,
            )
        ]
    else:
        nodes = resolve_nodes(args.nodes)
    summary = run_crawl(
        fetch=UrlFetcher(token=_token_from_env()),
        publishers=publishers,
        repo_limit=args.repo_limit,
        revision=args.revision,
        known_sha256=known_sha256_from(default_known_paths()),
        nodes=nodes,
        bench_limit=args.bench_limit,
        execute=args.execute,
        out_dir=args.out,
        ledger_path=args.ledger,
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
