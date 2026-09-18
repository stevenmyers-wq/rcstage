"""Durable storage for generated As-Built documents (7-day retention).

Generation runs in a background thread; the finished document is written to
Cloud Storage (a small JSON bundle) and indexed in Firestore (metadata doc).
This makes a finished As-Built retrievable from ANY Cloud Run instance and after
the browser tab is closed or the instance is recycled — the in-memory task store
alone can't do that.

We store the RAW result bundle — the assembled body HTML plus the structured
`doc` — so any format (PDF / Word / Excel) can be re-exported later from history,
not just the one downloaded at generation time.

Retention is 7 days:
  * `list_recent` / `load_document` ignore anything older than the cutoff, and
  * expired objects encountered while listing are pruned best-effort.
For authoritative physical deletion, also apply a 7-day Object Lifecycle rule to
the `as_built/` prefix of the bucket (belt and suspenders).

Everything degrades gracefully: if AUDIT_BUCKET is unset (storage not
configured) the helpers no-op and the caller falls back to the in-memory copy.
The GCP clients are imported and constructed lazily so importing this module
(and unit-testing the generation logic) needs no cloud libraries or credentials.
"""
import os
import json
from datetime import datetime, timezone

# GCS object prefix and Firestore collection for stored As-Built documents.
# Reuses the same bucket as the audit tools (AUDIT_BUCKET), under its own prefix.
_GCS_PREFIX = 'as_built'
_FS_COLLECTION = 'as_built_documents'
_JSON_MIME = 'application/json'
_RETENTION_DAYS = 7

_storage_client = None
_fs_client = None


def _bucket_name():
    return os.getenv('AUDIT_BUCKET')


def storage_enabled():
    """True when a bucket is configured (durable storage available)."""
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
            print(f"as_built: Firestore init failed: {e}")
            _fs_client = None
    return _fs_client


def _blob_path(task_id):
    return f"{_GCS_PREFIX}/{task_id}.json"


def diagnostics():
    """Read-only self-check: is durable storage configured and reachable?"""
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


def record_status(task_id, status, user_email=None, account_name=None,
                  customer_name=None, error=None):
    """Best-effort Firestore write of a run's status ('running'/'error'), so a
    status poll that lands on another instance can still resolve the run. Merges
    into the same doc save_document finalises."""
    fs = _fs()
    if fs is None:
        return
    try:
        doc = {'task_id': task_id, 'status': status,
               'updated_at': datetime.now(timezone.utc)}
        if user_email is not None:
            doc['user_email'] = user_email
        if account_name is not None:
            doc['account_name'] = account_name
        if customer_name is not None:
            doc['customer_name'] = customer_name
        if error is not None:
            doc['error'] = str(error)[:500]
        if status == 'running':
            doc['created_at'] = datetime.now(timezone.utc)
        fs.collection(_FS_COLLECTION).document(task_id).set(doc, merge=True)
    except Exception as e:
        print(f"as_built: Firestore status write failed: {e}")


def save_document(task_id, bundle, user_email, account_name=None,
                  customer_name=None):
    """Uploads the result bundle (JSON: {customer_name, body_html, doc}) to GCS
    and writes/updates its Firestore index doc. Returns the GCS object path, or
    None when storage isn't configured (the caller keeps the in-memory copy)."""
    bkt = _bucket()
    if bkt is None:
        return None

    blob_path = _blob_path(task_id)
    payload = json.dumps(bundle, default=str)
    bkt.blob(blob_path).upload_from_string(payload, content_type=_JSON_MIME)

    fs = _fs()
    if fs is not None:
        try:
            fs.collection(_FS_COLLECTION).document(task_id).set({
                'task_id': task_id,
                'user_email': user_email or 'unknown',
                'account_name': account_name or '',
                'customer_name': customer_name or account_name or '',
                'gcs_path': blob_path,
                'size_bytes': len(payload),
                'status': 'completed',
                'created_at': datetime.now(timezone.utc),
            }, merge=True)
        except Exception as e:
            print(f"as_built: Firestore index write failed: {e}")
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
        print(f"as_built: Firestore read failed: {e}")
        return None


def _delete(task_id, gcs_path=None):
    """Best-effort removal of a stored document (GCS object + Firestore doc)."""
    try:
        bkt = _bucket()
        if bkt is not None:
            bkt.blob(gcs_path or _blob_path(task_id)).delete()
    except Exception:
        pass
    fs = _fs()
    if fs is not None:
        try:
            fs.collection(_FS_COLLECTION).document(task_id).delete()
        except Exception:
            pass


def load_document(task_id):
    """Fetches a completed As-Built's result bundle from GCS. Returns the parsed
    dict {customer_name, body_html, doc}, or None if not found, storage not
    configured, or the document is past the 7-day retention window."""
    rec = get_record(task_id)
    if not rec or not rec.get('gcs_path'):
        return None
    ca = rec.get('created_at')
    ts = ca.timestamp() if hasattr(ca, 'timestamp') else 0
    if ts and ts < datetime.now(timezone.utc).timestamp() - _RETENTION_DAYS * 86400:
        _delete(task_id, rec.get('gcs_path'))
        return None
    bkt = _bucket()
    if bkt is None:
        return None
    try:
        data = bkt.blob(rec['gcs_path']).download_as_bytes()
        return json.loads(data)
    except Exception as e:
        print(f"as_built: GCS download failed: {e}")
        return None


def list_recent(user_email, limit=20, max_age_days=_RETENTION_DAYS):
    """Lists a user's recent stored As-Builts, newest first. Uses an
    equality-only Firestore query (no composite index needed) and sorts / trims
    in Python. Prunes anything past the retention window best-effort."""
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
                _delete(r.get('task_id'), r.get('gcs_path'))  # enforce retention
                continue
            rows.append({
                'task_id': r.get('task_id'),
                'account_name': r.get('account_name') or '',
                'customer_name': r.get('customer_name') or r.get('account_name') or '',
                'created_at': ca.isoformat() if hasattr(ca, 'isoformat') else '',
                '_ts': ts,
            })
        rows.sort(key=lambda x: x['_ts'], reverse=True)
        for x in rows:
            x.pop('_ts', None)
        return rows[:limit]
    except Exception as e:
        print(f"as_built: recent list failed: {e}")
        return []
