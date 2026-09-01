"""Extension PIN — business logic and RingCentral calls.

The only job of this module is to (re)set the *mailbox PIN* on RingCentral
extensions. In the RingCentral platform API this value is the extension's
``ivrPin`` — the numeric code a user (or a Call Queue voicemail box, etc.) keys
in to check messages / log into the phone system by phone. It is write-only:
RingCentral never returns the current PIN, so this tool only ever *sets* a new
one on the extensions the operator ticks.

The PIN lives on the extension *credentials* sub-resource, not on the main
extension body, so it is written with::

    PUT /restapi/v1.0/account/~/extension/{extensionId}/credentials
    {"ivrPin": "<pin>"}

(the same resource also carries the login password / secret question, which this
tool never touches). RingCentral enforces the PIN rules server-side — 6–10
digits, digits only, no more than 2 repeating or 3 consecutive digits, etc. —
and rejects some extension types entirely (EXT-406). Those errors are surfaced
verbatim per-extension rather than pre-judged here.

The list endpoint simply enumerates every account extension (Users, Call
Queues, IVR menus, …) so the UI can offer Type / Site filters and a tick-box
selection; the update endpoint pushes ``{"ivrPin": "<pin>"}`` to each chosen
extension via updateExtension.
"""
import time

from webapp.rc_api import rc_api_call


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

    All extension types are returned — the UI filters by Type / Site — because
    Users, Call Queues and other mailbox-bearing objects can all carry an IVR
    PIN. summary counts the total plus a per-type breakdown for the header.
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
            'status': ext.get('status') or '',
        })

    # Sort by extension number (numeric where possible) for a stable view.
    def _sort_key(r):
        num = str(r.get('extensionNumber') or '')
        return (0, int(num)) if num.isdigit() else (1, num)
    rows.sort(key=_sort_key)

    by_type = {}
    for r in rows:
        by_type[r['type']] = by_type.get(r['type'], 0) + 1
    summary = {'total': len(rows), 'by_type': by_type}
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


def set_pin(ext_id, pin, token):
    """Set an extension's mailbox PIN (RingCentral ``ivrPin``).

    Writes only the PIN to the extension's credentials sub-resource (the login
    password / secret question on the same resource are left untouched). Returns
    (ok, message) — message is RingCentral's error text on failure (e.g. a PIN
    rule violation or EXT-406 for an unsupported extension type). The PIN is
    write-only in the API, so a success just confirms RingCentral accepted it.
    """
    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}/credentials",
        method='PUT', json={'ivrPin': str(pin)},
        token=token, return_response=True,
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, 'PIN updated'
    return False, _error_message(resp)
