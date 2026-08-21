#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PORT="${1:-8080}"

# Use the Python that install.sh provisioned, if available
if [ -f state/.python_bin ]; then
  PYBIN="$(cat state/.python_bin 2>/dev/null | tr -d ' \r\n')"
  if [ -n "$PYBIN" ] && [ -x "$PYBIN" ] 2>/dev/null; then
    exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
  fi
  # PATH-based fallback (uv installs to ~/.local/bin)
  if command -v "$PYBIN" >/dev/null 2>&1; then
    exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
  fi
fi

# Fallback: python with 3.11+ on PATH
for cand in python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    exec "$cand" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
  fi
done

# Last resort: uv-managed python
if command -v uv >/dev/null 2>&1 && uv python find 3.11 >/dev/null 2>&1; then
  PYBIN="$(uv python find 3.11 2>/dev/null)"
  exec "$PYBIN" -m vsphere_auto serve --host 0.0.0.0 --port "$PORT"
fi

echo "ERROR: Python >=3.11 not found. Run bash install.sh first." >&2
exit 1
