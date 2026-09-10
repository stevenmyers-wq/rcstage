"""Extension Region — business logic and RingCentral calls.

This tool sets the *regional language* settings on RingCentral extensions:

  - ``regionalSettings.language``         — the user's interface / account language
  - ``regionalSettings.greetingLanguage`` — the language used for system greetings

Both reference an id from RingCentral's language dictionary
(``/restapi/v1.0/dictionary/language``); e.g. English (Australian) is id ``3081``
(localeCode ``en-AU``). They live on the main extension body, so they are written
with::

    PUT /restapi/v1.0/account/~/extension/{extensionId}
    {"regionalSettings": {"language": {"id": "3081"}, "greetingLanguage": {"id": "3081"}}}

The list endpoint enumerates every account extension so the UI can offer Type /
Site filters and a tick-box selection; the languages endpoint feeds the two
dropdowns; the update endpoint pushes the chosen language id(s) to each ticked
extension.
"""
import time

from webapp.rc_api import rc_api_call

LANGUAGE_DICTIONARY_ENDPOINT = '/restapi/v1.0/dictionary/language'


# ---------------------------------------------------------------------------
# Language dictionary
# ---------------------------------------------------------------------------

def load_languages(token):
    """Return the selectable languages from the RingCentral dictionary.

    Each record is trimmed to what the UI needs, plus the ``ui`` / ``greeting``
    flags so the client can show which values are valid for the interface
    language vs. the greeting language dropdown.
    """
    records = []
    page = 1
    while True:
        resp = rc_api_call(
            f"{LANGUAGE_DICTIONARY_ENDPOINT}?perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        records.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)

    languages = [{
        'id': str(r.get('id', '')),
        'name': r.get('name', ''),
        'localeCode': r.get('localeCode', ''),
        'ui': bool(r.get('ui')),
        'greeting': bool(r.get('greeting')),
    } for r in records]
    languages.sort(key=lambda l: l['name'].lower())
    return languages


# ---------------------------------------------------------------------------
# Extension list (mirrors the Extension PIN tool's selection table)
# ---------------------------------------------------------------------------

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
    """Resolve an extension's site name for the Site filter."""
    if record.get('type') == 'Site':
        return record.get('name') or 'Main Site'
    site = record.get('site') or {}
    return (site.get('name') or '').strip() or 'Main Site'


def build_extension_rows(token):
    """Enumerate every account extension for the selection table.

    Returns (rows, summary) where each row is
    ``{id, extensionNumber, name, type, site, status}``. The UI filters by
    Type / Site. The current language is not fetched here — it lives on the
    per-extension detail, not the list endpoint, so reading it for every
    extension would be far too many calls; the update reports the applied value.
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

    def _sort_key(r):
        num = str(r.get('extensionNumber') or '')
        return (0, int(num)) if num.isdigit() else (1, num)
    rows.sort(key=_sort_key)

    by_type = {}
    for r in rows:
        by_type[r['type']] = by_type.get(r['type'], 0) + 1
    summary = {'total': len(rows), 'by_type': by_type}
    return rows, summary


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

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


def set_region(ext_id, language_id, greeting_language_id, token):
    """Set an extension's language and/or greeting language.

    Only the fields provided (non-empty) are written, so the operator can change
    just the interface language, just the greeting language, or both. Returns
    (ok, message) — message is RingCentral's error text on failure (e.g. an
    unsupported extension type).
    """
    regional = {}
    if language_id:
        regional['language'] = {'id': str(language_id)}
    if greeting_language_id:
        regional['greetingLanguage'] = {'id': str(greeting_language_id)}

    if not regional:
        return False, 'Nothing to update (no language selected).'

    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        method='PUT', json={'regionalSettings': regional},
        token=token, return_response=True,
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, 'Language settings updated'
    return False, _error_message(resp)
