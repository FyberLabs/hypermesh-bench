# Host job → Phase 1 soak entrypoint

How the Hypermesh **host agent** maps an existing `kind=path_b` job onto this repo’s automated soak. Bench **emits files**. Host **owns the POST**. This document does not invent Panopticon or control-plane routes.

## Product lock

Automated **deploy + run** for AGX host certification. Not chat. Not `lease_stop`.

Panopticon owns the **validation override** (schedule hold + temporary lease preempt). This repo only **consumes** that door via the existing `path_b` job. Do not add CP APIs here.

## Mapping

```
CP  — existing GET /api/v1/hypermesh/agent/jobs
      kind=path_b  suite_id=thin-v1 (empty → thin-v1)
      catalog_id=llama-3.1-8b-q4  device_id=<AGX64-1>
Host — invoke (instead of chat / lease_stop):

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

## Validation override (consume only)

1. Operator or CP opens a validation window the way Panopticon ships (hold schedule + preempt lease). Bench does not call that API.
2. During the window, CP enqueues the **existing** `path_b` job (`suite_id=thin-v1`, `catalog_id=llama-3.1-8b-q4`).
3. Host, on `path_b`, runs `scripts/phase1_soak.sh` — not a chat / `lease_stop` handoff.
4. If Host is told there is no hold / validation is denied, set `HM_VALIDATION_WINDOW=denied` (or `no_hold` / `0`) or `HM_VALIDATION_DENIED=1`. The entrypoint exits **4** (skip). Do not fight an active renter lease. Do not invent preempt.
5. On completion, Host POSTs the existing job-result body. CP clears hold / restores lease policy on its side.

Until the override exists: run the same entrypoint on an idle host. Keep `out/` if the agent is not enrolled. Still no invented metrics POST.

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
- No AImmune tokens. No new CP endpoints.
- Do not promote `catalog/agx64.yaml` to `certified` from this job.
