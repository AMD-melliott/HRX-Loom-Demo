#!/usr/bin/env python3
"""
HRX Loom capability demo - backend.

Two modes, never blended:

  REPLAY  No loom-serve reachable. Everything on screen is the PUBLISHED
          loom-fun catalog (51 models, generated 2026-11-13), carried in
          data/catalog.json with each number's GitHub Actions run URL.
          Nothing is presented as measured here.

  LIVE    A loom-serve is answering on LOOM_SERVE_URL. The demo drives real
          chat completions against it and reports what IT measured on THIS
          box. Published catalog numbers stay visible for comparison but
          remain labelled as published.

The mode is decided per-tick by probing the server and is always reported in
the payload as `mode`, so the frontend can never show a rehearsal run as if it
were hardware.

Env vars:
  LOOM_SERVE_URL   default http://127.0.0.1:8080   (loom-serve's documented port)
  LOOM_MODEL       model id to exercise; default = first id the server lists
  DEMO_MAX_WORKERS default 8    hard cap on concurrent probe requests
  DEMO_PORT        default 5000
  DEMO_HOST        default 127.0.0.1 (set 0.0.0.0 only on a trusted network)
"""

import json
import os
import pathlib
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT = pathlib.Path(__file__).parent
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"

LOOM_SERVE_URL = os.environ.get("LOOM_SERVE_URL", "http://127.0.0.1:8080").rstrip("/")
LOOM_MODEL = os.environ.get("LOOM_MODEL") or None
MAX_WORKERS = int(os.environ.get("DEMO_MAX_WORKERS", "8"))
DEMO_PORT = int(os.environ.get("DEMO_PORT", "5000"))
# Loopback by default: /api/probe drives real GPU load and has no auth.
DEMO_HOST = os.environ.get("DEMO_HOST", "127.0.0.1")

# Separate connect and read timeouts: a stalled server must free the worker
# rather than pin it for the whole read budget.
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 60.0

app = Flask(__name__, static_folder=None)

# requests' default connection pool is 10; size it to the worker cap so a
# burst does not silently serialise behind the pool.
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=MAX_WORKERS + 4, pool_maxsize=MAX_WORKERS + 4
)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

_lock = threading.Lock()
_state = {
    "probe": None,           # last live probe result, or None
    "probe_running": False,
    "history": [],           # list of completed live probes
}


def load_catalog() -> dict:
    path = DATA / "catalog.json"
    if not path.exists():
        return {"error": "data/catalog.json missing - run scripts/build_catalog.py",
                "models": [], "model_count": 0}
    return json.loads(path.read_text(encoding="utf-8"))


CATALOG = load_catalog()


# --------------------------------------------------------------------------
# live-mode probing
# --------------------------------------------------------------------------

def discover_server():
    """Ask loom-serve what it is hosting.

    loom-serve is documented as an OpenAI-compatible server on :8080, so
    GET /v1/models is the readiness probe. Returns (ok, info_dict).
    """
    try:
        r = _session.get(f"{LOOM_SERVE_URL}/v1/models",
                         timeout=(CONNECT_TIMEOUT, 5.0))
        if r.status_code != 200:
            return False, {"reason": f"/v1/models returned HTTP {r.status_code}"}
        payload = r.json()
        ids = [m.get("id") for m in payload.get("data", []) if m.get("id")]
        return True, {"models": ids}
    except requests.RequestException as e:
        return False, {"reason": type(e).__name__}
    except ValueError:
        return False, {"reason": "/v1/models did not return JSON"}


def one_request(model: str, prompt: str, max_tokens: int):
    """Send one non-streamed completion and measure it.

    Non-streamed, so this measures end-to-end latency and an AVERAGE output
    rate over the whole response. It is deliberately NOT reported as a
    time-to-first-token or a steady-state decode rate: separating those
    needs token-level streaming timestamps, which this demo does not do.
    Keeping the weaker claim honest matters more than a nicer-looking number.
    """
    started = time.perf_counter()
    try:
        r = _session.post(
            f"{LOOM_SERVE_URL}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        elapsed = time.perf_counter() - started
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "elapsed": elapsed}
        body = r.json()
        usage = body.get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        return {
            "ok": True,
            "elapsed": elapsed,
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            # None, not 0, when the server did not report usage - an absent
            # token count must not read as a measured zero rate.
            "output_tok_s": (completion_tokens / elapsed
                             if completion_tokens and elapsed > 0 else None),
        }
    except requests.RequestException as e:
        return {"ok": False, "error": type(e).__name__,
                "elapsed": time.perf_counter() - started}


def run_probe(concurrency: int, prompt_tokens_approx: int, max_tokens: int):
    """Drive a bounded burst of real requests and summarise what came back."""
    ok, info = discover_server()
    if not ok:
        return {"mode": "replay", "reason": info.get("reason")}

    model = LOOM_MODEL or (info["models"][0] if info.get("models") else None)
    if not model:
        return {"mode": "replay", "reason": "server listed no models"}

    concurrency = max(1, min(concurrency, MAX_WORKERS))
    # A crude but honest way to vary prompt length: repeat a filler sentence.
    filler = ("The quick brown fox jumps over the lazy dog. " *
              max(1, prompt_tokens_approx // 10))
    prompt = filler + "\n\nIn one sentence, summarise the text above."

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(
            lambda _: one_request(model, prompt, max_tokens),
            range(concurrency)))
    wall = time.perf_counter() - started

    good = [r for r in results if r.get("ok")]
    failed = len(results) - len(good)
    latencies = sorted(r["elapsed"] for r in good)
    rates = [r["output_tok_s"] for r in good if r["output_tok_s"] is not None]
    total_completion = sum(r.get("completion_tokens") or 0 for r in good)

    def pct(p):
        if not latencies:
            return None
        k = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
        return round(latencies[k], 3)

    return {
        "mode": "live",
        "server": LOOM_SERVE_URL,
        "model": model,
        "concurrency": concurrency,
        "max_workers": MAX_WORKERS,
        "requests": len(results),
        "failed": failed,
        "wall_seconds": round(wall, 3),
        "latency_p50": pct(50),
        "latency_p99": pct(99),
        "mean_output_tok_s": round(statistics.mean(rates), 1) if rates else None,
        "aggregate_output_tok_s": (round(total_completion / wall, 1)
                                   if total_completion and wall > 0 else None),
        "measurement_note": (
            "End-to-end, non-streamed. Average output rate over the whole "
            "response; not a TTFT or steady-state decode figure."
        ),
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get("/api/status")
def api_status():
    ok, info = discover_server()
    return jsonify({
        "mode": "live" if ok else "replay",
        "loom_serve_url": LOOM_SERVE_URL,
        "server_models": info.get("models") if ok else None,
        "reason": None if ok else info.get("reason"),
        "max_workers": MAX_WORKERS,
        "catalog_generated": CATALOG.get("generated"),
        "catalog_models": CATALOG.get("model_count"),
        "last_probe": _state["probe"],
        "probe_running": _state["probe_running"],
    })


@app.get("/api/catalog")
def api_catalog():
    return jsonify(CATALOG)


@app.post("/api/probe")
def api_probe():
    """Run one bounded live measurement burst against a real loom-serve."""
    with _lock:
        if _state["probe_running"]:
            return jsonify({"error": "a probe is already running"}), 409
        _state["probe_running"] = True

    try:
        body = request.get_json(silent=True) or {}
        try:
            concurrency = int(body.get("concurrency", 4))
            prompt_tokens = max(1, min(int(body.get("prompt_tokens", 200)), 32768))
            max_tokens = max(1, min(int(body.get("max_tokens", 128)), 4096))
        except (TypeError, ValueError):
            return jsonify({"error": "concurrency, prompt_tokens and max_tokens "
                                     "must be integers"}), 400
        result = run_probe(
            concurrency=concurrency,
            prompt_tokens_approx=prompt_tokens,
            max_tokens=max_tokens,
        )
        with _lock:
            _state["probe"] = result
            if result.get("mode") == "live":
                _state["history"].append(result)
                _state["history"] = _state["history"][-20:]
        return jsonify(result)
    finally:
        with _lock:
            _state["probe_running"] = False


@app.post("/api/reset")
def api_reset():
    with _lock:
        _state["probe"] = None
        _state["history"] = []
    return jsonify({"ok": True})


@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(FRONTEND, filename)


if __name__ == "__main__":
    ok, info = discover_server()
    print("=" * 68)
    print("  HRX Loom capability demo")
    print("=" * 68)
    print(f"  catalog      : {CATALOG.get('model_count')} models, "
          f"published {CATALOG.get('generated')}")
    print(f"  loom-serve   : {LOOM_SERVE_URL}")
    if ok:
        print(f"  mode         : LIVE - server hosting {info.get('models')}")
    else:
        print(f"  mode         : REPLAY - no server ({info.get('reason')})")
        print("                 All figures shown are PUBLISHED, not measured here.")
    print(f"  open         : http://{'127.0.0.1' if DEMO_HOST in ('0.0.0.0', '::') else DEMO_HOST}:{DEMO_PORT}")
    if DEMO_HOST not in ("127.0.0.1", "localhost", "::1"):
        print(f"  WARNING      : listening on {DEMO_HOST}; /api/probe is reachable from the network")
    print("=" * 68)
    app.run(host=DEMO_HOST, port=DEMO_PORT, debug=False)
