#!/usr/bin/env bash
set -e
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  _SRC="${BASH_SOURCE[0]}"
else
  _SRC="$0"
fi
if [[ "$_SRC" != *"/"* ]]; then
  SCRIPT_DIR="$(pwd)"
else
  SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
fi
cd "$SCRIPT_DIR"

# Share one state dir between manual starts and systemd unless overridden.
export VSPHERE_STATE_DIR="${VSPHERE_STATE_DIR:-${SCRIPT_DIR}/state}"
mkdir -p "$VSPHERE_STATE_DIR" 2>/dev/null || true

# Usage: bash start.sh [port] [--debug]
#   VSPHERE_DEBUG=1 bash start.sh   # env-var alias
#   LOG_LEVEL=DEBUG bash start.sh   # log level override
#   VSPHERE_STATE_DIR=/data/vsphere bash start.sh  # custom state dir
# Note: repo root has a separate install.sh for doc-translator (Node) — not this one.
PORT="8080"
DEBUG_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --debug) DEBUG_ARGS+=(--debug) ;;
    --help|-h) DEBUG_ARGS+=("$arg") ;;
    *) # first non-flag is the port
      if [ "$PORT" = "8080" ] && [[ "$arg" =~ ^[0-9]+$ ]]; then
        PORT="$arg"
      else
        DEBUG_ARGS+=("$arg")
      fi
      ;;
  esac
done

# Support VSPHERE_HOST for the bind address (default 0.0.0.0 — reachable from
# other hosts; set VSPHERE_HOST=127.0.0.1 to restrict to loopback).
HOST="${VSPHERE_HOST:-0.0.0.0}"

# Resolve the interpreter (same order as before)
PYBIN_RESOLVED=""
if [ -x ".venv/bin/python" ]; then
  PYBIN_RESOLVED=".venv/bin/python"
elif [ -f state/.python_bin ]; then
  _p="$(tr -d ' \r\n' < state/.python_bin 2>/dev/null)"
  if [ -n "$_p" ]; then
    if [ -x "$_p" ] 2>/dev/null; then PYBIN_RESOLVED="$_p"
    elif command -v "$_p" >/dev/null 2>&1; then PYBIN_RESOLVED="$_p"
    fi
  fi
fi
if [ -z "$PYBIN_RESOLVED" ]; then
  for cand in python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 \
      && "$cand" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null \
      && "$cand" -c 'import vsphere_auto' 2>/dev/null; then
      PYBIN_RESOLVED="$cand"; break
    fi
  done
fi
if [ -z "$PYBIN_RESOLVED" ] && command -v uv >/dev/null 2>&1 && uv python find 3.11 >/dev/null 2>&1; then
  _p="$(uv python find 3.11 2>/dev/null)"
  if [ -n "$_p" ] && "$_p" -c 'import vsphere_auto' 2>/dev/null; then
    PYBIN_RESOLVED="$_p"
  fi
fi

if [ -z "$PYBIN_RESOLVED" ]; then
  echo "ERROR: vsphere_auto not importable. Run bash install.sh first." >&2
  echo "  Tried: .venv/bin/python, state/.python_bin, python3.11 on PATH, uv python" >&2
  exit 1
fi

exec "$PYBIN_RESOLVED" -m vsphere_auto serve --host "$HOST" --port "$PORT" "${DEBUG_ARGS[@]}"
