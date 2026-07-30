import io
import time
from urllib.parse import urlparse
import pandas as pd
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.rc_api import rc_api_call

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
    """Fetches all active users."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp: 
            break
        for u in resp['records']:
            if u.get('status') in ['Enabled', 'NotActivated']:
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


def _poll_task(endpoint, token, timeout=180):
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
        resp = rc_api_call(endpoint, method='GET', return_response=True, token=token)
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


def _apply_batch(t_id, ext_ids, token, note=None):
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
    wait, so the caller can surface progress to the UI.

    Returns (status, detail) where status is one of
    'Applied' | 'Failed' | 'Partial' | 'Submitted'.
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

    deadline = time.time() + ACCOUNT_LOCK_TIMEOUT
    started = time.time()
    interval = POLL_INTERVAL_START
    resp = None
    while True:
        resp = rc_api_call(endpoint, method='POST', json=body, return_response=True, token=token)
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

        time.sleep(min(wait, max(1, remaining)))

    # Accepted -- poll the async task to a terminal state where possible.
    task_path, _initial_status = _task_endpoint(resp)
    final_status, detail = _poll_task(task_path, token)

    if final_status is None:
        # No pollable task was exposed; we genuinely cannot confirm completion.
        return 'Submitted', 'Accepted by RingCentral (async; completion not confirmed)'
    norm = str(final_status).lower()
    if norm in ('completed', 'success', 'succeeded', 'done'):
        return 'Applied', 'Template applied'
    if 'error' in norm or 'partial' in norm:
        return 'Partial', detail or f'Task status: {final_status}'
    return 'Failed', detail or f'Task status: {final_status}'


def _snapshot_extension(ext_id, token):
    """Capture a user's current key settings BEFORE any change, so there is a
    rollback reference. Read-only.

    Note: this is a lightweight snapshot (site + status). Full call-handling /
    phone-option state lives on separate endpoints and is not captured here.
    """
    resp = safe_api_call(f'/restapi/v1.0/account/~/extension/{ext_id}', token=token)
    if not resp:
        return {'Original Site': '', 'Original Status': '', 'Snapshot': 'unavailable'}
    return {
        'Original Site': (resp.get('site') or {}).get('name', ''),
        'Original Status': resp.get('status', ''),
        'Snapshot': 'captured',
    }


def process_upload_background(task_id, file_bytes, token):
    """Parses the Excel file, snapshots current state, chunks the requests, and
    applies each batch via the async Bulk Apply task."""
    template_progress_store[task_id] = {
        'status': 'running', 'current': 0, 'total': 0, 'message': 'Parsing Excel file...',
        'results': []
    }
    store = template_progress_store[task_id]

    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
        templates = fetch_all_templates(token)
        template_map = {t.get('name', '').replace(',', ''): t['id'] for t in templates}
        template_names = {t['id']: t.get('name', '') for t in templates}

        # Group extension IDs by the template they need applied.
        application_batches = {}
        unmatched = []
        for _, row in df.iterrows():
            template_name = row.get("Template to Apply")
            ext_id = row.get("Extension ID")
            if pd.isna(template_name) or str(template_name).strip() == "":
                continue
            t_name_clean = str(template_name).strip()
            if t_name_clean not in template_map:
                unmatched.append(t_name_clean)
                continue
            t_id = template_map[t_name_clean]
            application_batches.setdefault(t_id, []).append(str(ext_id).split('.')[0].strip())

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
                    snapshots[ext_id] = _snapshot_extension(ext_id, token)
                    time.sleep(0.05)

        # Apply each batch, waiting for its async task to finish before the next
        # (RC runs these serially per account).
        for idx, (t_id, chunk) in enumerate(tasks):
            label = (
                f'Batch {idx + 1} of {len(tasks)} '
                f'("{template_names.get(t_id, t_id)}", {len(chunk)} user(s))'
            )
            store['message'] = f'Applying {label}...'
            status, detail = _apply_batch(
                t_id, chunk, token,
                note=lambda m, _l=label: store.__setitem__('message', f'{_l}: {m}')
            )
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
        store['message'] = 'Finished: ' + ', '.join(parts) + '.'

    except Exception as e:
        store['status'] = 'error'
        store['error'] = str(e)
