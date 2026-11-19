#!/usr/bin/env bash
# Install the HRX Loom SDK into a venv on a Strix Halo box.
#
# CONFIRMED against the HRX Loom quickstart and the loom-skills README:
#   - the package is `hrx-loom` on PyPI, extra `[hf]` for Hugging Face fetching
#   - it needs Python >= 3.12 with venv
#   - Linux x86-64 with glibc >= 2.39 (Ubuntu 24.04+), or Windows 11 Git Bash
#   - loom-skills itself installs into ~/loom/.venv; this script does the same
#     by default so the two agree
#
# Pinned to 0.2.0 by default, matching the loom-fun cards on Hugging Face
# `main` (promoted to 0.2.0 on 2026-11-17). 0.2.0 cannot serve 0.1.0 packages
# and 0.1.0 cannot serve 0.2.0 ones, so keep this in step with the card
# revision 02 fetches. LOOM_VERSION="" installs the latest release.
#
#   ./01_install_loom.sh
#   LOOM_VERSION=0.2.0 LOOM_HOME=~/loom ./01_install_loom.sh

set -euo pipefail

LOOM_HOME="${LOOM_HOME:-$HOME/loom}"
LOOM_VERSION="${LOOM_VERSION-0.2.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> HRX Loom install"
echo "    target venv : $LOOM_HOME/.venv"

# --- preflight -------------------------------------------------------------
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "!! $PYTHON_BIN not found. Loom needs Python 3.12 or newer." >&2
  exit 1
fi

PY_VER=$("$PYTHON_BIN" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PY_OK=$("$PYTHON_BIN" -c 'import sys;print(1 if sys.version_info>=(3,12) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "!! Python $PY_VER is too old; Loom requires >= 3.12." >&2
  echo "   Set PYTHON_BIN to a newer interpreter, e.g. PYTHON_BIN=python3.12" >&2
  exit 1
fi
echo "    python      : $PY_VER ok"

# glibc check - the wheel is built for glibc >= 2.39, and the failure mode
# otherwise is an unhelpful pip resolution error rather than a clear message.
if command -v ldd >/dev/null 2>&1; then
  GLIBC=$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)
  if [ -n "$GLIBC" ]; then
    echo "    glibc       : $GLIBC (wheel needs >= 2.39)"
    if [ "$(printf '%s\n2.39\n' "$GLIBC" | sort -V | head -1)" != "2.39" ]; then
      echo "    !! glibc looks older than 2.39 - the hrx-loom wheel may refuse to install."
      echo "       Ubuntu 24.04 or newer is the documented baseline."
    fi
  fi
fi

# Report the GPU if the ROCm tooling is present. Informational only: Loom talks
# to the driver itself and does not require rocm-smi to be installed.
if command -v rocm-smi >/dev/null 2>&1; then
  echo "    gpu         : $(rocm-smi --showproductname 2>/dev/null | grep -i 'card series' | head -1 | sed 's/^.*: //' || echo 'rocm-smi present')"
else
  echo "    gpu         : rocm-smi not found (informational only)"
fi

# --- install ---------------------------------------------------------------
mkdir -p "$LOOM_HOME"
if [ ! -d "$LOOM_HOME/.venv" ]; then
  echo "==> creating venv"
  "$PYTHON_BIN" -m venv "$LOOM_HOME/.venv"
fi

# shellcheck disable=SC1091
source "$LOOM_HOME/.venv/bin/activate"

PKG="hrx-loom[hf]"
[ -n "$LOOM_VERSION" ] && PKG="hrx-loom[hf]==$LOOM_VERSION"

echo "==> pip install '$PKG'"
pip install --upgrade pip --quiet
pip install "$PKG"

echo
echo "==> installed"
for tool in loom-fetch loom-serve; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "    $tool : $(command -v "$tool")"
  else
    echo "    !! $tool not on PATH after install - check the pip output above"
  fi
done

cat <<EOF

Next:
  source $LOOM_HOME/.venv/bin/activate
  ./02_fetch_and_serve.sh <package>     # e.g. loom-fun/bartowski-Qwen2.5-0.5B-Instruct-loom
EOF
