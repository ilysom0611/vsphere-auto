# vSphere Auto — Batch VM Deployment

Automated, **Linux-first** batch VM deployment for **vSphere (vCenter / ESXi)**. Supports template clone and ISO-based provisioning, auto resource selection, concurrent multi-VM batches, idempotent/robust execution, **Web UI + CLI** sharing the same core, and Fernet-encrypted credential storage.

> Runtime: Linux · Python 3.11+ · vCenter 6.7 / 7.0 U3+ / 8.0+ or direct ESXi

---

## Prerequisites

- **OS:** Linux with systemd. See [Recommended OS Versions](#recommended-os-versions) below. macOS works for development only.
- **Python:** 3.11 or newer. `install.sh` auto-provisions Python 3.11 when missing (including CentOS 7 via `uv` without root) — just run `bash install.sh`.
  Manual install if needed: `sudo apt install python3.11 python3.11-venv python3-pip` (Debian/Ubuntu) or `sudo dnf install python3.11` (RHEL/Rocky).
- **Network:** Reachability to vCenter/ESXi on port 443.
- **vSphere access:** An account with permissions to create/clone VMs, read datastores/networks/folders, and run guest customization (e.g. `Administrator@vsphere.local` or a custom role).
- **Optional:** `uv` (faster installs; `pip` works fine without it). Install via `curl -LsSf https://astral.sh/uv/install.sh | sh`.

### Recommended OS Versions

> `install.sh` auto-installs Python 3.11 when not present, so most modern distros work out of the box. The table below is the **tested / recommended** matrix for production.

| OS | Version | Status | Notes |
|----|---------|--------|-------|
| **Ubuntu** | **22.04 LTS / 24.04 LTS** | ✅ Recommended | Best tested; Python 3.11+ available via `apt`. First choice for new deployments. |
| Ubuntu | 20.04 LTS | ⚠️ Supported | Works, but EOL Apr 2030 (ESM). Python 3.11 via `apt` / `deadsnakes` PPA. |
| **RHEL / Rocky / Alma** | **9.x** | ✅ Recommended | Production recommended; `dnf install python3.11` natively. |
| RHEL / Rocky / Alma | 8.x | ✅ Supported | Fully supported; `dnf install python3.11` natively. |
| **Debian** | **12 (bookworm)** | ✅ Recommended | `apt install python3.11` natively. |
| Debian | 11 (bullseye) | ⚠️ Supported | Works; Python 3.11 via `bullseye-backports` or `uv`. |
| CentOS | 7 | ⚠️ Legacy — EOL Jun 30 2024 | Still works via `install.sh` auto-provision (`uv` pulls Python 3.11 without root). **Migrate to Rocky/Alma 8/9 strongly recommended** — no security updates. |
| CentOS Stream / Fedora | latest | ⚠️ Community | Should work; not formally tested in CI. |
| macOS | 13+ | 🛠️ Dev only | For development / `plan` dry runs; not for production service. |

**vSphere:** vCenter **6.7 / 7.0 U3+ / 8.0+** and ESXi 6.7/7.0/8.0. Direct ESXi connections work but some `CustomizationSpec` (guest customization) features require vCenter — the tool falls back automatically. On **6.7** the client relaxes TLS ciphers automatically (see Troubleshooting if discover still fails).

**Why not CentOS 7 for new installs?** CentOS 7 reached EOL and ships Python 3.6 + pip 8 which cannot build this project. `install.sh` works around it, but you inherit an unpatched base OS. For any new host, use **Ubuntu 22.04/24.04** or **Rocky/Alma 9**.

### Guest customization prerequisites

IP/hostname customization (static IP, hostname via `CustomizationSpec` / LinuxPrep) only works if the **template's guest OS can run it**:

- **VMware Tools must be installed and running** inside the template (`open-vm-tools` on Linux). Without it, vCenter customization silently does nothing or fails mid-task.
- **Cloud-init-enabled templates will override vCenter customization.** Most CentOS 7.9 cloud images ship cloud-init, which rewrites the network config at first boot and discards the static IP/hostname set by LinuxPrep. Either:
  - disable cloud-init network configuration in the template:
    ```bash
    echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
    ```
  - or use a **non-cloud-init template** for static-IP deployments.
- Templates cloned without any explicit IP (`ip: dhcp` / no ipPool) do not need customization and work with plain templates.

---

## Dependencies

All Python dependencies are declared in `pyproject.toml` and installed automatically by `install.sh`. No manual `pip install` is needed.

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | >=3.0 | Web UI and REST API |
| `waitress` | >=3.0 | Production WSGI server (used by `serve` outside debug mode) |
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

`install.sh` auto-clones the repo when piped, so no prior `git clone` is needed:

```bash
curl -fsSL https://raw.githubusercontent.com/ilysom0611/vsphere-auto/main/install.sh | bash
# repo is cloned to ./vsphere-auto automatically
bash vsphere-auto/start.sh
# or: cd vsphere-auto && bash start.sh
# Open http://localhost:8080
```

> If `git` is not installed the script will exit with install instructions.

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
.venv/bin/vsphere-auto --help   # venv entrypoint (always works after install.sh)
# or: vsphere-auto --help            # if .venv/bin is on PATH or pip --user
# or: python3 -m vsphere_auto --help # module form
# or: uv run vsphere-auto --help     # if uv is installed

# 4) Start Web UI
bash start.sh  # http://localhost:8080 (auto-picks .venv/bin/python)
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

> `install.sh` auto-provisions **Python 3.11** when missing (CentOS 7 included: via `uv` without root), picks `uv sync` or `pip install -e .` automatically, creates `state/` and the encryption key `state/.fernet.key` (0600), and records the interpreter to `state/.python_bin` for `start.sh`. When piped via `curl | bash` the script clones the repo to `./vsphere-auto` automatically.

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
  # cluster: auto             # auto = cluster with the most hosts
  # datastore: auto           # auto = datastore with the most free space
  # network: auto             # auto = first network found

defaults:
  folder: workloads/demo
  guestId: ubuntu64Guest

batch:
  concurrency: 5             # parallel VM creations
  onError: continue          # continue | fail-fast
  naming: "demo-{index:02d}"

ipPool:
  cidr: 10.10.20.0/24
  gateway: 10.10.20.1
  netmask: 255.255.255.0
  dns: [10.10.20.2, 8.8.8.8]

# Count-based expansion (alternative to listing vms[]):
# count: 3                   # generate 3 VMs named via batch.naming
# template: tpl-ubuntu22-04

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

- Any resource field set to `auto` or omitted is auto-selected: **cluster** = the one with the **most hosts**, **datastore** = the one with the **most free space**, **network** = the **first** one discovered. There is no free-CPU/memory scoring or reachability probing — pin explicit names when you need precise placement.
- Each VM needs `template` **or** `iso`; disks can only be expanded, not shrunk.
- IP pool allocation is all-or-nothing per plan/deploy: if the pool runs out mid-expansion, already-taken leases are rolled back and the run aborts with a clear error instead of deploying half the batch.
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
vsphere-auto serve [--host 0.0.0.0] [--port 8080] [--debug]
```

Exit codes: `0` all succeeded, `2` partial success, `1` failure — easy to check in scripts.

---

## Running as a Service (Linux)

```bash
sudo cp systemd/vsphere-auto.service /etc/systemd/system/
sudoedit /etc/systemd/system/vsphere-auto.service  # adjust User / WorkingDirectory / ExecStart paths
sudo systemctl daemon-reload
sudo systemctl enable --now vsphere-auto
sudo systemctl status vsphere-auto
journalctl -u vsphere-auto -f
```

Or let the installer do it (best-effort; substitutes paths automatically):

```bash
sudo bash install.sh --install-service
```

The unit runs as `User=vsphereauto` (create it first or adjust `User=`/`Group=`), sets `VSPHERE_STATE_DIR=/opt/vsphere-auto/state`, restarts on failure after 5 seconds, and binds to `0.0.0.0` (all interfaces) — restrict to loopback via `systemctl edit` if you only need local access.

Docker: **not yet provided** — there is no Dockerfile in the repository at the moment. Run directly with `install.sh` + `start.sh` or the systemd unit above.

---

## Idempotency & Robustness

- **Idempotency key** `specHash = sha256(normalizedSpec)` — re-running with the same spec skips unchanged VMs (reported as `skipped` in the batch summary). Auto-assigned IPs are excluded from the hash so pool drift cannot break idempotency.
- **State** in `state/batch.db` (SQLite, race-safe batch id allocation) — existing VMs are looked up by `vmName + folder` before cloning; stale `running`/`pending` rows left by a crash are marked `interrupted` on the next startup (`recover_interrupted`), so a re-run starts clean instead of colliding.
- **IP pool safety:** auto-allocated leases are persisted to `state/ip_pools.json`; pool exhaustion aborts the plan with a clear error and already-taken leases are rolled back — no half-planned batches.
- **Robustness:** vCenter connections use `tenacity` retry with backoff, vCenter task errors are classified with readable messages, `SIGINT/SIGTERM` stop batches gracefully, and secrets are redacted in logs, state and inventory.

---

## Security

- **Default bind address is `0.0.0.0`** — the UI/API are reachable from other hosts out of the box. Set `--host 127.0.0.1`, `VSPHERE_HOST=127.0.0.1`, or edit the systemd unit to restrict to loopback.
- **Set `VSPHERE_API_TOKEN` when the service is reachable from other hosts** — without it the API is unauthenticated (a loud warning is printed at startup in that case). When the token is set, requests must send it as `Authorization: Bearer <token>` (or `X-API-Token: <token>`). The Web UI handles this automatically: on the first `401` the browser prompts for the token, remembers it in `localStorage`, and retries — CLI/API callers pass the header explicitly:
  ```bash
  curl -H "X-API-Token: $VSPHERE_API_TOKEN" http://<host>:8080/api/health
  ```
- **Use a reverse proxy** (nginx/caddy with TLS + auth) in front of port 8080 for any non-loopback access, and restrict with firewalld:
  ```bash
  firewall-cmd --add-port=8080/tcp --permanent && firewall-cmd --reload
  ```
- **TLS certificate verification is currently disabled** for vCenter connections — a compatibility measure for vSphere 6.7's self-signed certificates. Do not rely on the transport layer for confidentiality against active attackers on the vCenter path; keep the management network trusted.
- Passwords are encrypted with Fernet and stored in `state/creds.db`. The key is resolved as `VSPHERE_AUTO_KEY` env var > `state/.fernet.key` (0600, auto-generated on first run). Back up `state/.fernet.key` — losing it makes saved passwords unrecoverable.
- The API returns `hasPassword` instead of the raw value; logs, batch state and inventory are redacted (`***` for secret-looking keys).

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `VSPHERE_STATE_DIR` | Directory holding all runtime state (`creds.db`, `batch.db`, `ip_pools.json`, `inventory.json`, `.fernet.key`). `start.sh` defaults it to `<repo>/state` so manual starts and systemd share one state dir. | `<repo>/state` |
| `VSPHERE_AUTO_KEY` | Fernet key used to encrypt stored credentials. Overrides `state/.fernet.key`; set it when using an external secret store or a shared state dir. | auto-generated key file |
| `VSPHERE_API_TOKEN` | When set, all API requests must present this token (`Authorization: Bearer …` / `X-API-Token`). Required before exposing the UI beyond loopback. | unset (no auth) |
| `VSPHERE_HOST` | Bind address for `start.sh`, passed through to `serve --host`. The `serve` CLI default itself is fixed at `0.0.0.0`; use `--host` to override per-invocation. | `0.0.0.0` |
| `VSPHERE_PASSWORD` | vCenter password for CLI/Web flows that don't use saved credentials. | unset |
| `VSPHERE_PASSWORD_FILE` | Alternative to `VSPHERE_PASSWORD`: read the password from this file. | unset |
| `VSPHERE_DEBUG` | `1`/`true` enables Flask debug mode + DEBUG logging (same as `serve --debug`). Debug mode forces loopback binding. | unset |
| `LOG_LEVEL` | Log level override (`DEBUG`, `INFO`, …). | `INFO` (`DEBUG` with debug mode) |

---

## Troubleshooting

**Cannot connect to vCenter?** Run `vsphere-auto creds test <name>` and check the output. Untrusted certificates are skipped by default; you can configure a CA if needed. Some `CustomizationSpec` features are unavailable with direct ESXi connections — the tool falls back automatically.

**Discover shows "Failed" or "no feedback" (especially vSphere 6.7):** The Deploy page now prints the exact error. Common causes on **6.7.0 (e.g. 6.7.0.42000)**:
  1. **TLS / cipher mismatch** — Old hosts negotiate TLS 1.0/1.1 or legacy ciphers blocked by Python 3.11 + OpenSSL 3.x. The client now lowers `SECLEVEL` to 1 and allows TLS 1.0+ automatically; if you still see `SSL: ...` or `handshake failed`, verify the host is reachable on `443` (`curl -vk https://<vc>:443/sdk`) and not blocked by a proxy/firewall.
  2. **Wrong credential / port** — On the deploy page pick the saved credential (dropdown) and watch the status line; it will show `Failed: <reason>` instead of staying blank. Or run `vsphere-auto creds test <name>` from the CLI — it prints the same error.
  3. **ESXi direct (no vCenter)** — `datacenters` will be `0` even on success; clusters/networks may be empty. The `summary` line in the status bar (`DC:0 clusters:0 ...`) is expected.
  Re-run with `VSPHERE_DEBUG=1 bash start.sh` to get full stack traces in the server log.

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
