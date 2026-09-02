#!/usr/bin/env python3
import os
import sys
import json
import urllib.request
import base64

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://localhost:8090")
ORTHANC_USER = os.environ.get("ORTHANC_USER", "orthanc")
ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "orthanc")
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/var/lib/orthanc/db")

def get_auth_header():
    creds = f"{ORTHANC_USER}:{ORTHANC_PASSWORD}"
    encoded = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}"}

def http_get(endpoint):
    url = f"{ORTHANC_URL}{endpoint}"
    req = urllib.request.Request(url, headers=get_auth_header())
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def http_delete(endpoint):
    url = f"{ORTHANC_URL}{endpoint}"
    headers = get_auth_header()
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def http_post_bytes(endpoint, data):
    url = f"{ORTHANC_URL}{endpoint}"
    headers = get_auth_header()
    headers["Content-Type"] = "application/dicom"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def main():
    print("Collecting all existing .dcm files on disk...")
    dcm_files = []
    for root, _, files in os.walk(STORAGE_DIR):
        for f in files:
            if f.endswith(".dcm"):
                dcm_files.append(os.path.join(root, f))

    print(f"Found {len(dcm_files)} .dcm files in {STORAGE_DIR}.")

    dcm_contents = []
    for fpath in dcm_files:
        try:
            with open(fpath, "rb") as f:
                dcm_contents.append((fpath, f.read()))
        except Exception as e:
            print(f"Failed to read {fpath}: {e}")

    print(f"Loaded {len(dcm_contents)} DICOM files into memory.")

    # Get current instance IDs in Orthanc and delete them to reset the DB index
    try:
        raw_instances = http_get("/instances")
        instance_ids = json.loads(raw_instances.decode("utf-8"))
        print(f"Deleting {len(instance_ids)} stale instances from Orthanc index...")
        for iid in instance_ids:
            try:
                http_delete(f"/instances/{iid}")
            except Exception as e:
                print(f"Warning deleting {iid}: {e}")
    except Exception as e:
        print(f"Error fetching/deleting instances: {e}")

    # Re-upload DICOM files to create clean database index matching new paths
    print("Re-uploading DICOM files to sync DB index with AdvancedStorage...")
    success_count = 0
    for fpath, data in dcm_contents:
        try:
            resp = http_post_bytes("/instances", data)
            result = json.loads(resp.decode("utf-8"))
            print(f" Uploaded {os.path.basename(fpath)} -> ID {result.get('ID')}")
            success_count += 1
        except Exception as e:
            print(f" Failed to upload {fpath}: {e}")

    print(f"Re-indexing complete! {success_count}/{len(dcm_contents)} instances indexed.")

if __name__ == "__main__":
    main()
