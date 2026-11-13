#!/usr/bin/env bash
# Sanity-check a running loom-serve and take a small end-to-end measurement.
#
# This is deliberately NOT a benchmark. The real benchmark is loom-skills'
# own `loom-benchmark` skill, which takes a llama.cpp ROCm baseline and runs
# the validation gates. This script only answers: "is the server up, does it
# answer, and roughly how fast on this box?" - enough to drive the demo's
# live mode and to catch an obviously broken serve.
#
#   ./03_measure.sh
#   LOOM_SERVE_URL=http://127.0.0.1:8080 REQUESTS=8 MAX_TOKENS=128 ./03_measure.sh

set -euo pipefail

LOOM_SERVE_URL="${LOOM_SERVE_URL:-http://127.0.0.1:8080}"
REQUESTS="${REQUESTS:-5}"
MAX_TOKENS="${MAX_TOKENS:-64}"
OUT="${OUT:-$PWD/measurement.json}"

echo "==> probing $LOOM_SERVE_URL"

if ! curl -fsS "$LOOM_SERVE_URL/v1/models" >/dev/null 2>&1; then
  cat >&2 <<EOF
!! No loom-serve answering on $LOOM_SERVE_URL/v1/models

   Start one first:
     ./02_fetch_and_serve.sh <package>

   If it IS running, check the address it printed in its ready event -
   loom-serve binds 127.0.0.1 by default, so it is not reachable from
   another machine without changing that.
EOF
  exit 1
fi

MODEL=$(curl -fsS "$LOOM_SERVE_URL/v1/models" \
  | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print((d.get("data") or [{}])[0].get("id",""))')

if [ -z "$MODEL" ]; then
  echo "!! server is up but lists no models" >&2
  exit 1
fi

echo "    model: $MODEL"
echo "==> sending $REQUESTS sequential requests (max_tokens=$MAX_TOKENS)"
echo

python3 -I - "$LOOM_SERVE_URL" "$MODEL" "$REQUESTS" "$MAX_TOKENS" "$OUT" <<'PY'
import json, statistics, sys, time, urllib.request

url, model, n, max_tokens, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]

prompt = ("The quick brown fox jumps over the lazy dog. " * 20 +
          "\n\nIn one sentence, summarise the text above.")

records = []
for i in range(n):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.load(r)
        dt = time.perf_counter() - t0
        usage = payload.get("usage") or {}
        ct = usage.get("completion_tokens")
        # Absent usage stays None. A server that does not report token counts
        # must not be recorded as having produced 0 tokens per second.
        records.append({"ok": True, "elapsed": dt, "completion_tokens": ct,
                        "tok_s": (ct / dt) if ct and dt > 0 else None})
        rate = f"{ct/dt:7.1f} tok/s" if ct and dt > 0 else "   (no usage reported)"
        print(f"    [{i+1}/{n}] {dt:6.3f}s {rate}")
    except Exception as e:
        records.append({"ok": False, "error": type(e).__name__})
        print(f"    [{i+1}/{n}] FAILED: {type(e).__name__}")

good = [r for r in records if r.get("ok")]
lat = sorted(r["elapsed"] for r in good)
rates = [r["tok_s"] for r in good if r["tok_s"] is not None]

summary = {
    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "server": url,
    "model": model,
    "requests": n,
    "succeeded": len(good),
    "failed": n - len(good),
    "latency_p50": round(statistics.median(lat), 3) if lat else None,
    "latency_min": round(min(lat), 3) if lat else None,
    "latency_max": round(max(lat), 3) if lat else None,
    "mean_tok_s": round(statistics.mean(rates), 1) if rates else None,
    "note": ("End-to-end, non-streamed, sequential. Average output rate over the "
             "whole response - NOT a TTFT or steady-state decode figure, and NOT "
             "comparable to the published prefill/decode catalog numbers."),
}

print()
print("==> summary")
for k in ("succeeded", "failed", "latency_p50", "latency_min", "latency_max", "mean_tok_s"):
    print(f"    {k:<14}: {summary[k] if summary[k] is not None else '—'}")

with open(out, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=1)
print(f"\n    wrote {out}")
PY

cat <<EOF

For a real comparison against llama.cpp ROCm, use loom-skills rather than this
script:

  > /loom Benchmark this port against llama.cpp and write the report.

To drive the demo UI against this server:

  LOOM_SERVE_URL=$LOOM_SERVE_URL python3 ../app.py
EOF
