# Phase 1 soak runbook — AGX64-1

Host-facing steps for the first Path B / Full Model soak on **AGX64-1** (`class_id: fyber-agx-orin-64gb`). One model only: **`llama-3.1-8b-q4`**.

This runbook does **not** invent numbers. Leave every measured scorecard field `null` until this box writes it. Do not write TOPS as tok/s. Third-party Jetson blog rates are other people's boxes — cite them only as citations, never copy them into `inference_sustained.*` or `catalog/agx64.yaml` scorecard fields.

The site must not sell `llama-3.1-8b-q4` until `catalog/agx64.yaml` says `status: certified` with a measured envelope and hashes. After this soak, `status` is still `soak_pending` until someone promotes the row on purpose.

## Automated entrypoint (preferred)

One command on AGX64-1 (JetPack host). **Only** Host, after it pulls `kind=path_b` / `suite_id=thin-v1`, should invoke this. Not chat. Not a hand `lease_stop`. See [HOST_JOB.md](HOST_JOB.md).

**Plane door** (panopticon#108 — Product sign-off): the control plane opens the certification window. Exact Create body (plane operator, **not** this script): see [HOST_JOB.md](HOST_JOB.md). `preempt` defaults `true`; `suite_id` is not in the body (plane picks `path_b` / `thin-v1`). Host then `GET /agent/jobs` → `phase1_soak` → job result → auto-restore. No hand `lease_stop`.

A real run fails closed before llama-bench unless AGX preflight passes and `/var/lib/hypermesh/device.json` is enrolled known host AGX64-1 (`a6400000-0640-4000-8000-000000000001`). `HM_DEVICE_ID` does not skip that gate. `--stub` does not start a bench.

```bash
./scripts/phase1_soak.sh \
  --model-id llama-3.1-8b-q4 \
  --pack packs/thin-v1 \
  --device-id a6400000-0640-4000-8000-000000000001 \
  --out "out/$(hostname)-$(date +%Y%m%d)" \
  --loader gguf
```

Dry-run (CI / no GPU; schema-valid scorecard + `job_result.json` with nulls):

```bash
./scripts/phase1_soak.sh --stub --out /tmp/hm-phase1
```

What the driver does:

1. Consume the existing `path_b` job env (`HM_JOB_KIND`, `HM_SUITE_ID`, `HM_CATALOG_ID`, `HM_DEVICE_ID`, …). Exit **4** if `HM_VALIDATION_WINDOW` is denied / no hold. Do not POST the host-certification override or `lease_stop` from this script.
2. On a real run, AGX preflight then the enrolled known host. Exit **3** on failure and do not start the bench. Then probe host → `host_probe.json` + scorecard `host.*`. `--stub` still leaves unmeasured host fields null.
3. Disk gate: models volume free must be ≥ pin `size_bytes` + **2 GiB** headroom. On ~9.9 GiB free, pull GGUF **once** and use a **thin** OCI runtime (binaries only). Do not bake the GGUF into the image and keep a second local copy.
4. Ensure 30W / `nvpmodel` 2 (sudo from the wrapper when not `--stub`).
5. Pull or reuse the pinned GGUF (`pull-gguf.sh --model-id`; refuses TBD). Verified sha256 → `identity.artifact_hash`.
6. Resolve harness: native `llama-bench` if present, else `docker run --runtime=nvidia` when `--loader oci` and `HM_IMAGE_DIGEST` is set. `--require-bench` fails if the binary is missing.
7. thin-v1 cells → `llama-bench.json`. After the pack hot window, `ttft_client.py` against llama-server. If the server is down, TTFT / sustained decode stay **null**.
8. `mem_after_load.json`, `power.json` (wall watts stay null without a meter), `reliability.json`.
9. Map raws → `scorecard.json`. Unmeasured = null. `check_scorecard.py` then `--path-b`. `run.passed` is that result only.
10. `job_result.json` = `{passed, image_hash?}` for the **existing** Host POST. Bench does not POST.

Exit codes: `0` Path B green (or `--stub` without `--require-path-b`); `2` Path B red; `3` setup/disk/pin; `4` skip (no validation window).

Manual steps below remain as an appendix if you need to run pieces by hand.

## 0. What “done” means

A Phase 1 soak is done when AGX64-1 has, on disk under `out/`:

- a `scorecard.json` that `check_scorecard.py` accepts (schema)
- **and** `check_scorecard.py --path-b` is green — which requires non-empty hashes and sustained metrics **measured on this box**

`--path-b` failing on a null stub is the correct starting state. Do not fake a hash or a tok/s to make it pass.

## 1. JetPack / L4T host (not Alpine)

Run this on the Jetson **host image** (JetPack / L4T). Not Alpine. Not a laptop. Harness containers may still be Debian/Alpine later; the soak host itself is JetPack.

Record exact versions. They are the soak source of truth:

```bash
cat /etc/nv_tegra_release
dpkg -l | grep -E 'nvidia-jetpack|nvidia-l4t-core' || true
nvcc --version || true
```

Write them into the scorecard as `host.jetpack_l4t` and `host.cuda`. No `nvidia-smi` on Jetson — use `tegrastats` or `jtop`.

## 2. Clone hypermesh-bench `main`

```bash
git clone https://github.com/FyberLabs/hypermesh-bench
cd hypermesh-bench
git checkout main
git pull origin main
python3 -m pip install -r requirements.txt
```

Pins and the Product-2 catalog live on `main`. Do not soak against a stale clone that still has `sha256: TBD`.

## 3. Power mode: 30W (`nvpmodel` 2); record `jetson_clocks`

Product default is **30W**. Do not default MAXN. A MAXN run is a separate labeled profile, never mixed into this envelope.

```bash
sudo nvpmodel -m 2
nvpmodel -q
```

`jetson_clocks` is optional. Record whether it was on:

```bash
# optional; if you enable it, set host.jetson_clocks: true on the scorecard
# sudo jetson_clocks
```

Write `host.power_mode: "30W"`, `host.nvpmodel_id: 2`, and `host.jetson_clocks: true|false`.

## 4. Build or pull llama.cpp `sm_87`

Native (this host):

```bash
./recipes/gguf/build-llama-cpp-sm87.sh
```

Or pull an OCI image that already has an sm_87 `llama-bench` and run with `--runtime=nvidia` (never `--gpus`). First Full Model packaging is an **OCI wrapper that contains the GGUF**; native GGUF is the next loader on the same job kind.

Record the llama.cpp git hash (or image `repo@digest`) as `harness.version`. Verify CUDA at load: the log must show `ggml_cuda_init` / compute capability **8.7**.

## 5. Pull `llama-3.1-8b-q4` with the pinned sha256

`pull-gguf.sh` **requires a pin**. It refuses `TBD` / `<pin>` / empty unless `--allow-unpinned` (do not use that on a soak).

From the batch-12 pin (preferred):

```bash
./recipes/gguf/pull-gguf.sh \
  --model-id llama-3.1-8b-q4 \
  --out /var/lib/hypermesh/models/
```

Or pass the pin yourself (same oid as `models/agx64-batch12.yaml` / `catalog/agx64.yaml`):

```bash
./recipes/gguf/pull-gguf.sh \
  --url "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf" \
  --sha256 7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c \
  --out /var/lib/hypermesh/models/
```

Dry-run (no download; prints the pin):

```bash
./recipes/gguf/pull-gguf.sh --model-id llama-3.1-8b-q4 --dry-run
```

Filename is not identity. The script verifies sha256 after download. That hex is `identity.artifact_hash` once the file is on this box.

## 6. Run `run_one` / thin-v1 soak; emit `scorecard.json`

```bash
OUT="out/$(hostname)-$(date +%Y%m%d)"

python3 harness/run_one.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --model-id llama-3.1-8b-q4 \
  --out "$OUT"
```

Phase 0 `run_one` still writes a **null** scorecard plus TODOs. On AGX64-1 you then fill the raw files the stub already names:

| File under `$OUT/llama-3.1-8b-q4/` | What to write |
|---|---|
| `llama-bench.json` | `llama-bench -m … -ngl 99 -fa 1 -p 512,2048 -n 128 -r 5 --output-format json` |
| `ttft_hot.json` | p50/p95 TTFT **after** the hot window (`packs/thin-v1` default 600s) |
| `mem_after_load.json` / `free-after-load.txt` | `usable_ram_gib`, `ram_after_load_gib` from `free -b` / tegrastats after weights+KV |
| `power.json` | wall watts idle + load ([recipes/power/wall-watts.md](../recipes/power/wall-watts.md)); tegrastats is not wall watts |
| `identity.json` | `artifact_hash` = verified GGUF sha256; `image_hash` = `repo@digest` for `oci` |
| `reliability.json` | reboot count, OOM, unexpected restart |
| `tegrastats.log` | junction / throttle proxy |
| `scorecard.json` | map the above; **leave unknown fields null** |

Do not promote a cold `llama-bench` tg* cell into `decode_tok_s_p50_after_throttle`. Peak alone fails. Sustained band after the box is hot is the pass.

`context_fit_no_swap` starts at the advertised **4096**. If usable RAM after load cannot hold 4096, record the measured fit and do not advertise a higher ctx.

## 7. `check_scorecard.py --path-b`

Schema only (null stubs must pass):

```bash
python3 harness/check_scorecard.py "$OUT/llama-3.1-8b-q4/scorecard.json"
```

Path B enroll rules (empty `image_hash` fails; peak-only fails; unexpected reboot fails; no invented tok/s threshold):

```bash
python3 harness/check_scorecard.py --path-b "$OUT/llama-3.1-8b-q4/scorecard.json"
```

`--path-b` is red until this box measures hashes + sustained TTFT / decode after throttle. That is expected. Do not paste a blog rate to turn it green.

## 8. File under `out/` and POST back to Path B

### Where to file (this box)

```
out/<hostname>-<YYYYMMDD>/llama-3.1-8b-q4/
  scorecard.json          # Path B payload
  llama-bench.json
  ttft_hot.json
  mem_after_load.json
  power.json
  identity.json
  reliability.json
  tegrastats.log
  free-after-load.txt
  host_probe.json         # L4T, CUDA, nvpmodel, disk_free_gib, jetson_clocks
  job_result.json         # {passed, image_hash?} for the existing Host POST
  check_path_b.txt
```

`out/` is gitignored. Never commit measured trees or invented numbers.

Optional batch wrapper (still first-model only for Phase 1):

```bash
python3 harness/batch_runner.py \
  --manifest models/agx64-batch12.yaml \
  --pack packs/thin-v1 \
  --first-only \
  --out "$OUT"
```

### What to POST

The host agent already owns Path B. Do not invent a second channel. Do not hand-POST `lease_stop` to “make room.”

1. Plane operator opens the window with the Create body in [HOST_JOB.md](HOST_JOB.md) (`device_id` + optional `preempt`; no `suite_id`). Plane enqueues existing `kind=path_b` / `thin-v1`.
2. Agent pulls that job and runs `scripts/phase1_soak.sh` (or you drop the `out/` tree where the agent reads results).
3. Agent **POSTs the job result on the existing job-result channel** (`{passed, image_hash?}` plus scorecard/raw refs on disk). Not a new URL. Not a homemade curl to a dashboard. Not the override URL.
4. CP persists a `path_b_runs` row, emits `hypermesh.cert.path_b.passed.v1` or `failed.v1`, and restores the override (or `POST …/overrides/{id}/restore`).

If the agent is not enrolled on AGX64-1 yet: keep the `out/` tree. Do not POST invented metrics. Empty required Path B fields fail enroll — that is correct.

After a real green soak: promote `catalog/agx64.yaml` `llama-3.1-8b-q4` from `soak_pending` → `certified`, copy measured envelope + hashes, set `visibility: listed`. Until that edit, the site does not sell this `catalog_id`.

## Do not

- Invent tok/s, or write TOPS / nvpmodel watts as tok/s
- Copy ProventusNova / forum / Jetson AI Lab rates into the scorecard
- Default MAXN, or mix 30W and MAXN in one envelope
- Treat `cudaMemGetInfo` as Tegra allocator truth
- Count swap as “fits”
- Use `--allow-unpinned` on a soak
- Mark `run.passed: true` or catalog `status: certified` without `--path-b` green on this box
- Sell or list a `catalog_id` before certified + measured envelope + hashes
- Call `/host-certification/overrides` or hand-POST `lease_stop` / `POST /jobs` from this repo
- Invent a second job-result POST, or `docker exec` stops
- Bake GGUF into the thin runtime image and keep a second copy under ~9.9 GiB free
