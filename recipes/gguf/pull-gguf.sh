#!/usr/bin/env bash
# Pull a GGUF by URL and verify sha256. Filename is not identity.
# Pins come from --sha256 or from models/agx64-batch12.yaml via --model-id.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEFAULT_MANIFEST="$ROOT/models/agx64-batch12.yaml"

usage() {
  cat <<'EOF'
Usage:
  pull-gguf.sh --url URL --sha256 HEX --out DIR [--filename NAME]
  pull-gguf.sh --model-id ID [--manifest PATH] --out DIR [--filename NAME]

Options:
  --dry-run          Print the pin (url, sha256, file) and exit 0. No download.
  --allow-unpinned   Phase 0 only. Do not soak with it.

A pinned 64-hex sha256 is required. Refuses TBD / <pin> / empty unless
--allow-unpinned. --model-id reads url + sha256 + file from the batch manifest.
EOF
  exit 2
}

resolve_from_manifest() {
  local manifest="$1"
  local model_id="$2"
  python3 - "$manifest" "$model_id" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "error: PyYAML is required to resolve --model-id; "
        "pip install -r requirements.txt\n"
    )
    sys.exit(1)

path, model_id = Path(sys.argv[1]), sys.argv[2]
if not path.is_file():
    sys.stderr.write(f"error: manifest not found: {path}\n")
    sys.exit(1)
data = yaml.safe_load(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    sys.stderr.write(f"error: {path}: expected a mapping\n")
    sys.exit(1)
for row in data.get("models") or []:
    if row.get("id") == model_id or row.get("catalog_id") == model_id:
        print(row.get("url") or "")
        print(row.get("sha256") or "")
        print(row.get("file") or "")
        print(row.get("size_bytes") or "")
        raise SystemExit(0)
sys.stderr.write(f"error: model id not in manifest: {model_id}\n")
raise SystemExit(1)
PY
}

is_unpinned() {
  local sha="${1:-}"
  [[ -z "$sha" || "$sha" == "TBD" || "$sha" == "<pin>" ]]
}

is_hex64() {
  [[ "${1:-}" =~ ^[0-9a-fA-F]{64}$ ]]
}

URL=""
SHA256=""
OUT_DIR=""
FILENAME=""
MODEL_ID=""
MANIFEST="$DEFAULT_MANIFEST"
ALLOW_UNPINNED=0
DRY_RUN=0
SIZE_BYTES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    --sha256) SHA256="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --filename) FILENAME="${2:-}"; shift 2 ;;
    --model-id) MODEL_ID="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --allow-unpinned) ALLOW_UNPINNED=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -n "$MODEL_ID" ]]; then
  mapfile -t RESOLVED < <(resolve_from_manifest "$MANIFEST" "$MODEL_ID")
  if [[ -z "$URL" ]]; then
    URL="${RESOLVED[0]:-}"
  fi
  if [[ -z "$SHA256" ]]; then
    SHA256="${RESOLVED[1]:-}"
  elif [[ -n "${RESOLVED[1]:-}" && "$SHA256" != "${RESOLVED[1]}" ]]; then
    echo "warning: --sha256 does not match manifest pin for $MODEL_ID" >&2
    echo "  flag:     $SHA256" >&2
    echo "  manifest: ${RESOLVED[1]}" >&2
  fi
  if [[ -z "$FILENAME" ]]; then
    FILENAME="${RESOLVED[2]:-}"
  fi
  SIZE_BYTES="${RESOLVED[3]:-}"
fi

if [[ -z "$URL" || -z "$SHA256" ]]; then
  echo "error: need --url and --sha256, or --model-id that resolves both" >&2
  usage
fi

if [[ "$DRY_RUN" -ne 1 && -z "$OUT_DIR" ]]; then
  echo "error: --out DIR is required unless --dry-run" >&2
  usage
fi

if [[ -z "$FILENAME" ]]; then
  FILENAME="$(basename "${URL%%\?*}")"
fi

if is_unpinned "$SHA256"; then
  if [[ "$ALLOW_UNPINNED" -ne 1 ]]; then
    echo "error: sha256 is unpinned ($SHA256). Pass a 64-hex pin or --model-id." >&2
    exit 1
  fi
  echo "warning: pulling without a pinned sha256; artifact_hash will stay null" >&2
elif ! is_hex64 "$SHA256"; then
  echo "error: sha256 must be 64 hex chars (got: $SHA256)" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "dry-run"
  [[ -n "$MODEL_ID" ]] && echo "model_id=$MODEL_ID"
  echo "url=$URL"
  echo "sha256=$SHA256"
  echo "filename=$FILENAME"
  [[ -n "$SIZE_BYTES" ]] && echo "size_bytes=$SIZE_BYTES"
  [[ -n "$OUT_DIR" ]] && echo "out=$OUT_DIR"
  echo "manifest=$MANIFEST"
  exit 0
fi

mkdir -p "$OUT_DIR"
DEST="$OUT_DIR/$FILENAME"

if command -v huggingface-cli >/dev/null 2>&1 && [[ "$URL" == *huggingface.co* ]]; then
  echo "huggingface-cli is available; still using curl so the exact URL+sha256 pair is honored"
fi

if command -v curl >/dev/null 2>&1; then
  curl -fL --retry 3 --retry-delay 2 -o "$DEST" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget -O "$DEST" "$URL"
else
  echo "error: need curl or wget" >&2
  exit 1
fi

if ! is_unpinned "$SHA256"; then
  ACTUAL="$(sha256sum "$DEST" | awk '{print $1}')"
  if [[ "$ACTUAL" != "$SHA256" ]]; then
    echo "error: sha256 mismatch for $DEST" >&2
    echo "  expected: $SHA256" >&2
    echo "  actual:   $ACTUAL" >&2
    rm -f "$DEST"
    exit 1
  fi
  echo "ok $DEST sha256=$ACTUAL"
else
  echo "pulled $DEST (unverified)"
fi
