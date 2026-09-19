# OCI packaging (AGX / JetPack)

Thin **runtime** image: `llama-bench` + `llama-server` only. GGUF is volume-mounted at `/models`. Do not bake weights into the default tag.

## Disk (~9.9 GiB free on AGX64-1)

| Strategy | Disk | Phase 1 |
|---|---|---|
| Thin runtime pull + one `pull-gguf.sh` | Image hundreds of MB–few GB + ~4.92 GiB GGUF | **Preferred** |
| Native llama.cpp build + GGUF | Build tree + GGUF | OK if toolchain present and cache pruned |
| Fat image with baked GGUF | Image ≥ GGUF; easy to double with a local copy | Defer |
| Fat layers **and** a local GGUF | Likely OOS | **Forbidden** until space is recovered |

Headroom for the soak disk gate: pin `size_bytes` + **2 GiB**. Prefer pull-once, prune dangling images.

## Build (JetPack builder)

`L4T_BASE` is **required** for a release image. The default `ubuntu:22.04` is reviewable off-box only.

```bash
L4T_BASE=nvcr.io/nvidia/l4t-cuda:<pin>-runtime \
  ./recipes/oci/build-and-push.sh
```

Copy sm_87 binaries into the image from a native `./recipes/gguf/build-llama-cpp-sm87.sh` (or a multi-stage build on JetPack). Record `image_hash` as `repo@digest` after push — never invent a digest.

## Run

```bash
docker run --rm --runtime=nvidia --network host \
  -e REQUIRE_BENCH=1 \
  -v /var/lib/hypermesh/models:/models:ro \
  -v /var/lib/hypermesh/out:/out \
  ghcr.io/fyberlabs/hypermesh-llama:agx64-jp-thin \
  llama-bench -m /models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
    -p 512,2048 -n 128 -r 5 -ngl 99 -fa 1
```

Never `--gpus`. Never `docker exec` as a stop invent. Entrypoint exits non-zero if the binary is missing (`REQUIRE_BENCH=1` or default llama-bench miss).
