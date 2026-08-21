#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "Installing vsphere-auto..."

# ---------------------------------------------------------------------------
# 1) Ensure Python >= 3.11 — auto-provision on CentOS 7 / old distros
# ---------------------------------------------------------------------------
PYBIN=""

check_py311() {
  local bin="$1"
  [ -x "$bin" ] 2>/dev/null || { command -v "$bin" >/dev/null 2>&1 && bin="$(command -v "$bin")"; }
  [ -x "$bin" ] || return 1
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null
}

for cand in python3.11 python3.10 python3 python; do
  if check_py311 "$cand"; then
    PYBIN="$(command -v "$cand" 2>/dev/null || echo "$cand")"
    if "$PYBIN" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      break
    else
      PYBIN=""
    fi
  fi
done

if [ -z "$PYBIN" ] && command -v uv >/dev/null 2>&1; then
  if uv python find 3.11 >/dev/null 2>&1; then
    PYBIN="$(uv python find 3.11 2>/dev/null)"
  fi
fi

if [ -z "$PYBIN" ]; then
  echo "[install] No Python >=3.11 found (CentOS 7 ships 3.6). Provisioning..."
  if ! command -v uv >/dev/null 2>&1; then
    echo "[install] Installing uv (Python version manager)..."
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 || true
      export PATH="$HOME/.local/bin:$PATH"
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | sh 2>&1 || true
      export PATH="$HOME/.local/bin:$PATH"
    fi
  fi
  if command -v uv >/dev/null 2>&1; then
    echo "[install] Installing Python 3.11 via uv..."
    uv python install 3.11 2>&1 || true
    if uv python find 3.11 >/dev/null 2>&1; then
      PYBIN="$(uv python find 3.11 2>/dev/null)"
      echo "[install] Provisioned: $PYBIN ($($PYBIN --version 2>&1))"
    fi
  fi
  if [ -z "$PYBIN" ] && [ -f /etc/redhat-release ] && grep -q "release 7" /etc/redhat-release 2>/dev/null; then
    echo "[install] Trying SCL Python on CentOS 7 (needs root)..."
    if command -v yum >/dev/null 2>&1; then
      yum install -y centos-release-scl epel-release 2>&1 | tail -5 || true
      yum install -y python311 2>&1 | tail -5 || yum install -y rh-python311 2>&1 | tail -5 || true
      for cand in /opt/rh/rh-python311/root/usr/bin/python3.11 /usr/bin/python3.11 /usr/bin/python311; do
        if check_py311 "$cand"; then PYBIN="$cand"; break; fi
      done
    fi
  fi
  if [ -z "$PYBIN" ]; then
    if command -v dnf >/dev/null 2>&1; then
      dnf install -y python3.11 python3.11-pip 2>&1 | tail -5 || true
      check_py311 python3.11 && PYBIN="$(command -v python3.11)"
    elif command -v yum >/dev/null 2>&1; then
      yum install -y python3.11 2>&1 | tail -5 || true
      check_py311 python3.11 && PYBIN="$(command -v python3.11)"
    elif command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq 2>&1 | tail -3 || true
      apt-get install -y python3.11 python3.11-venv 2>&1 | tail -5 || true
      check_py311 python3.11 && PYBIN="$(command -v python3.11)"
    fi
  fi
fi

if [ -z "$PYBIN" ]; then
  echo ""
  echo "[install] ERROR: Python >=3.11 is required but could not be provisioned."
  echo "  CentOS 7 ships Python 3.6 which is too old. Please install Python 3.11 manually:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "    uv python install 3.11 && uv python find 3.11"
  echo "    yum install -y centos-release-scl && yum install -y rh-python311"
  echo "    scl enable rh-python311 bash"
  echo "  Then re-run: bash install.sh"
  exit 1
fi

echo "[install] Using Python: $PYBIN ($($PYBIN --version 2>&1))"

# Ensure pip is modern (>=19) for the chosen interpreter.
# `pip install --upgrade pip` fails on pip 8 (no PEP 517), so use get-pip.py.
PIP_VER=""
if "$PYBIN" -m pip --version >/dev/null 2>&1; then
  PIP_VER="$("$PYBIN" -m pip --version 2>/dev/null | sed -n 's/.*pip \([0-9]*\)\..*/\1/p')"
fi
if [ -z "$PIP_VER" ] || [ "$PIP_VER" -lt 19 ] 2>/dev/null; then
  echo "[install] pip too old or missing (pip $PIP_VER) — bootstrapping via get-pip.py..."
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PYBIN" 2>&1 | tail -5 || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://bootstrap.pypa.io/get-pip.py | "$PYBIN" 2>&1 | tail -5 || true
  else
    echo "[install] curl/wget not found, trying ensurepip..."
    "$PYBIN" -m ensurepip --upgrade 2>&1 | tail -5 || true
  fi
  "$PYBIN" -m pip install --upgrade setuptools wheel 2>&1 | tail -5 || true
fi

# Persist the interpreter for start.sh. For uv-managed interpreters this is a
# bare cpython binary without an associated environment — we create a venv next
# to it so deps are actually importable.
mkdir -p state
echo "$PYBIN" > state/.python_bin 2>/dev/null || true

# If PYBIN is a uv-managed cpython (isolated tree), create/use .venv so
# `python -m vsphere_auto` can import the installed package. System python
# (e.g. /usr/bin/python3.11) works fine without a venv, but using one is
# still harmless and makes activation consistent.
VENV_PY=""
if echo "$PYBIN" | grep -qE "uv/python|cpython-.*-linux" 2>/dev/null; then
  NEED_VENV=1
else
  # Even for system python, a local venv keeps the install isolated and avoids
  # needing root / --user. Prefer it when we can create it.
  NEED_VENV=1
fi

if [ "$NEED_VENV" = 1 ]; then
  if [ ! -x ".venv/bin/python" ]; then
    echo "[install] Creating virtualenv .venv with $PYBIN..."
    "$PYBIN" -m venv .venv 2>&1 || "$PYBIN" -m virtualenv .venv 2>&1 || {
      echo "[install] venv creation failed, will install to user/site instead"
      NEED_VENV=0
    }
  fi
  if [ -x ".venv/bin/python" ]; then
    VENV_PY=".venv/bin/python"
    echo "[install] venv python: $VENV_PY ($($VENV_PY --version 2>&1))"
    # Keep venv pip modern too
    "$VENV_PY" -m pip install --upgrade pip setuptools wheel 2>&1 | tail -3 || true
  fi
fi

# ---------------------------------------------------------------------------
# 2) Install the package — always into the venv when we have one
# ---------------------------------------------------------------------------
INSTALL_PY="${VENV_PY:-$PYBIN}"

if [ -n "$VENV_PY" ]; then
  echo "[install] Installing into .venv ($INSTALL_PY -m pip install -e .)..."
  "$INSTALL_PY" -m pip install -e . 2>&1 || "$INSTALL_PY" -m pip install --no-build-isolation -e . 2>&1 || {
    echo "[install] ERROR: install into .venv failed. Try manually:"
    echo "  $INSTALL_PY -m pip install -e . -v"
    exit 1
  }
elif command -v uv >/dev/null 2>&1; then
  echo "[install] Installing with uv (using $PYBIN)..."
  uv sync --python "$PYBIN" 2>&1 || uv pip install -e . --python "$PYBIN" 2>&1 || "$PYBIN" -m pip install -e . 2>&1 || {
    echo "[install] uv install failed, falling back to pip..."
    "$PYBIN" -m pip install -e . 2>&1 || "$PYBIN" setup.py develop 2>&1 || "$PYBIN" -m pip install --no-build-isolation -e . 2>&1
  }
else
  echo "[install] Installing with pip ($INSTALL_PY -m pip)..."
  "$INSTALL_PY" -m pip install -e . 2>&1 || "$INSTALL_PY" -m pip install --no-build-isolation -e . 2>&1 || {
    echo "[install] pip install failed — trying setup.py fallback..."
    "$INSTALL_PY" setup.py develop 2>&1 || {
      echo "[install] ERROR: install failed. Try manually:"
      echo "  $INSTALL_PY -m pip install -e . -v"
      exit 1
    }
  }
fi

# Record the effective runtime for start.sh (venv wins when present)
if [ -n "$VENV_PY" ] && [ -x "$VENV_PY" ]; then
  echo "$VENV_PY" > state/.python_bin 2>/dev/null || true
  echo "$VENV_PY" > state/.venv_python 2>/dev/null || true
fi

echo ""
echo "Done. Python: $INSTALL_PY ($($INSTALL_PY --version 2>&1))"
# Verify import
if "$INSTALL_PY" -c "import vsphere_auto" 2>/dev/null; then
  echo "  Verify: $INSTALL_PY -m vsphere_auto --help  (ok)"
else
  echo "  WARNING: import vsphere_auto failed — check pip output above"
fi
if command -v vsphere-auto >/dev/null 2>&1; then
  echo "  or:   vsphere-auto --help"
fi
if [ -n "$VENV_PY" ]; then
  echo "  (venv) Activate with: source .venv/bin/activate"
fi
echo "  Start Web UI:  bash start.sh  (http://localhost:8080)"
