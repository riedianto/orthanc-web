import os
import sys
import json
import time
import uuid
import datetime
import pyodbc

HMS_HOST = os.environ.get("HMS_SQLSERVER_HOST", "localhost")
HMS_PORT = os.environ.get("HMS_SQLSERVER_PORT", "1433")
HMS_DB   = os.environ.get("HMS_SQLSERVER_DB", "artha_medika")
HMS_USER = os.environ.get("HMS_SQLSERVER_USER", "sa")
HMS_PWD  = os.environ.get("HMS_SQLSERVER_PASSWORD", "secret")

WORKLISTS_DIR  = os.environ.get("WORKLISTS_DIR", "/var/lib/orthanc/worklists")
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
POLL_DAYS_BACK = int(os.environ.get("POLL_DAYS_BACK", "1"))


def get_hms_conn():
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={HMS_HOST},{HMS_PORT};"
        f"DATABASE={HMS_DB};"
        f"UID={HMS_USER};"
        f"PWD={HMS_PWD};"
        f"Encrypt=no;"
        f"TrustServerCertificate=yes;"
        f"Connection Timeout=10;"
    )
    return pyodbc.connect(conn_str)


def generate_accession_number():
    """Generate Accession Number unik jika noradio dari HMS kosong."""
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    suffix   = str(uuid.uuid4().int)[:4]
    return f"RAD-{date_str}-{suffix}"


def determine_modality(procedure_name: str) -> str:
    """Deteksi otomatis modality dari nama tindakan/prosedur."""
    proc = (procedure_name or "").upper()
    if "CT" in proc or "MSCT" in proc:
        return "CT"
    elif "MRI" in proc or "MAGNETIC" in proc:
        return "MR"
    elif "USG" in proc or "ULTRASOUND" in proc or "ULTRA" in proc:
        return "US"
    elif "MAMMO" in proc:
        return "MG"
    elif "PANORAMIC" in proc or "DENTAL" in proc:
        return "DX"
    elif any(k in proc for k in ("CR", "DR", "FOTO", "X-RAY", "XRAY", "THORAX", "RONTGEN")):
        return "DX"
    return "DX"


def normalize_modality(code: str) -> str:
    """Standarisasi kode modality; plain X-ray selalu DX."""
    c = (code or "").strip().upper()
    if c in ("DR", "CR", "DX"):
        return "DX"
    return c or "DX"


def load_blacklist() -> set:
    """Baca daftar accession number yang sudah di-dismiss / selesai (tidak boleh dibuat ulang)."""
    bl_file = os.path.join(WORKLISTS_DIR, "completed_worklist.json")
    try:
        if os.path.exists(bl_file):
            with open(bl_file, "r") as f:
                data = json.load(f)
                return set(data.get("dismissed", []))
    except Exception as e:
        print(f"[Polling] Gagal membaca blacklist: {e}", file=sys.stderr)
    return set()


def get_existing_accessions() -> set:
    """Baca accession number yang sudah ada di folder worklists (via nama file JSON) + blacklist."""
    existing = set()
    try:
        for f in os.listdir(WORKLISTS_DIR):
            if f.startswith("order_") and f.endswith(".json"):
                acc = f[len("order_"):-len(".json")]
                existing.add(acc)
    except Exception as e:
        print(f"[Polling] Gagal membaca folder worklists: {e}", file=sys.stderr)

    # Tambahkan blacklist agar order yang di-dismiss tidak pernah dibuat ulang
    existing.update(load_blacklist())
    return existing


def build_study_instance_uid(accession_number: str) -> str:
    """
    Generate valid DICOM StudyInstanceUID compliant with PS 3.5 (no leading zeros in components).
    Format: 1.2.410.200067.100.1.<high>.<low>
    """
    u = uuid.uuid5(uuid.NAMESPACE_DNS, f"orthanc.mwl.{accession_number}")
    high = (u.int >> 64) % 1000000000000000 + 1
    low = (u.int & ((1 << 64) - 1)) % 1000000000000000 + 1
    return f"1.2.410.200067.100.1.{high}.{low}"


def save_order_wl(acc: str, order: dict):
    """Generate binary DICOM .wl file for Orthanc core Worklists plugin."""
    try:
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.sequence import Sequence
        from pydicom.uid import ImplicitVRLittleEndian

        study_uid = build_study_instance_uid(acc)

        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.31'
        meta.MediaStorageSOPInstanceUID = study_uid
        meta.TransferSyntaxUID = ImplicitVRLittleEndian

        ds = Dataset()
        ds.file_meta = meta
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        ds.SpecificCharacterSet = 'ISO_IR 100'
        ds.AccessionNumber = str(order.get("accessionNumber", ""))
        ds.PatientID = str(order.get("patientId", ""))
        ds.PatientName = str(order.get("patientName", ""))

        birth_date = str(order.get("patientBirthDate", "")).replace("-", "").replace("/", "")
        ds.PatientBirthDate = birth_date if len(birth_date) == 8 else ""
        ds.PatientSex = str(order.get("patientSex", "M"))

        doc_name = str(order.get("doctorName", "DR. SIMRS PHYSICIAN"))
        ds.ReferringPhysicianName = doc_name
        ds.InstitutionName = str(order.get("institutionName", os.environ.get("INSTITUTION_NAME", "RSU Artha Medica")))
        ds.StudyInstanceUID = study_uid

        sps_ds = Dataset()
        sps_ds.ScheduledStationAETitle = str(order.get("scheduledAet", "MOD_XRAY"))

        now = datetime.datetime.now()
        sps_ds.ScheduledProcedureStepStartDate = now.strftime("%Y%m%d")
        sps_ds.ScheduledProcedureStepStartTime = now.strftime("%H%M%S")
        sps_ds.Modality = str(order.get("modality", "DX"))
        sps_ds.ScheduledPerformingPhysicianName = ""
        sps_ds.ScheduledProcedureStepDescription = str(order.get("procedureDescription", "Pemeriksaan Radiologi"))
        sps_ds.ScheduledProcedureStepID = ds.AccessionNumber[:16]
        sps_ds.ScheduledStationName = "ORTHANC_STATION"
        sps_ds.ScheduledProcedureStepLocation = "RADIOLOGI_DEPT"

        ds.ScheduledProcedureStepSequence = Sequence([sps_ds])
        ds.RequestedProcedureID = ds.AccessionNumber[:16]
        ds.RequestedProcedureDescription = str(order.get("procedureDescription", "Pemeriksaan Radiologi"))

        safe_acc = acc.replace("/", "_").replace("\\", "_").replace(" ", "_")
        wl_filepath = os.path.join(WORKLISTS_DIR, f"order_{safe_acc}.wl")
        ds.save_as(wl_filepath, write_like_original=False)
    except Exception as e:
        print(f"[Polling] Gagal membuat file .wl: {e}", file=sys.stderr, flush=True)


def save_order_json(acc: str, order: dict):
    """Simpan order sebagai file JSON & DICOM .wl ke WORKLISTS_DIR (CT Scan dilewati untuk file .wl agar tidak masuk Xmaru)."""
    safe_acc = acc.replace("/", "_").replace("\\", "_").replace(" ", "_")
    filename = f"order_{safe_acc}.json"
    filepath = os.path.join(WORKLISTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)

    wl_filepath = os.path.join(WORKLISTS_DIR, f"order_{safe_acc}.wl")
    if order.get("modality") == "CT":
        if os.path.exists(wl_filepath):
            try:
                os.remove(wl_filepath)
            except Exception:
                pass
        print(f"[Polling] Order disimpan: {filename} (CT Scan: file .wl dilewati agar tidak masuk Xmaru) | Pasien: {order['patientName']} | Modality: {order['modality']}", flush=True)
    else:
        save_order_wl(acc, order)
        print(f"[Polling] Order disimpan: {filename} & order_{safe_acc}.wl | Pasien: {order['patientName']} | Modality: {order['modality']}", flush=True)



def poll_new_orders():
    """
    Query HMS SQL Server untuk order radiologi baru (dari N hari terakhir),
    lalu simpan sebagai JSON worklist jika belum ada.
    Output order radiologi baru ke file JSON worklist.
    """
    conn = None
    try:
        conn = get_hms_conn()
        cursor = conn.cursor()

        # Ambil accession yang sudah ada agar tidak duplikat
        existing_accessions = get_existing_accessions()

        # Query order dari HMS
        sql = f"""
            SELECT
                hr.noradio,
                hr.rekmed,
                hr.namapas,
                hr.tgllahir,
                hr.jkel,
                dk.nadokter,
                hr.tglradio,
                t.tindakan,
                hr.noreg
            FROM
                tbl_hradio hr WITH (NOLOCK)
            LEFT JOIN
                tbl_dokter dk WITH (NOLOCK) ON hr.drperiksa = dk.kodokter
            LEFT JOIN
                tbl_dradio dr WITH (NOLOCK) ON hr.noradio = dr.noradio
            LEFT JOIN
                tbl_tarif t WITH (NOLOCK) ON dr.kodetarif = t.kodetarif
            WHERE
                hr.tglradio >= DATEADD(day, -{POLL_DAYS_BACK}, GETDATE())
        """
        cursor.execute(sql)
        rows = cursor.fetchall()

        # Gabungkan tindakan ganda per noradio
        orders_dict = {}
        for row in rows:
            noradio   = str(row[0]).strip() if row[0] else ""
            rekmed    = str(row[1]).strip() if row[1] else ""
            namapas   = str(row[2]).strip() if row[2] else "PASIEN TIDAK DIKETAHUI"
            tgllahir  = row[3]
            jkel      = str(row[4]).strip() if row[4] else "0"
            nadokter  = str(row[5]).strip() if row[5] else "Dokter Tidak Diketahui"
            tglradio  = row[6]
            tindakan  = str(row[7]).strip() if row[7] else "Pemeriksaan Radiologi"
            noreg     = str(row[8]).strip() if row[8] else ""

            acc = noradio or generate_accession_number()

            if acc in existing_accessions:
                continue

            if acc not in orders_dict:
                orders_dict[acc] = {
                    "rekmed":     rekmed,
                    "namapas":    namapas,
                    "tgllahir":   tgllahir,
                    "jkel":       jkel,
                    "nadokter":   nadokter,
                    "tglradio":   tglradio,
                    "noreg":      noreg,
                    "procedures": []
                }
            if tindakan not in orders_dict[acc]["procedures"]:
                orders_dict[acc]["procedures"].append(tindakan)

        new_count = 0
        for acc, info in orders_dict.items():
            procedure_name = ", ".join(info["procedures"])
            if len(procedure_name) > 255:
                procedure_name = procedure_name[:252] + "..."

            modality = normalize_modality(determine_modality(procedure_name))

            # Map jenis kelamin ke DICOM sex
            gender = "O"
            if info["jkel"] == "1":
                gender = "M"
            elif info["jkel"] == "2":
                gender = "F"

            # Format tanggal lahir YYYYMMDD
            birth_date_str = ""
            if info["tgllahir"]:
                try:
                    if isinstance(info["tgllahir"], (datetime.date, datetime.datetime)):
                        birth_date_str = info["tgllahir"].strftime("%Y-%m-%d")
                    else:
                        birth_date_str = str(info["tgllahir"])[:10]
                except Exception:
                    pass

            # Format tanggal order
            scheduled_date = ""
            if info["tglradio"]:
                try:
                    if isinstance(info["tglradio"], (datetime.date, datetime.datetime)):
                        scheduled_date = info["tglradio"].strftime("%Y-%m-%d %H:%M")
                    else:
                        scheduled_date = str(info["tglradio"])[:16]
                except Exception:
                    pass

            # AET mapping berdasarkan modality
            aet_map = {"CT": "MOD_CT", "MR": "MOD_MRI", "US": "MOD_USG", "MG": "MOD_MG"}
            scheduled_aet = aet_map.get(modality, "MOD_XRAY")

            order_json = {
                "filename":             f"order_{acc}.json",
                "timestamp":            int(datetime.datetime.now().timestamp() * 1000),
                "scheduledDate":        scheduled_date,
                "institutionName":      os.environ.get("INSTITUTION_NAME", "RSUD SIMRS"),
                "patientId":            info["rekmed"],
                "patientName":          info["namapas"],
                "patientSex":           gender,
                "patientBirthDate":     birth_date_str,
                "modality":             modality,
                "scheduledAet":         scheduled_aet,
                "accessionNumber":      acc,
                "procedureDescription": procedure_name,
                "doctorName":           info["nadokter"],
                "noreg":                info["noreg"],
                "source":               "HMS_SQLSERVER_POLLING"
            }

            save_order_json(acc, order_json)
            existing_accessions.add(acc)
            new_count += 1

        if new_count > 0:
            print(f"[Polling] {new_count} order baru disinkronisasi dari HMS.")

    except pyodbc.Error as e:
        print(f"[Polling] Koneksi HMS SQL Server gagal: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[Polling] Error: {e}", file=sys.stderr)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def start_polling_loop():
    """Loop polling utama, berjalan setiap POLL_INTERVAL detik."""
    os.makedirs(WORKLISTS_DIR, exist_ok=True)
    print(f"[Polling] Memulai HMS SQL Server Polling Engine (setiap {POLL_INTERVAL} detik)...")
    print(f"[Polling] Target HMS: {HMS_HOST}:{HMS_PORT}/{HMS_DB}")
    while True:
        poll_new_orders()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    start_polling_loop()
