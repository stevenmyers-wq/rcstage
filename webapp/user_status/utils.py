import io
import json
import time
import logging

import pandas as pd

from webapp.rc_api import rc_api_call

logger = logging.getLogger(__name__)

# In-memory task store keyed by task_id. Each entry mirrors the shape the
# cq_hours audit uses so the frontend polling contract is identical:
#   { current, total, status, file_ready, error, summary, file_data }
# Reset on container restart — audits/updates are short-lived, one-shot jobs.
progress_store = {}


def format_api_error(err):
    """Turn whatever safe_api_call returned as an error into a readable string,
    pulling RingCentral's own message out of its JSON envelope when present."""
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


# ---------------------------------------------------------------------------
# Transport: mirror the proven cq_hours helper. rc_api_call needs the token
# passed explicitly because these run in background threads with no request
# (and therefore no session) context. 429s are retried honouring Retry-After;
# transient exceptions get a short backoff.
# ---------------------------------------------------------------------------
def safe_api_call(endpoint, method='GET', json_payload=None, token=None, max_retries=5):
    for attempt in range(max_retries):
        try:
            resp = rc_api_call(endpoint, method=method, json=json_payload, token=token, return_response=True)
            status_code = getattr(resp, 'status_code', None)

            if status_code in (429, 503):
                try:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                except Exception:
                    retry_after = 60
                logger.warning("User Status %s %s -> %s. Sleeping %ss (attempt %s/%s)",
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
            logger.warning("User Status transport error on %s: %s", endpoint, e)
            time.sleep(2)
    return False, "Max retries exceeded due to rate limiting."


def fetch_directory(endpoint, token):
    """Fetch every page of a paginated collection endpoint."""
    records = []
    page = 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        succ, resp = safe_api_call(f'{endpoint}{sep}perPage=1000&page={page}', method='GET', token=token)
        if not succ:
            return False, resp
        if isinstance(resp, dict) and 'records' in resp:
            records.extend(resp['records'])
            if 'navigation' in resp and 'nextPage' in resp.get('navigation', {}):
                page += 1
            else:
                break
        else:
            break
    return True, records


def fetch_all_users(token):
    """All enabled User-type extensions."""
    succ, records = fetch_directory('/restapi/v1.0/account/~/extension?type=User&status=Enabled', token)
    if not succ:
        raise Exception(f"Failed to fetch users: {format_api_error(records)}")
    return records


def fetch_all_queues(token):
    """All call queues, formatted for the UI selector."""
    succ, records = fetch_directory('/restapi/v1.0/account/~/call-queues', token)
    if not succ:
        raise Exception(f"Failed to fetch call queues: {format_api_error(records)}")
    queues = []
    for q in records:
        queues.append({
            "id": str(q.get('id', '')),
            "name": q.get('name', 'Unknown'),
            "extensionNumber": str(q.get('extensionNumber', '')),
            "site": (q.get('site') or {}).get('name', 'Main Site'),
        })
    return sorted(queues, key=lambda x: x['name'].lower())


def _build_report(rows, sheet_name, columns=None):
    """Return an in-memory .xlsx built from a list of dict rows."""
    df = pd.DataFrame(rows if rows else [])
    if columns:
        # Keep a stable column order even when the frame is empty.
        for c in columns:
            if c not in df.columns:
                df[c] = None
        df = df[columns]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    output.seek(0)
    return output.getvalue()


def _init_task(task_id, total):
    progress_store[task_id] = {
        'current': 0,
        'total': total,
        'status': 'running',
        'file_ready': False,
        'error': '',
        'summary': '',
    }


def _finish_task(task_id, rows, sheet_name, columns, summary):
    task = progress_store.get(task_id, {})
    task['file_data'] = _build_report(rows, sheet_name, columns)
    task['file_ready'] = True
    task['summary'] = summary
    task['status'] = 'completed'


def _fail_task(task_id, error):
    task = progress_store.setdefault(task_id, {})
    task['status'] = 'error'
    task['error'] = str(error)


# ---------------------------------------------------------------------------
# 1. USER DND STATUS
# ---------------------------------------------------------------------------
DND_AUDIT_COLUMNS = ["Name", "Extension", "Extension ID", "DND State"]


def run_dnd_audit(task_id, token):
    """Report every enabled user's Do Not Disturb state."""
    try:
        users = fetch_all_users(token)
        _init_task(task_id, len(users))
        rows = []
        for idx, user in enumerate(users):
            progress_store[task_id]['current'] = idx + 1
            uid = user.get('id')
            succ, presence = safe_api_call(
                f'/restapi/v1.0/account/~/extension/{uid}/presence', token=token)
            dnd_state = presence.get('dndStatus', 'Unknown') if succ else f"Error: {format_api_error(presence)}"
            rows.append({
                "Name": user.get('name', ''),
                "Extension": user.get('extensionNumber', ''),
                "Extension ID": str(uid),
                "DND State": dnd_state,
            })
        full_dnd = sum(1 for r in rows if r["DND State"] == "DoNotAcceptAnyCalls")
        summary = (f"Audited {len(rows)} user(s). {full_dnd} on Full DND "
                   f"(DoNotAcceptAnyCalls).")
        _finish_task(task_id, rows, 'DND Status', DND_AUDIT_COLUMNS, summary)
    except Exception as e:
        logger.exception("DND audit failed")
        _fail_task(task_id, e)


DND_RESET_COLUMNS = ["Name", "Extension", "Extension ID", "Previous State", "New State", "Result"]


def run_dnd_reset(task_id, token):
    """Disable Full DND: any user on DoNotAcceptAnyCalls is set to TakeAllCalls."""
    try:
        users = fetch_all_users(token)
        _init_task(task_id, len(users))
        rows = []
        updated = 0
        for idx, user in enumerate(users):
            progress_store[task_id]['current'] = idx + 1
            uid = user.get('id')
            presence_url = f'/restapi/v1.0/account/~/extension/{uid}/presence'
            succ, presence = safe_api_call(presence_url, token=token)
            if not succ:
                continue
            if presence.get('dndStatus') != "DoNotAcceptAnyCalls":
                continue

            ok, resp = safe_api_call(
                presence_url, method='PUT',
                json_payload={"dndStatus": "TakeAllCalls"}, token=token)
            if ok:
                updated += 1
            rows.append({
                "Name": user.get('name', ''),
                "Extension": user.get('extensionNumber', ''),
                "Extension ID": str(uid),
                "Previous State": "DoNotAcceptAnyCalls",
                "New State": "TakeAllCalls" if ok else "DoNotAcceptAnyCalls",
                "Result": "Updated" if ok else f"Failed: {format_api_error(resp)}",
            })
        summary = (f"Disabled Full DND for {updated} of {len(rows)} affected user(s). "
                   f"Scanned {len(users)} user(s).")
        _finish_task(task_id, rows, 'DND Reset', DND_RESET_COLUMNS, summary)
    except Exception as e:
        logger.exception("DND reset failed")
        _fail_task(task_id, e)


# ---------------------------------------------------------------------------
# 2. CALL QUEUE STATUS
# ---------------------------------------------------------------------------
CQ_AUDIT_COLUMNS = ["User", "Extension", "Queue", "Queue Extension",
                    "Overall Status", "Queue Status"]


def _on_off(val):
    return "On" if val is True else "Off"


def run_cq_audit(task_id, token, queue_ids=None):
    """Report each member's overall (acceptQueueCalls) and per-queue
    (acceptCurrentQueueCalls) accept-call status across the selected queues."""
    try:
        queues = fetch_all_queues(token)
        if queue_ids:
            wanted = {str(q) for q in queue_ids}
            queues = [q for q in queues if q['id'] in wanted]
        _init_task(task_id, len(queues))
        rows = []
        for idx, queue in enumerate(queues):
            progress_store[task_id]['current'] = idx + 1
            qid = queue['id']
            succ, presence = safe_api_call(
                f'/restapi/v1.0/account/~/call-queues/{qid}/presence', token=token)
            if not succ:
                logger.warning("Skipping queue %s: %s", queue['name'], format_api_error(presence))
                continue
            for record in presence.get('records', []):
                member = record.get('member') or {}
                rows.append({
                    "User": member.get('name', ''),
                    "Extension": str(member.get('extensionNumber', '')),
                    "Queue": queue['name'],
                    "Queue Extension": queue['extensionNumber'],
                    "Overall Status": _on_off(record.get('acceptQueueCalls')),
                    "Queue Status": _on_off(record.get('acceptCurrentQueueCalls')),
                })
        summary = f"Audited {len(rows)} member entry(ies) across {len(queues)} queue(s)."
        _finish_task(task_id, rows, 'Queue Status', CQ_AUDIT_COLUMNS, summary)
    except Exception as e:
        logger.exception("CQ audit failed")
        _fail_task(task_id, e)


CQ_ENABLE_COLUMNS = ["User", "Extension", "Queue", "Scope", "Result"]


def run_cq_enable(task_id, token, queue_ids=None):
    """Turn on both the overall member status (acceptQueueCalls) and the
    per-queue status (acceptCurrentQueueCalls) for every member who is off."""
    try:
        queues = fetch_all_queues(token)
        if queue_ids:
            wanted = {str(q) for q in queue_ids}
            queues = [q for q in queues if q['id'] in wanted]
        _init_task(task_id, len(queues))
        rows = []
        globally_enabled = set()   # avoid re-enabling the same member per queue
        overall_updates = 0
        queue_updates = 0

        for idx, queue in enumerate(queues):
            progress_store[task_id]['current'] = idx + 1
            qid = queue['id']
            succ, presence = safe_api_call(
                f'/restapi/v1.0/account/~/call-queues/{qid}/presence', token=token)
            if not succ:
                logger.warning("Skipping queue %s: %s", queue['name'], format_api_error(presence))
                continue

            per_queue_records = []
            for record in presence.get('records', []):
                member = record.get('member') or {}
                member_id = member.get('id')
                member_name = member.get('name', '')
                member_ext = str(member.get('extensionNumber', ''))

                # Overall (global) member status — enable once per member.
                if record.get('acceptQueueCalls') is False and member_id not in globally_enabled:
                    ok, resp = safe_api_call(
                        f'/restapi/v1.0/account/~/extension/{member_id}/presence',
                        method='PUT', json_payload={"acceptCallQueueCalls": True}, token=token)
                    if ok:
                        globally_enabled.add(member_id)
                        overall_updates += 1
                    rows.append({
                        "User": member_name, "Extension": member_ext,
                        "Queue": "(Account-wide)", "Scope": "Overall",
                        "Result": "Enabled" if ok else f"Failed: {format_api_error(resp)}",
                    })

                # Per-queue status — batched into one PUT per queue.
                if record.get('acceptCurrentQueueCalls') is False:
                    per_queue_records.append({
                        "member": {"id": member_id},
                        "acceptCurrentQueueCalls": True,
                        "_name": member_name, "_ext": member_ext,
                    })

            if per_queue_records:
                payload = {"records": [
                    {"member": r["member"], "acceptCurrentQueueCalls": True}
                    for r in per_queue_records
                ]}
                ok, resp = safe_api_call(
                    f'/restapi/v1.0/account/~/call-queues/{qid}/presence',
                    method='PUT', json_payload=payload, token=token)
                for r in per_queue_records:
                    if ok:
                        queue_updates += 1
                    rows.append({
                        "User": r["_name"], "Extension": r["_ext"],
                        "Queue": queue['name'], "Scope": "Queue",
                        "Result": "Enabled" if ok else f"Failed: {format_api_error(resp)}",
                    })

        summary = (f"Enabled overall status for {overall_updates} member(s) and "
                   f"per-queue status for {queue_updates} entry(ies) across "
                   f"{len(queues)} queue(s).")
        _finish_task(task_id, rows, 'Queue Enable', CQ_ENABLE_COLUMNS, summary)
    except Exception as e:
        logger.exception("CQ enable failed")
        _fail_task(task_id, e)
