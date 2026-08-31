# SIMRS Notification Script for Orthanc (Python)
# Intercepts OnStoredInstance callback, notifies SIMRS API, and auto-clears completed worklist

import os
import json
import urllib.request

try:
    import orthanc

    def OnStoredInstance(dicom, instanceId):
        try:
            tags = json.loads(orthanc.GetInstanceTags(instanceId))
            webhook_url = os.environ.get("SIMRS_WEBHOOK_URL", "http://simrs.local/api/radiology/notify-stored")
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

        except Exception as e:
            orthanc.LogError(f"SIMRS Webhook notification error: {str(e)}")

    orthanc.RegisterOnStoredInstanceCallback(OnStoredInstance)
except ImportError:
    pass
