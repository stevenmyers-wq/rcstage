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

Of those, only ``Enabled`` and ``Disabled`` are the administrator-settable
*activation* states, so those are the only two targets this tool offers. The
common workflow the tool is built for is taking a user from ``NotActivated`` (or
``Disabled``) to ``Enabled`` — i.e. activating / enabling them — and the reverse
``Enabled`` → ``Disabled`` to switch a user off.

The status lives on the main extension body, so it is written with::

    PUT /restapi/v1.0/account/~/extension/{extensionId}
    {"status": "Enabled"}

RingCentral treats this as a partial update (only the ``status`` key is sent, so
nothing else on the extension is touched) and enforces which transitions are
legal server-side — some extension types cannot be toggled, and some
transitions are rejected outright. Those errors are surfaced verbatim
per-extension rather than pre-judged here.

The list endpoint simply enumerates every account extension (Users, Call
Queues, IVR menus, …) so the UI can offer Type / Site / current-Status filters
and a tick-box selection; the update endpoint pushes ``{"status": "<target>"}``
to each chosen extension via updateExtension.
"""
import time

from webapp.rc_api import rc_api_call

# The two administrator-settable activation states. Everything else
# (NotActivated, Unassigned, Frozen) is a current-state that RingCentral manages
# or that is reached as a side effect, so it is shown for context but never
# offered as a write target.
SETTABLE_STATUSES = ('Enabled', 'Disabled')


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


def set_status(ext_id, status, token):
    """Set an extension's activation status (RingCentral ``status``).

    Sends only the ``status`` key to the extension body so nothing else on the
    extension is disturbed (RingCentral merges the partial update). Returns
    (ok, message) — message is RingCentral's error text on failure (e.g. an
    illegal transition or an extension type that can't be toggled). ``status``
    must be one of ``SETTABLE_STATUSES``.
    """
    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        method='PUT', json={'status': str(status)},
        token=token, return_response=True,
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, f'Status set to {status}'
    return False, _error_message(resp)
