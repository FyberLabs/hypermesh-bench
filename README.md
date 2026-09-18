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

## Numbers policy

**Do not invent tok/s. Do not write TOPS as tok/s.**

Unmeasured fields stay `null`. Third-party Jetson blog rates are other people's boxes — cite them only as citations, never copy them into `inference_sustained.*`. `sha256: TBD` until a real file hash is pinned at soak.

The JetPack/L4T version recorded on the Fyber AGX is the only soak source of truth.

## Phase 0 status

This commit is a **repo skeleton**. Harness scripts emit valid scorecards with null metrics and TODOs for real `llama-bench` parse. No GPU CI. No measured envelope.

| Phase | Goal |
|---|---|
| **0** (this) | Layout, schema, thin-v1 pack, batch-12 manifest, recipe stubs |
| **1** | Green Path B on Llama 3.1 8B Q4_K_M only |
| **2** | Pin sha256s and run the 12-model batch at 30W |
| **3** | Optional serving / quality sidecars (not Path B pass/fail) |
| **4** | Per-host store + portal dashboards (see [docs/DASHBOARDS.md](docs/DASHBOARDS.md)) |

## How to run (AGX / JetPack host — not Alpine)

```bash
git clone https://github.com/FyberLabs/hypermesh-bench
cd hypermesh-bench
python3 -m pip install -r requirements.txt

# Native llama.cpp (Phase 1) — or skip and use the OCI recipe
./recipes/gguf/build-llama-cpp-sm87.sh

# Product default power. Record whether jetson_clocks was enabled.
sudo nvpmodel -m 2
# sudo jetson_clocks   # optional; label the scorecard if used

# Phase 0: writes null scorecards. Phase 1: same entry point, real soak.
python3 harness/batch_runner.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --out "out/$(hostname)-$(date +%Y%m%d)"

# First-model only (Phase 1 unblock)
python3 harness/run_one.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --model-id llama-3.1-8b-q4 \
  --out "out/$(hostname)-$(date +%Y%m%d)"

python3 harness/check_scorecard.py out/<run>/*/scorecard.json
```

GGUF pull (refuses `sha256: TBD` unless `--allow-unpinned`):

```bash
./recipes/gguf/pull-gguf.sh \
  --url "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf" \
  --sha256 "<pin>" \
  --out /var/lib/hypermesh/models/
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
packs/thin-v1/          # prompt pack + suite (512/2048 prefill, 128 gen, 30W)
schemas/                # Path B scorecard JSON Schema
models/                 # AGX batch-12 manifest (hashes TBD)
recipes/gguf/           # pull + sm_87 llama.cpp build
recipes/oci/            # Dockerfile + entrypoint + ghcr push
recipes/power/          # tegrastats sampler + wall-watt procedure
harness/                # run_one, batch_runner, llama-bench map, schema check
out/                    # gitignored results
docs/SCORECARD.md       # field SoT
docs/DASHBOARDS.md      # per-host / class UI notes (not this repo's job)
```

## Scorecard

See [docs/SCORECARD.md](docs/SCORECARD.md). Schema: [schemas/scorecard.schema.json](schemas/scorecard.schema.json).

Null-allowed metrics include `usable_ram_gib`, `ttft_ms_p50_after_throttle`, `decode_tok_s_p50_after_throttle`, `wall_watts_idle`, `wall_watts_load`, `image_hash`, `artifact_hash`, `power_mode`, `nvpmodel_id`, `device_id`, `class_id`, `catalog_id`, `harness.name` / `harness.version`, and `passed`.

`check_scorecard.py` validates shape. `--path-b` applies enroll rules (non-empty `image_hash`, no peak-only pass, unexpected reboot fails). Phase 0 stubs are schema-valid and **not** Path B green.

## SoT (do not contradict)

- [host-scorecard.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/host-scorecard.md)
- [host-runtimes.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/host-runtimes.md)
- [jobs-and-loaders.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/jobs-and-loaders.md)
- [models.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/models.md)
- [hardware-classes.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/hardware-classes.md)
- [analytics.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/analytics.md)
