#!/usr/bin/env python3
import os
import sys
import json
import urllib.request
import re
import shutil

ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://localhost:8090")
ORTHANC_USER = os.environ.get("ORTHANC_USER", "orthanc")
ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "orthanc")
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/var/lib/orthanc/db")

def get_auth_header():
    creds = f"{ORTHANC_USER}:{ORTHANC_PASSWORD}"
    encoded = base64_str(creds)
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/dicom"}

def base64_str(s):
    import base64
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def main():
    print(f"Scanning for existing raw DICOM files in {STORAGE_DIR}...")
    if not os.path.exists(STORAGE_DIR):
        print(f"Directory {STORAGE_DIR} does not exist.")
        sys.exit(1)

    # Find files in two-hex-character subdirectories (e.g. 1a/58/...)
    hex_pattern = re.compile(r"^[0-9a-fA-F]{2}$")
    old_files = []

    for item in os.listdir(STORAGE_DIR):
        item_path = os.path.join(STORAGE_DIR, item)
        if os.path.isdir(item_path) and hex_pattern.match(item):
            for root, _, files in os.walk(item_path):
                for f in files:
                    old_files.append(os.path.join(root, f))

    print(f"Found {len(old_files)} old DICOM files in default hash subdirectories.")
    if not old_files:
        print("No old DICOM files to migrate.")
        return

    url = f"{ORTHANC_URL}/instances"
    headers = get_auth_header()

    migrated_count = 0
    for fpath in old_files:
        try:
            print(f"Migrating file: {fpath}...")
            with open(fpath, "rb") as f:
                data = f.read()

            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                print(f" Successfully imported instance (ID: {result.get('ID', 'unknown')})")
                migrated_count += 1
        except Exception as e:
            print(f" Failed to import {fpath}: {e}")

    print(f"Imported {migrated_count}/{len(old_files)} DICOM files into new storage structure.")

    # Remove old hash subdirectories
    print("Cleaning up old hash subdirectories...")
    for item in os.listdir(STORAGE_DIR):
        item_path = os.path.join(STORAGE_DIR, item)
        if os.path.isdir(item_path) and hex_pattern.match(item):
            try:
                shutil.rmtree(item_path)
                print(f" Removed old directory: {item}")
            except Exception as e:
                print(f" Could not remove {item_path}: {e}")

    print("Migration and cleanup finished successfully!")

if __name__ == "__main__":
    main()
