#!/usr/bin/env bash
# Build and push ghcr.io/fyberlabs/hypermesh-llama for AGX / JetPack 6.
# image_hash for Path B is repo@digest — print the digest, do not invent one.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGISTRY="${REGISTRY:-ghcr.io/fyberlabs}"
IMAGE_NAME="${IMAGE_NAME:-hypermesh-llama}"
TAG="${TAG:-agx64-jp6}"
# Override on the Jetson builder, e.g. nvcr.io/nvidia/l4t-cuda:12.6.11-runtime
L4T_BASE="${L4T_BASE:-ubuntu:22.04}"

IMAGE="$REGISTRY/$IMAGE_NAME:$TAG"

echo "building $IMAGE (L4T_BASE=$L4T_BASE)"
echo "TODO(phase1): set L4T_BASE to the JetPack CUDA image pinned on the Fyber AGX"

docker build \
  -f "$ROOT/recipes/oci/Dockerfile.llama" \
  --build-arg "L4T_BASE=$L4T_BASE" \
  -t "$IMAGE" \
  "$ROOT"

if [[ "${PUSH:-0}" == "1" ]]; then
  docker push "$IMAGE"
  DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)"
  echo "pushed. record image_hash as repo@digest, e.g. $DIGEST"
else
  echo "built locally. re-run with PUSH=1 after ghcr login to publish."
  echo "do not write an empty or guessed digest into a Path B scorecard."
fi
