#!/usr/bin/env python3
"""Crawl → fit → bench. No network. No invented tok/s."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from crawl_bench import (  # noqa: E402
    GIB,
    CrawlError,
    LabNode,
    classes_that_fit,
    discover_huggingface,
    fits_class,
    hf_models_url,
    hf_resolve_url,
    hf_tree_url,
    load_ledger,
    make_catalog_id,
    node_from_env,
    run_crawl,
    select_lab_node,
    sha256_hex,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_KNOWN = "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c"
GIT_OID = "c" * 40


class MapFetch:
    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_json(self, url: str) -> object:
        self.calls.append(url)
        if url not in self.pages:
            raise CrawlError(f"missing {url}")
        return self.pages[url]


def _model(repo: str, modified: str, private: bool = False) -> dict:
    return {
        "id": repo,
        "lastModified": modified,
        "private": private,
        "tags": ["gguf", "license:apache-2.0"],
    }


def _gguf(path: str, size: int, digest: str | None, git_oid: str = GIT_OID) -> dict:
    lfs = {"oid": digest, "size": size} if digest else {}
    return {"path": path, "size": size, "type": "file", "oid": git_oid, "lfs": lfs}


def _pages(repo: str, files: list[dict], modified: str = "2026-09-24T00:00:00.000Z") -> dict:
    return {
        hf_models_url("bartowski", 5): [_model(repo, modified)],
        hf_tree_url(repo, "main"): files,
    }


class FitAndIdentityTest(unittest.TestCase):
    def test_sha256_rejects_git_oid(self) -> None:
        self.assertIsNone(sha256_hex(GIT_OID))
        self.assertEqual(sha256_hex(f"sha256:{SHA_A}"), SHA_A)
        self.assertEqual(sha256_hex(SHA_B.upper()), SHA_B)

    def test_catalog_id_stays_within_64(self) -> None:
        name = "Qwen2.5-Very-Long-Model-Name-Instruct-Extra-Q4_K_M.gguf"
        catalog_id = make_catalog_id("bartowski", name, SHA_A)
        self.assertLessEqual(len(catalog_id), 64)
        self.assertTrue(catalog_id.startswith("hf-bartowski-"))

    def test_resolve_url_stays_on_huggingface(self) -> None:
        url = hf_resolve_url(
            "bartowski/Some-Model-GGUF",
            "main",
            "Some Model Q4_K_M.gguf",
        )
        self.assertTrue(url.startswith("https://huggingface.co/bartowski/"))
        self.assertIn("/resolve/main/", url)
        self.assertNotIn(" ", url)

    def test_fit_boundary_matches_class_budgets(self) -> None:
        exact = int(58.5 * GIB)
        self.assertTrue(fits_class("agx-large", exact).fits)
        self.assertFalse(fits_class("agx-large", exact + 4096).fits)
        tiny = 1 * GIB
        self.assertEqual(
            [row.plane_class_id for row in classes_that_fit(tiny)],
            ["agx-large", "nx-volume", "thor"],
        )
        huge = int(130 * GIB)
        self.assertEqual(classes_that_fit(huge), [])
        thor_only = int(100 * GIB)
        self.assertEqual(
            [row.plane_class_id for row in classes_that_fit(thor_only)],
            ["thor"],
        )


class NodeSelectTest(unittest.TestCase):
    def test_ready_node_is_oldest_lab_green(self) -> None:
        held = LabNode(
            device_id="held",
            class_id="fyber-agx-orin-64gb",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=True,
            created_at="2020-01-01",
        )
        newer = LabNode(
            device_id="newer",
            class_id="agx-large",
            label="AGX-B",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
            created_at="2024-01-01",
        )
        older = LabNode(
            device_id="older",
            class_id="fyber-agx-orin-64gb",
            label="AGX-A",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
            created_at="2021-01-01",
        )
        suspended = LabNode(
            device_id="suspended",
            class_id="agx-large",
            lab=True,
            enrolled=True,
            path_b="suspended",
            schedule_hold=False,
            created_at="2019-01-01",
        )
        renter = LabNode(
            device_id="renter",
            class_id="agx-large",
            lab=False,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
            created_at="2018-01-01",
        )
        picked = select_lab_node([held, newer, older, suspended, renter], "agx-large")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked.device_id, "older")

    def test_env_node_uses_device_id_only(self) -> None:
        node = node_from_env({"HM_DEVICE_ID": "dev-1", "HM_CLASS_ID": "agx-large"})
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.device_id, "dev-1")
        self.assertTrue(node.lab)
        self.assertEqual(node.path_b, "green")
        self.assertIsNone(node_from_env({}))


class CrawlBenchTest(unittest.TestCase):
    def _run(
        self,
        pages: dict,
        tmp: str,
        *,
        nodes: list[LabNode] | None = None,
        known: set[str] | None = None,
        execute: bool = False,
        bench_limit: int = 1,
        publishers: tuple[str, ...] = ("bartowski",),
        preflight=None,
    ) -> tuple[dict, dict]:
        root = Path(tmp)
        ledger = root / "candidates.jsonl"
        summary = run_crawl(
            fetch=MapFetch(pages),
            publishers=publishers,
            repo_limit=5,
            revision="main",
            known_sha256=known or set(),
            nodes=nodes or [],
            bench_limit=bench_limit,
            execute=execute,
            out_dir=root / "bench",
            ledger_path=ledger,
            pack_root=ROOT,
            preflight=preflight,
        )
        return summary, load_ledger(ledger)

    def test_untrusted_publisher_is_not_fetched(self) -> None:
        fetch = MapFetch({})
        errors: list[str] = []
        found = discover_huggingface(
            fetch,
            ("not-a-publisher",),
            repo_limit=1,
            errors=errors,
        )
        self.assertEqual(found, [])
        self.assertEqual(fetch.calls, [])
        self.assertIn("untrusted publisher not-a-publisher", errors)

    def test_lfs_oid_not_git_oid_and_private_skipped(self) -> None:
        repo = "bartowski/New-Model-GGUF"
        pages = {
            hf_models_url("bartowski", 5): [
                _model("bartowski/Secret-GGUF", "2026-09-24T02:00:00.000Z", private=True),
                _model(repo, "2026-09-24T01:00:00.000Z"),
            ],
            hf_tree_url(repo, "main"): [
                _gguf("New-Model-Q4_K_M.gguf", 2 * GIB, SHA_A),
                _gguf("New-Model-Q4_K_M-00001-of-00002.gguf", GIB, SHA_B),
                {"path": "README.md", "type": "file", "size": 10},
            ],
        }
        found = discover_huggingface(MapFetch(pages), ("bartowski",), repo_limit=5)
        names = [row.file_name for row in found]
        self.assertEqual(
            names,
            [
                "New-Model-Q4_K_M.gguf",
                "New-Model-Q4_K_M-00001-of-00002.gguf",
            ],
        )
        by_name = {row.file_name: row for row in found}
        self.assertEqual(by_name["New-Model-Q4_K_M.gguf"].sha256, SHA_A)
        self.assertEqual(by_name["New-Model-Q4_K_M.gguf"].quant, "Q4_K_M")
        self.assertEqual(by_name["New-Model-Q4_K_M.gguf"].license, "apache-2.0")
        self.assertFalse(by_name["New-Model-Q4_K_M.gguf"].shard)
        self.assertTrue(by_name["New-Model-Q4_K_M-00001-of-00002.gguf"].shard)

    def test_stub_bench_on_ready_node_leaves_metrics_null(self) -> None:
        repo = "bartowski/Fresh-GGUF"
        pages = _pages(
            repo,
            [
                _gguf("Fresh-Q4_K_M.gguf", 2 * GIB, SHA_A),
                _gguf("Fresh-Q8_0.gguf", 4 * GIB, SHA_B),
            ],
        )
        node = LabNode(
            device_id="lab-node-1",
            class_id="agx-large",
            label="AGX64-1",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
            created_at="2024-01-01T00:00:00Z",
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary, ledger = self._run(pages, tmp, nodes=[node])
            self.assertEqual(summary["benched"], 1)
            self.assertEqual(summary["errors"], [])
            q4 = next(row for row in ledger.values() if row["file_name"] == "Fresh-Q4_K_M.gguf")
            self.assertEqual(q4["status"], "benched")
            self.assertEqual(q4["bench"]["mode"], "stub")
            self.assertEqual(q4["bench"]["device_id"], "lab-node-1")
            self.assertEqual(q4["bench"]["llama_bench_status"], "not_run")
            card = json.loads(Path(q4["bench"]["scorecard"]).read_text(encoding="utf-8"))
            inf = card["inference_sustained"]
            self.assertIsNone(inf["prefill_tok_s_p50"])
            self.assertIsNone(inf["decode_tok_s_p50_after_throttle"])
            self.assertIsNone(inf["ttft_ms_p50_after_throttle"])
            self.assertIsNone(card["identity"]["artifact_hash"])
            self.assertIsNone(card["identity"]["image_hash"])
            self.assertIsNot(card["run"]["passed"], True)
            self.assertEqual(card["run"]["device_id"], "lab-node-1")
            self.assertEqual(card["run"]["class_id"], "fyber-agx-orin-64gb")
            q8 = next(row for row in ledger.values() if row["file_name"] == "Fresh-Q8_0.gguf")
            self.assertEqual(q8["status"], "candidate")
            self.assertIsNone(q8["bench"])

            q4["bench"] = {"mode": "stub", "marker": "keep"}
            ledger_path = Path(tmp) / "candidates.jsonl"
            lines = [
                json.dumps(ledger[key], sort_keys=True)
                for key in sorted(ledger)
            ]
            ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            summary2, ledger2 = self._run(pages, tmp, nodes=[node])
            again = next(
                row for row in ledger2.values() if row["file_name"] == "Fresh-Q4_K_M.gguf"
            )
            self.assertEqual(again["bench"].get("marker"), "keep")
            self.assertEqual(summary2["benched"], 1)
            self.assertEqual(len(ledger2), 2)

    def test_known_shard_and_missing_node(self) -> None:
        repo = "bartowski/Fresh-GGUF"
        pages = _pages(
            repo,
            [
                _gguf("Fresh-Q4_K_M.gguf", 2 * GIB, SHA_KNOWN),
                _gguf("Other-Q4_K_M.gguf", 2 * GIB, SHA_A),
                _gguf("Split-Q4_K_M-00001-of-00002.gguf", GIB, SHA_B),
                _gguf("NoHash-Q4_K_M.gguf", 2 * GIB, None),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary, ledger = self._run(pages, tmp, known={SHA_KNOWN})
            by_name = {row["file_name"]: row for row in ledger.values()}
            self.assertEqual(by_name["Fresh-Q4_K_M.gguf"]["status"], "known")
            self.assertEqual(by_name["Other-Q4_K_M.gguf"]["status"], "skipped_no_node")
            self.assertEqual(by_name["Split-Q4_K_M-00001-of-00002.gguf"]["status"], "shard")
            self.assertEqual(by_name["NoHash-Q4_K_M.gguf"]["status"], "no_hash")
            self.assertEqual(summary["benched"], 0)
            self.assertEqual(summary["skipped_no_node"], 1)
            self.assertFalse(any(row["bench"] for row in ledger.values()))

    def test_oversize_for_agx_is_not_benched(self) -> None:
        repo = "bartowski/Huge-GGUF"
        pages = _pages(repo, [_gguf("Huge-Q4_K_M.gguf", int(100 * GIB), SHA_A)])
        node = LabNode(
            device_id="agx",
            class_id="agx-large",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary, ledger = self._run(pages, tmp, nodes=[node])
            row = next(iter(ledger.values()))
            self.assertEqual(row["status"], "skipped_no_pack")
            self.assertEqual(
                [fit["class_id"] for fit in row["fit"]],
                ["thor"],
            )
            self.assertEqual(summary["benched"], 0)

    def test_execute_blocks_when_preflight_fails(self) -> None:
        repo = "bartowski/Fresh-GGUF"
        pages = _pages(repo, [_gguf("Fresh-Q4_K_M.gguf", 2 * GIB, SHA_A)])
        node = LabNode(
            device_id="lab-node-1",
            class_id="fyber-agx-orin-64gb",
            label="AGX64-1",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
        )

        def failed() -> dict:
            return {"ok": False, "checks": [{"name": "l4t", "ok": False}]}

        with tempfile.TemporaryDirectory() as tmp:
            summary, ledger = self._run(
                pages,
                tmp,
                nodes=[node],
                execute=True,
                preflight=failed,
            )
            row = next(iter(ledger.values()))
            self.assertEqual(row["status"], "blocked_preflight")
            self.assertEqual(summary["preflight_ok"], False)
            self.assertIn("l4t", summary["preflight_error"])
            self.assertEqual(summary["benched"], 0)
            self.assertFalse((Path(tmp) / "bench").exists() and any((Path(tmp) / "bench").rglob("scorecard.json")))

    def test_stub_does_not_preflight(self) -> None:
        called: list[int] = []

        def failed() -> dict:
            called.append(1)
            return {"ok": False}

        repo = "bartowski/Fresh-GGUF"
        pages = _pages(repo, [_gguf("Fresh-Q4_K_M.gguf", 2 * GIB, SHA_A)])
        with tempfile.TemporaryDirectory() as tmp:
            summary, _ledger = self._run(pages, tmp, preflight=failed)
            self.assertIsNone(summary["preflight_ok"])
        self.assertEqual(called, [])

    def test_thor_node_is_not_a_bench_target(self) -> None:
        from crawl_bench import select_bench_target, classes_that_fit

        fits = classes_that_fit(2 * GIB)
        thor = LabNode(
            device_id="thor-1",
            class_id="thor",
            lab=True,
            enrolled=True,
            path_b="green",
            schedule_hold=False,
        )
        self.assertIsNone(select_bench_target(fits, [thor]))


if __name__ == "__main__":
    unittest.main()
