#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PORT="${1:-8080}"

# 1) venv provisioned by install.sh (preferred — uv cpython needs this)
if [ -x ".venv/bin/python" ]; then
  exec ".venv/bin/python" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
fi

# 2) Interpreter recorded by install.sh (venv path or system python)
if [ -f state/.python_bin ]; then
  PYBIN="$(tr -d ' \r\n' < state/.python_bin 2>/dev/null)"
  if [ -n "$PYBIN" ]; then
    if [ -x "$PYBIN" ] 2>/dev/null; then
      exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
    fi
    if command -v "$PYBIN" >/dev/null 2>&1; then
      exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
    fi
  fi
fi

# 3) Fallback: any python >=3.11 on PATH that can import the package
for cand in python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 \
    && "$cand" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null \
    && "$cand" -c 'import vsphere_auto' 2>/dev/null; then
    exec "$cand" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
  fi
done

# 4) uv-managed python with a venv that already has the package
if command -v uv >/dev/null 2>&1 && uv python find 3.11 >/dev/null 2>&1; then
  PYBIN="$(uv python find 3.11 2>/dev/null)"
  if "$PYBIN" -c 'import vsphere_auto' 2>/dev/null; then
    exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
  fi
fi

echo "ERROR: vsphere_auto not importable. Run bash install.sh first." >&2
echo "  Tried: .venv/bin/python, state/.python_bin, python3.11 on PATH, uv python" >&2
exit 1
