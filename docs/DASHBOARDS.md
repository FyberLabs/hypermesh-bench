# Per-host telemetry, class aggregation, dashboards

This repo **emits** Path B `scorecard.json`. It does not host graphs.

Chris requirement: record performance **per host**; aggregate among similar hardware **classes**; graphs click through to a specific run; dashboards let users pick **hosts** and **models**. Path B scorecard fields remain the metric SoT. Do not invent tok/s. First-party analytics only ([analytics.md](https://github.com/FyberLabs/hypermesh-docs/blob/main/analytics.md)).

## Ownership (do not blur)

| Surface | Owner | Shows | Must not |
|---|---|---|---|
| Panopticon portal / ops UI | **UI Master** | Host list, Path B history, class aggregates, run detail, host×model picker for authenticated users | Second lease truth; public fake leaderboards |
| Control plane API | Platform / CP | Persist scorecard runs, schedule Path B, emit `hypermesh.cert.path_b.*`, serve query APIs | New Kafka topic; prompt/completion logging |
| hyperme.sh | **Marketing Site** | Qualitative story; certified **only after soak**; optional sanitized class summary | Host IPs; raw dumps; third-party tok/s as ours; TOPS-as-speed |
| Renter CLI / REST | Same REST as portal | Certified classes / catalog; lease; usage | Scraping portal HTML; SSH as the product |

Build graphs, run click-through, and host/model pickers in the **Panopticon portal**. Marketing gets frozen class envelopes after soak — not a live ops dashboard.

## Data path

```
Host agent runs pack (thin-v1)
  → scorecard.json + raw harness files
  → POST Path B job result
  → persist PathBRun
  → emit hypermesh.cert.path_b.passed.v1 | failed.v1
  → portal queries REST aggregates / run detail
```

Do not invent a second monitoring product. Azure Monitor stays for ops-plane health. Product bench metrics live on Hypermesh domain rows + those cert events.

## Store grain

Metrics columns are **only** Path B scorecard fields (or null until measured).

### `path_b_runs` (click-through target)

`run_id`, `device_id`, `class_id`, `host_org_id`, `suite_id` / `prompt_pack_id`, `catalog_id`, `loader` / `backend`, `image_hash` / `artifact_hash`, `power_mode` / `nvpmodel_id` / `jetson_clocks`, `started_at` / `ended_at` / `passed`, scorecard metrics (`usable_ram_gib`, `ttft_ms_p50_after_throttle`, `decode_tok_s_p50_after_throttle`, `wall_watts_*`, reboot rate, NIC, throttle events, …), `raw_uri`, `harness_name` / `harness_version`.

Heartbeat `loaded_model_hash` is telemetry only — it does not stamp the catalog.

### `device_perf_latest`

Latest green run + trailing window per `device_id` for the host picker.

### `class_perf_agg`

Key: **`class_id` + `suite_id` + `catalog_id` (or artifact hash) + `power_mode` + context cell**.

Prefer **median-of-host-medians**. Never mix classes (NX ≠ AGX). Never mix 30W and MAXN. Never fill aggregates from TOPS or blogs. Nulls stay labeled “not soaked”, not `0`.

## Query APIs (sketch)

Additive under `/api/v1/hypermesh/`:

| Endpoint | Returns |
|---|---|
| `GET …/devices/{id}/path-b/runs` | Paginated runs for one host |
| `GET …/path-b/runs/{run_id}` | Full scorecard + raw links (ACL) |
| `GET …/classes/{class_id}/path-b/summary` | `class_perf_agg` |
| `GET …/path-b/compare?class_id=&catalog_id=&power_mode=` | Chart series |

Hosts see their devices. Renters see class-level certified envelopes + listed catalog. Ops sees full run detail.

## Portal surfaces (UI Master)

1. **Host picker** — class, Path B state, last green, last sustained decode/TTFT (`null` → “not soaked”).
2. **Model / catalog picker** — only measured envelopes; no “try Llama” as certified without hash + our numbers.
3. **Graphs** — sustained decode, TTFT after throttle, class distribution, wall watts, pass/fail. Every point → run detail (`run_id`).
4. **Layout** — class + catalog + power-mode toggle (**30W default**); aggregate strip with nulls labeled; host drill-down.

## Marketing site

Allowed after real soaks: class certified for catalog X at context Y, Hardware vs Full Model, honest fees.

Not allowed: live multi-host ops graphs on hyperme.sh; public host picker; third-party tok/s as Hypermesh; invented averages before `n_runs` / `n_hosts` exist.

## Non-goals

- Second analytics SaaS
- Prompt/completion logging “for dashboards”
- Aggregating across different `class_id` or unlabeled power modes
- Filling charts with TOPS-derived fake tok/s
- Replacing Path B with a vibe benchmark in the UI
