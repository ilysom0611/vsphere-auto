# vSphere Auto — การปรับใช้ VM จำนวนมากบน vSphere

> [English](README.md) | [中文](README.zh-CN.md) | [ไทย](README.th.md)

![License: MIT](https://img.shields.io/badge/License-MIT-green) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![Platform](https://img.shields.io/badge/vCenter-6.7%20%7C%207.0%20%7C%208.0-orange)

**ปัญหาที่แก้ไข**: อินเทอร์เฟซของ vSphere สร้าง VM ได้ครั้งละเครื่อง และต้องกรอกสเปก / เครือข่าย / IP เป็นรายเครื่อง เครื่องมือนี้เปลี่ยนงานนั้นให้เป็นการดำเนินการเดียว — ผ่าน Web Wizard 4 ขั้นตอน หรือคำสั่ง CLI เพียงคำสั่งเดียว สามารถสร้าง VM ที่มีสเปกเหมือนกันได้หลายสิบเครื่องพร้อมกัน: ค้นหาทรัพยากร vSphere อัตโนมัติ, เลือกวาง folder / datastore / host ด้วยตนเองได้, ปรับใช้แบบขนาน, ใส่ static IP และ hostname ตอนบูตครั้งแรก, แสดงความคืบหน้าของงานแบบเรียลไทม์ และเมื่อล้มเหลวจะระบุสาเหตุพร้อมวิธีแก้ไขอย่างชัดเจน

- โคลนจากเทมเพลต (แนะนำ) หรือติดตั้งใหม่จาก ISO
- DHCP หรือ static IP (netmask / gateway / DNS / hostname ถูกเขียนลง VM ผ่าน Guest Customization)
- รันซ้ำได้อย่างปลอดภัย (idempotent): แบตช์ที่ถูกขัดจังหวะสามารถรันซ้ำได้โดยไม่สร้าง VM ซ้ำ
- จัดเก็บรหัสผ่านแบบเข้ารหัส; ปกปิดข้อมูลลับใน log และผลลัพธ์อัตโนมัติ

> สภาพแวดล้อม: Linux · Python 3.11+ · vCenter 6.7 / 7.0 U3+ / 8.0+ หรือเชื่อมต่อ ESXi โดยตรง

| Deployment Wizard | หน้า Tasks (ความคืบหน้าแบบเรียลไทม์ / ตัวกรอง / คำแนะนำข้อผิดพลาด) |
|-------------------|------------------------------------------------------------------|
| ![Deployment wizard](docs/screenshots/wizard.png) | ![หน้า Tasks](docs/screenshots/tasks.png) |

---

## กรณีการใช้งาน

- **สร้างสภาพแวดล้อมทดสอบ / พัฒนา / สาธิตจำนวนมาก**: สร้าง VM สเปกเดียวกันหลายสิบเครื่องในครั้งเดียว; ลบและสร้างใหม่ได้ตลอดเวลาด้วยคุณสมบัติ idempotency
- **การส่งมอบตามมาตรฐาน**: สเปกคงที่ + วางแผน static IP ส่งมอบพร้อมใช้งานทันที โดยมี IP/gateway/DNS/hostname ตั้งค่าไว้แล้ว
- **ติดตั้งใหม่จาก ISO**: ไม่มีเทมเพลต? ติดตั้งจาก ISO เป็นชุดพร้อม customization เครือข่าย
- **การวางตำแหน่งที่แม่นยำ**: เลือก folder (แบบทรี), datastore และ host ด้วยตนเอง (รวมถึงการตรวจสอบการ mount host↔datastore); หากไม่ระบุจะเลือกให้อัตโนมัติ

## ความเข้ากันได้

| รายการ | ข้อกำหนด |
|--------|----------|
| vCenter | 6.7 / 7.0 U3+ / 8.0+ (ทดสอบจริงกับ vCenter 6.7.3) |
| ESXi | 6.7 / 7.0 / 8.0; เชื่อมต่อ ESXi โดยตรงได้ แต่ฟีเจอร์ guest customization บางส่วนจำกัด (มี fallback อัตโนมัติ) |
| เทมเพลต | ต้องติดตั้งและรัน VMware Tools / open-vm-tools แล้ว (จำเป็นสำหรับ customization static IP) |
| ระบบปฏิบัติการ | ดู "ข้อกำหนดในการติดตั้ง" ด้านล่าง |

> **ข้อควรระวังสำหรับเทมเพลต cloud-init**: cloud image ของ CentOS 7.9 ส่วนใหญ่มาพร้อม cloud-init ซึ่งจะเขียนทับ static IP/hostname ที่ vCenter ตั้งค่าไว้ตอนบูตครั้งแรก สำหรับการปรับใช้แบบ static IP ให้ปิดการตั้งค่าเครือข่ายของ cloud-init ในเทมเพลต:
> ```bash
> echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
> ```
> การปรับใช้แบบ DHCP ไม่ได้รับผลกระทบ

---

## ข้อกำหนดในการติดตั้ง

- **OS**: Linux (systemd ไม่บังคับ) แนะนำ: **Ubuntu 22.04/24.04 LTS**, **RHEL/Rocky/Alma 8/9**, **Debian 12** CentOS 7 ยังใช้งานได้ (`install.sh` ติดตั้ง Python 3.11 ให้โดยไม่ต้องใช้ root) แต่หมดอายุการสนับสนุนแล้ว — ไม่แนะนำสำหรับการติดตั้งใหม่
- **Python**: 3.11+ (`install.sh` ตรวจจับและติดตั้งให้อัตโนมัติ; โดยปกติไม่ต้องทำอะไรเอง)
- **เครือข่าย**: ติดต่อ vCenter/ESXi ผ่านพอร์ต 443 ได้; การติดตั้งครั้งแรกต้องเข้าถึง pypi.org (เครื่อง offline ควรติดตั้ง Python 3.11 และ dependencies ไว้ล่วงหน้า)
- **บัญชี vSphere**: ต้องมีสิทธิ์สร้าง/โคลน VM, อ่าน datacenter/datastore/network/folder และรัน guest customization (เช่น `Administrator@vsphere.local`)

## ติดตั้งด่วน

```bash
# วิธีที่ 1: git
git clone https://github.com/ilysom0611/vsphere-auto.git
cd vsphere-auto
bash install.sh     # สร้าง venv, ติดตั้ง dependencies, สร้าง encryption key
bash start.sh       # เริ่ม web service, ค่าเริ่มต้น http://localhost:8080

# วิธีที่ 2: คำสั่งเดียวไม่ต้องมี git (clone repo ไปที่ ./vsphere-auto ให้อัตโนมัติ)
curl -fsSL https://raw.githubusercontent.com/ilysom0611/vsphere-auto/main/install.sh | bash
bash vsphere-auto/start.sh
```

สำหรับ production แนะนำให้ลงทะเบียนเป็น systemd service:

```bash
sudo bash install.sh --install-service   # แทนที่ path ให้อัตโนมัติ; เริ่มอัตโนมัติเมื่อบูต + รีสตาร์ทเมื่อ crash
```

ยังไม่รองรับ Docker

### การหยุดการทำงาน

```bash
# ติดตั้งเป็น systemd service
sudo systemctl stop vsphere-auto        # ใช้อื่น ๆ: start / restart; ตรวจสอบ autostart: systemctl is-enabled vsphere-auto

# รัน foreground ผ่าน bash start.sh: กด Ctrl+C

# เริ่มเองใน background (nohup ฯลฯ)
pkill -f 'vsphere_auto serve'
```

การหยุดไม่กระทบ credentials หรือประวัติงานที่บันทึกไว้ (อยู่ใน `state/` ทั้งหมด จะถูกโหลดกลับเมื่อเริ่มระบบ; แบตช์ที่ถูกขัดจังหวะจะถูกทำเครื่องหมาย `interrupted`)

### การอัปเดต

```bash
cd vsphere-auto
git pull origin main
bash install.sh     # รีเฟรช venv หาก dependencies เปลี่ยน (รันซ้ำได้ปลอดภัย)
# รีสตาร์ทตามวิธีที่รันอยู่:
sudo systemctl restart vsphere-auto    # หรือ: bash start.sh อีกครั้ง
```

ไดเรกทอรี `state/` (credential DB, ประวัติงาน, encryption key, IP pools) แยกจากโค้ดและจะคงอยู่หลังอัปเกรด ควรสำรอง `state/` ก่อนอัปเกรดข้าม major version

---

## การใช้งาน: Web Wizard (แนะนำ)

เปิด `http://<server>:8080` แล้วปรับใช้ใน 4 ขั้นตอน:

| ขั้นตอน | การดำเนินการ |
|---------|--------------|
| ① เชื่อมต่อ | เลือก credential ที่บันทึกไว้ → ค้นหาทรัพยากรอัตโนมัติ (templates/folders/datastores/hosts/networks/ISOs) |
| ② แหล่งที่มาและการวางตำแหน่ง | เลือก template (หรือ ISO); **เลือกเอง** folder (แบบทรี), datastore, host, network — ระบบจะเตือนและบล็อกกรณี host ที่เลือกไม่ได้ mount datastore นั้น |
| ③ สเปกและจำนวน | จำนวน VM, รูปแบบชื่อ (เช่น `demo-{index:02d}`), CPU/memory/disk, ประเภท provisioning, โหมด IP (DHCP / static: netmask·gateway·DNS), concurrency |
| ④ ตรวจสอบและปรับใช้ | ระบบสร้าง deployment plan ให้ตรวจทาน → ยืนยันเพื่อปรับใช้และไปที่หน้า Tasks |

**หน้า Tasks (/tasks)**: แสดง operation ปัจจุบันและเปอร์เซ็นต์การโคลนของแต่ละ VM แบบเรียลไทม์; เมื่อล้มเหลวจะแสดง**สาเหตุที่จำแนกประเภทพร้อมวิธีแก้** (เช่น "host ที่เลือกไม่ได้ mount datastore นี้") พร้อม error ดิบ; กรองตามชื่อ/สถานะ/เวลาสร้าง; ลบแบตช์และ task เก่าที่เสร็จแล้วได้

**หน้า Settings (/settings)**: บันทึก credentials ของ vCenter (รหัสผ่านเข้ารหัสด้วย Fernet), ทดสอบการเชื่อมต่อด้วยคลิกเดียว

> `/advanced` คือฟอร์มเต็มสำหรับผู้ที่คุ้นเคยกับการตั้งค่าแบบ YAML; ฟังก์ชันเทียบเท่ากับ wizard

### CLI (core ชุดเดียวกัน)

```bash
vsphere-auto creds add --name prod-vc --host 10.0.0.10 --username administrator@vsphere.local
vsphere-auto creds test prod-vc
vsphere-auto discover --creds prod-vc                 # ค้นหาทรัพยากร
cp config/config.example.yaml my.yaml                 # แก้ไข spec ของคุณ
vsphere-auto plan --config my.yaml --creds prod-vc    # ดูตัวอย่างแบบ dry-run
vsphere-auto deploy --config my.yaml --creds prod-vc --yes
vsphere-auto serve --port 8080                        # เริ่ม web UI
```

Exit codes: `0` สำเร็จทั้งหมด / `2` สำเร็จบางส่วน / `1` ล้มเหลว — ตรวจสอบจาก script ได้ง่าย

---

## ความน่าเชื่อถือ

- **Idempotent**: รัน spec เดิมซ้ำจะข้าม VM ที่มีอยู่แล้ว (จับคู่ด้วย ชื่อ + folder) — ไม่โคลนซ้ำเด็ดขาด
- **ปลอดภัยเมื่อ crash**: หลัง service crash, task ที่ค้างสถานะ running/pending จะถูกทำเครื่องหมาย `interrupted` ตอนเริ่มระบบ และรันซ้ำได้ทันที
- **การป้องกัน IP pool**: เมื่อ pool หมดจะยกเลิกทั้งแบตช์และ rollback lease ที่จัดสรรไป — ไม่มีการปรับใช้ครึ่งๆ กลางๆ
- **การรักษาความลับ**: รหัสผ่านเข้ารหัสขณะจัดเก็บ; ค่าที่เป็นความลับถูกปกปิดจาก log, รายการงานและ inventory; ห้ามใส่รหัสผ่านแบบ plaintext ใน YAML (ใช้ `VSPHERE_PASSWORD` หรือ credential ที่บันทึกไว้)

## สาระสำคัญด้านความปลอดภัย (อ่านก่อนเปิดให้ใช้งานจากภายนอก)

1. **ตั้ง API token เสมอ**: `export VSPHERE_API_TOKEN=<random-string>` แล้วรีสตาร์ท; คำขอ API ทั้งหมดต้องส่ง token (เบราว์เซอร์จะถามครั้งเดียวตอน 401 แรกแล้วจดจำให้) หากไม่ตั้ง API จะไม่มีการยืนยันตัวตนและจะพิมพ์คำเตือนตอนเริ่มระบบ
2. **bind address เริ่มต้นคือ `0.0.0.0`**: ใช้ `VSPHERE_HOST=127.0.0.1` หากใช้เฉพาะเครื่อง; หากเข้าถึงข้ามเครือข่าย แนะนำใช้ nginx/caddy (TLS + auth) ข้างหน้า และจำกัดพอร์ต 8080 ด้วย firewall
3. **การตรวจสอบ TLS ของ vCenter ปิดอยู่ในปัจจุบัน** (เพื่อความเข้ากันได้กับใบรับรอง self-signed ของ 6.7) — โปรดให้เครือข่าย management ไว้วางใจได้

## ตัวแปรสภาพแวดล้อม

| ตัวแปร | คำอธิบาย |
|--------|----------|
| `VSPHERE_STATE_DIR` | ไดเรกทอรี state ขณะรัน (credentials DB / task DB / IP pools / key), ค่าเริ่มต้น `<repo>/state` |
| `VSPHERE_API_TOKEN` | เมื่อตั้งค่า จะเปิดใช้การยืนยันตัวตน API (header Bearer / X-API-Token) |
| `VSPHERE_HOST` | bind address ของเว็บ, ค่าเริ่มต้น `0.0.0.0` |
| `VSPHERE_AUTO_KEY` | Fernet key ภายนอก (ค่าเริ่มต้นสร้างอัตโนมัติที่ `state/.fernet.key`; **โปรดสำรองไว้ — หาก key สูญหาย รหัสผ่านที่บันทึกไว้จะกู้คืนไม่ได้**) |
| `VSPHERE_PASSWORD` | รหัสผ่าน vCenter สำหรับ flow CLI/web ที่ไม่ใช้ credential ที่บันทึกไว้ |
| `VSPHERE_DEBUG` | `1` เปิด debug logging (บังคับ bind loopback only) |

---

## คำถามที่พบบ่อย

**เชื่อมต่อ vCenter ไม่ได้ (โดยเฉพาะ 6.7)?**
รัน `vsphere-auto creds test <ชื่อ>` เพื่อดู error ที่แท้จริง ปัญหา TLS 1.0 / legacy cipher บน host 6.7 รุ่นเก่า client จัดการให้อัตโนมัติแล้ว; หากยังล้มเหลว ตรวจสอบการเข้าถึงพอร์ต 443 (`curl -vk https://<vc>:443/sdk`) และ proxy/firewall รัน `VSPHERE_DEBUG=1 bash start.sh` เพื่อดู stack trace เต็ม

**Static IP / hostname ไม่ถูกตั้งค่า?**
ตรวจสอบว่า VMware Tools รันอยู่ในเทมเพลต; เทมเพลต cloud-init ต้องปิดการตั้งค่าเครือข่ายตามที่อธิบายด้านบน การปรับใช้แบบ DHCP ไม่มีการ injection โดยดีไซน์

**Discover แสดง DC/clusters = 0 เมื่อต่อ ESXi ตรง?**
เป็นเรื่องปกติ — ESXi ไม่มีโครงสร้าง datacenter/cluster; เลือก host และ datastore ตรงๆ ได้เลย

**จะเปลี่ยน (rotate) รหัสผ่านได้อย่างไร?**
แก้ไข credential ในหน้า Settings แล้วใส่รหัสผ่านใหม่ (เว้นว่าง = คงค่าเดิม) หรือรัน `vsphere-auto creds update <id> --password '***'`

---

## การสนับสนุนและข้อเสนอแนะ

พบข้อผิดพลาดหรือมีข้อเสนอแนะฟีเจอร์? กรุณา[เปิด issue](https://github.com/ilysom0611/vsphere-auto/issues)

---

## License

โปรเจกต์นี้เผยแพร่ภายใต้ **[MIT License](LICENSE)** — ใช้งาน แก้ไข แจกจ่ายและใช้เชิงพาณิชย์ได้โดยเสรี ข้อกำหนดเดียวคือต้องคงหมายเหตุลิขสิทธิ์และข้อความ license เดิมไว้ (ให้บริการ "ตามสภาพ" ไม่มีการรับประกัน)

Dependencies ของบุคคลที่สามทั้งหมดใช้ permissive licenses ที่เข้ากันได้กับ MIT — ไม่มีข้อผูกพันแบบ copyleft ไม่ต้องขออนุญาตเพิ่มสำหรับการใช้งานเชิงพาณิชย์:

| Dependency | License |
|------------|---------|
| pyvmomi, requests, cryptography | Apache-2.0 (cryptography เป็น dual Apache-2.0 / BSD-3) |
| flask, jinja2 | BSD-3-Clause |
| waitress | Zope Public License 2.1 (ZPL-2.1) |
| pyyaml, pydantic, typer, rich, tenacity, hatchling | MIT |
