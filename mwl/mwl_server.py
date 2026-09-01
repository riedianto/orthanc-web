import os
import sys
import json
import glob
import threading
import datetime
from io import BytesIO
import requests
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pynetdicom import AE, evt, debug_logger, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import ModalityWorklistInformationFind, Verification, _STORAGE_CLASSES

# Debug logger enabled for troubleshooting DICOM associations
debug_logger()

AE_TITLE = os.environ.get("MWL_AE_TITLE", "ORTHANC")
MWL_PORT = int(os.environ.get("MWL_PORT", "4242"))
WORKLISTS_DIR = os.environ.get("WORKLISTS_DIR", "/var/lib/orthanc/worklists")

XRAY_MODALITIES = frozenset({"DR", "CR", "DX"})



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
    """Nama dokter perujuk: pertahankan prefix DR. seperti worklist Orthanc SIMDUDICOM."""
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


import uuid

def build_study_instance_uid(accession_number: str) -> str:
    """
    Generate valid DICOM StudyInstanceUID compliant with PS 3.5 (no leading zeros in components).
    Format: 1.2.410.200067.100.1.<high>.<low>
    """
    u = uuid.uuid5(uuid.NAMESPACE_DNS, f"orthanc.mwl.{accession_number}")
    high = (u.int >> 64) % 1000000000000000 + 1
    low = (u.int & ((1 << 64) - 1)) % 1000000000000000 + 1
    return f"1.2.410.200067.100.1.{high}.{low}"


def load_worklist_orders():
    """Membaca file order worklist dari folder WORKLISTS_DIR."""
    orders = []
    if not os.path.exists(WORKLISTS_DIR):
        return orders

    json_files = glob.glob(os.path.join(WORKLISTS_DIR, "*.json"))
    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                orders.append(data)
        except Exception as e:
            print(f"[MWL] Gagal membaca order {filepath}: {e}", file=sys.stderr)
    return orders


def handle_c_find(event):
    """
    Handler untuk DICOM C-FIND MWL request.
    """
    request_ds = event.identifier
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima C-FIND Query...")

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

    print(f"Kriteria Query - PatientID: '{patient_id_query}', PatientName: '{patient_name_query}', "
          f"Accession: '{accession_query}', Modality: '{modality_query}', Date: '{start_date_query}', "
          f"StationAE: '{station_ae_query}'")

    raw_orders = load_worklist_orders()
    matching_orders = []

    for order in raw_orders:
        p_id = str(order.get("patientId", "")).strip().lower()
        p_name = str(order.get("patientName", "")).strip().lower()
        acc = str(order.get("accessionNumber", "")).strip().lower()
        mod = str(order.get("modality", "")).strip().upper()

        # CT Scan tidak boleh masuk ke aplikasi Xmaru / DICOM MWL C-FIND
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

        matching_orders.append(order)

    print(f"Ditemukan {len(matching_orders)} order worklist yang cocok.")

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
        ds.InstitutionName = str(order.get("institutionName", os.environ.get("INSTITUTION_NAME", "RSUD SIMRS")))
        ds.StudyInstanceUID = build_study_instance_uid(ds.AccessionNumber)

        sps_ds = Dataset()
        sps_ds.ScheduledStationAETitle = (
            station_ae_query or str(order.get("scheduledAet", "")) or event.assoc.requestor.ae_title or AE_TITLE
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

        print(f"-> Mengirim respons worklist: {ds.AccessionNumber} | PatientID={ds.PatientID} | PN={ds.PatientName}")
        yield 0xFF00, ds

    yield 0x0000, None


def handle_echo(event):
    """Handler untuk DICOM C-ECHO (Ping)"""
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"{datetime.datetime.now()} {logger_prefix} Menerima C-ECHO Ping...")
    return 0x0000


def handle_c_store(event):
    """
    Handler untuk DICOM C-STORE di port 4242.
    Menerima citra DICOM dari Xmaru Pro / alat radiologi dan meneruskannya
    secara otomatis ke Orthanc PACS Server via HTTP REST API.
    """
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima C-STORE di port {MWL_PORT}...")
    try:
        ds = event.dataset
        ds.file_meta = event.file_meta

        bio = BytesIO()
        ds.save_as(bio, write_like_original=False)
        dicom_bytes = bio.getvalue()

        orthanc_url = os.environ.get("ORTHANC_URL", "http://orthanc:8090/instances")
        orthanc_user = os.environ.get("ORTHANC_USER", "orthanc")
        orthanc_pass = os.environ.get("ORTHANC_PASSWORD", "orthanc")

        print(f"[C-STORE] Meneruskan {len(dicom_bytes)} bytes ke Orthanc ({orthanc_url})...")
        res = requests.post(
            orthanc_url,
            data=dicom_bytes,
            headers={"Content-Type": "application/dicom"},
            auth=(orthanc_user, orthanc_pass),
            timeout=30
        )
        if res.status_code in (200, 201):
            print(f"[C-STORE] SUKSES meneruskan citra ke Orthanc PACS (HTTP {res.status_code})")
            return 0x0000
        else:
            print(f"[C-STORE] Orthanc menolak citra: HTTP {res.status_code} - {res.text[:200]}")
            return 0x0000
    except Exception as e:
        print(f"[C-STORE] Error memproses C-STORE di port {MWL_PORT}: {e}", file=sys.stderr)
        return 0x0110


def start_server():
    ae = AE(ae_title=AE_TITLE)
    ae.add_supported_context(ModalityWorklistInformationFind)
    ae.add_supported_context(Verification)
    for sop_uid in _STORAGE_CLASSES.values():
        ae.add_supported_context(sop_uid, ALL_TRANSFER_SYNTAXES)

    handlers = [
        (evt.EVT_C_FIND, handle_c_find),
        (evt.EVT_C_ECHO, handle_echo),
        (evt.EVT_C_STORE, handle_c_store)
    ]

    # Jalankan polling HMS SQL Server sebagai background thread
    try:
        from polling_engine import start_polling_loop
        polling_thread = threading.Thread(target=start_polling_loop, daemon=True)
        polling_thread.start()
        print("[MWL] HMS SQL Server Polling Engine dimulai sebagai background thread.")
    except Exception as e:
        print(f"[MWL] Polling Engine tidak dimulai (HMS SQL Server mungkin belum dikonfigurasi): {e}", file=sys.stderr)

    print(f"Menjalankan SIMDUDICOM MWL & C-STORE SCP Server [{AE_TITLE}] di port {MWL_PORT}...")
    ae.start_server(('', MWL_PORT), block=True, evt_handlers=handlers)


if __name__ == "__main__":
    start_server()
