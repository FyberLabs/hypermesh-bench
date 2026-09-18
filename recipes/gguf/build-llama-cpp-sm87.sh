#!/usr/bin/env bash
# Build llama.cpp with CUDA sm_87 (AGX Orin). Run on a JetPack/L4T host.
# Phase 0: this script is a recipe stub. It will not invent a bench number.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC_DIR="${LLAMA_CPP_SRC:-$ROOT/.cache/llama.cpp}"
BUILD_DIR="${LLAMA_CPP_BUILD:-$SRC_DIR/build-sm87}"
REPO_URL="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp}"
# Pin a git hash at soak time. Do not treat "latest" as a Path B identity.
REPO_REF="${LLAMA_CPP_REF:-master}"

if [[ "$(uname -m)" != "aarch64" ]] && [[ "${FORCE_BUILD:-}" != "1" ]]; then
  echo "warning: not aarch64; AGX native build is expected on the Jetson host." >&2
  echo "set FORCE_BUILD=1 to continue on this machine (likely useless for Path B)." >&2
fi

if ! command -v nvcc >/dev/null 2>&1 && [[ "${FORCE_BUILD:-}" != "1" ]]; then
  echo "error: nvcc not found. Install JetPack CUDA or set FORCE_BUILD=1 to skip." >&2
  echo "TODO(phase1): build on the Fyber AGX and record llama.cpp git hash as harness.version." >&2
  exit 1
fi

if [[ ! -d "$SRC_DIR/.git" ]]; then
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$SRC_DIR"
fi

cmake -S "$SRC_DIR" -B "$BUILD_DIR" \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=87 \
  -DLLAMA_BUILD_TOOLS=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build "$BUILD_DIR" --target llama-bench llama-server llama-cli -j"$(nproc)"

echo "built:"
echo "  $BUILD_DIR/bin/llama-bench"
echo "  $BUILD_DIR/bin/llama-server"
echo "record git: $(git -C "$SRC_DIR" rev-parse HEAD)"
echo "verify CUDA at load: log must show ggml_cuda_init / compute capability 8.7"
echo "no nvidia-smi on Jetson — use tegrastats or jtop"
