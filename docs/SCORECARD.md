# Path B scorecard

Source of truth for fields: Hypermesh [`host-scorecard.md`](https://github.com/FyberLabs/hypermesh-docs/blob/main/host-scorecard.md). Machine schema: [`schemas/scorecard.schema.json`](../schemas/scorecard.schema.json). Schema id: `hypermesh.path_b.scorecard.v0`.

Empty required enroll fields fail Path B. **Unmeasured metrics stay `null`.** Do not invent tok/s. Do not write TOPS as tok/s. Peak alone fails; sustained band after the box is hot is the pass.

## Shape

```json
{
  "schema": "hypermesh.path_b.scorecard.v0",
  "run": {
    "run_id": null,
    "device_id": null,
    "class_id": "fyber-agx-orin-64gb",
    "catalog_id": null,
    "passed": null,
    "started_at": null,
    "ended_at": null,
    "skip_reason": null
  },
  "host": {
    "class_id": "fyber-agx-orin-64gb",
    "sku": "NVIDIA Jetson AGX Orin 64GB",
    "serial": null,
    "device_id": null,
    "jetpack_l4t": null,
    "cuda": null,
    "container_runtime": "nvidia",
    "backend": "cuda-jetson",
    "loader": "oci",
    "power_mode": "30W",
    "nvpmodel_id": 2,
    "jetson_clocks": false,
    "nic": { "iface": "eth0", "speed_mbps": null, "wan_rtt_ms": null }
  },
  "identity": {
    "image_hash": null,
    "artifact_hash": null,
    "prompt_pack_id": "thin-v1",
    "suite_id": "thin-v1",
    "catalog_id": null
  },
  "memory": {
    "advertised_ram_gib": 64,
    "usable_ram_gib": null,
    "ram_after_load_gib": null,
    "bandwidth_class": "LPDDR5-UMA",
    "precision": "Q4_K_M",
    "context_fit_no_swap": 4096,
    "kv_headroom_gib": null,
    "notes": "Measure free after OS+runtime+weights+KV. Do not treat cudaMemGetInfo as Tegra allocator truth."
  },
  "inference_sustained": {
    "prompt_pack": "thin-v1",
    "batch": 1,
    "hot_window_s": 600,
    "ttft_ms_p50_after_throttle": null,
    "ttft_ms_p95_after_throttle": null,
    "decode_tok_s_p50_after_throttle": null,
    "decode_tok_s_p95_after_throttle": null,
    "prefill_tok_s_p50": null,
    "peak_vs_sustained": {
      "peak_decode_tok_s": null,
      "sustained_decode_tok_s": null,
      "pass_rule": "sustained_within_band_of_class_envelope; peak_alone_fails"
    }
  },
  "power_thermals": {
    "wall_watts_idle": null,
    "wall_watts_load": null,
    "tokens_per_kwh": null,
    "junction_c_max": null,
    "throttle_events": 0,
    "duty_cycle": null
  },
  "storage": {
    "nvme_gib": null,
    "artifact_and_image_fit": null
  },
  "reliability": {
    "reboot_rate_per_24h": null,
    "path_b": null,
    "time_to_fail_s": null,
    "unexpected_restart": false
  },
  "harness": {
    "name": "llama-bench",
    "version": null,
    "raw_outputs": ["llama-bench.json", "tegrastats.log", "free-after-load.txt"]
  },
  "numbers_policy": "Do not invent tok/s. Leave null until measured on this class + pack + image."
}
```

`class_id`, `power_mode`, and `nvpmodel_id` are labels, not measurements. They default to the AGX product profile (`fyber-agx-orin-64gb`, `30W`, `2`) so every stub is labeled. `image_hash` / `artifact_hash` stay null until a real digest exists — never invent a hash.

## Must-have raw outputs (Phase 1+)

| Output | Producer | Required fields |
|---|---|---|
| `llama-bench.json` | llama-bench `--output-format json` | model, quant, ngl, fa, pp*/tg* tok/s mean±stdev |
| `ttft_hot.json` | custom client or AIPerf against `llama-server` | p50/p95 TTFT after warm requests + thermal soak |
| `mem_after_load.json` | `free -b`, tegrastats parse | `usable_ram_gib`, `ram_after_load_gib` |
| `power.json` | wall meter or labeled INA/tegrastats proxy | idle_w, load_w, method |
| `identity.json` | sha256sum + `docker inspect` | `artifact_hash`, `image_hash` (`repo@digest` for oci) |
| `reliability.json` | soak supervisor | reboot count, OOM kills, unexpected restart |
| `host_probe.json` | `harness/host_probe.py` | L4T, CUDA, nvpmodel, disk_free_gib, jetson_clocks (null off-box) |
| `job_result.json` | `harness/emit_job_result.py` | `{passed, image_hash?}` for the existing Host POST — no new URL |

## Pass / fail (`thin-v1`, proposed)

1. Non-empty `image_hash` on a Path B result (empty fails — no fake hash).
2. Artifact loads without OOM; no swap thrash during the decode window.
3. Sustained batch-1 decode and TTFT recorded **after** the hot window (default 600s).
4. Peak-only submission → **fail**.
5. Unexpected reboot during soak → **fail**.
6. Power mode and image digest recorded; same artifact on two images = two products.
7. **No tok/s threshold is invented here.** Thresholds come from the first Fyber AGX soak envelope, then freeze.

`python3 harness/check_scorecard.py FILE` checks schema. Add `--path-b` for the enroll rules above. Phase 0 stubs are schema-valid with `passed: null`.

## AGX notes

- Advertised 64 GiB LPDDR5 is **shared** (UMA). Usable RAM is measured after OS + runtime + weights + KV.
- Default product power is **30W (`nvpmodel` 2)**. MAXN is an alternate labeled profile only.
- Record the exact JetPack/L4T and CUDA on the Fyber box. That pin is the soak SoT.
- Do not treat `cudaMemGetInfo` as Tegra allocator truth. No `nvidia-smi` on Jetson — use `tegrastats` / `jtop`.
- `tegrastats` is a **board proxy**, not wall watts. See [../recipes/power/wall-watts.md](../recipes/power/wall-watts.md).
