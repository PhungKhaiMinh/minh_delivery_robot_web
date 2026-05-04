"""
Firestore (Firebase) — triển khai cùng API với FirestoreOffline trong db_service.
Chỉ dùng trên server qua Firebase Admin SDK (không lộ khóa ra trình duyệt).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import FIREBASE_CREDENTIALS_PATH


def _sanitize_value(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _sanitize_value(x) for k, x in v.items() if k != "_id"}
    if isinstance(v, list):
        return [_sanitize_value(x) for x in v]
    return v


def _strip_id(data: dict) -> dict:
    out = {k: _sanitize_value(v) for k, v in data.items() if k != "_id"}
    return out


class FirestoreDocument:
    def __init__(self, doc_ref: firestore.DocumentReference):
        self._ref = doc_ref
        self.id = doc_ref.id
        self.last_set_error: str = ""

    def get(self) -> Optional[dict]:
        try:
            snap = self._ref.get()
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            data["_id"] = snap.id
            return data
        except Exception as e:
            print(f"[FIRESTORE ERROR] get {self._ref.path}: {e}")
            return None

    def set(self, data: dict, merge: bool = False) -> bool:
        self.last_set_error = ""
        try:
            clean = _strip_id(data)
            now = datetime.now(timezone.utc).isoformat()
            if merge:
                snap = self._ref.get()
                base = dict(snap.to_dict() or {}) if snap.exists else {}
                base.update(clean)
                base["_updated_at"] = now
                if "_created_at" not in base:
                    base["_created_at"] = now
                self._ref.set(base)
            else:
                clean["_updated_at"] = now
                if "_created_at" not in clean:
                    clean["_created_at"] = now
                self._ref.set(clean)
            return True
        except Exception as e:
            self.last_set_error = str(e)
            print(f"[FIRESTORE ERROR] set {self._ref.path}: {e}")
            return False

    def update(self, data: dict) -> bool:
        if not self.exists:
            print(f"[FIRESTORE WARNING] Document {self.id} không tồn tại để update.")
            return False
        try:
            patch = _strip_id(data)
            patch["_updated_at"] = datetime.now(timezone.utc).isoformat()
            self._ref.update(patch)
            return True
        except Exception as e:
            print(f"[FIRESTORE ERROR] update {self._ref.path}: {e}")
            return False

    def delete(self) -> bool:
        try:
            self._ref.delete()
            return True
        except Exception as e:
            print(f"[FIRESTORE ERROR] delete {self._ref.path}: {e}")
            return False

    @property
    def exists(self) -> bool:
        try:
            return self._ref.get().exists
        except Exception:
            return False


class FirestoreCollection:
    def __init__(self, col_ref: firestore.CollectionReference, name: str):
        self.name = name
        self._ref = col_ref

    def document(self, doc_id: str) -> FirestoreDocument:
        return FirestoreDocument(self._ref.document(doc_id))

    def add(self, data: dict, doc_id: Optional[str] = None) -> tuple[str, bool]:
        clean = _strip_id(dict(data))
        now = datetime.now(timezone.utc).isoformat()
        if "_created_at" not in clean:
            clean["_created_at"] = now
        clean["_updated_at"] = now
        try:
            if doc_id:
                ref = self._ref.document(doc_id)
                if ref.get().exists:
                    print(f"[FIRESTORE WARNING] Document {doc_id} đã tồn tại trong {self.name}.")
                    return doc_id, False
                ref.set(clean)
                return doc_id, True
            _, doc_ref = self._ref.add(clean)
            return doc_ref.id, True
        except Exception as e:
            print(f"[FIRESTORE ERROR] add {self.name}: {e}")
            return doc_id or "", False

    def get_all(self) -> list[dict]:
        try:
            out = []
            for snap in self._ref.stream():
                d = snap.to_dict() or {}
                d["_id"] = snap.id
                out.append(d)
            return out
        except Exception as e:
            print(f"[FIRESTORE ERROR] get_all {self.name}: {e}")
            return []

    def where(self, field: str, op: str, value: Any) -> list[dict]:
        """Lọc giống bản JSON local (đọc toàn collection) — dữ liệu ít thì ổn định, không cần index Firestore."""
        ops = {
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            ">": lambda a, b: a > b,
            "<": lambda a, b: a < b,
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            "in": lambda a, b: a in b,
            "contains": lambda a, b: b in a if isinstance(a, (list, str)) else False,
        }
        compare = ops.get(op)
        if compare is None:
            print(f"[FIRESTORE ERROR] Toán tử '{op}' không được hỗ trợ.")
            return []
        results = []
        for doc_data in self.get_all():
            try:
                fv = FirestoreCollection._get_nested_field(doc_data, field)
                if fv is not None and compare(fv, value):
                    results.append(doc_data)
            except (TypeError, ValueError):
                continue
        return results

    @staticmethod
    def _get_nested_field(data: dict, field: str) -> Any:
        keys = field.split(".")
        current: Any = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def count(self) -> int:
        try:
            return sum(1 for _ in self._ref.stream())
        except Exception as e:
            print(f"[FIRESTORE ERROR] count {self.name}: {e}")
            return 0

    def delete_all(self) -> int:
        deleted = 0
        try:
            for snap in self._ref.stream():
                snap.reference.delete()
                deleted += 1
        except Exception as e:
            print(f"[FIRESTORE ERROR] delete_all {self.name}: {e}")
        return deleted


class FirestoreCloud:
    """Giống FirestoreOffline: db.collection('users').document(...)."""

    def __init__(self, client: firestore.Client):
        self._client = client

    def collection(self, name: str) -> FirestoreCollection:
        return FirestoreCollection(self._client.collection(name), name)


_fs_client: Optional[firestore.Client] = None


def get_firestore_cloud() -> FirestoreCloud:
    global _fs_client
    if _fs_client is None:
        path = (FIREBASE_CREDENTIALS_PATH or "").strip()
        if not path:
            raise RuntimeError(
                "Đang bật Firestore nhưng không tìm thấy file khóa. Hãy đặt biến FIREBASE_CREDENTIALS_PATH "
                "hoặc GOOGLE_APPLICATION_CREDENTIALS, hoặc đặt file firebase-service-account.json ở thư mục gốc project."
            )
        if not firebase_admin._apps:
            cred = credentials.Certificate(path)
            firebase_admin.initialize_app(cred)
        _fs_client = firestore.client()
    return FirestoreCloud(_fs_client)
