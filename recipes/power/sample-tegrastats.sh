#!/usr/bin/env bash
# Sample tegrastats while a soak runs. This is a BOARD PROXY, not wall watts.
# Label the method. Leave wall_watts_* null unless a wall meter was used.
set -euo pipefail

OUT_DIR="${1:-.}"
SECONDS_TO_RUN="${2:-30}"
INTERVAL_MS="${3:-1000}"
mkdir -p "$OUT_DIR"

LOG="$OUT_DIR/tegrastats.log"
META="$OUT_DIR/power.json"

if ! command -v tegrastats >/dev/null 2>&1; then
  echo "tegrastats not found (expected on JetPack). writing null power.json" >&2
  cat >"$META" <<EOF
{
  "method": "unavailable",
  "note": "tegrastats missing; wall_watts_* stay null. Do not invent watts.",
  "idle_w": null,
  "load_w": null,
  "junction_c_max": null,
  "log": null
}
EOF
  exit 0
fi

# tegrastats has no JSON mode; keep the raw log for Phase 1 parse.
timeout "${SECONDS_TO_RUN}s" tegrastats --interval "$INTERVAL_MS" >"$LOG" || true

cat >"$META" <<EOF
{
  "method": "tegrastats_proxy",
  "note": "Board rail proxy only. See recipes/power/wall-watts.md. wall_watts_* stay null unless a wall meter is attached.",
  "idle_w": null,
  "load_w": null,
  "junction_c_max": null,
  "log": "tegrastats.log"
}
EOF

echo "wrote $LOG and $META (watts still null)"
