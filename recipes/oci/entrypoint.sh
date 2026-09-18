#!/usr/bin/env bash
# llama-bench | llama-server entry for the thin OCI runtime.
# Runtime contract: docker run --runtime=nvidia (never --gpus).
# Do not bake GGUF. Weights come from /models.
set -euo pipefail

REQUIRE_BENCH="${REQUIRE_BENCH:-0}"
if [[ "${1:-}" == "--require-bench" ]]; then
  REQUIRE_BENCH=1
  shift
fi

MODE="${1:-llama-bench}"
if [[ "$MODE" == "llama-bench" || "$MODE" == "llama-server" ]]; then
  shift || true
fi

BIN_DIR="${LLAMA_BIN_DIR:-/opt/hypermesh/bin}"
OUT_DIR="${HYPERMESH_OUT:-/out}"
mkdir -p "$OUT_DIR"

fail_missing() {
  local bin="$1"
  echo "error: $bin not in image ($BIN_DIR). Thin runtime needs sm_87 binaries." >&2
  echo "Do not invent tok/s. Scorecard metrics stay null until this binary runs on AGX." >&2
  if [[ "$REQUIRE_BENCH" == "1" ]]; then
    exit 1
  fi
}

case "$MODE" in
  llama-bench)
    BENCH="$BIN_DIR/llama-bench"
    if [[ ! -x "$BENCH" ]]; then
      fail_missing "llama-bench"
      cat >"$OUT_DIR/llama-bench.json" <<'JSON'
{"status": "not_run", "reason": "llama-bench binary not in thin image", "results": []}
JSON
      # Not silent: soak with --require-bench already exited. Scaffold exits 1.
      exit 1
    fi
    exec "$BENCH" --output-format json "$@"
    ;;
  llama-server)
    SERVER="$BIN_DIR/llama-server"
    if [[ ! -x "$SERVER" ]]; then
      fail_missing "llama-server"
      exit 1
    fi
    exec "$SERVER" "$@"
    ;;
  *)
    echo "usage: entrypoint.sh [--require-bench] llama-bench|llama-server [args…]" >&2
    exit 2
    ;;
esac
