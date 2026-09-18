# Wall watts procedure

Path B records **wall** watts idle and load (`power_thermals.wall_watts_idle`, `wall_watts_load`). Tokens/kWh waits until soak tokens and wall energy both exist.

`tegrastats` / INA rails are a **board proxy**. They are useful for throttle and junction, not a substitute for wall watts. Always label `method`.

## Preferred (product)

1. Set `nvpmodel -m 2` (30W). Record `jetson_clocks` on/off.
2. Plug the AGX brick into a logging wall meter (Kill-A-Watt class or a PDU with per-outlet watts).
3. Idle: 5 minutes after login, no llama-bench, no desktop chrome if that is the certified image. Median watts → `wall_watts_idle`.
4. Load: attach the meter for the full hot window (default 600s) plus llama-bench / TTFT client. Median watts during the decode window → `wall_watts_load`.
5. Write `power.json` with `method: wall_meter`, meter model, and the two medians.
6. Do not copy a NVIDIA module TDP or nvpmodel cap into these fields.

## Acceptable proxy (ops only, not a Path B pass)

If no wall meter is on the bench:

- Run `recipes/power/sample-tegrastats.sh`.
- Keep `wall_watts_*` **null**.
- Store the tegrastats log under `harness.raw_outputs`.
- Junction / throttle events may be parsed later from that log.

## Do not

- Invent watts from TOPS, nvpmodel name, or a blog.
- Mix 30W and MAXN in one envelope.
- Treat `tokens_per_kwh` as known before both tokens and wall energy exist.
