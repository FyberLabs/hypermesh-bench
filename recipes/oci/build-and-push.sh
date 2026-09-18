#!/usr/bin/env bash
# Build and push a thin ghcr.io/fyberlabs/hypermesh-llama runtime (no GGUF).
# image_hash for Path B is repo@digest — print the digest, do not invent one.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGISTRY="${REGISTRY:-ghcr.io/fyberlabs}"
IMAGE_NAME="${IMAGE_NAME:-hypermesh-llama}"
# Thin runtime tag. Do not bake GGUF into this default.
TAG="${TAG:-agx64-jp-thin}"
# Production MUST set L4T_BASE to the JetPack CUDA image on the Fyber AGX.
# ubuntu:22.04 is reviewable off-box only — not a Path B identity.
L4T_BASE="${L4T_BASE:-ubuntu:22.04}"

IMAGE="$REGISTRY/$IMAGE_NAME:$TAG"

echo "building thin runtime $IMAGE (L4T_BASE=$L4T_BASE)"
if [[ "$L4T_BASE" == "ubuntu:22.04" ]]; then
  echo "warning: L4T_BASE is the off-box default. Release builds must pin JetPack CUDA." >&2
fi

docker build \
  -f "$ROOT/recipes/oci/Dockerfile.llama" \
  --build-arg "L4T_BASE=$L4T_BASE" \
  -t "$IMAGE" \
  "$ROOT"

if [[ "${PUSH:-0}" == "1" ]]; then
  docker push "$IMAGE"
  DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)"
  echo "pushed. record image_hash as repo@digest, e.g. $DIGEST"
  echo "do not write an empty or guessed digest into a Path B scorecard."
else
  echo "built locally. re-run with PUSH=1 after ghcr login to publish."
  echo "do not write an empty or guessed digest into a Path B scorecard."
fi
