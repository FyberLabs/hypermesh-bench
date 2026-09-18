# Host job → Phase 1 soak entrypoint

How the Hypermesh **host agent** maps an existing `kind=path_b` job onto this repo’s automated soak. Bench **emits files**. Host **owns the job-result POST**. The **plane** owns the certification window. This document names the signed-off override routes so operators know who does what — it does not add those calls to the soak script.

## Product lock

Automated **deploy + run** for AGX host certification. Not chat. Not a hand `lease_stop`.

The plane door is Panopticon **host certification override** ([panopticon#108](https://github.com/FyberLabs/panopticon/pull/108)):

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/hypermesh/host-certification/overrides` |
| `POST` | `/api/v1/hypermesh/host-certification/overrides/{id}/restore` |

The plane creates the window: **schedule_hold** → optional **preempt** (internal `enqueue_lease_stop`, not a human POST) → enqueue existing **`path_b` `thin-v1`** → **restore** (automatic on complete/fail-closed, or explicit `…/restore`).

This repo’s entrypoint **only runs when Host pulls `kind=path_b` / `suite_id=thin-v1`**. Do not add those override URLs to `scripts/phase1_soak.sh`. Do not teach hand POST `lease_stop` / `lease_start` / `POST /jobs` to get a box ready for soaks.

## Mapping

```
CP  — existing GET /api/v1/hypermesh/agent/jobs
      kind=path_b  suite_id=thin-v1 (empty → thin-v1)
      catalog_id=llama-3.1-8b-q4  device_id=<AGX64-1>
Host — invoke only for this pulled path_b (not chat / not this script POSTing lease_stop):

        ./scripts/phase1_soak.sh \
          --model-id llama-3.1-8b-q4 \
          --pack packs/thin-v1 \
          --device-id "$HM_DEVICE_ID" \
          --loader gguf \
          --out "$HM_OUT_DIR"

      read job_result.json + scorecard.json
CP  — existing POST /api/v1/hypermesh/agent/jobs/{job_id}/result
      body: { "passed": bool, "image_hash": "…" }   // JobResultRequest
      persist path_b_runs; emit hypermesh.cert.path_b.{passed|failed}.v1
```

Job fields Host already has (`hypermesh-host` `api.Job`): `job_id`, `kind`, `device_id`, optional `catalog_id`, `image_digest`, `runtime`, `backend`, `suite_id`, `until`.

| Job / env | Entrypoint |
|---|---|
| `kind=path_b` / `HM_JOB_KIND` | Only accepted kind. `lease_stop` / `lease_start` / chat → setup fail (exit 3) |
| `suite_id` empty or `thin-v1` / `HM_SUITE_ID` | `packs/thin-v1` |
| `catalog_id` / `HM_CATALOG_ID` | `--model-id` (Phase 1: `llama-3.1-8b-q4`) |
| `device_id` / `HM_DEVICE_ID` | `--device-id` → `run.device_id` |
| `image_digest` / `HM_IMAGE_DIGEST` | Optional OCI pin when `--loader oci` |
| class | `HM_CLASS_ID=fyber-agx-orin-64gb` |
| models / out | `HM_MODELS_DIR`, `HM_OUT_DIR` |

Result body Host already POSTs (`api.JobResultRequest`): **`passed`**, optional **`image_hash`**. `emit_job_result.py` writes those two fields first. Extra keys on `job_result.json` (`raw_refs`, `scorecard`, …) are for the agent on disk; Go unmarshal ignores unknowns. Do not invent a second URL or a dashboard curl.

`image_hash` is only written when measured (`repo@digest` from `docker inspect`, or a job pin that is already `repo@digest`). Loader `gguf` leaves it null here; Host may fill the telem / host hash it already uses for Path B. Do not mint a hash from L4T text. Catalog GGUF `artifact_hash` is not the job-result `image_hash`.

## Validation override (plane creates the window; bench consumes `path_b`)

Product sign-off (panopticon#108): the **plane** is the door. Plane operator (`X-Tenant-ID` / portal JWT — not a site `hm_site_…` token, not `POST /jobs`):

```
POST /api/v1/hypermesh/host-certification/overrides
     { device_id, preempt? }     # preempt defaults true
GET  /api/v1/hypermesh/host-certification/overrides
GET  /api/v1/hypermesh/host-certification/overrides/{id}
POST /api/v1/hypermesh/host-certification/overrides/{id}/restore
```

Sequence the **plane** owns (Host only sees agent-jobs):

| Step | Plane | Host sees |
|---|---|---|
| Hold | `Device.schedule_hold = true` (chooser omit; not `sell_pause`) | nothing |
| Preempt | optional; internal `enqueue_lease_stop` if a live lease | `kind=lease_stop` on `GET /agent/jobs` — Host’s existing stop path, **not** this soak script |
| Cert | enqueue existing `path_b` (`thin-v1` on AGX) | `kind=path_b` on the same pull → **`./scripts/phase1_soak.sh`** |
| Restore | lift hold (`ready` / fail-closed / `POST …/restore`) | nothing |

`enqueue_lease_stop` is an **internal primitive**. Do not hand-POST `lease_stop` (or chat it) to clear a renter. A later plane-side operator helper (portal JWT → `POST …/overrides`) may live **outside** Host and **outside** this soak script. It is not in this repo.

Bench / Host consume-only:

1. Host pulls `kind=path_b`, `suite_id=thin-v1` (empty suite → `thin-v1`). That is the only signal that a window is open for this entrypoint.
2. Host runs `scripts/phase1_soak.sh`. Not chat. Not `lease_stop`. The script does not call override or `/jobs` URLs.
3. If Host is told there is no window (`HM_VALIDATION_WINDOW=denied` / `no_hold` / `0`, or `HM_VALIDATION_DENIED=1`), exit **4**. Do not fight an active renter lease. Do not invent preempt.
4. Host POSTs the existing job-result body `{passed, image_hash?}`. The plane advances the override and restores on its side.

Until the override is deployed: same entrypoint on an idle host. Keep `out/` if the agent is not enrolled. Still no invented metrics POST. Still no hand `lease_stop`.

## Exit codes Host should honor

| Code | Meaning |
|---|---|
| 0 | Scorecard written and `--path-b` green |
| 2 | Bundle written; Path B enroll rules red (`passed=false`) |
| 3 | Setup / disk / pin / wrong kind — do not claim a soak |
| 4 | Skip — no validation window |

Never treat exit 0 as a pass when `job_result.passed` is false. Never invent tok/s to force green.

## Locks

- JetPack / L4T on the host. Alpine-first only for containers (this CUDA runtime is L4T-based).
- `docker run --runtime=nvidia` only. No `docker exec` stop invents.
- No AImmune tokens. Soak script adds no CP endpoints and does not POST the override.
- Do not promote `catalog/agx64.yaml` to `certified` from this job.
