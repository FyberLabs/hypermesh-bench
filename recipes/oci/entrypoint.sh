#!/usr/bin/env bash
# llama-bench | llama-server entry for the OCI wrapper.
# Runtime contract: docker run --runtime=nvidia (never --gpus).
set -euo pipefail

MODE="${1:-llama-bench}"
if [[ "$MODE" == "llama-bench" || "$MODE" == "llama-server" ]]; then
  shift || true
fi

BIN_DIR="${LLAMA_BIN_DIR:-/opt/hypermesh/bin}"
OUT_DIR="${HYPERMESH_OUT:-/out}"
mkdir -p "$OUT_DIR"

case "$MODE" in
  llama-bench)
    BENCH="$BIN_DIR/llama-bench"
    if [[ ! -x "$BENCH" ]]; then
      echo "TODO(phase1): llama-bench binary not in image yet ($BENCH)." >&2
      echo "Do not invent tok/s. Scorecard metrics stay null until this binary runs on AGX." >&2
      cat >"$OUT_DIR/llama-bench.json" <<'JSON'
{"status": "not_run", "reason": "llama-bench binary not baked in Phase 0 image", "results": []}
JSON
      exit 0
    fi
    # Product default cells: -p 512,2048 -n 128. Caller may override.
    exec "$BENCH" --output-format json "$@"
    ;;
  llama-server)
    SERVER="$BIN_DIR/llama-server"
    if [[ ! -x "$SERVER" ]]; then
      echo "TODO(phase1): llama-server binary not in image yet ($SERVER)." >&2
      exit 1
    fi
    exec "$SERVER" "$@"
    ;;
  *)
    echo "usage: entrypoint.sh llama-bench|llama-server [args…]" >&2
    exit 2
    ;;
esac
