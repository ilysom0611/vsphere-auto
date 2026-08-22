# vSphere Auto — vSphere 批量虚拟机部署工具

> [English](README.md) | [中文](README.zh-CN.md) | [ไทย](README.th.md)

![License: MIT](https://img.shields.io/badge/License-MIT-green) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![Platform](https://img.shields.io/badge/vCenter-6.7%20%7C%207.0%20%7C%208.0-orange)

**解决什么问题**：vSphere 自带界面一次只能创建一台 VM，规格/网络/IP 要逐台手填。本工具把这件事变成一次操作——Web 向导 4 步或一条 CLI 命令，批量创建数十台规格一致的虚拟机：自动发现 vSphere 资源、可手动指定文件夹/存储/主机放置、并发部署、静态 IP 与主机名首次开机自动注入、任务实时进度、失败时给出明确原因和处置建议。

- 模板克隆（推荐）或 ISO 全新装机
- DHCP 或静态 IP（掩码/网关/DNS/主机名经 Guest Customization 写入 VM）
- 幂等可重跑：中断后重新执行不会重复建 VM
- 密码加密存储，日志与输出自动脱敏

> 运行环境：Linux · Python 3.11+ · vCenter 6.7 / 7.0 U3+ / 8.0+ 或直连 ESXi

| 部署向导 | 任务页（实时进度 / 筛选 / 报错提示） |
|----------|--------------------------------------|
| ![部署向导](docs/screenshots/wizard.png) | ![任务页](docs/screenshots/tasks.png) |

---

## 使用场景

- **测试/开发/演示环境批量发放**：一次创建数十台同规格 VM；环境用完可删，配合幂等特性随时重新发放
- **标准化交付**：固定规格 + 静态 IP 规划，批量交付给使用方，IP/网关/DNS/主机名开箱即用
- **ISO 全新装机**：无模板时从 ISO 批量安装并完成网络定制
- **精确放置**：手动指定目标文件夹（分级树选择）、数据存储、宿主机（含主机↔存储挂载校验）；不指定则自动选择

## 兼容性

| 项目 | 要求 |
|------|------|
| vCenter | 6.7 / 7.0 U3+ / 8.0+（已在 vCenter 6.7.3 实测验证） |
| ESXi | 6.7 / 7.0 / 8.0；直连 ESXi 可用，但部分客户机定制功能受限（自动回退） |
| 模板 | 需已安装并运行 VMware Tools / open-vm-tools（静态 IP 定制依赖它） |
| 操作系统 | 见下方「安装要求」 |

> **cloud-init 模板注意**：多数 CentOS 7.9 云镜像自带 cloud-init，会在首次开机时覆盖 vCenter 下发的静态 IP/主机名。静态 IP 部署请在模板中禁用 cloud-init 网络配置：
> ```bash
> echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
> ```
> DHCP 部署不受影响。

---

## 安装要求

- **操作系统**：Linux（systemd 可选）。推荐 **Ubuntu 22.04/24.04 LTS**、**RHEL/Rocky/Alma 8/9**、**Debian 12**；CentOS 7 仍可用（脚本自动安装 Python 3.11，无需 root，但系统本身已 EOL，不建议新部署）
- **Python**：3.11+（`install.sh` 自动检测并安装，通常无需手动处理）
- **网络**：服务器到 vCenter/ESXi 的 443 端口可达；首次安装需要访问 pypi.org（离线主机请预装 Python 3.11 及依赖）
- **vSphere 账号**：具备创建/克隆 VM、读取数据中心/数据存储/网络/文件夹、执行客户机定制的权限（如 `Administrator@vsphere.local`）

## 快速安装

```bash
# 方式一：git
git clone https://github.com/ilysom0611/vsphere-auto.git
cd vsphere-auto
bash install.sh     # 自动创建 venv、安装依赖、初始化加密密钥
bash start.sh       # 启动 Web 服务，默认 http://localhost:8080

# 方式二：无 git，一行安装（自动克隆仓库到 ./vsphere-auto）
curl -fsSL https://raw.githubusercontent.com/ilysom0611/vsphere-auto/main/install.sh | bash
bash vsphere-auto/start.sh
```

生产环境建议注册为 systemd 服务：

```bash
sudo bash install.sh --install-service   # 自动替换路径，开机自启、崩溃自动重启
```

Docker 暂未提供。

### 停止方式

```bash
# systemd 方式安装的服务
sudo systemctl stop vsphere-auto        # 启动: start / 重启: restart / 开机自启状态: systemctl is-enabled vsphere-auto

# bash start.sh 前台运行：直接 Ctrl+C

# nohup / 后台手动启动的进程
pkill -f 'vsphere_auto serve'
```

停止不影响已保存的凭据和历史任务（都在 `state/` 目录，重启后自动恢复；中断批次会被标记为 interrupted）。

### 更新方式

```bash
cd vsphere-auto
git pull origin main
bash install.sh     # 依赖有变化时刷新 venv（可重复执行）
# 按当前运行方式重启：
sudo systemctl restart vsphere-auto    # 或重新 bash start.sh
```

`state/` 目录（凭据库、任务记录、加密密钥、IP 池）独立于代码，升级自动保留。跨大版本升级前建议先备份 `state/` 目录。

---

## 使用：Web 向导（推荐）

打开 `http://<服务器>:8080`，按 4 步完成部署：

| 步骤 | 操作 |
|------|------|
| ① 连接 | 选择已保存的凭据 → 自动发现资源（模板/文件夹/存储/主机/网络/ISO） |
| ② 来源与放置 | 选择模板（或 ISO）；**手动指定**文件夹（分级树）、数据存储、宿主机、网络 —— 所选主机未挂载该存储时页面会直接警告拦截 |
| ③ 规格与数量 | 台数、命名规则（如 `demo-{index:02d}`）、CPU/内存/磁盘、置备方式、IP 模式（DHCP/静态：掩码·网关·DNS）、并发数 |
| ④ 确认部署 | 自动生成部署计划供复核 → 确认后跳转任务页 |

**任务页（/tasks）**：实时显示每台 VM 的当前操作与克隆百分比；失败时给出**原因分类和处置建议**（如“该主机未挂载所选数据存储”）及原始报错；支持按名称/状态/创建时间筛选，可删除已完成的历史批次和任务。

**设置页（/settings）**：保存 vCenter 凭据（密码 Fernet 加密存储）、一键测试连通性。

> `/advanced` 是面向熟悉 YAML 配置用户的完整表单，功能与向导一致。

### CLI（同一套核心）

```bash
vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local
vsphere-auto creds test prod-vc
vsphere-auto discover --creds prod-vc                 # 发现资源
cp config/config.example.yaml my.yaml                 # 编辑规格
vsphere-auto plan --config my.yaml --creds prod-vc    # 干跑预览
vsphere-auto deploy --config my.yaml --creds prod-vc --yes
vsphere-auto serve --port 8080                        # 启动 Web
```

退出码：`0` 全部成功 / `2` 部分成功 / `1` 失败，便于脚本判断。

---

## 可靠性设计

- **幂等**：相同规格重复执行自动跳过已存在的 VM（按 名称+文件夹 识别），不会重复克隆
- **断点安全**：服务崩溃后重启，残留的 running/pending 任务标记为 interrupted，可直接重新执行
- **IP 池保护**：池耗尽时整批中止并回滚已分配地址，不会发出一半的批次
- **保密**：密码加密入库；日志、任务记录、清单输出中的敏感值统一打码；YAML 中永远不要写明文密码（用 `VSPHERE_PASSWORD` 环境变量或已保存凭据）

## 安全要点（对外暴露前必读）

1. **务必设置 API Token**：`export VSPHERE_API_TOKEN=<随机串>` 后重启，所有 API 请求需携带该 Token（浏览器首次 401 时会弹窗记住）。不设置时 API 无鉴权且启动时会打印醒目告警。
2. **默认监听 `0.0.0.0`**：仅本机使用请改 `VSPHERE_HOST=127.0.0.1`；跨网段访问建议前置 nginx/caddy（TLS + 认证）并用防火墙收敛 8080 端口。
3. **vCenter TLS 校验当前关闭**（兼容 6.7 自签名证书的取舍），请保证管理网络可信。

## 环境变量

| 变量 | 说明 |
|------|------|
| `VSPHERE_STATE_DIR` | 运行时状态目录（凭据库/任务库/IP 池/密钥），默认 `<repo>/state` |
| `VSPHERE_API_TOKEN` | 设置后启用 API 鉴权（Bearer / X-API-Token 头） |
| `VSPHERE_HOST` | Web 监听地址，默认 `0.0.0.0` |
| `VSPHERE_AUTO_KEY` | 外置 Fernet 密钥（默认自动生成 `state/.fernet.key`，**请备份，丢失则已存密码不可恢复**） |
| `VSPHERE_PASSWORD` | CLI/Web 未用已存凭据时的 vCenter 密码 |
| `VSPHERE_DEBUG` | `1` 开启调试日志（强制仅本机监听） |

---

## 常见问题

**连接 vCenter 失败（尤其 6.7）？**
先 `vsphere-auto creds test <名称>` 看具体错误。6.7 老主机的 TLS 1.0/legacy cipher 问题客户端已自动降级处理；仍失败时检查 443 可达性（`curl -vk https://<vc>:443/sdk`）和代理/防火墙。`VSPHERE_DEBUG=1 bash start.sh` 可看完整堆栈。

**静态 IP / 主机名没有生效？**
确认模板内 VMware Tools 在运行；cloud-init 模板需按上文禁用其网络配置。DHCP 部署不做任何注入，属正常。

**直连 ESXi 时发现结果里 DC/clusters 为 0？**
正常现象——ESXi 无数据中心/集群概念，直接选择主机和数据存储即可。

**如何轮换密码？**
设置页编辑凭据填入新密码（留空保持不变），或 `vsphere-auto creds update <id> --password '***'`。

---

## 支持与反馈

发现问题或有功能建议？请[提交 Issue](https://github.com/ilysom0611/vsphere-auto/issues)。

---

## License

本项目采用 **[MIT License](LICENSE)** —— 可自由使用、修改、商用、再分发，唯一要求是保留原始版权与许可声明（无担保条款）。

第三方依赖均为宽松许可证，与本项目 MIT 兼容，无传染性（ copyleft）问题，商用无需额外授权：

| 依赖 | 许可证 |
|------|--------|
| pyvmomi, requests, cryptography | Apache-2.0（cryptography 为 Apache-2.0 / BSD-3 双许可） |
| flask, jinja2 | BSD-3-Clause |
| waitress | Zope Public License 2.1 (ZPL-2.1) |
| pyyaml, pydantic, typer, rich, tenacity, hatchling | MIT |
