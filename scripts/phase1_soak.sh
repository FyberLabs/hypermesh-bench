#!/usr/bin/env bash
# Host-job-shaped Phase 1 thin-v1 soak wrapper for llama-3.1-8b-q4.
# Calls harness/phase1_soak.py. Does not POST. Does not invent tok/s.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/phase1_soak.sh [options]

Host-job-shaped entrypoint for AGX Path B Phase 1 (thin-v1 / llama-3.1-8b-q4).
Writes out/<host>-<YYYYMMDD>/llama-3.1-8b-q4/{scorecard.json,job_result.json,…}.
Host POSTs job_result {passed, image_hash?} on the existing job-result channel.
This script does not POST and does not invent tok/s, watts, or hashes.

Options:
  --model-id ID          Default: llama-3.1-8b-q4 (or HM_CATALOG_ID)
  --pack DIR             Default: packs/thin-v1
  --device-id UUID       Or HM_DEVICE_ID
  --out DIR              Default: out/$(hostname)-$(date +%Y%m%d) or HM_OUT_DIR
  --loader gguf|oci      Default: gguf (or HM_LOADER). oci needs --image / HM_IMAGE_DIGEST
  --image REF            Thin runtime image (no baked GGUF)
  --models-dir DIR       Default: HM_MODELS_DIR or /var/lib/hypermesh/models
  --stub                 Dry-run: no pull, no GPU, null metrics (CI)
  --require-path-b       Exit 2 if check_scorecard --path-b is red
  --require-bench        Fail if llama-bench binary / image is missing
  --skip-hot-window      Do not sleep pack hot_window_s; TTFT stays null if unmeasured
  --skip-pull            Reuse a verified GGUF cache
  --no-nvpmodel          Do not sudo nvpmodel -m 2
  -h, --help             This help

Host-job env (run only when Host pulled kind=path_b thin-v1):
  HM_JOB_KIND            Must be path_b (lease_stop is not this entrypoint)
  HM_SUITE_ID            thin-v1 (empty → thin-v1)
  HM_CATALOG_ID          llama-3.1-8b-q4
  HM_DEVICE_ID           Agent identity
  HM_CLASS_ID            fyber-agx-orin-64gb
  HM_IMAGE_DIGEST        Optional OCI pin (repo@digest)
  HM_MODELS_DIR          Local GGUF cache
  HM_OUT_DIR             Artifact root
  HM_REQUIRE_PATH_B=1    Fail process if Path B is red
  HM_VALIDATION_WINDOW   denied|no_hold|0 → exit 4 (skip). Unset/open → run
  HM_VALIDATION_DENIED=1 Exit 4. Plane owns hold+preempt+restore
                         (POST/GET …/host-certification/overrides).
                         This script does not POST those URLs or lease_stop.

Exit codes:
  0  scorecard written; Path B green (or --stub without --require-path-b)
  2  measured / written but Path B enroll rules red
  3  setup / disk / pin / wrong job kind
  4  skipped (no validation window / preempt denied)

Disk: refuse if models volume free < pin size_bytes + 2 GiB headroom.
On ~9.9 GiB free: pull GGUF once + thin runtime image. Do not bake+duplicate.
EOF
}

STUB=0
NO_NVP=0
FORWARD=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --stub)
      STUB=1
      FORWARD+=(--stub)
      shift
      ;;
    --no-nvpmodel)
      NO_NVP=1
      shift
      ;;
    --out|--model-id|--pack|--device-id|--class-id|--suite-id|--job-kind|--models-dir|--loader|--image|--completions-url|--hot-window-s|--skip-reason|--manifest)
      FORWARD+=("$1")
      if [[ $# -lt 2 ]]; then
        echo "error: $1 requires a value" >&2
        exit 3
      fi
      FORWARD+=("$2")
      shift 2
      ;;
    --require-path-b|--require-bench|--require-model|--skip-hot-window|--skip-pull)
      FORWARD+=("$1")
      shift
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 3
      ;;
  esac
done

if [[ "$STUB" -ne 1 && "$NO_NVP" -ne 1 ]]; then
  if command -v nvpmodel >/dev/null 2>&1; then
    if [[ "${EUID}" -eq 0 ]]; then
      nvpmodel -m 2 || echo "warning: nvpmodel -m 2 failed" >&2
    elif command -v sudo >/dev/null 2>&1; then
      sudo nvpmodel -m 2 || echo "warning: sudo nvpmodel -m 2 failed" >&2
    fi
  fi
fi

exec "$PYTHON" "$ROOT/harness/phase1_soak.py" "${FORWARD[@]}"
