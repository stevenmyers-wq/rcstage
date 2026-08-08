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
# Call log -> telephony session IDs for one extension
# ---------------------------------------------------------------------------
def fetch_session_ids(ext_id, date_from, date_to, token, task_id=None):
    """Page a single extension's Voice call log across the range and return a
    list of dicts: { sessionId, partyId, startTime, direction, from, to }.

    De-duplicated by telephonySessionId (a call can produce multiple legs that
    share one session). Records with no telephonySessionId are skipped."""
    seen = set()
    calls = []
    page = 1
    while True:
        if task_id and task_control.is_stopped(task_id):
            break
        endpoint = (f'/restapi/v1.0/account/~/extension/{ext_id}/call-log'
                    f'?type=Voice&view=Simple&dateFrom={date_from}&dateTo={date_to}'
                    f'&perPage=250&page={page}')
        succ, resp = safe_api_call(endpoint, token=token)
        if not succ:
            # Surface the failure to the caller so it can annotate the user row,
            # but don't abort the whole job for one extension.
            raise Exception(format_api_error(resp))

        batch = resp.get('records', []) if isinstance(resp, dict) else []
        for rec in batch:
            sid = rec.get('telephonySessionId')
            if not sid or sid in seen:
                continue
            seen.add(sid)
            calls.append({
                "sessionId": sid,
                "partyId": rec.get('id', ''),
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
def search_ai_notes(session_ids, token):
    """POST the AI-notes search for a chunked list of session IDs. Returns
    { sessionId: record } for every note found.

    IMPORTANT — the search runs under ``extension/~`` (the authenticated
    caller's own identity), NOT under each target user's extension. The
    endpoint enforces same-extension access on the *path* extension: putting a
    different user's extension ID there returns "Attempt to access another
    extension" even for an account admin. Scoping is done instead by the
    ``telephonySessionIds`` in the body (harvested per-user from the call log),
    so results still attribute back to the right user. With an ``AllInternal``-
    scoped token this searches across the account by session ID.

    The metadata field is a oneOf; we defensively reach for callSummary."""
    found = {}
    for i in range(0, len(session_ids), SEARCH_CHUNK):
        chunk = session_ids[i:i + SEARCH_CHUNK]
        endpoint = ('/restapi/v1.0/account/~/extension/~'
                    '/telephony/metadata/ai-notes/search')
        succ, resp = safe_api_call(
            endpoint, method='POST',
            json_payload={"telephonySessionIds": chunk}, token=token)
        if not succ:
            # Propagate so the caller can mark this user's notes as unavailable
            # (e.g. feature flag off, 403/404) without killing the whole run.
            raise Exception(format_api_error(resp))
        for rec in (resp.get('records', []) if isinstance(resp, dict) else []):
            sid = rec.get('telephonySessionId')
            if sid:
                found[sid] = rec
    return found


def _extract_note_fields(rec):
    """Flatten a metadata record's AI-note payload into display fields."""
    meta = rec.get('metadata') if isinstance(rec, dict) else None
    summary = {}
    if isinstance(meta, dict):
        cs = meta.get('callSummary')
        if isinstance(cs, dict):
            summary = cs
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

        # Resolve names/extension numbers for the selected IDs up front so the
        # report is readable even for users with no notes.
        all_users = {u['id']: u for u in fetch_all_users(token)}

        _init_task(task_id, len(ext_ids))
        rows = []
        users_with_notes = 0
        notes_total = 0

        for idx, ext_id in enumerate(ext_ids):
            if task_control.is_stopped(task_id):
                break
            progress_store[task_id]['current'] = idx + 1

            u = all_users.get(str(ext_id), {})
            uname = u.get('name', str(ext_id))
            unum = u.get('extensionNumber', '')

            # 1. Session IDs from the call log
            try:
                calls = fetch_session_ids(ext_id, df_bound, dt_bound, token, task_id=task_id)
            except Exception as e:
                rows.append(_error_row(uname, unum, ext_id,
                                       f"Call log error: {e}"))
                continue

            if not calls:
                continue

            # 2. AI notes for those sessions
            session_ids = [c['sessionId'] for c in calls]
            try:
                notes_map = search_ai_notes(session_ids, token)
            except Exception as e:
                rows.append(_error_row(uname, unum, ext_id,
                                       f"AI notes lookup error: {e}"))
                continue

            if notes_map:
                users_with_notes += 1
            calls_by_sid = {c['sessionId']: c for c in calls}
            for sid, rec in notes_map.items():
                call = calls_by_sid.get(sid, {})
                fields = _extract_note_fields(rec)
                notes_total += 1
                rows.append({
                    "User": uname,
                    "Extension": unum,
                    "Extension ID": str(ext_id),
                    "Telephony Session ID": sid,
                    "Party ID": call.get('partyId', ''),
                    "Call Time": call.get('startTime', ''),
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
                   f"{users_with_notes} user(s) of {len(ext_ids)} selected.")
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
