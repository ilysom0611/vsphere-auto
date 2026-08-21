#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "Installing vsphere-auto..."

# ---------------------------------------------------------------------------
# 1) Ensure Python >= 3.11 — auto-provision on CentOS 7 / old distros
# ---------------------------------------------------------------------------
PYBIN=""

# helper: check if a python binary is >= 3.11
check_py311() {
  local bin="$1"
  [ -x "$bin" ] 2>/dev/null || { command -v "$bin" >/dev/null 2>&1 && bin="$(command -v "$bin")"; }
  [ -x "$bin" ] || return 1
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null
}

# try common names first (no side effects)
for cand in python3.11 python3.10 python3 python; do
  if check_py311 "$cand"; then
    PYBIN="$(command -v "$cand" 2>/dev/null || echo "$cand")"
    # re-check: python3 on CentOS 7 is 3.6 -> will fail the check, so loop continues
    if "$PYBIN" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      break
    else
      PYBIN=""
    fi
  fi
done

# Also try uv-managed python if uv is present
if [ -z "$PYBIN" ] && command -v uv >/dev/null 2>&1; then
  if uv python find 3.11 >/dev/null 2>&1; then
    PYBIN="$(uv python find 3.11 2>/dev/null)"
  fi
fi

# Not found -> try to provision
if [ -z "$PYBIN" ]; then
  echo "[install] No Python >=3.11 found (CentOS 7 ships 3.6). Provisioning..."

  # a) Try uv python (works without root, best for CentOS 7)
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

  # b) Try SCL on CentOS/RHEL 7 (needs root)
  if [ -z "$PYBIN" ] && [ -f /etc/redhat-release ] && grep -q "release 7" /etc/redhat-release 2>/dev/null; then
    echo "[install] Trying SCL Python on CentOS 7 (needs root)..."
    if command -v yum >/dev/null 2>&1; then
      yum install -y centos-release-scl epel-release 2>&1 | tail -5 || true
      # rh-python311 not in default SCL; try rh-python38 as hint, but prefer 3.11 from IUS/EPEL if available
      yum install -y python311 2>&1 | tail -5 || yum install -y rh-python311 2>&1 | tail -5 || true
      for cand in /opt/rh/rh-python311/root/usr/bin/python3.11 /usr/bin/python3.11 /usr/bin/python311; do
        if check_py311 "$cand"; then PYBIN="$cand"; break; fi
      done
    fi
  fi

  # c) Try yum/dnf python3.11 on newer RHEL/Rocky
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
  echo "    # via uv (no root needed, recommended):"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "    uv python install 3.11 && uv python find 3.11"
  echo "    # or via SCL/yum (needs root):"
  echo "    yum install -y centos-release-scl && yum install -y rh-python311"
  echo "    scl enable rh-python311 bash"
  echo "  Then re-run: bash install.sh"
  exit 1
fi

echo "[install] Using Python: $PYBIN ($($PYBIN --version 2>&1))"

# Ensure pip is modern (>=19) via get-pip.py on ancient systems
# `pip install --upgrade pip` itself fails on pip 8 (no PEP 517), so use bootstrap
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
  # also ensure setuptools/wheel
  "$PYBIN" -m pip install --upgrade setuptools wheel 2>&1 | tail -5 || true
fi

# Remember the python for start.sh
mkdir -p state
echo "$PYBIN" > state/.python_bin 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2) Install the package
# ---------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  echo "[install] Installing with uv (using $PYBIN)..."
  # uv sync respects the python we just provisioned
  uv sync --python "$PYBIN" 2>&1 || uv pip install -e . --python "$PYBIN" 2>&1 || "$PYBIN" -m pip install -e . 2>&1 || {
    echo "[install] uv install failed, falling back to pip..."
    "$PYBIN" -m pip install -e . 2>&1 || "$PYBIN" setup.py develop 2>&1 || "$PYBIN" -m pip install --no-build-isolation -e . 2>&1
  }
else
  echo "[install] Installing with pip ($PYBIN -m pip)..."
  "$PYBIN" -m pip install -e . 2>&1 || "$PYBIN" -m pip install --no-build-isolation -e . 2>&1 || {
    echo "[install] pip install failed — trying setup.py fallback..."
    "$PYBIN" setup.py develop 2>&1 || {
      echo "[install] ERROR: install failed. Try manually:"
      echo "  $PYBIN -m pip install -e . -v"
      exit 1
    }
  }
fi

echo ""
echo "Done. Python: $PYBIN"
echo "  Run:  $PYBIN -m vsphere_auto --help"
if command -v vsphere-auto >/dev/null 2>&1; then
  echo "  or:   vsphere-auto --help"
fi
echo "  Start Web UI:  bash start.sh  (http://localhost:8080)"
