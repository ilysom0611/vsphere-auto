#!/usr/bin/env bash
set -e
# ---------------------------------------------------------------------------
# Resolve the directory this script lives in — works for:
#   1) `bash install.sh` / `./install.sh`  (BASH_SOURCE populated)
#   2) `curl … | bash`                     (BASH_SOURCE empty → use $PWD)
#   3) `bash /tmp/install.sh`              (absolute path)
# ---------------------------------------------------------------------------
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  _SRC="${BASH_SOURCE[0]}"
else
  _SRC="$0"
fi
# For `curl | bash`, _SRC is "bash" (no slash) or "-bash" — treat as PWD.
# For any _SRC without a slash, fall back to PWD so we don't cd to /usr/bin.
if [[ "$_SRC" != *"/"* ]]; then
  SCRIPT_DIR="$(pwd)"
else
  SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
fi
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Flags
#   --install-service : also install/refresh the systemd unit (best-effort)
# ---------------------------------------------------------------------------
INSTALL_SERVICE=0
for _arg in "$@"; do
  case "$_arg" in
    --install-service) INSTALL_SERVICE=1 ;;
    *) : ;;  # unknown flags ignored for forward-compat
  esac
done

echo "Installing vsphere-auto..."
# If invoked via `curl | bash` the repo is not present — clone it.
if [ ! -f "pyproject.toml" ]; then
  echo "[install] No pyproject.toml in $SCRIPT_DIR — checking for existing checkout..."
  if [ -f "vsphere-auto/pyproject.toml" ]; then
    echo "[install] Found vsphere-auto/pyproject.toml — cd there"
    cd vsphere-auto
    SCRIPT_DIR="$(pwd)"
  elif command -v git >/dev/null 2>&1; then
    echo "[install] Cloning https://github.com/ilysom0611/vsphere-auto.git ..."
    if [ -d "vsphere-auto" ]; then
      echo "[install] Directory vsphere-auto already exists — updating"
      if ! _pull_out="$(git -C vsphere-auto pull --ff-only 2>&1)"; then
        echo "$_pull_out" | tail -3
        echo "[install] WARNING: 'git pull' failed — proceeding with the EXISTING checkout,"
        echo "[install]          which may be STALE. Update manually: git -C vsphere-auto pull --ff-only"
      else
        echo "$_pull_out" | tail -3
      fi
      cd vsphere-auto
    else
      git clone https://github.com/ilysom0611/vsphere-auto.git vsphere-auto 2>&1 | tail -5 || {
        echo "[install] git clone failed — please run manually:"
        echo "  git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto && bash install.sh"
        exit 1
      }
      cd vsphere-auto
    fi
    SCRIPT_DIR="$(pwd)"
    echo "[install] Repo ready at $SCRIPT_DIR"
  else
    echo "[install] ERROR: Not in repo and git not found."
    echo "  Install git, or run:"
    echo "    git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto && bash install.sh"
    exit 1
  fi
fi

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

# NOTE: We deliberately do NOT bootstrap pip/setuptools/wheel into the SYSTEM
# interpreter (get-pip against system site-packages breaks dpkg/apt-managed
# installs). pip is only ever upgraded inside the venv we create below.

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
    if ! "$PYBIN" -m venv .venv 2>/dev/null; then
      # Missing ensurepip (common on Debian/Ubuntu without python3-venv, or
      # uv-managed pythons) — bootstrap pip INSIDE the venv, never system-wide.
      echo "[install] plain 'python -m venv' failed (ensurepip missing?) — retrying without pip..."
      if "$PYBIN" -m venv --without-pip .venv 2>/dev/null; then
        echo "[install] Bootstrapping pip inside .venv (get-pip targeted at the venv python)..."
        if command -v curl >/dev/null 2>&1; then
          curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python 2>&1 | tail -5 || true
        elif command -v wget >/dev/null 2>&1; then
          wget -qO- https://bootstrap.pypa.io/get-pip.py | .venv/bin/python 2>&1 | tail -5 || true
        else
          .venv/bin/python -m ensurepip --upgrade 2>&1 | tail -5 || true
        fi
      else
        "$PYBIN" -m virtualenv .venv 2>&1 || {
          echo "[install] venv creation failed, will install to user/site instead"
          NEED_VENV=0
        }
      fi
    fi
  fi
  if [ -x ".venv/bin/python" ]; then
    VENV_PY=".venv/bin/python"
    echo "[install] venv python: $VENV_PY ($($VENV_PY --version 2>&1))"
    # Keep venv pip modern (>=19 for PEP 517). Only ever inside the venv.
    VENV_PIP_VER=""
    if "$VENV_PY" -m pip --version >/dev/null 2>&1; then
      VENV_PIP_VER="$("$VENV_PY" -m pip --version 2>/dev/null | sed -n 's/.*pip \([0-9]*\)\..*/\1/p')"
    fi
    if [ -z "$VENV_PIP_VER" ] || [ "$VENV_PIP_VER" -lt 19 ] 2>/dev/null; then
      echo "[install] venv pip too old or missing (pip ${VENV_PIP_VER:-none}) — bootstrapping inside the venv..."
      "$VENV_PY" -m ensurepip --upgrade 2>&1 | tail -3 || true
      if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$VENV_PY" 2>&1 | tail -5 || true
      elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://bootstrap.pypa.io/get-pip.py | "$VENV_PY" 2>&1 | tail -5 || true
      fi
    fi
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
    "$PYBIN" -m pip install -e . 2>&1 || "$PYBIN" -m pip install --no-build-isolation -e . 2>&1
  }
else
  echo "[install] Installing with pip ($INSTALL_PY -m pip)..."
  "$INSTALL_PY" -m pip install -e . 2>&1 || "$INSTALL_PY" -m pip install --no-build-isolation -e . 2>&1 || {
    echo "[install] ERROR: install failed. Try manually:"
    echo "  $INSTALL_PY -m pip install -e . -v"
    exit 1
  }
fi

# Record the effective runtime for start.sh (venv wins when present)
if [ -n "$VENV_PY" ] && [ -x "$VENV_PY" ]; then
  echo "$VENV_PY" > state/.python_bin 2>/dev/null || true
  echo "$VENV_PY" > state/.venv_python 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 3) Optional: install the systemd unit (--install-service, best-effort)
# ---------------------------------------------------------------------------
install_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "[install] systemctl not available — skipping service installation."
    return 0
  fi
  if [ "$(id -u)" != "0" ]; then
    echo "[install] --install-service needs root. Re-run with: sudo bash install.sh --install-service"
    return 0
  fi
  local svc_src="$SCRIPT_DIR/systemd/vsphere-auto.service"
  if [ ! -f "$svc_src" ]; then
    echo "[install] WARNING: $svc_src not found — skipping service installation."
    return 0
  fi
  local esc_dir
  esc_dir="$(printf '%s' "$SCRIPT_DIR" | sed 's/[&/\]/\\&/g')"
  echo "[install] Installing systemd unit -> /etc/systemd/system/vsphere-auto.service"
  sed -e "s#/opt/vsphere-auto#$esc_dir#g" "$svc_src" > /etc/systemd/system/vsphere-auto.service || {
    echo "[install] WARNING: could not write /etc/systemd/system/vsphere-auto.service — skipping."
    return 0
  }
  mkdir -p "$SCRIPT_DIR/state" || true
  # The unit runs as User=vsphereauto by default (hardening) — create it or adjust.
  if ! id vsphereauto >/dev/null 2>&1; then
    echo "[install] NOTE: the unit uses User=vsphereauto which does not exist yet:"
    echo "          sudo useradd -r -s /sbin/nologin -d $SCRIPT_DIR vsphereauto"
    echo "          sudo chown -R vsphereauto:vsphereauto $SCRIPT_DIR"
    echo "          (or edit User=/Group= in the unit to an existing user)"
  fi
  systemctl daemon-reload || true
  echo "[install] Unit installed. Start it with:"
  echo "  sudo systemctl enable --now vsphere-auto"
}

if [ "$INSTALL_SERVICE" = 1 ]; then
  install_service
fi

echo ""
echo "--- Security notes ---"
echo "  * The Web UI binds 127.0.0.1 by default (start.sh honours VSPHERE_HOST)."
echo "    Do NOT expose it remotely without setting VSPHERE_API_TOKEN first and/or"
echo "    putting a reverse proxy with auth/TLS in front of port 8080."
echo "  * Consider firewalld rules for port 8080 if you do expose it:"
echo "    firewall-cmd --add-port=8080/tcp --permanent && firewall-cmd --reload"
echo "----------------------"

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
