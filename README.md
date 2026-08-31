# 🏥 Orthanc PACS — Integrasi HMS SIMRS Rumah Sakit

**Orthanc PACS** terintegrasi penuh dengan **HMS (Hospital Management System) SQL Server** untuk manajemen Modality Worklist (MWL) DICOM, notifikasi webhook, dan dashboard radiologi berbasis web.

> Dibangun untuk: RS yang menggunakan **HMS SIMRS** berbasis Microsoft SQL Server dan alat radiologi berbasis **DICOM** (X-Ray, CT Scan, MRI, USG).

---

## 🌟 Fitur Utama

| Fitur | Keterangan |
|-------|-----------|
| 🔄 **Polling HMS otomatis** | Tarik order radiologi dari HMS SQL Server tiap 5 detik |
| 📋 **DICOM MWL (C-FIND)** | Kirim worklist ke alat radiologi (XMARUM, dsb.) via DICOM |
| 🖥️ **Dashboard Worklist** | UI web modern untuk monitor & kelola antrean pasien |
| ⚙️ **Konfigurasi via UI** | Atur koneksi HMS, nama faskes, storage path dari browser |
| 🔔 **Webhook Notifikasi** | Notifikasi ke SIMRS saat citra diterima (C-STORE) |
| 🗑️ **Auto-cleanup** | Order hilang otomatis dari worklist setelah citra dikirim |
| 🛡️ **Blacklist** | Order yang dihapus manual tidak akan muncul kembali |
| 🔒 **Auth Proxy** | Login terpusat sebelum akses Orthanc Explorer 2 |

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────────────────┐
│                        HMS SQL Server                           │
│                  (103.167.x.x:1433 / MSSQL)                     │
│            tbl_hradio, tbl_dradio, tbl_tarif, dsb.             │
└─────────────────────────┬───────────────────────────────────────┘
                          │ polling tiap 5 detik (pyodbc)
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    orthanc-mwl (Python)                         │
│              mwl_server.py + polling_engine.py                  │
│  - Buat file .wl (binary DICOM) + .json (untuk UI)             │
│  - Simpan ke /var/lib/orthanc/worklists/                        │
└──────────┬──────────────────────────────┬───────────────────────┘
           │ DICOM C-FIND (port 4242)     │ file .json
           ▼                              ▼
┌──────────────────────┐    ┌─────────────────────────────────────┐
│  Alat Radiologi      │    │        orthanc-auth (Node.js)       │
│  (XMARUM, dsb.)      │    │  - Auth proxy (port 8090)           │
│                      │    │  - Dashboard Worklist UI             │
│  → C-STORE setelah   │    │  - REST API /api/worklists          │
│    foto selesai      │    │  - REST API /api/settings           │
└──────────┬───────────┘    └─────────────────────────────────────┘
           │ DICOM C-STORE
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   orthanc-server (Orthanc)                      │
│              DICOM server port 4242 | HTTP port 8042            │
│  - Simpan citra ke PostgreSQL                                   │
│  - Jalankan script simrs_notifier.lua saat C-STORE masuk        │
│  - Hapus .wl + .json → notifikasi webhook ke HMS               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  orthanc-postgres      │
              │  (PostgreSQL 15)       │
              │  Simpan citra DICOM    │
              └────────────────────────┘
```

---

## 📦 Prasyarat

- **Docker** & **Docker Compose** v2+
- **Port yang harus terbuka:**
  - `8090` — Orthanc Web UI & Dashboard (akses publik/LAN)
  - `4242` — DICOM port (untuk alat radiologi)
  - `5432` — PostgreSQL (internal Docker saja)
- **Akses jaringan** ke HMS SQL Server (default: port 1433)

---

## 🚀 Instalasi

### 1. Clone Repository

```bash
git clone https://github.com/riedianto/orthanc-web.git
cd orthanc-web
```

### 2. Buat File `.env`

```bash
cp .env.example .env
# atau buat manual:
nano .env
```

Isi file `.env`:

```env
# Orthanc
ORTHANC_PASSWORD=orthanc

# HMS SQL Server
HMS_SQLSERVER_HOST=103.167.236.130
HMS_SQLSERVER_PORT=1433
HMS_SQLSERVER_DB=artha_medika
HMS_SQLSERVER_USER=hmsdb
HMS_SQLSERVER_PASSWORD=password_anda

# Nama Rumah Sakit (muncul di tag DICOM InstitutionName)
INSTITUTION_NAME=RSUD Nama Rumah Sakit

# Webhook notifikasi ke SIMRS (opsional)
SIMRS_WEBHOOK_URL=http://simrs-internal/api/radiology/notify

# Polling HMS (detik)
POLL_INTERVAL_SECONDS=5
POLL_DAYS_BACK=1
```

> **Catatan:** Setelah deploy, konfigurasi bisa diubah langsung dari UI tanpa edit `.env` lagi — via menu **⚙️ Pengaturan HMS & Faskes** di sidebar.

### 3. Buat Folder Worklists

```bash
mkdir -p worklists
```

### 4. Jalankan Docker Compose

```bash
docker compose up -d --build
```

Tunggu sekitar 30 detik untuk semua service siap.

### 5. Cek Status Container

```bash
docker compose ps
```

Output yang diharapkan:
```
NAME                STATUS
orthanc-auth        running
orthanc-mwl         running
orthanc-postgres    running (healthy)
orthanc-server      running
```

---

## 🖥️ Akses Dashboard

Buka browser ke:

```
http://<IP_SERVER>:8090
```

Login dengan:
- **Username:** `orthanc`
- **Password:** sesuai `ORTHANC_PASSWORD` di `.env` (default: `orthanc`)

### Menu Sidebar:

| Menu | Fungsi |
|------|--------|
| **All local Studies** | Lihat citra DICOM yang sudah masuk |
| **Worklists** | Daftar worklist aktif (dari Orthanc Explorer 2) |
| **Worklist Dashboard** | Dashboard antrean pasien (custom UI) |
| **Settings → Pengaturan HMS & Faskes** | Konfigurasi koneksi HMS, nama faskes, storage |

---

## ⚙️ Konfigurasi dari UI (tanpa edit file)

1. Buka `http://<IP>:8090/worklist`
2. Klik **⚙️ Pengaturan System** di kanan atas
3. Atur:
   - **Koneksi HMS SQL Server** (Host, Port, Database, Username, Password)
   - **Nama Rumah Sakit / Faskes** (tampil di tag DICOM `InstitutionName`)
   - **Folder Penyimpanan Citra** (Storage Path)
   - **URL Webhook SIMRS**
   - **Interval Polling**
4. Klik **Uji Koneksi SQL** untuk test koneksi ke HMS
5. Klik **Simpan & Terapkan**

Konfigurasi disimpan ke `worklists/system_settings.json` (persisten).

---

## 🔌 Integrasi Alat Radiologi (XMARUM / DICOM)

### Konfigurasi di XMARUM / Alat Radiologi:

| Parameter | Nilai |
|-----------|-------|
| **AE Title** | `ORTHANC` |
| **Host/IP** | IP server Orthanc |
| **DICOM Port** | `4242` |
| **Query Type** | Modality Worklist (C-FIND) |

### Alur Kerja:

1. Radiografer buka XMARUM → klik **Worklist** → alat query C-FIND ke Orthanc port 4242
2. Orthanc menjawab dengan daftar order dari file `.wl` di folder `worklists/`
3. Radiografer pilih pasien → mulai foto
4. Setelah foto selesai, XMARUM kirim C-STORE ke Orthanc port 4242
5. Orthanc simpan citra ke PostgreSQL + hapus file `.wl` & `.json` → pasien hilang dari worklist
6. Webhook terkirim ke HMS (opsional)

---

## 📂 Struktur Folder

```
orthanc-web/
├── docker-compose.yml          # Konfigurasi Docker Compose
├── orthanc.json                # Konfigurasi Orthanc server
├── .env                        # Credentials (tidak masuk git)
├── .gitignore
│
├── mwl/                        # Service polling HMS → generate worklist
│   ├── Dockerfile
│   ├── mwl_server.py           # DICOM MWL server (pynetdicom)
│   ├── polling_engine.py       # Polling HMS SQL Server
│   └── requirements.txt
│
├── orthanc-auth/               # Web proxy + Dashboard UI
│   ├── Dockerfile
│   ├── server.js               # Express-like proxy server (Node.js)
│   ├── package.json
│   └── views/
│       ├── index.html          # Halaman login
│       ├── style.css           # Styling login
│       └── worklist.html       # Dashboard worklist
│
├── scripts/                    # Script Orthanc (dijalankan saat event)
│   ├── simrs_notifier.lua      # Entry point Lua (dipanggil Orthanc)
│   └── simrs_notifier.py       # Logika Python: hapus worklist + webhook
│
└── worklists/                  # Folder runtime worklist (mount Docker volume)
    ├── README.md
    ├── order_<acc>.json        # Data order per pasien (untuk UI)
    ├── order_<acc>.wl          # Binary DICOM worklist (untuk C-FIND)
    ├── completed_worklist.json # Blacklist order yang sudah selesai
    └── system_settings.json   # Konfigurasi persisten dari UI
```

> **Catatan:** File `order_*.json`, `order_*.wl`, `completed_worklist.json`, `system_settings.json`, dan `.env` tidak masuk ke git (lihat `.gitignore`).

---

## 🗃️ Tabel HMS yang Digunakan

Service polling membaca dari tabel berikut di HMS SQL Server:

| Tabel | Keterangan |
|-------|-----------|
| `tbl_hradio` | Header order radiologi (accession, rekmed, tglradio, dll.) |
| `tbl_dradio` | Detail tindakan per order (nama prosedur, kode tarif) |
| `tbl_tarif` | Master tarif / nama prosedur |
| `tbl_dokter` | Data dokter pengirim |

### Field yang Dipetakan ke DICOM:

| Field HMS | Tag DICOM | Keterangan |
|-----------|-----------|-----------|
| `noradio` | `AccessionNumber` | Nomor order radiologi |
| `rekmed` | `PatientID` | Nomor Rekam Medis |
| `namapas` | `PatientName` | Nama pasien |
| `tgllahir` | `PatientBirthDate` | Tanggal lahir |
| `jkel` | `PatientSex` | Jenis kelamin (1=L, 2=P) |
| `nadokter` | `ReferringPhysicianName` | Nama dokter pengirim |
| `tindakan` | `StudyDescription` | Nama prosedur/tindakan |

---

## 🛡️ Sistem Blacklist (Anti-Spam Worklist)

Ketika order dihapus manual dari Dashboard, accession number-nya otomatis masuk ke **blacklist** (`completed_worklist.json`). Polling engine akan skip order tersebut sehingga **tidak muncul kembali di alat radiologi**.

Blacklist juga otomatis terisi saat citra diterima via C-STORE.

---

## 🔧 Perintah Berguna

```bash
# Lihat semua log
docker compose logs -f

# Restart semua service
docker compose restart

# Rebuild setelah ada perubahan kode
docker compose up -d --build

# Cek isi worklist aktif
ls worklists/order_*.json

# Cek blacklist
cat worklists/completed_worklist.json

# Test DICOM C-FIND ke Orthanc (butuh dcm4che atau findscu)
findscu -v -S -k "0008,0050=" -k "0010,0020=" \
  <IP_SERVER> 4242 -aet WORKSTATION -aec ORTHANC
```

---

## 🐛 Troubleshooting

### Worklist tidak muncul di alat radiologi?
- Pastikan AE Title di alat = `ORTHANC`
- Pastikan port `4242` terbuka di firewall
- Cek log: `docker compose logs orthanc-mwl`

### Polling HMS gagal?
- Cek koneksi ke SQL Server: buka **Pengaturan → Uji Koneksi SQL**
- Pastikan driver ODBC 18 tersedia di container
- Cek log: `docker compose logs orthanc-mwl`

### Citra tidak masuk ke Orthanc?
- Pastikan port `4242` bisa diakses dari alat radiologi
- Cek log: `docker compose logs orthanc-server`

### Dashboard tidak bisa diakses?
- Pastikan port `8090` terbuka
- Cek log: `docker compose logs orthanc-auth`

---

## 📄 Lisensi

MIT License — bebas digunakan, dimodifikasi, dan didistribusikan untuk kepentingan rumah sakit dan fasilitas kesehatan.

---

*Dibuat dengan ❤️ untuk digitalisasi radiologi rumah sakit Indonesia.*
