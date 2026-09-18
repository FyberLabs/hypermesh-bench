# hypermesh-bench

Path B model/harness soak for Hypermesh certified hosts.

**AGX first.** This repo is the public pull-and-run layout for NVIDIA Jetson AGX Orin 64GB (`class_id: fyber-agx-orin-64gb`). Other classes wait until this box has a real soak.

Harness scripts are Apache-2.0. Model weights stay under their upstream licenses. Do not commit `.gguf` files.

## What this is

Hypermesh Path B certifies a **host class + image + artifact + sustained envelope**, not TOPS.

- Peak bench alone fails. The pass is a sustained band after the box is hot.
- Loaders: `oci` | `gguf`. Default backend: `cuda-jetson` (alias `cuda-sm87`).
- First Full Model soak path: **OCI wrapper that contains the GGUF**, started with `docker run --runtime=nvidia` (never `--gpus`).
- Product default power: **30W (`nvpmodel` id 2)**. Do not default MAXN. A MAXN run is a separate labeled profile.
- First model: **Llama 3.1 8B Instruct Q4_K_M** (`llama-3.1-8b-q4`).
- 70B is conditional (`skip_on_oom: true`).

## Batch vs catalog

| Path | Role |
|---|---|
| [`models/agx64-batch12.yaml`](models/agx64-batch12.yaml) | **Bench matrix.** All 12 GGUFs to pull and soak. Hashes are pinned (HF LFS oids). |
| [`catalog/agx64.yaml`](catalog/agx64.yaml) | **Product-2 (Full Model) subset.** Rows the site may offer **after soak**, not before. |

The site must not sell a `catalog_id` until `status=certified` with a measured envelope and hashes. `llama-3.1-8b-q4` is `soak_pending`. Every other batch-12 id is `candidate`. See [catalog/README.md](catalog/README.md).

Host soak steps for AGX64-1: **[docs/PHASE1_SOAK.md](docs/PHASE1_SOAK.md)**.

## Numbers policy

**Do not invent tok/s. Do not write TOPS as tok/s.**

Unmeasured fields stay `null`. Third-party Jetson blog rates are other people's boxes — cite them only as citations, never copy them into `inference_sustained.*`. File sha256 pins are identity, not a speed claim.

The JetPack/L4T version recorded on the Fyber AGX is the only soak source of truth.

## Status

| Phase | Goal |
|---|---|
| **0** | Layout, schema, thin-v1 pack, batch-12 manifest, recipe stubs |
| **1** (this) | Pins + Product-2 catalog + host runbook. Green Path B on Llama 3.1 8B Q4_K_M only — [docs/PHASE1_SOAK.md](docs/PHASE1_SOAK.md) |
| **2** | Run the 12-model batch at 30W; promote catalog rows only after soak |
| **3** | Optional serving / quality sidecars (not Path B pass/fail) |
| **4** | Per-host store + portal dashboards (see [docs/DASHBOARDS.md](docs/DASHBOARDS.md)) |

Harness scripts still emit valid scorecards with null metrics until AGX64-1 measures them. No GPU CI. No measured envelope.

## How to run (AGX / JetPack host — not Alpine)

Follow [docs/PHASE1_SOAK.md](docs/PHASE1_SOAK.md) on AGX64-1. Short form:

```bash
git clone https://github.com/FyberLabs/hypermesh-bench
cd hypermesh-bench
python3 -m pip install -r requirements.txt

# Native llama.cpp (Phase 1) — or skip and use the OCI recipe
./recipes/gguf/build-llama-cpp-sm87.sh

# Product default power. Record whether jetson_clocks was enabled.
sudo nvpmodel -m 2
# sudo jetson_clocks   # optional; label the scorecard if used

# Pin from the batch-12 manifest (refuses TBD)
./recipes/gguf/pull-gguf.sh --model-id llama-3.1-8b-q4 --out /var/lib/hypermesh/models/

# Phase 0 stub still writes null scorecards. Phase 1: same entry point, then fill raw files.
python3 harness/run_one.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --model-id llama-3.1-8b-q4 \
  --out "out/$(hostname)-$(date +%Y%m%d)"

python3 harness/check_scorecard.py "out/$(hostname)-$(date +%Y%m%d)/llama-3.1-8b-q4/scorecard.json"
python3 harness/check_scorecard.py --path-b "out/$(hostname)-$(date +%Y%m%d)/llama-3.1-8b-q4/scorecard.json"
```

Batch (all 12, or `--first-only`):

```bash
python3 harness/batch_runner.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --out "out/$(hostname)-$(date +%Y%m%d)"
```

GGUF pull also accepts an explicit pin:

```bash
./recipes/gguf/pull-gguf.sh \
  --url "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf" \
  --sha256 7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c \
  --out /var/lib/hypermesh/models/
```

Dry-run (no download):

```bash
./recipes/gguf/pull-gguf.sh --model-id llama-3.1-8b-q4 --dry-run
```

OCI (first Full Model soak path):

```bash
docker run --rm --runtime=nvidia --network host \
  -v /var/lib/hypermesh/models:/models:ro \
  -v /var/lib/hypermesh/out:/out \
  ghcr.io/fyberlabs/hypermesh-llama:agx64-jp6 \
  llama-bench --pack thin-v1 --model /models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

`image_hash` for `oci` is `repo@digest`, never a bare sha256. Same artifact on two images is two products.

## Layout

```
catalog/                # Product-2 sellable subset (after soak) — see catalog/README.md
packs/thin-v1/          # prompt pack + suite (512/2048 prefill, 128 gen, 30W)
schemas/                # Path B scorecard JSON Schema
models/                 # AGX batch-12 bench matrix (pinned sha256)
recipes/gguf/           # pull (accepts --model-id / --sha256) + sm_87 llama.cpp build
recipes/oci/            # Dockerfile + entrypoint + ghcr push
recipes/power/          # tegrastats sampler + wall-watt procedure
harness/                # run_one, batch_runner, llama-bench map, schema + catalog check
out/                    # gitignored results — file soaks here (PHASE1_SOAK.md)
docs/SCORECARD.md       # field SoT
docs/PHASE1_SOAK.md     # host runbook for AGX64-1
docs/DASHBOARDS.md      # per-host / class UI notes (not this repo's job)
```

## Scorecard

See [docs/SCORECARD.md](docs/SCORECARD.md). Schema: [schemas/scorecard.schema.json](schemas/scorecard.schema.json).

Null-allowed metrics include `usable_ram_gib`, `ttft_ms_p50_after_throttle`, `decode_tok_s_p50_after_throttle`, `wall_watts_idle`, `wall_watts_load`, `image_hash`, `artifact_hash`, `power_mode`, `nvpmodel_id`, `device_id`, `class_id`, `catalog_id`, `harness.name` / `harness.version`, and `passed`.

`check_scorecard.py` validates shape. `--path-b` applies enroll rules (non-empty `image_hash`, no peak-only pass, unexpected reboot fails). Stubs are schema-valid and **not** Path B green. `check_catalog.py` refuses TBD pins and a listed/certified catalog row without a measured envelope.

## SoT (do not contradict)

- [host-scorecard.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/host-scorecard.md)
- [host-runtimes.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/host-runtimes.md)
- [jobs-and-loaders.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/jobs-and-loaders.md)
- [models.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/models.md)
- [hardware-classes.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/hardware-classes.md)
- [analytics.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/analytics.md)
