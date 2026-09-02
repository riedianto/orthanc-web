"""
DICOM C-STORE SCP (Receiver) - pintu masuk citra, forward ke Orthanc.

Pintu masuk citra utama di port 4242. Menerima C-STORE dari modalitas, mencatat
metadata ke PostgreSQL, lalu meneruskan (forward) via C-STORE ke Orthanc sebagai
satu-satunya penyimpan raw (StorageDirectory bind -> /storage/dicom).

Orthanc tetap melayani DICOMWeb / WebViewer / Explorer2 / worklist dan pipeline
SATUSEHAT DCMROUTER; service ini hanya menjadi gerbang masuk + pencatatan metadata.
"""

import os
import sys
import json
import glob
import uuid
import datetime
import psycopg2
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pynetdicom import AE, evt, debug_logger
from pynetdicom.sop_class import (
    Verification,
    ModalityWorklistInformationFind,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    CTImageStorage,
    MRImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    DigitalMammographyXRayImageStorageForPresentation,
)

XRAY_MODALITIES = frozenset({"DR", "CR", "DX"})

debug_logger()

# ---------------------------------------------------------------------------
# Konfigurasi environment
# ---------------------------------------------------------------------------
RECEIVER_AE_TITLE = os.environ.get("RECEIVER_AE_TITLE", "ORTHANC")
DICOM_STORE_PORT = int(os.environ.get("DICOM_STORE_PORT", "4242"))

# Tujuan forward C-STORE -> Orthanc (SCU)
ORTHANC_DICOM_HOST = os.environ.get("ORTHANC_DICOM_HOST", "orthanc")
ORTHANC_DICOM_PORT = int(os.environ.get("ORTHANC_DICOM_PORT", "4242"))
ORTHANC_DICOM_AET = os.environ.get("ORTHANC_DICOM_AET", "ORTHANC")

WORKLISTS_DIR = os.environ.get("WORKLISTS_DIR", "/var/lib/orthanc/worklists")

# PostgreSQL metadata (database orthanc yang sudah ada)
PG_HOST = os.environ.get("POSTGRES_HOST", "orthanc-postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB = os.environ.get("POSTGRES_DB", "orthanc")
PG_USER = os.environ.get("POSTGRES_USER", "orthanc")
PG_PWD = os.environ.get("POSTGRES_PASSWORD", "Orthanc2024!")


# ---------------------------------------------------------------------------
# Bantuan koneksi database
# ---------------------------------------------------------------------------
def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PWD,
    )


def ensure_schema():
    """Buat tabel metadata bila belum ada (auto-migrate, idempotent)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS bridge_radiology_orders (
        accession_number     TEXT PRIMARY KEY,
        patient_id           TEXT,
        patient_name         TEXT,
        birth_date           DATE,
        gender               TEXT,
        modality             TEXT,
        procedure_name       TEXT,
        order_datetime       TIMESTAMP,
        noreg                TEXT,
        status               TEXT,
        created_at           TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS bridge_dicom_studies (
        id                   SERIAL PRIMARY KEY,
        accession_number     TEXT,
        study_instance_uid   TEXT UNIQUE,
        series_instance_uid  TEXT,
        sop_instance_uid     TEXT,
        series_count         INTEGER DEFAULT 1,
        sop_count            INTEGER DEFAULT 1,
        modality             TEXT,
        satusehat_status     TEXT DEFAULT 'PENDING',
        received_at          TIMESTAMP DEFAULT NOW()
    );
    """
    conn = None
    try:
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
        cur.close()
        print("[DB] Skema bridge_radiology_orders / bridge_dicom_studies siap.", flush=True)
    except Exception as e:
        print(f"[DB ERROR] Gagal buat skema: {e}", file=sys.stderr, flush=True)
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Noreg lookup dari worklist JSON (dibuat polling engine dari HMS)
# ---------------------------------------------------------------------------
def get_noreg_for_accession(accession):
    try:
        safe_acc = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in accession)
        fpath = os.path.join(WORKLISTS_DIR, f"order_{safe_acc}.json")
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            noreg = str(data.get("noreg", "")).strip()
            if noreg:
                return noreg
    except Exception as e:
        print(f"[NOREG LOOKUP ERROR] {accession}: {e}", file=sys.stderr, flush=True)
    return "NOREG-UNKNOWN"


# ---------------------------------------------------------------------------
# Rekam metadata ke PostgreSQL
# ---------------------------------------------------------------------------
def write_to_bridge_study(accession, study_uid, series_uid, sop_uid, modality):
    conn = None
    try:
        conn = get_pg_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM bridge_dicom_studies WHERE study_instance_uid = %s",
            (study_uid,),
        )
        row = cur.fetchone()

        if not row:
            cur.execute(
                """
                INSERT INTO bridge_dicom_studies
                    (accession_number, study_instance_uid, series_instance_uid,
                     sop_instance_uid, series_count, sop_count, modality, satusehat_status)
                VALUES (%s, %s, %s, %s, 1, 1, %s, 'PENDING')
                """,
                (accession, study_uid, series_uid, sop_uid, modality),
            )
        else:
            cur.execute(
                "UPDATE bridge_dicom_studies SET sop_count = sop_count + 1 "
                "WHERE study_instance_uid = %s",
                (study_uid,),
            )

        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[BRIDGE DB ERROR] Gagal menulis ke bridge_dicom_studies: {e}",
              file=sys.stderr, flush=True)
    finally:
        if conn:
            conn.close()


def upsert_radiology_order(accession, ds):
    conn = None
    try:
        conn = get_pg_conn()
        cur = conn.cursor()

        patient_name = str(ds.get("PatientName", "UNKNOWN")).strip()
        patient_id = str(ds.get("PatientID", "")).strip()
        raw_modality = str(ds.get("Modality", "DX")).strip().upper()
        modality = "DX" if raw_modality in ("DR", "CR", "DX") else (raw_modality or "DX")

        birth_date_raw = str(ds.get("PatientBirthDate", "")).strip()
        birth_date = None
        if len(birth_date_raw) == 8 and birth_date_raw.isdigit():
            birth_date = f"{birth_date_raw[:4]}-{birth_date_raw[4:6]}-{birth_date_raw[6:8]}"

        dicom_sex = str(ds.get("PatientSex", "O")).strip().upper()
        gender = dicom_sex if dicom_sex in ("M", "F") else "O"

        noreg = get_noreg_for_accession(accession)

        cur.execute(
            """
            INSERT INTO bridge_radiology_orders
                (accession_number, patient_id, patient_name, birth_date, gender,
                 modality, procedure_name, order_datetime, noreg, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, 'SCAN_COMPLETED')
            ON CONFLICT (accession_number) DO UPDATE
              SET status = 'SCAN_COMPLETED', modality = EXCLUDED.modality,
                  noreg = EXCLUDED.noreg
            """,
            (accession, patient_id, patient_name, birth_date, gender, modality,
             "Pemeriksaan Radiologi", noreg),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[BRIDGE DB ERROR] Gagal upsert radiology_orders: {e}",
              file=sys.stderr, flush=True)
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Forward C-STORE ke Orthanc (SCU)
# ---------------------------------------------------------------------------
def forward_to_orthanc(ds, file_meta, sop_class_uid):
    """Kirim instance DICOM ke Orthanc sebagai SCU. Orthanc yang menyimpan raw."""
    ae = AE(ae_title=RECEIVER_AE_TITLE)
    # Wajib daftarkan presentation context yang diminta sebelum associate
    ae.add_requested_context(sop_class_uid)
    assoc = ae.associate(ORTHANC_DICOM_HOST, ORTHANC_DICOM_PORT,
                         ae_title=ORTHANC_DICOM_AET)
    if not assoc.is_established:
        print("[FORWARD ERROR] Gagal associate ke Orthanc "
              f"{ORTHANC_DICOM_HOST}:{ORTHANC_DICOM_PORT} (AET {ORTHANC_DICOM_AET})",
              file=sys.stderr, flush=True)
        return False

    ds.file_meta = file_meta
    status = assoc.send_c_store(ds)
    ok = status and status.Status in (0x0000, 0x0001)
    if not ok:
        print(f"[FORWARD ERROR] C-STORE ke Orthanc gagal, status={status}",
              file=sys.stderr, flush=True)
    else:
        print("[FORWARD OK] Diteruskan ke Orthanc.", flush=True)

    assoc.release()
    return ok


# ---------------------------------------------------------------------------
# Worklist (MWL) - melayani C-FIND dari modalitas berdasarkan order worklist
# ---------------------------------------------------------------------------
def pad_dicom_pn(value: str, width: int = 32) -> str:
    """Padding PN: 1 kata -> pad ke 32; multi-kata -> +8 caret setelah suffix."""
    if not value:
        return "^" * width
    if len(value) >= width and "," not in value:
        return value[:width]
    if "," in value:
        base = value
        main = value.split(",", 1)[0]
        word_count = main.count("^") + 1
        if word_count <= 2:
            return base + ("^" * max(0, width - len(base)))
        return base + ("^" * 8)
    return value + ("^" * max(0, width - len(value)))


def format_simrs_physician_name(name: str, max_component_len: int = 64) -> str:
    """Nama dokter perujuk: pertahankan prefix DR. seperti worklist Orthanc."""
    if not name:
        return ""
    text = " ".join(str(name).strip().split()).upper()
    if not text.startswith("DR"):
        text = f"DR. {text}" if not text.startswith("DR.") else text
    elif text.startswith("DR ") and not text.startswith("DR. "):
        text = "DR. " + text[3:]

    if "," in text:
        name_part, suffix_part = text.split(",", 1)
        words = name_part.split()
        if len(words) <= 1:
            formatted = words[0][:max_component_len] if words else ""
        else:
            formatted = "^".join(w[:max_component_len] for w in words)
        suffix_part = suffix_part.strip()[:max_component_len]
        result = f"{formatted},{suffix_part}" if suffix_part else formatted
    else:
        words = text.split()
        result = words[0][:max_component_len] if len(words) == 1 else "^".join(w[:max_component_len] for w in words)

    if "," in result and not result.split(",", 1)[1].startswith("^"):
        main, cred = result.split(",", 1)
        result = f"{main},^{cred.lstrip('^')}"

    return pad_dicom_pn(result, 64)


def mwl_response_modality(stored_modality: str) -> str:
    """Modality di respons worklist: plain X-ray selalu DX (standar DICOM)."""
    raw = (stored_modality or "").strip().upper()
    if raw in XRAY_MODALITIES:
        return "DX"
    return raw or "DX"


def build_study_instance_uid(accession_number: str) -> str:
    """StudyInstanceUID deterministik per accession (valid PS 3.5)."""
    u = uuid.uuid5(uuid.NAMESPACE_DNS, f"orthanc.mwl.{accession_number}")
    high = (u.int >> 64) % 1000000000000000 + 1
    low = (u.int & ((1 << 64) - 1)) % 1000000000000000 + 1
    return f"1.2.410.200067.100.1.{high}.{low}"


def load_worklist_orders():
    """Membaca order worklist (order_*.json) dari WORKLISTS_DIR."""
    orders = []
    if not os.path.isdir(WORKLISTS_DIR):
        return orders
    for filepath in glob.glob(os.path.join(WORKLISTS_DIR, "order_*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                orders.append(json.load(f))
        except Exception as e:
            print(f"[MWL] Gagal membaca order {filepath}: {e}", file=sys.stderr, flush=True)
    return orders


def handle_c_find(event):
    """Handler C-FIND untuk Modality Worklist (MWL)."""
    request_ds = event.identifier
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {RECEIVER_AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima C-FIND MWL...", flush=True)

    patient_id_query = ""
    patient_name_query = ""
    accession_query = ""
    modality_query = ""
    start_date_query = ""
    station_ae_query = ""

    if 'PatientID' in request_ds and request_ds.PatientID:
        patient_id_query = str(request_ds.PatientID).replace("*", "").strip().lower()
    if 'PatientName' in request_ds and request_ds.PatientName:
        patient_name_query = str(request_ds.PatientName).replace("*", "").strip().lower()
    if 'AccessionNumber' in request_ds and request_ds.AccessionNumber:
        accession_query = str(request_ds.AccessionNumber).replace("*", "").strip().lower()
    if 'ScheduledProcedureStepSequence' in request_ds and request_ds.ScheduledProcedureStepSequence:
        spss = request_ds.ScheduledProcedureStepSequence[0]
        if 'Modality' in spss and spss.Modality:
            modality_query = str(spss.Modality).strip().upper()
        if 'ScheduledProcedureStepStartDate' in spss and spss.ScheduledProcedureStepStartDate:
            start_date_query = str(spss.ScheduledProcedureStepStartDate).strip()
        if 'ScheduledStationAETitle' in spss and spss.ScheduledStationAETitle:
            station_ae_query = str(spss.ScheduledStationAETitle).strip()

    print(f"MWL Kriteria - PatientID: '{patient_id_query}', PatientName: '{patient_name_query}', "
          f"Accession: '{accession_query}', Modality: '{modality_query}', "
          f"Date: '{start_date_query}', StationAE: '{station_ae_query}'", flush=True)

    # Rentang tanggal query (format DICOM "YYYYMMDD-YYYYMMDD" atau "YYYYMMDD")
    date_start = ""
    date_end = ""
    if start_date_query:
        date_start = start_date_query[:8] if len(start_date_query) >= 8 else start_date_query
        date_end = start_date_query[9:17] if len(start_date_query) > 8 else date_start

    matching_orders = []
    for order in load_worklist_orders():
        p_id = str(order.get("patientId", "")).strip().lower()
        p_name = str(order.get("patientName", "")).strip().lower()
        acc = str(order.get("accessionNumber", "")).strip().lower()
        mod = str(order.get("modality", "")).strip().upper()

        if mod == "CT":
            continue

        if patient_id_query and patient_id_query not in p_id:
            continue
        if patient_name_query and patient_name_query not in p_name:
            continue
        if accession_query and accession_query not in acc:
            continue
        if modality_query:
            if modality_query in XRAY_MODALITIES:
                if mod not in XRAY_MODALITIES:
                    continue
            elif mod != modality_query:
                continue

        sched_date = str(order.get("scheduledDate", "")).strip()[:10].replace("-", "")
        if date_start and sched_date:
            if sched_date < date_start or sched_date > date_end:
                continue

        matching_orders.append(order)

    print(f"Ditemukan {len(matching_orders)} order worklist yang cocok.", flush=True)

    for order in matching_orders:
        if event.is_cancelled:
            yield 0xFE00, None
            return

        ds = Dataset()
        ds.SpecificCharacterSet = 'ISO_IR 100'
        ds.AccessionNumber = str(order.get("accessionNumber", ""))
        ds.PatientID = str(order.get("patientId", ""))
        ds.PatientName = str(order.get("patientName", ""))

        birth_date = str(order.get("patientBirthDate", "")).replace("-", "").replace("/", "")
        ds.PatientBirthDate = birth_date if len(birth_date) == 8 else ""
        ds.PatientSex = str(order.get("patientSex", "M"))

        doc_name = str(order.get("doctorName", "DR. SIMRS PHYSICIAN"))
        ds.ReferringPhysicianName = format_simrs_physician_name(doc_name)
        ds.InstitutionName = str(order.get("institutionName", "RSU ARTHA MEDICA"))
        ds.StudyInstanceUID = build_study_instance_uid(ds.AccessionNumber)

        sps_ds = Dataset()
        sps_ds.ScheduledStationAETitle = (
            station_ae_query or str(order.get("scheduledAet", "")) or event.assoc.requestor.ae_title or RECEIVER_AE_TITLE
        )
        now = datetime.datetime.now()
        sps_ds.ScheduledProcedureStepStartDate = now.strftime("%Y%m%d")
        sps_ds.ScheduledProcedureStepStartTime = now.strftime("%H%M%S")
        sps_ds.Modality = mwl_response_modality(str(order.get("modality", "DX")))
        sps_ds.ScheduledPerformingPhysicianName = ""
        sps_ds.ScheduledProcedureStepDescription = str(order.get("procedureDescription", "Pemeriksaan Radiologi"))
        sps_ds.ScheduledProcedureStepID = ds.AccessionNumber[:16]
        sps_ds.ScheduledStationName = "ORTHANC_STATION"
        sps_ds.ScheduledProcedureStepLocation = "RADIOLOGI_DEPT"

        ds.ScheduledProcedureStepSequence = Sequence([sps_ds])
        ds.RequestedProcedureID = ds.AccessionNumber[:16]
        ds.RequestedProcedureDescription = str(order.get("procedureDescription", "Pemeriksaan Radiologi"))

        print(f"-> MWL respons: {ds.AccessionNumber} | PatientID={ds.PatientID}", flush=True)
        yield 0xFF00, ds

    yield 0x0000, None


# ---------------------------------------------------------------------------
# Handler C-STORE
# ---------------------------------------------------------------------------
def handle_c_store(event):
    ds = event.dataset
    sop_class = ds.SOPClassUID
    sop_instance = ds.SOPInstanceUID

    accession = ds.get("AccessionNumber", None)
    study_uid = ds.get("StudyInstanceUID", None)
    series_uid = ds.get("SeriesInstanceUID", None)
    modality = str(ds.get("Modality", "DX")).strip().upper() or "DX"

    if accession:
        accession = str(accession).strip()

    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {RECEIVER_AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima berkas C-STORE...",
          flush=True)

    # Validasi 1: AccessionNumber wajib ada (sesuai panduan Kemenkes)
    if not accession:
        print("[C-STORE REJECTED] Accession Number (0008,0050) kosong! "
              f"SOPInstanceUID: {sop_instance}", file=sys.stderr, flush=True)
        return 0xA700

    # Validasi 2: StudyInstanceUID & SeriesInstanceUID harus ada
    if not study_uid or not series_uid:
        print(f"[C-STORE REJECTED] Data DICOM tidak lengkap! "
              f"Accession: {accession}, Study: {study_uid}, Series: {series_uid}.",
              file=sys.stderr, flush=True)
        return 0xA700

    meta = event.file_meta

    try:
        # 1. Forward ke Orthanc (penyimpanan raw utama)
        ok = forward_to_orthanc(ds, meta, sop_class)
        if not ok:
            return 0xC001

        # 2. Catat metadata ke PostgreSQL
        write_to_bridge_study(accession, study_uid, series_uid, sop_instance, modality)
        upsert_radiology_order(accession, ds)

        # Catatan: worklist order auto-cleared oleh Orthanc simrs_notifier.py
        # (OnStoredInstance) setelah instance disimpan.

        print(f"[ARCHIVE SUCCESS] Berkas {sop_instance} diteruskan & tercatat.", flush=True)
        return 0x0000
    except Exception as e:
        print(f"[ARCHIVE ERROR] Gagal memproses C-STORE: {e}", file=sys.stderr, flush=True)
        return 0xC001


def handle_echo(event):
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {RECEIVER_AE_TITLE}]"
    print(f"{datetime.datetime.now()} {logger_prefix} Menerima C-ECHO Ping...", flush=True)
    return 0x0000


def handle_assoc_requested(event):
    assoc = event.assoc
    calling = assoc.requestor.ae_title
    print(f"{datetime.datetime.now()} [DICOM] A-ASSOCIATE-RQ: {calling} -> {RECEIVER_AE_TITLE}",
          flush=True)


def start_server():
    ae = AE(ae_title=RECEIVER_AE_TITLE)
    ae.require_called_aet = False
    ae.maximum_pdu_size = 16384

    # C-ECHO
    ae.add_supported_context(Verification)
    # C-FIND Worklist (MWL)
    ae.add_supported_context(ModalityWorklistInformationFind)
    # C-STORE: DX/CR, CT, MR, US, MG
    ae.add_supported_context(DigitalXRayImageStorageForPresentation)
    ae.add_supported_context(DigitalXRayImageStorageForProcessing)
    ae.add_supported_context(ComputedRadiographyImageStorage)
    ae.add_supported_context(CTImageStorage)
    ae.add_supported_context(MRImageStorage)
    ae.add_supported_context(UltrasoundImageStorage)
    ae.add_supported_context(UltrasoundMultiFrameImageStorage)
    ae.add_supported_context(DigitalMammographyXRayImageStorageForPresentation)

    handlers = [
        (evt.EVT_REQUESTED, handle_assoc_requested),
        (evt.EVT_C_STORE, handle_c_store),
        (evt.EVT_C_ECHO, handle_echo),
        (evt.EVT_C_FIND, handle_c_find),
    ]

    ensure_schema()

    print(f"Menjalankan DICOM C-STORE & MWL SCP [{RECEIVER_AE_TITLE}] port {DICOM_STORE_PORT} "
          f"(forward -> Orthanc {ORTHANC_DICOM_HOST}:{ORTHANC_DICOM_PORT} AET "
          f"{ORTHANC_DICOM_AET})...", flush=True)
    ae.start_server(("", DICOM_STORE_PORT), block=True, evt_handlers=handlers)


if __name__ == "__main__":
    start_server()
