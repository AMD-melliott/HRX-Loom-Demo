#!/usr/bin/env bash
# Fetch a Loom model package and start loom-serve.
#
# Verified on Strix Halo with hrx-loom 0.1.0 (2026-11-13):
#   loom-fetch --output=<dir> https://huggingface.co/<org>/<pkg>/resolve/<rev>/model.json
#   loom-serve --model=<dir>/model.json --device=amdgpu [--listen=HOST:PORT]
#   -> {"event":"ready","address":"127.0.0.1:8080","models":1}
#   -> OpenAI-style chat API: GET /v1/models, POST /v1/chat/completions
#
# Notes for 0.1.0:
#   * loom-fetch needs --output, and the card must be a local model.json or an
#     HTTP(S) URL. The `huggingface:<org>/<pkg>` form is rejected.
#   * --listen picks the address (default 127.0.0.1:8080). Set LISTEN to use
#     another port, e.g. on a box where 8080 is taken.
#   * --memory_bytes is an optional elastic cap; leaving it out means no limit.
#     The cards' quick starts size it for the full context, which reads as a
#     requirement but is not one.
#
#   ./02_fetch_and_serve.sh loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom
#   LISTEN=127.0.0.1:8091 MEMORY_BYTES=5000000000 ./02_fetch_and_serve.sh <pkg>
#   REVISION=<commit> ./02_fetch_and_serve.sh <pkg>     # pin the card revision
#   EXTRA_SERVE_ARGS="--foo=bar" ./02_fetch_and_serve.sh <pkg>

set -euo pipefail

PKG="${1:-loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom}"
REVISION="${REVISION:-main}"
WORKDIR="${WORKDIR:-$PWD/loom-models}"
DEVICE="${DEVICE:-amdgpu}"
LISTEN="${LISTEN:-}"
MEMORY_BYTES="${MEMORY_BYTES:-}"
EXTRA_SERVE_ARGS="${EXTRA_SERVE_ARGS:-}"

for tool in loom-fetch loom-serve; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "!! $tool not on PATH. Activate the venv first:" >&2
    echo "   source \${LOOM_HOME:-\$HOME/loom}/.venv/bin/activate" >&2
    exit 1
  }
done

CARD_URL="https://huggingface.co/$PKG/resolve/$REVISION/model.json"
OUT_DIR="$WORKDIR/${PKG##*/}"
mkdir -p "$WORKDIR"

echo "==> loom-fetch $CARD_URL"
echo "    into $OUT_DIR"
echo "    (pulls the package plus the upstream GGUF from its original repo,"
echo "     verifying sha256 - the GGUF is not re-hosted by loom-fun)"
echo

loom-fetch --no-input --output="$OUT_DIR" "$CARD_URL"

MODEL_JSON="$OUT_DIR/model.json"
if [ ! -f "$MODEL_JSON" ]; then
  echo "!! loom-fetch finished but $MODEL_JSON is missing" >&2
  exit 1
fi

echo
echo "==> model package : $MODEL_JSON"
# The package has no `serve` block; the model id is what requests must carry.
python3 -I - "$MODEL_JSON" <<'PY' || true
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
m = d.get("model") or {}
print(f"    model id        : {m.get('id', '?')}  (send this as \"model\" in requests)")
PY

SERVE_ARGS=(--model="$MODEL_JSON" --device="$DEVICE")
[ -n "$LISTEN" ] && SERVE_ARGS+=(--listen="$LISTEN")
[ -n "$MEMORY_BYTES" ] && SERVE_ARGS+=(--memory_bytes="$MEMORY_BYTES")

echo
echo "==> loom-serve ${SERVE_ARGS[*]} $EXTRA_SERVE_ARGS"
echo "    expect: {\"event\":\"ready\",\"address\":\"${LISTEN:-127.0.0.1:8080}\",\"models\":1}"
echo "    leave this running; drive it from 03_measure.sh in another terminal."
echo

# shellcheck disable=SC2086
exec loom-serve "${SERVE_ARGS[@]}" $EXTRA_SERVE_ARGS
