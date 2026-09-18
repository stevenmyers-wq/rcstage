"""Durable storage for Account Migration outputs (7-day retention).

Export (ZIP), Audit (XLSX) and Import (results log XLSX) run in background
threads; the finished artifact is written to Cloud Storage and indexed in
Firestore, so it can be downloaded from ANY Cloud Run instance and retrieved
later for audit — the in-memory task store alone can't do that.

Everything degrades gracefully: if AUDIT_BUCKET is unset the helpers no-op and
the caller falls back to the in-memory copy. The GCP clients are imported and
constructed lazily so importing this module (and unit-testing the migration
logic) needs no cloud libraries or credentials.

Reuses the same bucket as the audit tools (AUDIT_BUCKET), under its own prefix.
Retention is 7 days: list_recent / load_file ignore anything older and prune it
best-effort. For authoritative physical deletion, also apply a 7-day Object
Lifecycle rule to the `account_migration/` prefix of the bucket.
"""
import os
from datetime import datetime, timezone

_GCS_PREFIX = 'account_migration'
_FS_COLLECTION = 'account_migration_runs'
_RETENTION_DAYS = 7

# File extension by run kind, used to name the stored object and the download.
_EXT = {'export': 'zip', 'audit': 'xlsx', 'import': 'xlsx'}

_storage_client = None
_fs_client = None


def _bucket_name():
    return os.getenv('AUDIT_BUCKET')


def storage_enabled():
    return bool(_bucket_name())


def _bucket():
    global _storage_client
    name = _bucket_name()
    if not name:
        return None
    if _storage_client is None:
        from google.cloud import storage  # lazy
        _storage_client = storage.Client()
    return _storage_client.bucket(name)


def _fs():
    global _fs_client
    if _fs_client is None:
        try:
            from google.cloud import firestore  # lazy
            _fs_client = firestore.Client()
        except Exception as e:
            print(f"account_migration: Firestore init failed: {e}")
            _fs_client = None
    return _fs_client


def _blob_path(task_id, kind):
    return f"{_GCS_PREFIX}/{task_id}.{_EXT.get(kind, 'bin')}"


def diagnostics():
    out = {'audit_bucket': _bucket_name(), 'storage_enabled': storage_enabled()}
    try:
        out['firestore_ok'] = _fs() is not None
    except Exception as e:
        out['firestore_ok'] = False
        out['firestore_error'] = str(e)[:200]
    try:
        bkt = _bucket()
        out['gcs_bucket_reachable'] = bool(bkt.exists()) if bkt is not None else None
    except Exception as e:
        out['gcs_bucket_reachable'] = False
        out['gcs_error'] = str(e)[:200]
    return out


def record_status(task_id, status, kind=None, user_email=None,
                  account_name=None, error=None):
    """Best-effort Firestore write of a run's status, so a poll landing on
    another instance can still resolve it. Merges into the same doc."""
    fs = _fs()
    if fs is None:
        return
    try:
        doc = {'task_id': task_id, 'status': status,
               'updated_at': datetime.now(timezone.utc)}
        if kind is not None:
            doc['kind'] = kind
        if user_email is not None:
            doc['user_email'] = user_email
        if account_name is not None:
            doc['account_name'] = account_name
        if error is not None:
            doc['error'] = str(error)[:500]
        if status == 'running':
            doc['created_at'] = datetime.now(timezone.utc)
        fs.collection(_FS_COLLECTION).document(task_id).set(doc, merge=True)
    except Exception as e:
        print(f"account_migration: Firestore status write failed: {e}")


def save_result(task_id, kind, file_bytes, filename, content_type,
                user_email, account_name=None):
    """Uploads a finished artifact to GCS and writes/updates its Firestore index
    doc. Returns the GCS object path, or None when storage isn't configured."""
    bkt = _bucket()
    if bkt is None:
        return None

    blob_path = _blob_path(task_id, kind)
    bkt.blob(blob_path).upload_from_string(file_bytes, content_type=content_type)

    fs = _fs()
    if fs is not None:
        try:
            fs.collection(_FS_COLLECTION).document(task_id).set({
                'task_id': task_id,
                'kind': kind,
                'user_email': user_email or 'unknown',
                'account_name': account_name or '',
                'filename': filename,
                'content_type': content_type,
                'size_bytes': len(file_bytes),
                'gcs_path': blob_path,
                'status': 'completed',
                'created_at': datetime.now(timezone.utc),
            }, merge=True)
        except Exception as e:
            print(f"account_migration: Firestore index write failed: {e}")
    return blob_path


def get_record(task_id):
    fs = _fs()
    if fs is None:
        return None
    try:
        snap = fs.collection(_FS_COLLECTION).document(task_id).get()
        return snap.to_dict() if snap.exists else None
    except Exception as e:
        print(f"account_migration: Firestore read failed: {e}")
        return None


def _delete(task_id, gcs_path=None):
    try:
        bkt = _bucket()
        if bkt is not None and gcs_path:
            bkt.blob(gcs_path).delete()
    except Exception:
        pass
    fs = _fs()
    if fs is not None:
        try:
            fs.collection(_FS_COLLECTION).document(task_id).delete()
        except Exception:
            pass


def load_file(task_id):
    """Fetches a stored artifact from GCS. Returns (bytes, filename,
    content_type), or (None, None, None) if missing, storage off, or expired."""
    rec = get_record(task_id)
    if not rec or not rec.get('gcs_path'):
        return None, None, None
    ca = rec.get('created_at')
    ts = ca.timestamp() if hasattr(ca, 'timestamp') else 0
    if ts and ts < datetime.now(timezone.utc).timestamp() - _RETENTION_DAYS * 86400:
        _delete(task_id, rec.get('gcs_path'))
        return None, None, None
    bkt = _bucket()
    if bkt is None:
        return None, None, None
    try:
        data = bkt.blob(rec['gcs_path']).download_as_bytes()
        return (data, rec.get('filename') or task_id,
                rec.get('content_type') or 'application/octet-stream')
    except Exception as e:
        print(f"account_migration: GCS download failed: {e}")
        return None, None, None


def list_recent(user_email, limit=25, max_age_days=_RETENTION_DAYS):
    """A user's recent completed migration outputs, newest first (last 7 days).
    Equality-only Firestore query (no composite index); sort/trim in Python."""
    fs = _fs()
    if fs is None or not user_email:
        return []
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        rows = []
        for snap in fs.collection(_FS_COLLECTION).where(
                'user_email', '==', user_email).stream():
            r = snap.to_dict() or {}
            if r.get('status') != 'completed' or not r.get('gcs_path'):
                continue
            ca = r.get('created_at')
            ts = ca.timestamp() if hasattr(ca, 'timestamp') else 0
            if ts and ts < cutoff:
                _delete(r.get('task_id'), r.get('gcs_path'))
                continue
            rows.append({
                'task_id': r.get('task_id'),
                'kind': r.get('kind') or '',
                'filename': r.get('filename') or '',
                'account_name': r.get('account_name') or '',
                'created_at': ca.isoformat() if hasattr(ca, 'isoformat') else '',
                '_ts': ts,
            })
        rows.sort(key=lambda x: x['_ts'], reverse=True)
        for x in rows:
            x.pop('_ts', None)
        return rows[:limit]
    except Exception as e:
        print(f"account_migration: recent list failed: {e}")
        return []
