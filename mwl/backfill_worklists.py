"""
Backfill worklist: buat file .wl untuk semua order_*.json yang belum punya .wl.

Digunakan satu kali ketika ada order lama (termasuk CT Scan) yang hanya memiliki
file .json sehingga tidak tampil di menu Worklists Orthanc Explorer 2 (plugin
Worklists milik Orthanc hanya membaca file .wl).

Menggunakan fungsi save_order_wl() yang sama dengan polling engine, sehingga
format file .wl identik dengan yang sudah berjalan.
"""

import os
import sys
import json

from polling_engine import save_order_wl, load_blacklist

WORKLISTS_DIR = os.environ.get("WORKLISTS_DIR", "/var/lib/orthanc/worklists")


def main():
    if not os.path.isdir(WORKLISTS_DIR):
        print(f"[Backfill] Folder worklist tidak ada: {WORKLISTS_DIR}", file=sys.stderr)
        return 1

    dismissed = load_blacklist()
    created = 0
    skipped_existing = 0
    errors = 0
    ct_created = 0

    for fname in sorted(os.listdir(WORKLISTS_DIR)):
        if not (fname.startswith("order_") and fname.endswith(".json")):
            continue

        acc = fname[len("order_"):-len(".json")]
        safe_acc = acc.replace("/", "_").replace("\\", "_").replace(" ", "_")

        if acc in dismissed:
            continue

        wl_path = os.path.join(WORKLISTS_DIR, f"order_{safe_acc}.wl")
        if os.path.exists(wl_path):
            skipped_existing += 1
            continue

        try:
            with open(os.path.join(WORKLISTS_DIR, fname), "r", encoding="utf-8") as f:
                order = json.load(f)
        except Exception as e:
            print(f"[Backfill] Gagal baca {fname}: {e}", file=sys.stderr)
            errors += 1
            continue

        save_order_wl(acc, order)

        if os.path.exists(wl_path):
            created += 1
            if order.get("modality") == "CT":
                ct_created += 1
            print(f"[Backfill] Buat {wl_path} | modality={order.get('modality')} | acc={acc}",
                  flush=True)
        else:
            print(f"[Backfill] GAGAL buat {wl_path} untuk {fname}", file=sys.stderr)
            errors += 1

    print(f"[Backfill] Selesai: {created} .wl dibuat ({ct_created} CT), "
          f"{skipped_existing} sudah ada, {errors} error.", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())