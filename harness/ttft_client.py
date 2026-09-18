#!/usr/bin/env python3
"""Measure TTFT / decode after a hot window against llama-server.

Uses the OpenAI-compatible /v1/chat/completions stream when the server
is up. If the endpoint is unreachable, writes ttft_hot.json with null
metrics. Never invents tok/s or milliseconds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_PROMPT = (
    "Continue with a short, deterministic listing of integers starting at 1."
)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _unavailable(reason: str, endpoint: str, hot_window_s: int) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "endpoint": endpoint,
        "hot_window_s": hot_window_s,
        "n_samples": 0,
        "ttft_ms_p50": None,
        "ttft_ms_p95": None,
        "decode_tok_s_p50": None,
        "decode_tok_s_p95": None,
        "samples": [],
    }


def _not_run(reason: str, endpoint: str, hot_window_s: int) -> dict[str, Any]:
    body = _unavailable(reason, endpoint, hot_window_s)
    body["status"] = "not_run"
    return body


def _sse_content(line: bytes) -> str | None:
    text = line.decode("utf-8", errors="replace").strip()
    if not text or text.startswith(":"):
        return None
    if text.startswith("data:"):
        text = text[5:].strip()
    if text == "[DONE]":
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    delta = choices[0].get("delta") or {}
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    message = choices[0].get("message") or {}
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(choices[0].get("text"), str):
        return choices[0]["text"]
    return None


def measure_one(
    endpoint: str,
    *,
    prompt: str,
    max_tokens: int,
    timeout_s: float,
) -> dict[str, Any] | None:
    body = json.dumps(
        {
            "model": "local",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    first: float | None = None
    chunks = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            for raw in resp:
                content = _sse_content(raw)
                if not content:
                    continue
                now = time.perf_counter()
                if first is None:
                    first = now
                chunks += 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    except Exception:  # noqa: BLE001 — treat any parse/transport miss as unmeasured
        return None
    if first is None:
        return None
    ended = time.perf_counter()
    ttft_ms = (first - t0) * 1000.0
    decode: float | None = None
    duration = ended - first
    if chunks > 1 and duration > 0:
        decode = (chunks - 1) / duration
    return {"ttft_ms": ttft_ms, "decode_tok_s": decode, "chunks": chunks}


def probe_endpoint(endpoint: str, timeout_s: float = 3.0) -> bool:
    """True only if the server answers. No invented metrics."""
    base = endpoint
    for suffix in ("/health", "/v1/models", ""):
        url = endpoint
        if suffix:
            if endpoint.rstrip("/").endswith("/v1/chat/completions"):
                url = endpoint[: -len("/v1/chat/completions")] + suffix
            else:
                url = endpoint.rstrip("/") + suffix
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        _ = base
    return False


def run_ttft(
    *,
    out_path: Path,
    endpoint: str = DEFAULT_ENDPOINT,
    hot_window_s: int = 0,
    reps: int = 5,
    max_tokens: int = 128,
    prompt: str = DEFAULT_PROMPT,
    stub: bool = False,
    skip: bool = False,
    timeout_s: float = 60.0,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    if stub:
        payload = _not_run("stub; TTFT client not invoked", endpoint, hot_window_s)
        _write(out_path, payload)
        return payload
    if skip:
        payload = _not_run("hot window / TTFT skipped", endpoint, hot_window_s)
        _write(out_path, payload)
        return payload

    if hot_window_s > 0:
        sleep_fn(hot_window_s)

    if not probe_endpoint(endpoint):
        # Still try one stream; probe miss is not a number source.
        sample = measure_one(
            endpoint, prompt=prompt, max_tokens=max_tokens, timeout_s=timeout_s
        )
        if sample is None:
            payload = _unavailable(
                "llama-server / completions endpoint unavailable",
                endpoint,
                hot_window_s,
            )
            _write(out_path, payload)
            return payload
        samples = [sample]
    else:
        samples = []
        for _ in range(max(1, reps)):
            sample = measure_one(
                endpoint, prompt=prompt, max_tokens=max_tokens, timeout_s=timeout_s
            )
            if sample is None:
                break
            samples.append(sample)

    if not samples:
        payload = _unavailable(
            "completions reachable check failed or returned no tokens",
            endpoint,
            hot_window_s,
        )
        _write(out_path, payload)
        return payload

    ttfts = [s["ttft_ms"] for s in samples if isinstance(s.get("ttft_ms"), (int, float))]
    decodes = [
        s["decode_tok_s"]
        for s in samples
        if isinstance(s.get("decode_tok_s"), (int, float))
    ]
    payload = {
        "status": "ok",
        "reason": None,
        "endpoint": endpoint,
        "hot_window_s": hot_window_s,
        "n_samples": len(samples),
        "ttft_ms_p50": percentile(ttfts, 50) if ttfts else None,
        "ttft_ms_p95": percentile(ttfts, 95) if ttfts else None,
        "decode_tok_s_p50": percentile(decodes, 50) if decodes else None,
        "decode_tok_s_p95": percentile(decodes, 95) if decodes else None,
        "samples": [
            {
                "ttft_ms": s.get("ttft_ms"),
                "decode_tok_s": s.get("decode_tok_s"),
                "chunks": s.get("chunks"),
            }
            for s in samples
        ],
    }
    _write(out_path, payload)
    return payload


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_ttft(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("HM_COMPLETIONS_URL") or DEFAULT_ENDPOINT,
    )
    parser.add_argument("--hot-window-s", type=int, default=0)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--skip", action="store_true")
    args = parser.parse_args(argv)
    run_ttft(
        out_path=args.out,
        endpoint=args.endpoint,
        hot_window_s=args.hot_window_s,
        reps=args.reps,
        max_tokens=args.max_tokens,
        stub=args.stub,
        skip=args.skip,
    )
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
