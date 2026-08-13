import io
import os
import time
import base64
import requests
from urllib.parse import urlparse
import pandas as pd
from flask import current_app
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.rc_api import rc_api_call
from webapp.auth_utils import get_impersonation_token
from webapp import task_control

# Global store for background task progress
template_progress_store = {}

CHUNK_SIZE = 100

# RingCentral serialises template-apply account-wide: while one apply runs, any
# other batch is rejected with 409 "apply operation is in progress". On large
# accounts a single apply can take well over ten minutes, so we don't give up on
# a fixed short deadline -- we keep re-checking on an interval until the account
# frees up.
#
# POLL_INTERVAL_START: first wait after a 409 (short, so small/fast applies
#   resume quickly). POLL_INTERVAL_MAX: the interval widens up to this cap
#   (default 5 min) for long-running applies. ACCOUNT_LOCK_TIMEOUT: overall
#   safety ceiling so a genuinely stuck apply can't spin forever.
POLL_INTERVAL_START = 30
POLL_INTERVAL_MAX = 300          # 5 minutes
ACCOUNT_LOCK_TIMEOUT = 6 * 3600  # 6 hours


def _rc_base():
    try:
        return current_app.config.get('RC_SERVER_URL', 'https://platform.ringcentral.com')
    except Exception:
        return os.getenv('RC_SERVER_URL', 'https://platform.ringcentral.com')


def capture_auth(session):
    """Snapshot everything needed to KEEP a token alive after the request ends.

    A background job runs with no Flask session and an explicit token, so
    rc_api_call's built-in 401 refresh never fires. We copy the refresh material
    out of the session here (at request time) so the worker can re-mint the
    token itself. Covers both auth modes: SM impersonation bridge and standard
    RingCentral OAuth."""
    return {
        'token': session.get('sm_isolated_token') or session.get('rc_access_token'),
        'mode': 'sm' if session.get('sm_isolated_token') else 'rc',
        # SM impersonation path
        'sm_target_id': session.get('sm_target_id'),
        'sm_employee_token': session.get('sm_employee_token'),
        'sm_employee_refresh_token': session.get('sm_employee_refresh_token'),
        # Standard RingCentral OAuth path
        'rc_refresh_token': session.get('rc_refresh_token'),
        'rc_client_id': session.get('rc_current_client_id'),
    }


def _refresh_sm_employee(b):
    """Refresh the SM employee OAuth token from env creds + captured refresh
    token (no session). Returns the new employee token or None."""
    rt = b.get('sm_employee_refresh_token')
    cid = os.getenv('SM_CLIENT_ID')
    csecret = os.getenv('SM_CLIENT_SECRET')
    if not rt or not cid:
        return None
    data = {'grant_type': 'refresh_token', 'refresh_token': rt}
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
    if csecret:
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'{cid}:{csecret}'.encode()).decode()
    else:
        data['client_id'] = cid
    try:
        resp = requests.post(f'{_rc_base()}/restapi/oauth/token', data=data, headers=headers)
        if resp.ok:
            td = resp.json()
            b['sm_employee_token'] = td.get('access_token')
            if td.get('refresh_token'):
                b['sm_employee_refresh_token'] = td.get('refresh_token')
            return b['sm_employee_token']
    except Exception:
        pass
    return None


def _refresh_rc(b):
    """Refresh a standard RingCentral OAuth token from the captured refresh
    token + client id (no session). Returns the new access token or None."""
    rt = b.get('rc_refresh_token')
    cid = b.get('rc_client_id')
    if not rt or not cid:
        return None
    data = {'grant_type': 'refresh_token', 'refresh_token': rt, 'client_id': cid}
    try:
        resp = requests.post(f'{_rc_base()}/restapi/oauth/token', data=data,
                             headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if resp.ok:
            td = resp.json()
            if td.get('refresh_token'):
                b['rc_refresh_token'] = td.get('refresh_token')
            return td.get('access_token')
    except Exception:
        pass
    return None


def _mint_token(b):
    """Re-mint an access token from captured refresh material, session-free."""
    if b.get('mode') == 'sm':
        tid = b.get('sm_target_id')
        emp = b.get('sm_employee_token')
        # Cheap path: re-mint the impersonation bridge with the current employee
        # token (doesn't rotate anything session-critical).
        if tid and emp:
            t = get_impersonation_token(emp, tid)
            if t:
                return t
        # Employee token itself expired -- refresh it, then re-mint the bridge.
        emp2 = _refresh_sm_employee(b)
        if emp2 and tid:
            return get_impersonation_token(emp2, tid)
        return None
    return _refresh_rc(b)


class _Auth:
    """Carries the current access token and can re-mint it on demand."""
    def __init__(self, bundle):
        self._b = bundle
        self.token = bundle.get('token')

    def refresh(self):
        new = _mint_token(self._b)
        if new:
            self.token = new
        return new


def _rc_request(auth, endpoint, method='GET', json_payload=None):
    """rc_api_call for background work, with a session-free 401 refresh: if the
    call returns 401, re-mint the token once and retry."""
    resp = rc_api_call(endpoint, method=method, json=json_payload,
                       return_response=True, token=auth.token)
    if getattr(resp, 'status_code', None) == 401 and auth.refresh():
        resp = rc_api_call(endpoint, method=method, json=json_payload,
                           return_response=True, token=auth.token)
    return resp

# Statuses RingCentral reports while an async bulk task is still running.
# Anything NOT in this set is treated as terminal.
_NONTERMINAL_STATUSES = {
    "accepted", "pending", "inprogress", "in_progress", "running", "queued", "processing"
}

def safe_api_call(endpoint, method='GET', json_payload=None, token=None):
    """Helper to safely request API data while respecting 429 Rate Limits."""
    for attempt in range(4):
        resp = rc_api_call(endpoint, method=method, json=json_payload, return_response=True, token=token)
        status_code = getattr(resp, 'status_code', None)
        
        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 10
            time.sleep(retry_after + 1)
            continue
            
        if resp and getattr(resp, 'ok', False):
            try:
                return resp.json() or {"success": True}
            except:
                return {"success": True}
                
        if status_code and status_code >= 500:
            time.sleep(3)
            continue
            
        return None
    return None

def fetch_all_templates(token):
    """Fetches all user templates with pagination."""
    templates = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/templates?perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp: 
            break
        templates.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return templates

def fetch_all_users(token):
    """Fetches all active users, de-duplicated by extension id.

    RC's paginated extension list can return the same record on two pages if the
    directory changes between page fetches, which would otherwise put a user in
    the roster (and the apply) twice. Track seen ids so each user appears once.
    """
    users = []
    seen = set()
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp:
            break
        for u in resp['records']:
            if u.get('status') in ['Enabled', 'NotActivated'] and u.get('id') not in seen:
                seen.add(u.get('id'))
                users.append(u)
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)
    return users

def generate_audit_spreadsheet(token):
    """Generates an Excel file with User, Site, and a Template dropdown."""
    users = fetch_all_users(token)
    templates = fetch_all_templates(token)
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Template Assignment"

    # Removed the "Current Template" column
    headers = ["Extension ID", "Extension Number", "Name", "Site", "Template to Apply"]
    ws.append(headers)

    # Note: openpyxl data validation formula must be a comma-separated string
    template_names = [t.get('name', '').replace(',', '') for t in templates]
    dropdown_formula = f'"{",".join(template_names)}"'
    
    # We must allow blank so the user doesn't have to assign a template to EVERY row
    dv = DataValidation(type="list", formula1=dropdown_formula, allow_blank=True)
    ws.add_data_validation(dv)

    for index, user in enumerate(users, start=2):
        ext_id = str(user.get('id', ''))
        ext_num = user.get('extensionNumber', '')
        name = user.get('name', 'Unknown')
        site = user.get('site', {}).get('name', 'Main Site')
        
        ws.append([ext_id, ext_num, name, site, ""])
        
        # Add the validation to the 'Template to Apply' column (now Column E / 5)
        dv.add(ws.cell(row=index, column=5)) 

    # Auto-adjust column widths
    for column in ws.columns:
        length = max(len(str(cell.value) or "") for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def _read_body(resp):
    """Best-effort JSON body from a response (real or MockResponse)."""
    try:
        return resp.json() or {}
    except Exception:
        return {}


def _error_detail(resp):
    """Human-readable error string, preserving RingCentral's own message."""
    if resp is None:
        return 'No response from RingCentral'
    code = getattr(resp, 'status_code', '?')
    body = _read_body(resp)
    msg = body.get('message') or body.get('error')
    if not msg and isinstance(body.get('errors'), list) and body['errors']:
        msg = body['errors'][0].get('message')
    if not msg:
        msg = (getattr(resp, 'text', '') or 'unknown error')
    return f'HTTP {code}: {msg}'[:300]


def _looks_like_busy(resp):
    """Detect the 'a bulk task is already running' style rejection so we can
    wait and retry the submission instead of falsely reporting a failure."""
    txt = (_error_detail(resp) or '').lower()
    return any(k in txt for k in (
        'in progress', 'already', 'busy', 'concurrent', 'try again', 'another task', 'in process'
    ))


def _task_endpoint(resp):
    """Extract a pollable async-task path from a bulk-apply response, if any."""
    body = _read_body(resp)
    task = body.get('task') if isinstance(body.get('task'), dict) else {}
    uri = None
    try:
        uri = resp.headers.get('Location') if hasattr(resp, 'headers') else None
    except Exception:
        uri = None
    uri = uri or body.get('uri') or task.get('uri')
    if not uri:
        return None, (body.get('status') or task.get('status'))
    path = urlparse(uri).path if uri.startswith('http') else uri
    return path, (body.get('status') or task.get('status'))


def _poll_task(endpoint, auth, timeout=180):
    """Poll an async RC task until it reaches a terminal state.

    Best-effort and non-raising: on any uncertainty it returns the last status
    it saw so the caller degrades gracefully rather than misreporting success.
    Returns (final_status_or_None, detail).
    """
    if not endpoint:
        return None, None
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        resp = _rc_request(auth, endpoint, method='GET')
        code = getattr(resp, 'status_code', None)
        if code == 429:
            wait = int(resp.headers.get('Retry-After', 10)) + 1 if hasattr(resp, 'headers') else 10
            time.sleep(wait)
            continue
        body = _read_body(resp)
        last_status = body.get('status') or last_status
        if last_status and str(last_status).lower().replace(' ', '') not in _NONTERMINAL_STATUSES:
            return last_status, (body.get('message') or '')
        time.sleep(2)
    return last_status, 'Timed out waiting for the RingCentral task to finish'


def _format_elapsed(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    return f'{m}m {s}s' if m else f'{s}s'


def _interruptible_sleep(seconds, should_cancel):
    """Sleep in short slices so a cancel request is honoured promptly instead of
    only after a multi-minute wait. Returns True if cancellation was requested."""
    end = time.time() + seconds
    while time.time() < end:
        if should_cancel and should_cancel():
            return True
        time.sleep(min(2, max(0.1, end - time.time())))
    return bool(should_cancel and should_cancel())


def _apply_batch(t_id, ext_ids, auth, note=None, should_cancel=None):
    """Apply one template batch, waiting out RingCentral's account-wide lock.

    bulk-apply is asynchronous and serialised account-wide: a 2xx only means
    RingCentral *accepted* the job, and while it runs, any other batch is
    rejected with 409 "apply operation is in progress". Rather than give up on a
    fixed deadline, we keep re-checking whether the account will accept the batch
    on a widening interval (up to POLL_INTERVAL_MAX) until it does -- this is the
    only reliable "is it free yet?" signal RC gives us, since a busy account
    simply 409s and a 409 has no side effect. Once accepted we poll the returned
    task to a terminal state where the API exposes one.

    `note`, if given, is called with a human-readable status string while we
    wait, so the caller can surface progress to the UI. `should_cancel`, if
    given, is polled during waits; if it returns True we abort before sending.

    Returns (status, detail) where status is one of
    'Applied' | 'Failed' | 'Partial' | 'Submitted' | 'Cancelled'.
    """
    endpoint = f'/restapi/v1.0/account/~/templates/{t_id}/bulk-apply'
    body = {
        "extensionIds": ext_ids,
        "notifyUsers": False,
        # overrideAll was previously True, which forced EVERY section of the
        # template (site, call handling, phone/device options, caller ID) onto
        # each user -- overwriting settings the template was never meant to
        # touch. False applies only the sections the template actually
        # configures. Do not set this back to True.
        "overrideAll": False,
    }

    if should_cancel and should_cancel():
        return 'Cancelled', 'Stopped by user before this batch was sent'

    deadline = time.time() + ACCOUNT_LOCK_TIMEOUT
    started = time.time()
    interval = POLL_INTERVAL_START
    resp = None
    while True:
        resp = _rc_request(auth, endpoint, method='POST', json_payload=body)
        code = getattr(resp, 'status_code', None)

        if getattr(resp, 'ok', False):
            break

        remaining = deadline - time.time()
        if remaining <= 0:
            waited = _format_elapsed(time.time() - started)
            return 'Failed', f'RingCentral still busy after {waited}: {_error_detail(resp)}'

        if code == 429:
            wait = int(resp.headers.get('Retry-After', 30)) + 1 if hasattr(resp, 'headers') else 30
        elif code in (400, 409, 503) and _looks_like_busy(resp):
            # Another apply is still running account-wide. Wait, then re-check.
            wait = interval
            interval = min(POLL_INTERVAL_MAX, int(interval * 1.5))
            if note:
                waited = _format_elapsed(time.time() - started)
                next_in = int(min(wait, remaining))
                note(f'RingCentral is still applying a previous batch '
                     f'(waited {waited}); re-checking in {next_in}s...')
        else:
            # A genuine error (bad template, auth, malformed request): don't spin.
            return 'Failed', _error_detail(resp)

        if _interruptible_sleep(min(wait, max(1, remaining)), should_cancel):
            return 'Cancelled', 'Stopped by user while waiting for the account to free up'

    # Accepted -- poll the async task to a terminal state where possible.
    task_path, _initial_status = _task_endpoint(resp)
    final_status, detail = _poll_task(task_path, auth)

    if final_status is None:
        # No pollable task was exposed; we genuinely cannot confirm completion.
        return 'Submitted', 'Accepted by RingCentral (async; completion not confirmed)'
    norm = str(final_status).lower()
    if norm in ('completed', 'success', 'succeeded', 'done'):
        return 'Applied', 'Template applied'
    if 'error' in norm or 'partial' in norm:
        return 'Partial', detail or f'Task status: {final_status}'
    return 'Failed', detail or f'Task status: {final_status}'


def _snapshot_extension(ext_id, auth):
    """Capture a user's current key settings BEFORE any change, so there is a
    rollback reference. Read-only.

    Note: this is a lightweight snapshot (site + status). Full call-handling /
    phone-option state lives on separate endpoints and is not captured here.
    """
    resp = _rc_request(auth, f'/restapi/v1.0/account/~/extension/{ext_id}', method='GET')
    if not resp or not getattr(resp, 'ok', False):
        return {'Original Site': '', 'Original Status': '', 'Snapshot': 'unavailable'}
    body = _read_body(resp)
    return {
        'Original Site': (body.get('site') or {}).get('name', ''),
        'Original Status': body.get('status', ''),
        'Snapshot': 'captured',
    }


def process_upload_background(task_id, file_bytes, auth_bundle, sheet_name=None):
    """Parses the Excel file, snapshots current state, chunks the requests, and
    applies each batch via the async Bulk Apply task."""
    template_progress_store[task_id] = {
        'status': 'running', 'current': 0, 'total': 0, 'message': 'Parsing Excel file...',
        'results': []
    }
    store = template_progress_store[task_id]
    auth = _Auth(auth_bundle)

    try:
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=(sheet_name or 0))
        templates = fetch_all_templates(auth.token)
        template_map = {t.get('name', '').replace(',', ''): t['id'] for t in templates}
        template_names = {t['id']: t.get('name', '') for t in templates}

        # Group extension IDs by the template they need applied.
        #
        # De-dupe by extension: an ID that appears more than once (duplicate row,
        # or the same user assigned two templates) must be applied only ONCE.
        # Without this, a repeated ID lands in two different batches and gets
        # applied a full lock-cycle apart -- which looks like "batch 1 running
        # again as batch 2". First occurrence wins.
        application_batches = {}
        unmatched = []
        seen_ext = set()
        duplicates = 0
        for _, row in df.iterrows():
            template_name = row.get("Template to Apply")
            ext_id = row.get("Extension ID")
            if pd.isna(template_name) or str(template_name).strip() == "":
                continue
            t_name_clean = str(template_name).strip()
            if t_name_clean not in template_map:
                unmatched.append(t_name_clean)
                continue
            if pd.isna(ext_id):
                continue
            ext_clean = str(ext_id).split('.')[0].strip()
            if not ext_clean or ext_clean.lower() == 'nan':
                continue
            if ext_clean in seen_ext:
                duplicates += 1
                continue
            seen_ext.add(ext_clean)
            t_id = template_map[t_name_clean]
            application_batches.setdefault(t_id, []).append(ext_clean)

        assigned_count = sum(len(v) for v in application_batches.values())

        # Nothing to do. This is NOT success -- the old code marked total==0 as
        # 'completed', which the UI rendered as a green "Success!" while the
        # progress text was stuck on "Initializing...". Report it plainly.
        if assigned_count == 0:
            store['status'] = 'empty'
            store['total'] = 0
            if unmatched:
                store['message'] = (
                    f"Nothing applied: no rows matched a known template. "
                    f"{len(unmatched)} row(s) had unrecognised template names "
                    f"(e.g. '{unmatched[0]}'). Re-download a fresh roster and "
                    f"pick templates from the dropdown."
                )
            else:
                store['message'] = (
                    "Nothing applied: no rows had a template selected in the "
                    "'Template to Apply' column."
                )
            return

        # Chunk into batches of CHUNK_SIZE.
        tasks = []
        for t_id, ext_ids in application_batches.items():
            for i in range(0, len(ext_ids), CHUNK_SIZE):
                tasks.append((t_id, ext_ids[i:i + CHUNK_SIZE]))
        store['total'] = len(tasks)

        # Snapshot current settings for every affected user first (read-only),
        # so there is a rollback reference regardless of what happens next.
        store['message'] = f'Backing up current settings for {assigned_count} user(s)...'
        snapshots = {}
        for _t_id, ext_ids in application_batches.items():
            for ext_id in ext_ids:
                if ext_id not in snapshots:
                    snapshots[ext_id] = _snapshot_extension(ext_id, auth)
                    time.sleep(0.05)

        # Apply each batch, waiting for its async task to finish before the next
        # (RC runs these serially per account).
        cancelled = False
        for idx, (t_id, chunk) in enumerate(tasks):
            if task_control.is_stopped(task_id):
                cancelled = True
                break

            label = (
                f'Batch {idx + 1} of {len(tasks)} '
                f'("{template_names.get(t_id, t_id)}", {len(chunk)} user(s))'
            )
            store['message'] = f'Applying {label}...'
            status, detail = _apply_batch(
                t_id, chunk, auth,
                note=lambda m, _l=label: store.__setitem__('message', f'{_l}: {m}'),
                should_cancel=lambda: task_control.is_stopped(task_id)
            )

            if status == 'Cancelled':
                # Stopped before this batch was sent; record it as skipped and
                # stop -- do NOT send any further batches.
                for ext_id in chunk:
                    row = {
                        'Extension ID': ext_id,
                        'Template': template_names.get(t_id, t_id),
                        'Status': 'Skipped',
                        'Detail': detail,
                    }
                    row.update(snapshots.get(ext_id, {}))
                    store['results'].append(row)
                cancelled = True
                break

            for ext_id in chunk:
                row = {
                    'Extension ID': ext_id,
                    'Template': template_names.get(t_id, t_id),
                    'Status': status,
                    'Detail': detail,
                }
                row.update(snapshots.get(ext_id, {}))
                store['results'].append(row)
            store['current'] = idx + 1

        # Sent to RingCentral (applied or accepted) vs not.
        sent = sum(1 for r in store['results']
                   if r['Status'] in ('Applied', 'Submitted', 'Partial'))

        if cancelled:
            store['status'] = 'cancelled'
            store['message'] = (
                f'Stopped by user. {sent} user(s) in already-sent batches were '
                f'submitted to RingCentral and cannot be recalled; the remaining '
                f'batches were not sent.'
            )
            return

        # Summarise honestly -- 'Applied' means confirmed complete, anything
        # else is called out separately.
        applied = sum(1 for r in store['results'] if r['Status'] == 'Applied')
        failed = sum(1 for r in store['results'] if r['Status'] == 'Failed')
        other = len(store['results']) - applied - failed
        store['status'] = 'completed'
        parts = [f'{applied} confirmed applied']
        if failed:
            parts.append(f'{failed} failed')
        if other:
            parts.append(f'{other} submitted/partial (see results)')
        msg = 'Finished: ' + ', '.join(parts) + '.'
        if duplicates:
            msg += f' ({duplicates} duplicate row(s) ignored — each user applied once.)'
        store['message'] = msg

    except Exception as e:
        store['status'] = 'error'
        store['error'] = str(e)
    finally:
        # Clear the stop flag whatever the outcome so a later run that reuses
        # this task_id doesn't inherit a stale cancel.
        task_control.clear(task_id)
