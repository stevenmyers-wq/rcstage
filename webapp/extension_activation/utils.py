"""Extension Activation — business logic and RingCentral calls.

The only job of this module is to change the *activation status* of RingCentral
extensions in bulk. In the RingCentral platform API this value is the
extension's ``status`` field, and the whole activation lifecycle of a user (or
any other extension) is expressed through it::

    NotActivated  — the extension exists but the user has never completed
                    activation (the Welcome email has not been accepted / the
                    account has not been set up). This is the state a freshly
                    provisioned user sits in until they activate.
    Enabled       — the extension is fully active and usable.
    Disabled      — the extension has been switched off by an administrator.
                    It is still assigned to its user but cannot be used.
    Unassigned    — the extension has no user attached to it (a spare / phantom
                    extension). System-managed — you assign a user to it rather
                    than flipping this flag directly.
    Frozen        — a transitional / administrative hold applied by RingCentral
                    (e.g. an account pending confirmation). System-managed.

RingCentral's ``ExtensionUpdateRequest`` schema accepts exactly
``Enabled | Disabled | NotActivated`` for the ``status`` field, so those three
are the write targets this tool offers; ``Unassigned`` and ``Frozen`` are not in
that enum (they are reached as side effects / managed by RingCentral) so they
are shown for context but never offered as a target. The common workflow the
tool is built for is taking a user from ``NotActivated`` (or ``Disabled``) to
``Enabled`` — i.e. activating / enabling them — with the reverse
``Enabled`` → ``Disabled`` to switch a user off and ``… → NotActivated`` to send
them back to the un-activated state.

When disabling, RingCentral also accepts an optional ``statusInfo`` object
carrying the *type of suspension* (``reason``: ``Voluntarily`` |
``Involuntarily``) and a free-form ``comment``; this tool passes those through
when supplied.

The status lives on the main extension body, so it is written with::

    PUT /restapi/v1.0/account/~/extension/{extensionId}
    {"status": "Enabled"}

RingCentral treats this as a partial update (only the ``status`` — and
``statusInfo`` when given — is sent, so nothing else on the extension is
touched) and enforces which transitions are legal server-side — some extension
types cannot be toggled, and some transitions are rejected outright. Those
errors are surfaced verbatim per-extension rather than pre-judged here.

The list endpoint simply enumerates every account extension (Users, Call
Queues, IVR menus, …) so the UI can offer Type / Site / current-Status filters
and a tick-box selection; the update endpoint pushes ``{"status": "<target>"}``
to each chosen extension via updateExtension.
"""
import io
import time

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from webapp.rc_api import rc_api_call
from webapp import task_control

# The activation states RingCentral's ExtensionUpdateRequest schema accepts for
# the ``status`` field. Everything else an extension can read as
# (Unassigned, Frozen) is a current-state RingCentral manages and is not in the
# update enum, so it is shown for context but never offered as a write target.
SETTABLE_STATUSES = ('Enabled', 'Disabled', 'NotActivated')

# Optional statusInfo.reason values RingCentral accepts when disabling an
# extension (the "type of suspension").
SUSPENSION_REASONS = ('Voluntarily', 'Involuntarily')

# Bulk template.
TEMPLATE_SHEET = 'Extension Activation'
TEMPLATE_HEADERS = [
    'Extension ID', 'Extension', 'Name', 'Type', 'Site',
    'Current Status', 'New Status', 'Suspension Reason', 'Comment',
]

# Common spellings the upload accepts for each canonical status, so an operator
# typing "Not Activated" / "Active" / "Off" still resolves cleanly.
_STATUS_SYNONYMS = {
    'enabled': 'Enabled', 'enable': 'Enabled', 'active': 'Enabled',
    'activate': 'Enabled', 'activated': 'Enabled', 'on': 'Enabled',
    'disabled': 'Disabled', 'disable': 'Disabled', 'inactive': 'Disabled',
    'off': 'Disabled', 'suspended': 'Disabled',
    'notactivated': 'NotActivated', 'not activated': 'NotActivated',
    'not-activated': 'NotActivated', 'unactivated': 'NotActivated',
    'reset': 'NotActivated',
}


def fetch_all_extensions(token):
    """Fetch every account extension (all pages) as raw records."""
    extensions = []
    page = 1
    while True:
        resp = rc_api_call(
            f"/restapi/v1.0/account/~/extension?perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        extensions.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)
    return extensions


def _display_name(record):
    """Best display name for an extension record (contact name, then name)."""
    contact = record.get('contact') or {}
    first = (contact.get('firstName') or '').strip()
    last = (contact.get('lastName') or '').strip()
    combined = f"{first} {last}".strip()
    return combined or (record.get('name') or '').strip() or '—'


def _site_name(record):
    """Resolve an extension's site name for the Site filter.

    A Site extension is its own site; everything else carries a ``site`` object
    when the account is multi-site. Single-site accounts omit it, so those fall
    back to the conventional "Main Site" label.
    """
    if record.get('type') == 'Site':
        return record.get('name') or 'Main Site'
    site = record.get('site') or {}
    return (site.get('name') or '').strip() or 'Main Site'


def build_extension_rows(token):
    """Enumerate every account extension for the selection table.

    Returns (rows, summary) where each row is::

        {id, extensionNumber, name, type, site, status}

    All extension types are returned — the UI filters by Type / Site / Status —
    because Users, Call Queues and other objects all carry an activation status.
    summary counts the total plus per-type and per-status breakdowns for the
    header and filters.
    """
    extensions = fetch_all_extensions(token)
    if extensions is None:
        return None, None

    rows = []
    for ext in extensions:
        rows.append({
            'id': ext.get('id'),
            'extensionNumber': ext.get('extensionNumber') or '',
            'name': _display_name(ext),
            'type': ext.get('type') or '—',
            'site': _site_name(ext),
            'status': ext.get('status') or '—',
        })

    # Sort by extension number (numeric where possible) for a stable view.
    def _sort_key(r):
        num = str(r.get('extensionNumber') or '')
        return (0, int(num)) if num.isdigit() else (1, num)
    rows.sort(key=_sort_key)

    by_type = {}
    by_status = {}
    for r in rows:
        by_type[r['type']] = by_type.get(r['type'], 0) + 1
        by_status[r['status']] = by_status.get(r['status'], 0) + 1
    summary = {'total': len(rows), 'by_type': by_type, 'by_status': by_status}
    return rows, summary


def _error_message(resp):
    """Human-readable error string from an RC response, preserving RC's message."""
    if resp is None:
        return 'No response from RingCentral'
    try:
        body = resp.json() or {}
    except Exception:
        body = {}
    msg = body.get('message') or body.get('error')
    if not msg and isinstance(body.get('errors'), list) and body['errors']:
        msg = body['errors'][0].get('message')
    if not msg:
        msg = getattr(resp, 'text', '') or f"HTTP {getattr(resp, 'status_code', '?')}"
    return str(msg)[:300]


def set_status(ext_id, status, token, reason=None, comment=None):
    """Set an extension's activation status (RingCentral ``status``).

    Sends only the ``status`` key (plus ``statusInfo`` when a suspension
    ``reason`` / ``comment`` is supplied) to the extension body so nothing else
    on the extension is disturbed (RingCentral merges the partial update).
    Returns (ok, message) — message is RingCentral's error text on failure (e.g.
    an illegal transition or an extension type that can't be toggled).
    ``status`` must be one of ``SETTABLE_STATUSES``; ``reason``, when given, one
    of ``SUSPENSION_REASONS``.
    """
    body = {'status': str(status)}
    status_info = {}
    if reason:
        status_info['reason'] = str(reason)
    if comment:
        status_info['comment'] = str(comment)
    if status_info:
        body['statusInfo'] = status_info

    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        method='PUT', json=body,
        token=token, return_response=True,
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, f'Status set to {status}'
    return False, _error_message(resp)


# ---------------------------------------------------------------------------
# Bulk XLSX template
# ---------------------------------------------------------------------------

def generate_template(token):
    """Build the bulk-update workbook, pre-filled with the account's current
    activation state.

    Every extension is written as a row keyed by its friendly Extension number
    (the stable Extension ID is carried alongside for exact matching), showing
    its Current Status and a ``New Status`` dropdown (Enabled / Disabled /
    NotActivated) the operator edits. A ``Suspension Reason`` dropdown
    (Voluntarily / Involuntarily) and free-form ``Comment`` apply only when the
    New Status is Disabled. Rows left unchanged are skipped on apply.
    """
    rows, _summary = build_extension_rows(token)
    if rows is None:
        raise Exception("Could not load account extensions. Token may be invalid or expired.")

    wb = Workbook()
    ws = wb.active
    ws.title = TEMPLATE_SHEET
    ws.append(TEMPLATE_HEADERS)

    for r in rows:
        current = r['status']
        ws.append([
            r['id'], r['extensionNumber'], r['name'], r['type'], r['site'],
            current,
            current,  # New Status defaults to current (a no-op until edited)
            '',       # Suspension Reason (only used when New Status is Disabled)
            '',       # Comment
        ])

    # Reference sheet backing the two dropdowns.
    ref = wb.create_sheet('Reference')
    ref['A1'] = 'Status'
    for i, v in enumerate(SETTABLE_STATUSES, start=2):
        ref.cell(row=i, column=1, value=v)
    ref['B1'] = 'Reason'
    for i, v in enumerate(SUSPENSION_REASONS, start=2):
        ref.cell(row=i, column=2, value=v)

    last = max(len(rows) + 1, 2)
    # New Status dropdown (column G). allow_blank so the pre-filled current
    # value survives even when it's a non-settable state (Unassigned / Frozen);
    # those rows are treated as no-ops unless the operator picks a new value.
    status_dv = DataValidation(
        type='list',
        formula1=f"=Reference!$A$2:$A${1 + len(SETTABLE_STATUSES)}",
        allow_blank=True,
    )
    ws.add_data_validation(status_dv)
    status_dv.add(f"G2:G{last}")
    # Suspension Reason dropdown (column H).
    reason_dv = DataValidation(
        type='list',
        formula1=f"=Reference!$B$2:$B${1 + len(SUSPENSION_REASONS)}",
        allow_blank=True,
    )
    ws.add_data_validation(reason_dv)
    reason_dv.add(f"H2:H{last}")

    widths = {'A': 14, 'B': 12, 'C': 30, 'D': 16, 'E': 18,
              'F': 16, 'G': 16, 'H': 18, 'I': 32}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ref.column_dimensions['A'].width = 14
    ref.column_dimensions['B'].width = 14

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# ---------------------------------------------------------------------------
# Cell cleaning
# ---------------------------------------------------------------------------

def _clean(value):
    """Normalise a spreadsheet cell to a trimmed string (handles NaN / '1001.0')."""
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text


def _parse_status(value):
    """Map a New Status cell to a canonical settable status.

    Accepts the dropdown values plus common synonyms (case-insensitive).
    Returns one of SETTABLE_STATUSES, or None if the value isn't recognised.
    """
    v = _clean(value)
    if not v:
        return None
    key = v.lower()
    if key in _STATUS_SYNONYMS:
        return _STATUS_SYNONYMS[key]
    # Exact (case-insensitive) match against the canonical settable values.
    for s in SETTABLE_STATUSES:
        if key == s.lower():
            return s
    return None


def _parse_reason(value):
    """Map a Suspension Reason cell to a canonical reason, or '' if blank,
    or None if a non-blank value isn't recognised."""
    v = _clean(value)
    if not v:
        return ''
    for s in SUSPENSION_REASONS:
        if v.lower() == s.lower():
            return s
    return None


# ---------------------------------------------------------------------------
# Bulk validate / apply (streamed NDJSON)
# ---------------------------------------------------------------------------

def process_upload_batch(records, token, is_preview=True, task_id=None):
    """Validate (preview) or apply (apply) activation-status changes from the
    uploaded rows, yielding NDJSON-friendly progress chunks:

        {"type": "start", ...}
        {"type": "progress", "result": {...}, "is_preview": bool}
        {"type": "cancelled", ...}   (apply only, on Stop)
        {"type": "done", "is_preview": bool}

    Each row is matched to a live extension (by Extension ID, else the friendly
    Extension number). Rows whose New Status equals the extension's current
    status are reported as no-ops and skipped; only genuine changes are applied.
    """
    total = len(records)
    yield {"type": "start", "total": total,
           "message": "Loading account extensions…"}

    rows, _summary = build_extension_rows(token)
    if rows is None:
        yield {"type": "error", "message": "Could not load account extensions. Token may be invalid or expired."}
        return

    by_id = {str(r['id']): r for r in rows if r.get('id')}
    by_number = {}
    for r in rows:
        num = str(r.get('extensionNumber') or '').strip()
        if num:
            by_number.setdefault(num, r)

    changed = 0
    for i, row in enumerate(records):
        if not is_preview and task_control.is_stopped(task_id):
            yield {"type": "cancelled", "current": i, "total": total,
                   "message": f"Stopped by user. {i} of {total} row(s) processed; the rest were skipped."}
            task_control.clear(task_id)
            return

        ext_id = _clean(row.get('Extension ID', ''))
        ext_num = _clean(row.get('Extension', ''))
        new_status_raw = row.get('New Status', '')
        reason_raw = row.get('Suspension Reason', '')
        comment = _clean(row.get('Comment', ''))

        def progress(status, message, target=None, new_val=None):
            return {
                "type": "progress",
                "current": i + 1,
                "total": total,
                "result": {
                    "row": i + 2,  # 1-based sheet row (header is row 1)
                    "ext": ext_num or (target or {}).get('extensionNumber') or "—",
                    "name": (target or {}).get('name') or "—",
                    "type": (target or {}).get('type') or "—",
                    "current": (target or {}).get('status') or "—",
                    "new": new_val if new_val is not None else (_clean(new_status_raw) or "—"),
                    "status": status,
                    "message": message,
                },
                "is_preview": is_preview,
            }

        # Fully blank row -> skip silently.
        if not any([ext_id, ext_num, _clean(new_status_raw)]):
            yield progress("info", "Skipped empty row")
            continue

        # Resolve the target extension (prefer the stable ID).
        target = by_id.get(ext_id) if ext_id else None
        if not target and ext_num:
            target = by_number.get(ext_num)
        if not target:
            key = ext_id or ext_num
            yield progress("error", f"No extension found for '{key}' on this account")
            continue

        desired = _parse_status(new_status_raw)
        if desired is None:
            yield progress(
                "error",
                f"Unrecognised New Status '{_clean(new_status_raw)}' "
                f"(use {', '.join(SETTABLE_STATUSES)})",
                target,
            )
            continue

        # No change requested -> nothing to do.
        if desired == target['status']:
            yield progress("info", f"Already {desired} — no change", target, new_val=desired)
            continue

        # Suspension reason / comment only apply when disabling.
        reason = ''
        if desired == 'Disabled':
            reason = _parse_reason(reason_raw)
            if reason is None:
                yield progress(
                    "error",
                    f"Unrecognised Suspension Reason '{_clean(reason_raw)}' "
                    f"(use {', '.join(SUSPENSION_REASONS)} or leave blank)",
                    target, new_val=desired,
                )
                continue
        else:
            comment = ''

        if is_preview:
            changed += 1
            arrow = f"{target['status']} → {desired}"
            yield progress("success", f"Will change {arrow}", target, new_val=desired)
            continue

        # Apply mode -- set the status.
        ok, msg = set_status(target['id'], desired, token,
                             reason=reason or None, comment=comment or None)
        if ok:
            changed += 1
            # Reflect the new status so a later row for the same ext compares right.
            target['status'] = desired
            yield progress("success", f"Set to {desired}", target, new_val=desired)
        else:
            yield progress("error", f"Update failed — {msg}", target, new_val=desired)
        time.sleep(0.1)

    task_control.clear(task_id)
    yield {"type": "done", "is_preview": is_preview, "changed": changed}
