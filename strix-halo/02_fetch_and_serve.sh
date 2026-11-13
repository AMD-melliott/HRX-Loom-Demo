#!/usr/bin/env bash
# Fetch a Loom model package and start loom-serve.
#
# CONFIRMED against the HRX Loom quickstart:
#   loom-fetch huggingface:loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom
#   loom-serve --model=<dir-or-model.json> --device=amdgpu
#   -> {"event":"ready","address":"127.0.0.1:8080","models":1}
#   -> OpenAI-compatible: POST /v1/chat/completions
#
# NOT CONFIRMED - check `loom-serve --help` on your box before relying on these:
#   * the flag that overrides the listen address/port. The server is documented
#     as binding 127.0.0.1:8080 and this script does NOT pass a port flag,
#     because guessing one that does not exist would fail the launch outright.
#     If you need a different port, find the real flag first.
#   * --memory_bytes as a *command-line* flag. memory_bytes is a real field in
#     each package's `serve` block (it is in the published catalog), but whether
#     loom-serve accepts it as a CLI override is unverified. Pass it via
#     EXTRA_SERVE_ARGS only after confirming with --help.
#
#   ./02_fetch_and_serve.sh loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom
#   EXTRA_SERVE_ARGS="--foo=bar" ./02_fetch_and_serve.sh <pkg>

set -euo pipefail

PKG="${1:-loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom}"
WORKDIR="${WORKDIR:-$PWD/loom-models}"
DEVICE="${DEVICE:-amdgpu}"
EXTRA_SERVE_ARGS="${EXTRA_SERVE_ARGS:-}"

for tool in loom-fetch loom-serve; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "!! $tool not on PATH. Activate the venv first:" >&2
    echo "   source \${LOOM_HOME:-\$HOME/loom}/.venv/bin/activate" >&2
    exit 1
  }
done

mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "==> loom-fetch huggingface:$PKG"
echo "    (pulls the package plus the upstream GGUF from its original repo,"
echo "     verifying sha256 - the GGUF is not re-hosted by loom-fun)"
echo

# loom-fetch prints the path of the model.json it wrote. Capture it so we do
# not have to guess the directory layout.
FETCH_LOG="$WORKDIR/.loom-fetch.log"
loom-fetch "huggingface:$PKG" 2>&1 | tee "$FETCH_LOG"

MODEL_JSON=$(grep -oE '[^[:space:]]+model\.json' "$FETCH_LOG" | tail -1 || true)
if [ -z "$MODEL_JSON" ]; then
  # Fall back to a filesystem search rather than inventing a path convention.
  MODEL_JSON=$(find "$WORKDIR" -name model.json -newer "$FETCH_LOG" 2>/dev/null | head -1 || true)
fi
if [ -z "$MODEL_JSON" ]; then
  MODEL_JSON=$(find "$WORKDIR" -name model.json 2>/dev/null | head -1 || true)
fi
if [ -z "$MODEL_JSON" ]; then
  echo "!! could not locate the fetched model.json under $WORKDIR" >&2
  echo "   Check $FETCH_LOG for the path loom-fetch reported." >&2
  exit 1
fi

echo
echo "==> model package : $MODEL_JSON"

# Show the serve block, which is what you actually tune per box.
if command -v python3 >/dev/null 2>&1; then
  python3 -I - "$MODEL_JSON" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    sys.exit(0)
serve = d.get("serve") or {}
if serve:
    print("    serve block from the package:")
    for k in ("device", "memory_bytes", "context_tokens", "rows", "max_tokens"):
        if k in serve:
            v = serve[k]
            extra = f"  ({v/1e9:.1f} GB)" if k == "memory_bytes" and isinstance(v, int) else ""
            print(f"      {k:<16}= {v}{extra}")
PY
fi

echo
echo "==> loom-serve --model=$MODEL_JSON --device=$DEVICE $EXTRA_SERVE_ARGS"
echo "    expect: {\"event\":\"ready\",\"address\":\"127.0.0.1:8080\",\"models\":1}"
echo "    leave this running; drive it from 03_measure.sh in another terminal."
echo

# shellcheck disable=SC2086
exec loom-serve --model="$MODEL_JSON" --device="$DEVICE" $EXTRA_SERVE_ARGS
