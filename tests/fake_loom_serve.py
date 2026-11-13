#!/usr/bin/env python3
"""
A protocol-accurate stand-in for `loom-serve`, so the demo's LIVE path can be
exercised without a Strix Halo box.

It imitates only what the demo actually depends on:
  GET  /v1/models            -> {"data":[{"id": ...}]}
  POST /v1/chat/completions  -> OpenAI-shaped reply with a `usage` block

This is NOT a Loom implementation and produces no real inference. It exists to
prove the demo's wiring, mode detection and error handling are correct before
the code ever meets a GPU.

  python3 tests/fake_loom_serve.py --port 8080
  python3 tests/fake_loom_serve.py --port 8080 --no-usage   # omit usage block
  python3 tests/fake_loom_serve.py --port 8080 --fail-rate 0.5

`--no-usage` is the important one: a server that does not report token counts
must make the demo show "—", never a measured-looking 0 tok/s.
"""

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # keep the test output readable

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list",
                             "data": [{"id": MODEL_ID, "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            self._send(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return

        if ARGS.fail_rate and random.random() < ARGS.fail_rate:
            self._send(503, {"error": "simulated overload"})
            return

        max_tokens = int(req.get("max_tokens") or 64)
        prompt_chars = sum(len(m.get("content", "")) for m in req.get("messages", []))

        # Rough, deliberately unexciting timing so nobody mistakes stub output
        # for a benchmark: a prefill term plus a per-token decode term.
        prefill = prompt_chars / 90000.0
        decode = max_tokens / ARGS.tok_per_s
        time.sleep(prefill + decode)

        payload = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "model": req.get("model") or MODEL_ID,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant",
                            "content": "This is stub output from fake_loom_serve."},
                "finish_reason": "stop",
            }],
        }
        if not ARGS.no_usage:
            payload["usage"] = {
                "prompt_tokens": max(1, prompt_chars // 4),
                "completion_tokens": max_tokens,
                "total_tokens": max(1, prompt_chars // 4) + max_tokens,
            }
        self._send(200, payload)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--tok-per-s", type=float, default=140.0)
    ap.add_argument("--fail-rate", type=float, default=0.0)
    ap.add_argument("--no-usage", action="store_true",
                    help="omit the usage block, so token counts are unavailable")
    ARGS = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(json.dumps({"event": "ready",
                      "address": f"127.0.0.1:{ARGS.port}",
                      "models": 1,
                      "note": "FAKE loom-serve - no real inference"}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
