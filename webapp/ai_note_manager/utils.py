"""AI Note Manager — backend.

Pulls AI-generated call notes (RingSense / "AI Notes") for a chosen set of users
over a date range and packages them into a downloadable report.

Flow, per selected user extension:
  1. Page the user's Voice call-log across the date range and collect every
     ``telephonySessionId`` (and its ``partyId`` for reference).
  2. Batch-search the AI Notes metadata endpoint for those session IDs.
  3. Flatten each returned note into a report row.

The AI Notes search endpoint is an internal/beta API gated behind the
``AIGeneratedNotes`` feature flag on the account:

    POST /restapi/v1.0/account/{accountId}/extension/{extensionId}
             /telephony/metadata/ai-notes/search
    body: { "telephonySessionIds": [ ... ] }

We call it against ``platform.ringcentral.com`` (production, not the lab host)
using the bridged partner token, per-extension, passing the session IDs we
harvested from that same extension's call log.

Concurrency/cancellation notes mirror the other UC tools: a single gunicorn
worker with threads, an in-memory ``progress_store`` keyed by task_id, and the
shared ``task_control`` cancel registry.
"""
import io
import json
import time
import logging

import pandas as pd

from webapp.rc_api import rc_api_call
from webapp import task_control

logger = logging.getLogger(__name__)

# In-memory task store keyed by task_id. Mirrors the shape the cq_hours /
# user_status jobs use so the frontend polling contract is identical:
#   { current, total, status, file_ready, error, summary, file_data, rows, columns }
# Reset on container restart — collections are short-lived, one-shot jobs.
progress_store = {}

# How many session IDs to send per AI-notes search request. The endpoint takes
# a list; we chunk to keep request bodies sane and stay friendly to rate limits.
SEARCH_CHUNK = 100

# Safety cap on how many call-log records we page per user, so a very busy
# extension over a wide range can't run unbounded.
MAX_CALLS_PER_USER = 3000

REPORT_COLUMNS = [
    "User", "Extension", "Extension ID",
    "Telephony Session ID", "Party ID",
    "Call Time", "Direction", "From", "To",
    "Category", "Digest", "Notes", "Raw AI Notes", "Version",
]


# ---------------------------------------------------------------------------
# Error formatting + transport (background-thread safe: token passed explicitly)
# ---------------------------------------------------------------------------
def format_api_error(err):
    """Pull RingCentral's own message out of its JSON error envelope when
    present, otherwise return the raw string."""
    try:
        obj = json.loads(err) if isinstance(err, str) else err
        if isinstance(obj, dict):
            if obj.get('message'):
                return obj['message']
            errors = obj.get('errors')
            if isinstance(errors, list) and errors:
                return '; '.join(e.get('message', str(e)) for e in errors)
    except Exception:
        pass
    return str(err)


def safe_api_call(endpoint, method='GET', json_payload=None, token=None, max_retries=5):
    """rc_api_call wrapper with 429/503 Retry-After handling and a short backoff
    on transient exceptions. Returns (ok: bool, data_or_error)."""
    for attempt in range(max_retries):
        try:
            resp = rc_api_call(endpoint, method=method, json=json_payload, token=token, return_response=True)
            status_code = getattr(resp, 'status_code', None)

            if status_code in (429, 503):
                try:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                except Exception:
                    retry_after = 60
                logger.warning("AI Notes %s %s -> %s. Sleeping %ss (attempt %s/%s)",
                               method, endpoint, status_code, retry_after, attempt + 1, max_retries)
                time.sleep(retry_after + 1)
                continue

            if resp and getattr(resp, 'ok', False):
                try:
                    return True, resp.json() if resp.content else {}
                except Exception:
                    return True, {}

            try:
                err_msg = json.dumps(resp.json())
            except Exception:
                body_text = getattr(resp, 'text', '')
                err_msg = body_text if body_text else f'HTTP {status_code} Error (empty response body)'
            return False, err_msg
        except Exception as e:
            logger.warning("AI Notes transport error on %s: %s", endpoint, e)
            time.sleep(2)
    return False, "Max retries exceeded due to rate limiting."


# ---------------------------------------------------------------------------
# Directory: enabled users
# ---------------------------------------------------------------------------
def fetch_all_users(token):
    """All enabled User-type extensions, formatted for the UI picker."""
    records = []
    page = 1
    while True:
        succ, resp = safe_api_call(
            f'/restapi/v1.0/account/~/extension?type=User&status=Enabled&perPage=1000&page={page}',
            token=token)
        if not succ:
            raise Exception(f"Failed to fetch users: {format_api_error(resp)}")
        if isinstance(resp, dict) and 'records' in resp:
            records.extend(resp['records'])
            if 'nextPage' in (resp.get('navigation') or {}):
                page += 1
            else:
                break
        else:
            break

    users = []
    for u in records:
        contact = u.get('contact') or {}
        users.append({
            "id": str(u.get('id', '')),
            "name": u.get('name') or contact.get('firstName', '') or 'Unknown',
            "extensionNumber": str(u.get('extensionNumber', '')),
            "email": contact.get('email', ''),
        })
    return sorted(users, key=lambda x: (x['name'] or '').lower())


# ---------------------------------------------------------------------------
# Preflight: is the AI-notes metadata API actually enabled for this account?
# ---------------------------------------------------------------------------
def metadata_service_status(token):
    """The ai-notes/search endpoint is powered by the ``MetadataServiceForAINotes``
    account feature. When that feature is off, the search returns 200 with empty
    records for every call — even though ``AIGeneratedNotes`` may be on and notes
    are visible in the app. Check the extension feature list so we can tell the
    user plainly instead of silently reporting "no notes".

    Returns { available: bool, reason: str } or None if the check itself fails
    (in which case we proceed and let the search speak for itself)."""
    ok, feat = safe_api_call('/restapi/v1.0/account/~/extension/~/features', token=token)
    if not ok or not isinstance(feat, dict):
        return None
    for f in feat.get('records', []):
        if f.get('id') == 'MetadataServiceForAINotes':
            return {"available": bool(f.get('available')),
                    "reason": (f.get('reason') or {}).get('message', '')}
    # Feature not listed at all — treat as unavailable but say so distinctly.
    return {"available": False,
            "reason": "Feature 'MetadataServiceForAINotes' is not present on this account."}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def to_range_bounds(date_from, date_to):
    """Turn ``YYYY-MM-DD`` day strings into full-day UTC ISO bounds the call-log
    endpoint accepts. Pass-through anything that already looks like a full
    timestamp (contains a 'T')."""
    df = date_from if 'T' in (date_from or '') else f"{date_from}T00:00:00.000Z"
    dt = date_to if 'T' in (date_to or '') else f"{date_to}T23:59:59.999Z"
    return df, dt


# ---------------------------------------------------------------------------
# Call log -> telephony parties for one extension
# ---------------------------------------------------------------------------
def fetch_call_parties(ext_id, date_from, date_to, token, task_id=None):
    """Page a single extension's Voice call log across the range and return a
    list of dicts: { sessionId, partyId, startTime, direction, from, to }.

    AI notes are keyed by *party*, not session — the ai-notes/search endpoint
    returns nothing for a telephonySessionId but matches on partyId — so we use
    the Detailed view (which carries a record-level ``partyId``) and search by
    those party IDs downstream. De-duplicated by partyId."""
    seen = set()
    calls = []
    page = 1
    while True:
        if task_id and task_control.is_stopped(task_id):
            break
        endpoint = (f'/restapi/v1.0/account/~/extension/{ext_id}/call-log'
                    f'?type=Voice&view=Detailed&dateFrom={date_from}&dateTo={date_to}'
                    f'&perPage=250&page={page}')
        succ, resp = safe_api_call(endpoint, token=token)
        if not succ:
            # Surface the failure to the caller so it can annotate the user row,
            # but don't abort the whole job for one extension.
            raise Exception(format_api_error(resp))

        batch = resp.get('records', []) if isinstance(resp, dict) else []
        for rec in batch:
            pid = rec.get('partyId', '')
            if not pid or pid in seen:
                continue
            seen.add(pid)
            calls.append({
                "sessionId": rec.get('telephonySessionId', ''),
                "partyId": pid,
                "startTime": rec.get('startTime', ''),
                "direction": rec.get('direction', ''),
                "from": (rec.get('from') or {}).get('phoneNumber')
                        or (rec.get('from') or {}).get('name', ''),
                "to": (rec.get('to') or {}).get('phoneNumber')
                      or (rec.get('to') or {}).get('name', ''),
            })
            if len(calls) >= MAX_CALLS_PER_USER:
                return calls

        nav = resp.get('navigation', {}) if isinstance(resp, dict) else {}
        if 'nextPage' in nav:
            page += 1
        else:
            break
    return calls


# ---------------------------------------------------------------------------
# AI Notes search for a batch of session IDs
# ---------------------------------------------------------------------------
def _record_session_id(rec):
    """Best-effort telephony-session id for a returned metadata record. The
    field name has varied across revisions of this internal endpoint, so try a
    few before giving up."""
    if not isinstance(rec, dict):
        return ''
    return (rec.get('telephonySessionId') or rec.get('sessionId')
            or rec.get('telephonySessionID') or '')


def _extract_records(resp):
    """Pull the list of note records out of a search response, tolerating the
    container-shape variations this internal endpoint has used: a dict under
    ``records`` (per the published spec), a dict under an alternate key, or a
    bare list. Returns [] when nothing note-like is present."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ('records', 'data', 'results', 'aiNotes', 'metadata', 'items'):
            val = resp.get(key)
            if isinstance(val, list):
                return val
    return []


def search_ai_notes(party_ids, token):
    """POST the AI-notes search for a chunked list of *party* IDs. Returns the
    flat list of raw metadata records the endpoint hands back (one per note).

    Why party IDs and not session IDs: the endpoint accepts ``partyIds`` OR
    ``telephonySessionIds``, but in practice a telephonySessionId search comes
    back empty even for calls that have notes — the notes are keyed by party.
    So we harvest the record-level ``partyId`` from the Detailed call log and
    search on that.

    IMPORTANT — the search runs under ``extension/~`` (the authenticated
    caller's own identity), NOT under each target user's extension. The
    endpoint enforces same-extension access on the *path* extension: putting a
    different user's extension ID there returns "Attempt to access another
    extension" even for an account admin. With an ``AllInternal``-scoped token
    the party IDs in the body do the scoping across the account.

    We return every record verbatim so the caller can surface whatever the API
    returns and match on a best-effort basis."""
    records = []
    logged_sample = False
    for i in range(0, len(party_ids), SEARCH_CHUNK):
        chunk = party_ids[i:i + SEARCH_CHUNK]
        endpoint = ('/restapi/v1.0/account/~/extension/~'
                    '/telephony/metadata/ai-notes/search')
        succ, resp = safe_api_call(
            endpoint, method='POST',
            json_payload={"partyIds": chunk}, token=token)
        if not succ:
            # Propagate so the caller can mark this user's notes as unavailable
            # (e.g. feature flag off, 403/404) without killing the whole run.
            raise Exception(format_api_error(resp))
        recs = _extract_records(resp)
        # Diagnostic: log the raw shape so we can see how notes are actually
        # keyed. On a non-empty search, log one sample record; on a 200-but-
        # empty search, log the whole (truncated) body so we can tell whether
        # the endpoint really returned nothing or used a shape we don't parse.
        if recs and not logged_sample:
            try:
                logger.info("AI Notes sample record: %s", json.dumps(recs[0])[:2000])
            except Exception:
                pass
            logged_sample = True
        elif not recs and not logged_sample:
            try:
                logger.info("AI Notes search 200-but-empty for %s session(s). Raw: %s",
                            len(chunk), json.dumps(resp)[:2000])
            except Exception:
                logger.info("AI Notes search 200-but-empty for %s session(s). Type: %s",
                            len(chunk), type(resp).__name__)
            logged_sample = True
        records.extend(recs)
    return records


def _extract_note_fields(rec):
    """Flatten a metadata record's AI-note payload into display fields.

    The note payload's location has varied: sometimes under
    ``metadata.callSummary``, sometimes ``metadata`` is itself the summary, or
    the fields sit at the record's top level. Check each."""
    candidates = []
    if isinstance(rec, dict):
        meta = rec.get('metadata')
        if isinstance(meta, dict):
            cs = meta.get('callSummary')
            if isinstance(cs, dict):
                candidates.append(cs)
            candidates.append(meta)      # metadata may itself hold the fields
        candidates.append(rec)           # or the fields sit at the top level

    def pick(*keys):
        for src in candidates:
            for k in keys:
                v = src.get(k)
                if v not in (None, ''):
                    return v
        return ''

    summary = {
        "digest": pick('digest'),
        "notes": pick('notes'),
        "rawAiNotes": pick('rawAiNotes'),
        "version": pick('version'),
    }
    return {
        "category": rec.get('metadataCategory', '') if isinstance(rec, dict) else '',
        "digest": summary.get('digest', ''),
        "notes": summary.get('notes', ''),
        "rawAiNotes": summary.get('rawAiNotes', ''),
        "version": summary.get('version', ''),
    }


# ---------------------------------------------------------------------------
# Task store helpers
# ---------------------------------------------------------------------------
def _init_task(task_id, total):
    progress_store[task_id] = {
        'current': 0,
        'total': total,
        'status': 'running',
        'file_ready': False,
        'error': '',
        'summary': '',
    }


def _build_report(rows):
    df = pd.DataFrame(rows if rows else [])
    for c in REPORT_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[REPORT_COLUMNS]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='AI Notes')
    output.seek(0)
    return output.getvalue()


def _finish_task(task_id, rows, summary):
    task = progress_store.get(task_id, {})
    task['file_data'] = _build_report(rows)
    task['file_ready'] = True
    task['summary'] = summary
    task['rows'] = rows
    task['columns'] = REPORT_COLUMNS
    task['status'] = 'completed'


def _fail_task(task_id, error):
    task = progress_store.setdefault(task_id, {})
    task['status'] = 'error'
    task['error'] = str(error)


# ---------------------------------------------------------------------------
# Main worker
# ---------------------------------------------------------------------------
def run_collection(task_id, ext_ids, date_from, date_to, token):
    """Background worker: harvest session IDs per user, search AI notes, build
    the report. Progress is measured in users processed."""
    try:
        df_bound, dt_bound = to_range_bounds(date_from, date_to)

        _init_task(task_id, len(ext_ids))

        # Preflight: if the AI-notes metadata API isn't enabled for this account,
        # every search returns empty — say so plainly instead of "0 notes".
        status = metadata_service_status(token)
        if status and not status['available']:
            _finish_task(task_id, [], (
                "AI Notes metadata API is not enabled for this account "
                f"(MetadataServiceForAINotes: {status['reason'] or 'unavailable'}). "
                "AI Notes are still generated and visible in the app, but cannot be "
                "retrieved through this API until the feature is enabled on the "
                "account's service plan. No code change will surface them until then."))
            return

        # Resolve names/extension numbers for the selected IDs up front so the
        # report is readable even for users with no notes.
        all_users = {u['id']: u for u in fetch_all_users(token)}

        rows = []
        users_with_notes = 0
        notes_total = 0
        parties_searched = 0

        for idx, ext_id in enumerate(ext_ids):
            if task_control.is_stopped(task_id):
                break
            progress_store[task_id]['current'] = idx + 1

            u = all_users.get(str(ext_id), {})
            uname = u.get('name', str(ext_id))
            unum = u.get('extensionNumber', '')

            # 1. Party IDs from the (Detailed) call log
            try:
                calls = fetch_call_parties(ext_id, df_bound, dt_bound, token, task_id=task_id)
            except Exception as e:
                rows.append(_error_row(uname, unum, ext_id,
                                       f"Call log error: {e}"))
                continue

            if not calls:
                continue

            # 2. AI notes, searched by party ID (session-ID search returns empty)
            party_ids = [c['partyId'] for c in calls if c['partyId']]
            parties_searched += len(party_ids)
            try:
                note_records = search_ai_notes(party_ids, token)
            except Exception as e:
                rows.append(_error_row(uname, unum, ext_id,
                                       f"AI notes lookup error: {e}"))
                continue

            if note_records:
                users_with_notes += 1
            calls_by_pid = {c['partyId']: c for c in calls}
            calls_by_sid = {c['sessionId']: c for c in calls if c['sessionId']}
            for rec in note_records:
                pid = rec.get('partyId', '') if isinstance(rec, dict) else ''
                sid = _record_session_id(rec)
                call = calls_by_pid.get(pid) or calls_by_sid.get(sid) or {}
                fields = _extract_note_fields(rec)
                notes_total += 1
                rows.append({
                    "User": uname,
                    "Extension": unum,
                    "Extension ID": str(ext_id),
                    "Telephony Session ID": sid or call.get('sessionId', ''),
                    "Party ID": pid or call.get('partyId', ''),
                    "Call Time": call.get('startTime', '')
                                 or (rec.get('creationTime') if isinstance(rec, dict) else ''),
                    "Direction": call.get('direction', ''),
                    "From": call.get('from', ''),
                    "To": call.get('to', ''),
                    "Category": fields['category'],
                    "Digest": fields['digest'],
                    "Notes": fields['notes'],
                    "Raw AI Notes": fields['rawAiNotes'],
                    "Version": fields['version'],
                })

        stopped = task_control.is_stopped(task_id)
        prefix = "Stopped early. " if stopped else ""
        summary = (f"{prefix}{notes_total} AI note(s) found across "
                   f"{users_with_notes} user(s) of {len(ext_ids)} selected "
                   f"({parties_searched} call(s) searched).")
        _finish_task(task_id, rows, summary)
    except Exception as e:
        logger.exception("AI Notes collection failed")
        _fail_task(task_id, e)
    finally:
        task_control.clear(task_id)


def _error_row(uname, unum, ext_id, message):
    return {
        "User": uname,
        "Extension": unum,
        "Extension ID": str(ext_id),
        "Telephony Session ID": "",
        "Party ID": "",
        "Call Time": "",
        "Direction": "",
        "From": "",
        "To": "",
        "Category": "Error",
        "Digest": "",
        "Notes": message,
        "Raw AI Notes": "",
        "Version": "",
    }


# ---------------------------------------------------------------------------
# Debug probe — returns raw RingCentral responses straight to the browser so we
# can see the actual call-log record + ai-notes/search response shapes without
# depending on Cloud Run log levels. Runs against the CALLER'S OWN extension.
# ---------------------------------------------------------------------------
def _decode_jwt_scope(token):
    """RingCentral access tokens are JWTs. Decode the (unverified) payload just
    to read the granted `scope`/permission claims — the decisive check for
    whether this token carries AllInternal. Returns a small dict; never raises."""
    try:
        import base64
        parts = (token or '').split('.')
        if len(parts) < 2:
            return {"note": "access token is not a JWT (no scope claim to read)"}
        seg = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg))
        keep = {}
        for k in ('scope', 'scopes', 'permissions', 'clientId', 'aud', 'sub', 'exp'):
            if k in payload:
                keep[k] = payload[k]
        keep["hasAllInternal"] = 'AllInternal' in json.dumps(payload)
        return keep
    except Exception as e:
        return {"error": f"could not decode token: {e}"}


def debug_probe(token, date_from=None, date_to=None, max_sessions=10):
    from datetime import datetime, timezone, timedelta
    if not date_from or not date_to:
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00.000Z')
        date_to = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    else:
        date_from, date_to = to_range_bounds(date_from, date_to)

    out = {"range": [date_from, date_to]}

    # Decisive check: does the connected token actually carry AllInternal?
    out["tokenScope"] = _decode_jwt_scope(token)

    # Who am I (own extension id + number)?
    ok_ext, me = safe_api_call('/restapi/v1.0/account/~/extension/~', token=token)
    own_ext_id = str(me.get('id', '')) if ok_ext and isinstance(me, dict) else ''
    out["ownExtension"] = {"ok": ok_ext, "id": own_ext_id,
                           "extensionNumber": me.get('extensionNumber') if isinstance(me, dict) else None}

    # Is the AI-notes feature actually on for this extension? Surface any
    # feature whose id/name hints at AI / notes / transcription.
    ok_feat, feat = safe_api_call('/restapi/v1.0/account/~/extension/~/features', token=token)
    if ok_feat and isinstance(feat, dict):
        hits = [f for f in feat.get('records', [])
                if any(t in json.dumps(f).lower() for t in ('note', 'ai', 'transcri', 'intellig'))]
        out["aiFeatureFlags"] = hits or "no matching feature entries"
    else:
        out["aiFeatureFlags"] = {"ok": ok_feat, "raw": feat}

    # Own call log (Detailed view) so we can see every identifier available.
    ok_cl, cl = safe_api_call(
        f'/restapi/v1.0/account/~/extension/~/call-log'
        f'?type=Voice&view=Detailed&dateFrom={date_from}&dateTo={date_to}&perPage=50',
        token=token)
    cl_records = cl.get('records', []) if (ok_cl and isinstance(cl, dict)) else []
    session_ids, party_ids, recording_ids = [], [], []
    for r in cl_records:
        sid = r.get('telephonySessionId')
        if sid and sid not in session_ids:
            session_ids.append(sid)
        pid = r.get('partyId')
        if pid and pid not in party_ids:
            party_ids.append(pid)
        for src in [r] + (r.get('legs') or []):
            rid = (src.get('recording') or {}).get('id')
            if rid and rid not in recording_ids:
                recording_ids.append(rid)
    out["callLog"] = {
        "ok": ok_cl,
        "count": len(cl_records),
        "sampleRawRecord": cl_records[0] if cl_records else None,
        "sessionIdsFound": session_ids[:max_sessions],
        "partyIdsFound": party_ids[:max_sessions],
        "recordingIdsFound": recording_ids[:max_sessions],
        "error": None if ok_cl else cl,
    }

    def _post(path, body):
        ok, resp = safe_api_call(path, method='POST', json_payload=body, token=token)
        return {"ok": ok, "raw": resp, "body": body, "path": path}

    def _get(path):
        ok, resp = safe_api_call(path, token=token)
        return {"ok": ok, "raw": resp, "path": path}

    p = party_ids[:max_sessions]
    s = session_ids[:max_sessions]
    r_ids = recording_ids[:max_sessions]
    # The counterparty party is usually "-1"; swap the "-2" suffix in case notes
    # are keyed against the other leg.
    p_alt = [pid.rsplit('-', 1)[0] + '-1' for pid in p]

    ext_search = '/restapi/v1.0/account/~/extension/~/telephony/metadata/ai-notes/search'
    acct_search = '/restapi/v1.0/account/~/telephony/metadata/ai-notes/search'
    ext_list = '/restapi/v1.0/account/~/extension/~/telephony/metadata/ai-notes?perPage=50'

    out["searchProbes"] = {}
    if p:
        out["searchProbes"]["ext__partyIds"] = _post(ext_search, {"partyIds": p})
        out["searchProbes"]["ext__partyIds_dash1"] = _post(ext_search, {"partyIds": p_alt})
        out["searchProbes"]["account__partyIds"] = _post(acct_search, {"partyIds": p})
    if s:
        out["searchProbes"]["ext__telephonySessionIds"] = _post(ext_search, {"telephonySessionIds": s})
    if r_ids:
        out["searchProbes"]["ext__recordingIds"] = _post(ext_search, {"recordingIds": r_ids})
    # List everything the endpoint has for this extension, ignoring any filter —
    # tells us whether the store simply has nothing for us (scope/feature) vs a
    # filter/key mismatch.
    out["searchProbes"]["ext__list_all_GET"] = _get(ext_list)

    return out
