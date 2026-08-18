"""Durable storage for finished ring-time audits.

The audit runs in a background thread; its result is written to Cloud Storage
(the workbook bytes) and indexed in Firestore (small metadata doc). This makes a
finished audit downloadable from ANY Cloud Run instance and after the browser
tab is closed or the instance is recycled — the in-memory task store alone can't
do that.

Everything degrades gracefully: if AUDIT_BUCKET is unset (storage not
configured) the helpers no-op and the caller falls back to the in-memory file.
The GCP clients are imported and constructed lazily so importing this module
(and unit-testing the audit logic) needs no cloud libraries or credentials.
"""
import os
from datetime import datetime, timezone

# GCS object prefix and Firestore collection for this tool's audits.
_GCS_PREFIX = 'deskphone_ring_time'
_FS_COLLECTION = 'deskphone_ring_time_audits'
_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

_storage_client = None
_fs_client = None


def _bucket_name():
    return os.getenv('AUDIT_BUCKET')


def storage_enabled():
    """True when an audit bucket is configured (durable storage available)."""
    return bool(_bucket_name())


def _bucket():
    global _storage_client
    name = _bucket_name()
    if not name:
        return None
    if _storage_client is None:
        from google.cloud import storage  # lazy: only when actually used
        _storage_client = storage.Client()
    return _storage_client.bucket(name)


def _fs():
    global _fs_client
    if _fs_client is None:
        try:
            from google.cloud import firestore  # lazy
            _fs_client = firestore.Client()
        except Exception as e:
            print(f"deskphone_ring_time: Firestore init failed: {e}")
            _fs_client = None
    return _fs_client


def record_status(task_id, status, user_email=None, error=None):
    """Best-effort Firestore write of a run's status (e.g. 'running', 'error'),
    so a status poll that lands on another instance — or a later visit — can
    still resolve the run. Merges into the same doc save_result finalises."""
    fs = _fs()
    if fs is None:
        return
    try:
        doc = {'task_id': task_id, 'status': status,
               'updated_at': datetime.now(timezone.utc)}
        if user_email is not None:
            doc['user_email'] = user_email
        if error is not None:
            doc['error'] = str(error)[:500]
        if status == 'running':
            doc['created_at'] = datetime.now(timezone.utc)
        fs.collection(_FS_COLLECTION).document(task_id).set(doc, merge=True)
    except Exception as e:
        print(f"deskphone_ring_time: Firestore status write failed: {e}")


def save_result(task_id, file_bytes, filename, rows, user_email):
    """Uploads the finished workbook to GCS and writes/updates its Firestore
    index doc. Returns the GCS object path, or None when storage isn't
    configured (the caller then keeps serving the in-memory copy)."""
    bkt = _bucket()
    if bkt is None:
        return None

    blob_path = f"{_GCS_PREFIX}/{task_id}.xlsx"
    bkt.blob(blob_path).upload_from_string(file_bytes, content_type=_XLSX_MIME)

    fs = _fs()
    if fs is not None:
        try:
            fs.collection(_FS_COLLECTION).document(task_id).set({
                'task_id': task_id,
                'user_email': user_email or 'unknown',
                'filename': filename,
                'rows': rows,
                'gcs_path': blob_path,
                'status': 'completed',
                'created_at': datetime.now(timezone.utc),
            }, merge=True)
        except Exception as e:
            print(f"deskphone_ring_time: Firestore index write failed: {e}")
    return blob_path


def get_record(task_id):
    """Returns the Firestore index doc for a task, or None."""
    fs = _fs()
    if fs is None:
        return None
    try:
        snap = fs.collection(_FS_COLLECTION).document(task_id).get()
        return snap.to_dict() if snap.exists else None
    except Exception as e:
        print(f"deskphone_ring_time: Firestore read failed: {e}")
        return None


def load_file(task_id):
    """Fetches a completed audit's workbook from GCS. Returns (bytes, filename),
    or (None, None) if not found / storage not configured."""
    rec = get_record(task_id)
    if not rec or not rec.get('gcs_path'):
        return None, None
    bkt = _bucket()
    if bkt is None:
        return None, None
    try:
        data = bkt.blob(rec['gcs_path']).download_as_bytes()
        return data, rec.get('filename') or f"{task_id}.xlsx"
    except Exception as e:
        print(f"deskphone_ring_time: GCS download failed: {e}")
        return None, None


def list_recent(user_email, limit=10, max_age_days=7):
    """Lists a user's recent completed audits, newest first. Uses an
    equality-only Firestore query (no composite index needed) and sorts /
    trims in Python, so no extra index setup is required."""
    fs = _fs()
    if fs is None or not user_email:
        return []
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        rows = []
        for snap in fs.collection(_FS_COLLECTION).where('user_email', '==', user_email).stream():
            r = snap.to_dict() or {}
            if r.get('status') != 'completed' or not r.get('gcs_path'):
                continue
            ca = r.get('created_at')
            ts = ca.timestamp() if hasattr(ca, 'timestamp') else 0
            if ts and ts < cutoff:
                continue
            rows.append({
                'task_id': r.get('task_id'),
                'filename': r.get('filename'),
                'rows': r.get('rows', 0),
                'created_at': ca.isoformat() if hasattr(ca, 'isoformat') else '',
                '_ts': ts,
            })
        rows.sort(key=lambda x: x['_ts'], reverse=True)
        for x in rows:
            x.pop('_ts', None)
        return rows[:limit]
    except Exception as e:
        print(f"deskphone_ring_time: recent list failed: {e}")
        return []
