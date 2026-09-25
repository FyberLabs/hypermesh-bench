# AGX64-1 · llama-3.1-8b-q4 · llama.cpp v0.5.0 (7fe450e) · MODE_30W

**Decode: 8.27 tok/s median** (min 8.21, max 8.31, 7 runs)
Prompt processing: 250.1 tok/s median (min 249.5, max 250.8)

| Field | Value |
|---|---|
| Device | AGX64-1, Jetson AGX Orin 64GB, L4T R39.2.1, kernel 6.8.12-1021-tegra |
| Power mode | `MODE_30W` (nvpmodel id 2), jetson_clocks not applied, unchanged |
| Image | `ghcr.io/fyberlabs/hypermesh-test@sha256:2d55427abb5fa0c5013985a7ef27a8821ae9046df165603219187c81923b2674` (`:llama-v0.5.0`) |
| llama.cpp | `version: 0.5.0 (build 1, commit 7fe450e)` |
| Model | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf, sha256 `7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c` |
| Server config | hypermesh-host#34 lease config: `LLAMA_ARG_JINJA=1`, `LLAMA_ARG_CTX_SIZE=16384`, `LLAMA_ARG_TIMEOUT=1800`, `--alias llama-3.1-8b-q4`, `--runtime nvidia`, image-default GPU offload (no `-ngl`) |
| Prompt / output | 512 prompt tokens (511 + BOS), exactly 256 generated tokens (`ignore_eos`), temperature 0, `cache_prompt: false` |
| Prompt sha256 | `3a120c87f20df08ee944de64d1a48a8fb9ec11cdd83443a4144cbe288a4fb776` (text in the JSON) |
| Timestamp | 2026-09-24T23:58:30-04:00 (2026-09-24 23:58:30 EDT) |
| Source | llama-server `timings` from `/completion` (primary; same path as leases). `llama-bench` is not in the image. |

## Runs (after 1 uncounted warm-up)

| run | prompt_n | prompt tok/s | predicted_n | decode tok/s |
|---|---|---|---|---|
| 1 | 512 | 250.80 | 256 | 8.274 |
| 2 | 512 | 250.64 | 256 | 8.310 |
| 3 | 512 | 250.74 | 256 | 8.280 |
| 4 | 512 | 249.50 | 256 | 8.211 |
| 5 | 512 | 249.79 | 256 | 8.251 |
| 6 | 512 | 249.52 | 256 | 8.305 |
| 7 | 512 | 250.08 | 256 | 8.273 |

All 7 outputs were byte-identical (greedy decoding).

## Reproduce

Run this on AGX64-1 with **no lease container running** (`docker ps` must be empty). Do not change nvpmodel.

```bash
docker run -d --name hm-bench-probe --runtime nvidia --network host -v /home/chris/hypermesh/workload/model.gguf:/models/model.gguf:ro -e LLAMA_ARG_JINJA=1 -e LLAMA_ARG_CTX_SIZE=16384 -e LLAMA_ARG_TIMEOUT=1800 --entrypoint /bin/sh ghcr.io/fyberlabs/hypermesh-test@sha256:2d55427abb5fa0c5013985a7ef27a8821ae9046df165603219187c81923b2674 -c 'exec llama-server -m /models/model.gguf --host 127.0.0.1 --port 18080 --alias llama-3.1-8b-q4'
# wait for: curl -s http://127.0.0.1:18080/health  -> {"status":"ok"}
python3 bench/bench.py 7      # 1 warm-up + 7 measured /completion runs, writes /tmp/hm-bench/bench_runs.json
docker rm -f hm-bench-probe
```

`bench/bench.sh` is the full driver used for this result: pre-check, metadata capture, docker run, health wait, bench, cleanup, and model sha256. It expects `bench.py` at `/tmp/bench.py`.
Raw per-run timings and all captured metadata are in [`20260924-v0.5.0-mode30w.json`](20260924-v0.5.0-mode30w.json).
