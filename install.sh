#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "Installing vsphere-auto..."

# On ancient pip (CentOS 7: pip 8.x) PEP 517 / pyproject.toml is not supported.
# Try to upgrade pip/setuptools/wheel first — but don't fail if offline.
have_old_pip=0
if command -v pip >/dev/null 2>&1; then
  _pip_ver="$(pip --version 2>/dev/null | sed -n 's/.*pip \([0-9]*\)\..*/\1/p')"
  if [ -n "$_pip_ver" ] && [ "$_pip_ver" -lt 19 ] 2>/dev/null; then
    have_old_pip=1
  fi
fi
if [ "$have_old_pip" = 1 ]; then
  echo "[install] Old pip detected (<19) — attempting to upgrade pip/setuptools/wheel..."
  pip install --upgrade pip setuptools wheel 2>&1 || echo "[install] pip upgrade failed (offline?), falling back to setup.py shim..."
fi

# Install with the best available tool. uv preferred; pip last.
if command -v uv >/dev/null 2>&1; then
  echo "[install] Using uv..."
  uv sync 2>&1 || uv pip install -e . 2>&1 || pip install -e .
else
  # Try standards-compliant pip first, then setup.py shim (setup.py/setup.cfg handle pip 8.x)
  pip install -e . 2>&1 || python3 -m pip install -e . 2>&1 || {
    echo "[install] pip install failed — trying python setup.py develop as last resort..."
    python3 setup.py develop 2>&1 || python setup.py develop
  }
fi

mkdir -p state
echo "Done. Run: vsphere-auto --help  or  ./start.sh"
echo "If 'vsphere-auto' is not found, try: python3 -m vsphere_auto --help"
echo "CentOS 7 note: system Python may be 2.7/3.6. Use SCL (rh-python38+) or a manual Python 3.11+ install:"
echo "  yum install centos-release-scl && yum install rh-python38 && scl enable rh-python38 bash"
echo "  # or install Python 3.11 from source / deadsnakes / uv-managed python"
