# Product-2 catalog

`catalog/` is the **sellable Full Model subset** (Product 2). It is not the bench matrix.

| Path | What it is | When the site may sell it |
|---|---|---|
| [`models/agx64-batch12.yaml`](../models/agx64-batch12.yaml) | Bench matrix of 12 GGUFs to soak on AGX64 | Never, by itself |
| [`catalog/agx64.yaml`](agx64.yaml) | Product-2 rows for `class_id: fyber-agx-orin-64gb` | Only when `status: certified` |

A Product-2 row is one artifact: file hash, quant, loader, backend, class, power profile, advertised context, and a **measured** envelope. It is tighter than “fits.” Filename is not identity.

## Status

| `status` | Meaning | Sell? |
|---|---|---|
| `soak_pending` | Proposed first row. Hash is pinned. Envelope not measured. | **No** |
| `candidate` | In the batch-12 matrix. Not offered. | **No** |
| `certified` | Soak on this class passed. Envelope + hashes recorded. | Yes (`visibility: listed`) |
| `rejected` | Soak failed or withdrawn. | **No** |

**Rule:** the site must not sell a `catalog_id` until `status=certified` with a measured envelope and pinned hashes. `soak_pending` and `candidate` are not sellable. Do not invent tok/s. Do not write TOPS as tok/s. Third-party blog rates are not ours.

Scorecard fields on every row stay `null` until a Fyber AGX soak writes them. Promoting a row is a deliberate edit after [`docs/PHASE1_SOAK.md`](../docs/PHASE1_SOAK.md), not a filename change.

First proposed row: `llama-3.1-8b-q4` on `fyber-agx-orin-64gb` at 30W / `nvpmodel` 2. Advertised context starts at 4096; soak must measure `usable_ram` before raising it.
