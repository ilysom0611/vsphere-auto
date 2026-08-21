# vSphere Auto — 批量自动化部署工具

Linux 主机运行，面向 **vSphere (vCenter / ESXi)** 的虚拟机批量部署工具。支持模板克隆 / ISO 空白机、自动选资源、并发批量、幂等与鲁棒、**Web 界面 + CLI 双入口**、凭据加密持久化。

> 运行环境：Linux 主机 · Python 3.11+ · vCenter 7.0 / 8.0 / ESXi 直连

---

## 一键下载与启动

### 方式 A：git 一键（推荐）

```bash
git clone https://github.com/ilysom0611/doc-translator.git
cd doc-translator/vsphere-auto
bash install.sh
bash start.sh
# 浏览器打开 http://localhost:8080
```

### 方式 B：curl 一键（无需先 clone）

```bash
curl -fsSL https://raw.githubusercontent.com/ilysom0611/doc-translator/main/vsphere-auto/install.sh | bash -s -- --from-curl
# 或分步：
curl -fsSL https://raw.githubusercontent.com/ilysom0611/doc-translator/main/vsphere-auto/install.sh -o /tmp/install-vsphere-auto.sh && bash /tmp/install-vsphere-auto.sh
bash vsphere-auto/start.sh
```

### 方式 C：uv 用户

```bash
git clone https://github.com/ilysom0611/doc-translator.git && cd doc-translator/vsphere-auto
uv sync && uv run vsphere-auto --help
uv run vsphere-auto serve --port 8080
```

> `install.sh` 会自动选择 `uv sync` / `pip install -e .`，并创建 `state/` 目录与加密密钥 `state/.fernet.key`（0600）。

---

## 快速开始（5 分钟）

### 1. 安装

```bash
cd vsphere-auto
bash install.sh
vsphere-auto --help
```

### 2. 保存 vCenter / ESXi 凭据（Web 或 CLI 二选一）

**Web：** 打开 `http://localhost:8080/settings` → 填写主机、端口、用户名、密码 → 保存 → 点「测试」验证连通性。密码落盘前用 Fernet 加密，支持随时更新（编辑时留空表示不改密码）。

**CLI：**

```bash
vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local
# 密码可交互输入或通过环境变量：
VSPHERE_PASSWORD='***' vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local --password "$VSPHERE_PASSWORD"
vsphere-auto creds list
vsphere-auto creds test prod-vc
```

### 3. 发现资源

**Web：** 部署页顶部下拉选择已保存凭据 → 点「🔍 发现资源」，自动回填模板 / 集群 / 存储 / 网络 / 文件夹 / ISO 列表。

**CLI：**

```bash
vsphere-auto discover --creds prod-vc
vsphere-auto discover --creds prod-vc --out /tmp/inventory.json
```

### 4. 部署虚拟机

**Web：** 在部署页填写 CPU / 内存 / 磁盘 / 网络 / IP 方式，选择模板或 ISO，设置批量数量与命名模板（如 `demo-{index:02d}`）→ 点「预览计划」确认自动选型 → 点「提交部署」→ 跳转任务页实时查看进度。

**CLI：**

```bash
# 1) 复制并编辑配置
cp config/config.example.yaml my.yaml
vim my.yaml

# 2) 干跑预览（展示自动选型与待创建清单）
vsphere-auto plan --config my.yaml --creds prod-vc

# 3) 正式部署
vsphere-auto deploy --config my.yaml --creds prod-vc --yes
```

---

## 配置说明

配置文件为 YAML（见 `config/config.example.yaml`），关键字段：

```yaml
vcenter:
  credsRef: prod-vc          # 引用已保存凭据（与 host/user/password 二选一）
  # host: 10.0.0.10
  # user: administrator@vsphere.local
  datacenter: DC1            # 省略则 auto（单 DC 时）
  # cluster: auto             # auto 按空闲打分
  # datastore: auto
  # network: auto

defaults:
  folder: workloads/demo
  guestId: ubuntu64Guest
  firmware: bios

batch:
  concurrency: 5             # 并发数
  onError: continue          # continue | fail-fast
  naming: "demo-{index:02d}"

ipPool:
  cidr: 10.10.20.0/24
  gateway: 10.10.20.1
  netmask: 255.255.255.0
  dns: [10.10.20.2, 8.8.8.8]

vms:
  - name: demo-01
    template: tpl-ubuntu22-04  # 与 iso 二选一
    cpu: 4
    memoryMB: 8192
    diskGB: 80
    networks:
      - network: auto
        ip: auto              # auto 从池分配 | dhcp | 10.10.20.11
  # - name: demo-02
  #   iso: "[datastore1] iso/ubuntu-22.04.iso"
```

- 任意资源字段填 `auto` 或省略即自动选择（集群按 CPU/内存空闲、存储按剩余空间、网络按可达性）。
- `template` 与 `iso` 二选一；磁盘仅支持扩容。
- 密码不要写进 YAML，通过 `VSPHERE_PASSWORD` / `VSPHERE_PASSWORD_FILE` 或已保存凭据传入。

---

## Web 界面

| 页面 | 路径 | 功能 |
|------|------|------|
| 部署 | `/` | 选择凭据 → 发现资源 → 填表单 → 预览 → 提交 → 任务页 |
| 任务 | `/tasks` | 批次列表、单机明细、轮询刷新 |
| 设置/凭据 | `/settings` | 凭据 CRUD、测试连接、更新密码 |
| 健康检查 | `/api/health` | `{"ok": true}` |

---

## CLI 完整命令

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

退出码：`0` 全成功、`2` 部分成功、`1` 失败，便于脚本判断。

---

## Linux 常驻服务

```bash
sudo cp systemd/vsphere-auto.service /etc/systemd/system/
sudoedit /etc/systemd/system/vsphere-auto.service  # 修改 WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now vsphere-auto
sudo systemctl status vsphere-auto
journalctl -u vsphere-auto -f
```

Docker（可选）：

```bash
docker build -t vsphere-auto -f vsphere-auto/Dockerfile vsphere-auto/
docker run -p 8080:8080 -v vsphere-auto/state:/app/state -e VSPHERE_AUTO_KEY="$(cat vsphere-auto/state/.fernet.key)" vsphere-auto
```

---

## 幂等与鲁棒

- **幂等键** `specHash = sha256(normalizedSpec)`，重放时已存在且一致则跳过，可选 `--recreate` 强制重建。
- **状态持久化** `state/batch.db`（SQLite），中断后重跑按 `vmName + folder` 反查续接，不重复克隆。
- **鲁棒**：`SmartConnect` + `tenacity` 重试、vCenter Task 分类报错、IP 池预占/回滚、`SIGINT/SIGTERM` 优雅停新任务、敏感字段全链路脱敏。

---

## 安全

- 密码 Fernet 加密落 `state/creds.db`，密钥优先级 `VSPHERE_AUTO_KEY` > `state/.fernet.key`（0600，首次启动自动生成）。
- API 返回 `hasPassword` 而非明文，日志与 inventory 脱敏。密钥丢失则已存密码不可解密，请备份 `state/.fernet.key`。

---

## 常见问题

**连不上 vCenter？** 检查 `vsphere-auto creds test <name>` 输出；证书不受信任时默认跳过验证，可配置 CA。ESXi 直连场景部分 `CustomizationSpec` 不可用，工具会自动回退。

**ISO 扫描慢？** 大存储扫描耗时，已做分页/超时 + 缓存，避免每次 `discover` 全扫；可在部署时直接填 `iso: "[datastore] path/to.iso"` 跳过扫描。

**如何更新密码？** Web 设置页编辑凭据、填新密码保存；CLI 用 `vsphere-auto creds update <id> --password '***'`，不填则保持不变。

---

## 目录结构

```
vsphere-auto/
  pyproject.toml
  config/config.example.yaml
  install.sh / start.sh
  systemd/vsphere-auto.service
  src/vsphere_auto/
    cli.py / web/app.py
    creds/  vsphere/  batch/  net/  utils/
    web/templates/  web/static/
  state/  # 运行时（gitignored）：creds.db / batch.db / inventory.json / .fernet.key
```

---

## 开发与验证

```bash
pip install -e .          # 或 uv sync
python -m vsphere_auto --help
vsphere-auto creds list
vsphere-auto plan --config config/config.example.yaml  # 无需 vCenter 即可干跑
pytest                    # mock pyVmomi 单测（待补充）
```
