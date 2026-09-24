# Crawl new GGUFs and bench the ones that fit

Operator command for models that are not already in the batch-12 manifest. It does not replace the AIuditor daily crawl, and it does not enqueue `lab_pull` / `path_b`. The plane still owns certification. This command only discovers metadata, records candidates, and calls the existing `run_one` harness.

No weight download. No invented tok/s. A stub scorecard is not a Path B pass.

## Depots

Hugging Face (`huggingface.co`, `hf.co`) is the only download host in `hypermesh-host` `internal/pull/trust.go`. Publishers match that allowlist and AIuditor `GGUF_PUBLISHER_ALLOWLIST`:

`bartowski`, `thebloke`, `quantfactory`, `ggml-org`, `lmstudio-community`, `fyberlabs`

Anything else is skipped. Ollama in inference-iface is an engine kind, not a weight depot. There is no second depot client in these repos.

Discovery is `GET /api/models?author={publisher}&sort=lastModified&direction=-1`, then the metadata tree for `.gguf` files. The file pin is the LFS oid (`sha256`, 64 hex). The git blob oid is not a hash. A redirect off Hugging Face fails closed. `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` is sent when set; public models do not need one.

## Flow

1. **Crawl.** Newest repos per allowlisted publisher, capped by `--repo-limit`.
2. **Record.** Upsert `source_ref` (`repo@rev/file`) into the JSONL ledger. Same sha256 already benched in stub mode is not run again. `--execute` re-runs a prior stub.
3. **Fit.** File size + 1.5 GiB KV headroom against class RAM after OS reserve (same numbers as AIuditor `fit.py`):

   | Plane class | Bench class id | RAM | OS reserve | Pack |
   |---|---|---|---|---|
   | `agx-large` | `fyber-agx-orin-64gb` | 64 GiB | 4 GiB | `packs/thin-v1` |
   | `nx-volume` | `nx-volume` | 16 GiB | 2 GiB | none in this repo |
   | `thor` | `thor` | 128 GiB | 6 GiB | none in this repo |

4. **Bench.** One preferred `Q4_K_M` file per repo (single file, not `N-of-M` shards), up to `--bench-limit`, on the first ready AGX (`agx-large` / `fyber-agx-orin-64gb`). Other classes stay fit-only. That calls `run_one`. Default is stub (null metrics, no preflight). `--execute` runs panopticon `hypermesh_service.agx_preflight` on this process first and exits 3 when it does not pass, then llama-bench only when the binary or image is already on the box. The Hugging Face oid is not copied into `artifact_hash`; that field stays null until a host pull verifies the file.

Rows already pinned in `models/*.yaml` or `catalog/*.yaml` are `known` and are not benched. Missing LFS oid is `no_hash` (the host pull refuses an unpinned file). Shards are recorded and not benched.

## Lab nodes

Same ready rule as Hypermesh `ready_lab_devices`: `lab`, `enrolled`, `path_b: green`, `schedule_hold: false`, class matches a class the file fits. Oldest `created_at` wins. A row missing `device_id` or `class_id` is ignored. Omitted `lab` / `enrolled` are false. Omitted `schedule_hold` is treated as held.

```yaml
nodes:
  - label: AGX64-1
    device_id: <enrolled uuid>
    class_id: agx-large          # or fyber-agx-orin-64gb
    lab: true
    enrolled: true
    path_b: green
    schedule_hold: false
    created_at: "2024-01-01T00:00:00Z"
```

Resolution order:

1. `--nodes PATH`
2. `HM_LAB_NODES`
3. `nodes/lab.yaml` if it exists
4. `HM_DEVICE_ID` (this process is that host; class defaults to `HM_CLASS_ID` or `fyber-agx-orin-64gb`)

No file and no `HM_DEVICE_ID` means the crawl still records and fit-filters, and bench status is `skipped_no_node`. Do not invent a device id or an SSH address. This command does not SSH.

## Run

```bash
python3 harness/crawl_bench.py \
  --repo-limit 1 \
  --publishers bartowski \
  --bench-limit 1 \
  --ledger out/crawl/candidates.jsonl \
  --summary out/crawl/summary.json \
  --out out/crawl/bench
```

`--bench-limit 0` records candidates and does not call `run_one`.

`out/` is gitignored. Do not commit ledgers or scorecards from this command into the catalog.
