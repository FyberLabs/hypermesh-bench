import json, urllib.request, hashlib, sys, time, statistics
BASE = "http://127.0.0.1:18080"
def post(path, body, timeout=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
para = ("The Jetson AGX Orin is an embedded computing module designed for robotics, edge inference and autonomous machines. "
        "It combines an Ampere architecture GPU with Arm Cortex-A78AE CPU cores, dedicated deep learning accelerators, and a large pool of unified LPDDR5 memory "
        "shared between the CPU and GPU. Engineers deploying language models on such hardware must balance power budgets, thermal limits, memory bandwidth, "
        "and latency requirements. Quantized weights reduce memory traffic, which is usually the bottleneck during token generation, while prompt processing "
        "is more compute bound and benefits from batching many tokens at once. ")
src = "Summarize the following technical notes and then continue writing a detailed engineering report.\n\n" + "\n".join("Note %d. %s" % (i + 1, para) for i in range(12))
toks = post("/tokenize", {"content": src, "add_special": False})["tokens"]
prompt = post("/detokenize", {"tokens": toks[:511]})["content"]
ptoks = len(post("/tokenize", {"content": prompt, "add_special": False})["tokens"])
body = {"prompt": prompt, "n_predict": 256, "temperature": 0, "cache_prompt": False, "ignore_eos": True, "seed": 42, "stream": False}
out = {"prompt_text": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_tokens_no_bos": ptoks,
       "request_endpoint": "/completion", "request_body_template": {k: v for k, v in body.items() if k != "prompt"}}
w = post("/completion", body)
out["warmup"] = {"timings": w.get("timings"), "tokens_predicted": w.get("tokens_predicted")}
runs = []
N = int(sys.argv[1]) if len(sys.argv) > 1 else 7
for i in range(N):
    t0 = time.time()
    r = post("/completion", body)
    t = r["timings"]
    runs.append({"run": i + 1, "wall_s": round(time.time() - t0, 3), "prompt_n": t.get("prompt_n"), "prompt_ms": t.get("prompt_ms"),
                 "prompt_per_second": t.get("prompt_per_second"), "predicted_n": t.get("predicted_n"), "predicted_ms": t.get("predicted_ms"),
                 "predicted_per_second": t.get("predicted_per_second"), "tokens_predicted": r.get("tokens_predicted"),
                 "stop_type": r.get("stop_type"), "content_sha256": hashlib.sha256(r.get("content", "").encode()).hexdigest(), "raw_timings": t})
    print("run", i + 1, t.get("prompt_n"), t.get("prompt_per_second"), t.get("predicted_n"), t.get("predicted_per_second"), flush=True)
out["runs"] = runs
d = [x["predicted_per_second"] for x in runs]; p = [x["prompt_per_second"] for x in runs]
out["summary"] = {"n_runs": N, "decode_tok_s_median": round(statistics.median(d), 3), "decode_tok_s_min": round(min(d), 3), "decode_tok_s_max": round(max(d), 3),
                  "decode_tok_s_mean": round(statistics.mean(d), 3), "prompt_tok_s_median": round(statistics.median(p), 3), "prompt_tok_s_min": round(min(p), 3),
                  "prompt_tok_s_max": round(max(p), 3), "prompt_n": sorted(set(x["prompt_n"] for x in runs)), "predicted_n": sorted(set(x["predicted_n"] for x in runs)),
                  "outputs_identical": len(set(x["content_sha256"] for x in runs)) == 1}
try:
    out["props"] = post("/props", {}) if False else json.loads(urllib.request.urlopen(BASE + "/props", timeout=30).read())
    out["props"].pop("chat_template", None)
except Exception as e:
    out["props_error"] = str(e)
json.dump(out, open("/tmp/hm-bench/bench_runs.json", "w"), indent=2)
print(json.dumps(out["summary"]))
