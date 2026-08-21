# vSphere Auto — Batch VM Deployment

Automated, **Linux-first** batch VM deployment for **vSphere (vCenter / ESXi)**. Supports template clone and ISO-based provisioning, auto resource selection, concurrent multi-VM batches, idempotent/robust execution, **Web UI + CLI** sharing the same core, and Fernet-encrypted credential storage.

> Runtime: Linux · Python 3.11+ · vCenter 7.0 / 8.0 or direct ESXi

---

## Prerequisites

- **OS:** Linux (Ubuntu 20.04+ / RHEL 8+ / CentOS 7 / any systemd host). macOS works for development.
- **Python:** 3.11 or newer. `install.sh` auto-provisions Python 3.11 on CentOS 7 via `uv` (no root) or SCL/yum — just run `bash install.sh`.
  Manual install if needed: `sudo apt install python3.11 python3.11-venv python3-pip` (Debian/Ubuntu) or `sudo dnf install python3.11` (RHEL/Rocky).
- **Network:** Reachability to vCenter/ESXi on port 443.
- **vSphere access:** An account with permissions to create/clone VMs, read datastores/networks/folders, and run guest customization (e.g. `Administrator@vsphere.local` or a custom role).
- **Optional:** `uv` (faster installs; `pip` works fine without it). Install via `curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## Dependencies

All Python dependencies are declared in `pyproject.toml` and installed automatically by `install.sh`. No manual `pip install` is needed.

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | >=3.0 | Web UI and REST API |
| `pyvmomi` | >=7.0 | vSphere SOAP API (clone, create VM, customization, discovery) |
| `pyyaml` | >=6.0 | YAML config parsing |
| `pydantic` | >=2.0 | Config validation |
| `typer` | >=0.12 | CLI |
| `rich` | >=13.0 | CLI tables and progress |
| `cryptography` | >=42.0 | Fernet encryption for stored passwords |
| `tenacity` | >=8.0 | Retry with backoff for vSphere connections |
| `requests` | >=2.31 | vSphere REST helpers (content library, etc.) |
| `jinja2` | >=3.1 | Template rendering (cloud-init, etc.) |

Build backend: `hatchling`.

---

## One-Click Download & Start

### Option A: git (recommended)

```bash
git clone https://github.com/ilysom0611/vsphere-auto.git
cd vsphere-auto
bash install.sh
bash start.sh
# Open http://localhost:8080
```

### Option B: curl (no git required)

```bash
curl -fsSL https://raw.githubusercontent.com/ilysom0611/vsphere-auto/main/install.sh | bash
bash start.sh
```

### Option C: uv users

```bash
git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto
uv sync && uv run vsphere-auto --help
uv run vsphere-auto serve --port 8080
```

### One-Click Install Commands (with dependency install)

If your host is fresh and you want everything in one go — system deps + Python + app:

**Debian / Ubuntu (apt):**

```bash
# 1) System packages
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git curl

# 2) App
git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto
bash install.sh

# 3) Verify
vsphere-auto --help
# or: python3 -m vsphere_auto --help
# or: uv run vsphere-auto --help  (if uv is installed)

# 4) Start Web UI
bash start.sh  # http://localhost:8080
```

**RHEL / Rocky 8+ (dnf):**

```bash
sudo dnf install -y python3.11 python3-pip git curl
git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto
bash install.sh && bash start.sh
```

**CentOS 7:**

```bash
# install.sh auto-installs Python 3.11 via uv (no root needed).
# Network access to pypi.org is required; offline hosts need Python 3.11 preinstalled.
git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto
bash install.sh          # pulls Python 3.11, upgrades pip, installs deps
bash start.sh            # uses the provisioned interpreter automatically
# If auto-provision fails (offline/no curl):
#   curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"
#   uv python install 3.11; bash install.sh
#   # or: yum install centos-release-scl && yum install rh-python311 && scl enable rh-python311 bash
```

**Using uv (any distro):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
git clone https://github.com/ilysom0611/vsphere-auto.git && cd vsphere-auto
uv sync
uv run vsphere-auto --help
uv run vsphere-auto serve --port 8080
```

**Manual pip (no install.sh):**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
vsphere-auto --help
python3 -m vsphere_auto serve --port 8080
```

> `install.sh` auto-provisions **Python 3.11** when missing (CentOS 7 included: via `uv` without root), picks `uv sync` or `pip install -e .` automatically, creates `state/` and the encryption key `state/.fernet.key` (0600), and records the interpreter to `state/.python_bin` for `start.sh`.

---

## Quick Start (5 minutes)

### 1. Install

```bash
bash install.sh
vsphere-auto --help
```

### 2. Save a vCenter / ESXi credential (Web or CLI)

**Web:** Open `http://localhost:8080/settings` → enter host, port, username, password → Save → click **Test** to verify. Passwords are encrypted with Fernet before being written to `state/creds.db`. To update a password later, edit the credential and fill in a new value; leaving it blank keeps the existing one.

**CLI:**

```bash
vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local
# Or via env var:
VSPHERE_PASSWORD='***' vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local --password "$VSPHERE_PASSWORD"
vsphere-auto creds list
vsphere-auto creds test prod-vc
```

### 3. Discover resources

**Web:** On the Deploy page, select a saved credential from the dropdown → click **Discover Resources**. Templates, clusters, datastores, networks, folders, and ISOs are auto-populated.

**CLI:**

```bash
vsphere-auto discover --creds prod-vc
vsphere-auto discover --creds prod-vc --out /tmp/inventory.json
```

### 4. Deploy VMs

**Web:** Fill in CPU / memory / disk / network / IP mode, pick a template or ISO, set batch count and naming template (e.g. `demo-{index:02d}`) → **Preview Plan** to review auto selections → **Deploy** → you will be redirected to the Tasks page for live progress.

**CLI:**

```bash
cp config/config.example.yaml my.yaml
# Edit my.yaml, then:
vsphere-auto plan --config my.yaml --creds prod-vc    # dry run — shows auto selections and VM list
vsphere-auto deploy --config my.yaml --creds prod-vc --yes
```

---

## Configuration

See `config/config.example.yaml`. Key fields:

```yaml
vcenter:
  credsRef: prod-vc          # reference a saved credential (or use host/user/password below)
  # host: 10.0.0.10
  # user: administrator@vsphere.local
  datacenter: DC1            # omit for auto when only one DC exists
  # cluster: auto             # auto = scored by free CPU/memory
  # datastore: auto
  # network: auto

defaults:
  folder: workloads/demo
  guestId: ubuntu64Guest
  firmware: bios

batch:
  concurrency: 5             # parallel VM creations
  onError: continue          # continue | fail-fast
  naming: "demo-{index:02d}"

ipPool:
  cidr: 10.10.20.0/24
  gateway: 10.10.20.1
  netmask: 255.255.255.0
  dns: [10.10.20.2, 8.8.8.8]

vms:
  - name: demo-01
    template: tpl-ubuntu22-04  # or iso: "[datastore1] iso/ubuntu-22.04.iso"
    cpu: 4
    memoryMB: 8192
    diskGB: 80
    networks:
      - network: auto
        ip: auto              # auto (from pool) | dhcp | 10.10.20.11
```

- Any resource field set to `auto` or omitted is auto-selected (clusters by free resources, datastores by free space, networks by reachability).
- Each VM needs `template` **or** `iso`; disks can only be expanded, not shrunk.
- Never put passwords in YAML — use `VSPHERE_PASSWORD` / `VSPHERE_PASSWORD_FILE` or a saved credential.

---

## Web UI

| Page | Path | Description |
|------|------|-------------|
| Deploy | `/` | Select credential → Discover → form → Preview → Deploy → Tasks |
| Tasks | `/tasks` | Batch list, per-VM details, auto-refresh |
| Settings / Credentials | `/settings` | CRUD for credentials, Test connection, update password |
| Health | `/api/health` | `{"ok": true}` |

---

## CLI Reference

```bash
vsphere-auto --help
vsphere-auto creds list
vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username admin --password '***'
vsphere-auto creds update --help
vsphere-auto creds remove <id>
vsphere-auto creds test <id|name>

vsphere-auto discover --creds prod-vc [--out inventory.json]
vsphere-auto plan --config my.yaml [--creds prod-vc]
vsphere-auto deploy --config my.yaml [--creds prod-vc] [--yes]
vsphere-auto serve --host 0.0.0.0 --port 8080
```

Exit codes: `0` all succeeded, `2` partial success, `1` failure — easy to check in scripts.

---

## Running as a Service (Linux)

```bash
sudo cp systemd/vsphere-auto.service /etc/systemd/system/
sudoedit /etc/systemd/system/vsphere-auto.service  # adjust WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now vsphere-auto
sudo systemctl status vsphere-auto
journalctl -u vsphere-auto -f
```

Docker (optional):

```bash
docker build -t vsphere-auto -f Dockerfile ./
docker run -p 8080:8080 -v ./state:/app/state -e VSPHERE_AUTO_KEY="$(cat state/.fernet.key)" vsphere-auto
```

---

## Idempotency & Robustness

- **Idempotency key** `specHash = sha256(normalizedSpec)` — re-running with the same spec skips unchanged VMs; use `--recreate` to force a rebuild.
- **State** in `state/batch.db` (SQLite) — interrupted runs can resume; existing VMs are looked up by `vmName + folder` before cloning.
- **Robustness:** `SmartConnect` + `tenacity` retries, vCenter task error classification, IP pool reserve/rollback, `SIGINT/SIGTERM` graceful stop, full redaction of secrets in logs and inventory.

---

## Security

- Passwords are encrypted with Fernet and stored in `state/creds.db`. The key is resolved as `VSPHERE_AUTO_KEY` env var > `state/.fernet.key` (0600, auto-generated on first run).
- The API returns `hasPassword` instead of the raw value; logs and inventory are redacted. Back up `state/.fernet.key` — losing it makes saved passwords unrecoverable.

---

## Troubleshooting

**Cannot connect to vCenter?** Run `vsphere-auto creds test <name>` and check the output. Untrusted certificates are skipped by default; you can configure a CA if needed. Some `CustomizationSpec` features are unavailable with direct ESXi connections — the tool falls back automatically.

**ISO scan is slow?** Scanning large datastores is paginated/cached and not run on every `discover`. You can also skip scanning by setting `iso: "[datastore] path/to.iso"` directly.

**How do I rotate a password?** Edit the credential in the Settings page and enter a new password, or run `vsphere-auto creds update <id> --password '***'`. Omitting the flag keeps the current value.

---

## Project Layout

```
.
├── pyproject.toml
├── config/config.example.yaml
├── install.sh / start.sh
├── systemd/vsphere-auto.service
├── src/vsphere_auto/
│   ├── cli.py / web/app.py
│   ├── creds/  vsphere/  batch/  net/  utils/
│   └── web/templates/  web/static/
└── state/  # runtime (gitignored): creds.db / batch.db / inventory.json / .fernet.key
```

---

## Development & Verification

```bash
pip install -e .          # or: uv sync
python -m vsphere_auto --help
vsphere-auto creds list
vsphere-auto plan --config config/config.example.yaml  # dry run without a vCenter
pytest                    # mock pyVmomi tests (to be added)
```
