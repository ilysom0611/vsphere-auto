#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "Installing vsphere-auto..."
if command -v uv >/dev/null 2>&1; then
  uv sync || uv pip install -e .
else
  pip install -e .
fi
mkdir -p state
echo "Done. Run: vsphere-auto --help  or  ./start.sh"
