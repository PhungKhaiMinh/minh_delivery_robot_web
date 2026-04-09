#!/usr/bin/env python3
"""
Đẩy toàn bộ dữ liệu từ thư mục JSON local (data/collections) lên Cloud Firestore.

Chạy MỘT LẦN sau khi đã tạo project Firebase + file service account.

Cách dùng (từ thư mục gốc dự án):
  export FIREBASE_CREDENTIALS_PATH=/đường/dẫn/serviceAccount.json
  export USE_FIRESTORE=1   # hoặc không cần — script tự init Firebase
  python scripts/migrate_local_to_firestore.py

Lưu ý: không commit file serviceAccount.json lên Git.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Thêm thư mục gốc project vào path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_FIRESTORE", "0")  # tránh db_service init Firestore trước khi migrate

import firebase_admin
from firebase_admin import credentials, firestore

COLLECTIONS = ("users", "bookings", "robots")


def main() -> None:
    cred_path = (
        os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )
    if not cred_path or not Path(cred_path).is_file():
        print("Thiếu file khóa: đặt FIREBASE_CREDENTIALS_PATH hoặc GOOGLE_APPLICATION_CREDENTIALS.")
        sys.exit(1)

    data_dir = ROOT / "data" / "collections"
    if not data_dir.is_dir():
        print(f"Không thấy thư mục dữ liệu: {data_dir}")
        sys.exit(1)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(cred_path))
    client = firestore.client()

    total = 0
    for name in COLLECTIONS:
        folder = data_dir / name
        if not folder.is_dir():
            print(f"[skip] Không có collection: {name}")
            continue
        col = client.collection(name)
        for fp in sorted(folder.glob("*.json")):
            doc_id = fp.stem
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[lỗi] {fp}: {e}")
                continue
            data.pop("_id", None)
            col.document(doc_id).set(data)
            print(f"[ok] {name}/{doc_id}")
            total += 1

    print(f"\nXong. Đã ghi {total} document lên Firestore.")


if __name__ == "__main__":
    main()
