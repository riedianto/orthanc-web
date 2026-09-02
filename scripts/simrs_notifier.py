# SIMRS Notification Script for Orthanc (Python)
# Intercepts OnStoredInstance callback, notifies SIMRS API, and auto-clears completed worklist

import os
import json
import urllib.request

try:
    import orthanc

    def record_dismissed_worklist(worklists_dir, accession_number):
        """Catat accession yang sudah selesai ke completed_worklist.json (blacklist)."""
        bl_file = os.path.join(worklists_dir, "completed_worklist.json")
        try:
            dismissed = []
            if os.path.exists(bl_file):
                with open(bl_file, "r", encoding="utf-8") as f:
                    dismissed = json.load(f).get("dismissed", [])
            if accession_number not in dismissed:
                dismissed.append(accession_number)
                with open(bl_file, "w", encoding="utf-8") as f:
                    json.dump({"dismissed": dismissed}, f, indent=2, ensure_ascii=False)
                orthanc.LogWarning(f"Worklist {accession_number} dicatat sebagai SELESAI (tidak akan ditarik lagi)")
        except Exception as e:
            orthanc.LogError(f"Gagal catat dismissed worklist {accession_number}: {str(e)}")


    def OnStoredInstance(dicom, instanceId):
        try:
            tags = json.loads(orthanc.GetInstanceTags(instanceId))
            webhook_url = os.environ.get("SIMRS_WEBHOOK_URL", "http://192.168.188.207:8090/api/radiology/notify-stored")
            accession_number = tags.get("AccessionNumber", "")

            payload = {
                "instanceId": instanceId,
                "patientId": tags.get("PatientID", ""),
                "patientName": tags.get("PatientName", ""),
                "studyInstanceUid": tags.get("StudyInstanceUID", ""),
                "accessionNumber": accession_number,
                "modality": tags.get("Modality", ""),
                "sopInstanceUid": tags.get("SOPInstanceUID", "")
            }

            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req, timeout=5)
            orthanc.LogWarning(f"SIMRS Webhook successfully notified for instance {instanceId}")

            # Auto-remove completed worklist file from MWL directory once C-STORE is received
            if accession_number:
                safe_acc = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in accession_number)
                worklists_dir = os.environ.get("WORKLISTS_DIR", "/var/lib/orthanc/worklists")
                for ext in (".json", ".wl"):
                    fpath = os.path.join(worklists_dir, f"order_{safe_acc}{ext}")
                    if os.path.exists(fpath):
                        try:
                            os.remove(fpath)
                            orthanc.LogWarning(f"Worklist order_{safe_acc}{ext} auto-removed (SCAN_COMPLETED)")
                        except Exception:
                            pass

                # Catat ke blacklist agar polling tidak membuatnya ulang
                record_dismissed_worklist(worklists_dir, accession_number)

        except Exception as e:
            orthanc.LogError(f"SIMRS Webhook notification error: {str(e)}")

    orthanc.RegisterOnStoredInstanceCallback(OnStoredInstance)
except ImportError:
    pass