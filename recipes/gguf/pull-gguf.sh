#!/usr/bin/env bash
# Pull a GGUF by URL and verify sha256. Filename is not identity.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: pull-gguf.sh --url URL --sha256 HEX --out DIR [--filename NAME] [--allow-unpinned]

Refuses sha256=TBD unless --allow-unpinned (Phase 0 only; do not soak with it).
EOF
  exit 2
}

URL=""
SHA256=""
OUT_DIR=""
FILENAME=""
ALLOW_UNPINNED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    --sha256) SHA256="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --filename) FILENAME="${2:-}"; shift 2 ;;
    --allow-unpinned) ALLOW_UNPINNED=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -n "$URL" && -n "$SHA256" && -n "$OUT_DIR" ]] || usage

if [[ -z "$FILENAME" ]]; then
  FILENAME="$(basename "${URL%%\?*}")"
fi

if [[ "$SHA256" == "TBD" || "$SHA256" == "<pin>" || "$SHA256" == "" ]]; then
  if [[ "$ALLOW_UNPINNED" -ne 1 ]]; then
    echo "error: sha256 is unpinned ($SHA256). Pin the file hash or pass --allow-unpinned." >&2
    exit 1
  fi
  echo "warning: pulling without a pinned sha256; artifact_hash will stay null" >&2
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

if [[ "$SHA256" != "TBD" && "$SHA256" != "<pin>" && -n "$SHA256" ]]; then
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
