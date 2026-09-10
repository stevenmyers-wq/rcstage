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

    Each record is trimmed to what the UI needs, plus the ``ui`` / ``greeting`` /
    ``formattingLocale`` flags so the client can show which values are valid for
    the interface language, the greeting language, and the formatting locale
    dropdowns respectively.
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
        'formattingLocale': bool(r.get('formattingLocale')),
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


def _read_regional_ids(ext_id, token):
    """Return the extension's current (language, greetingLanguage,
    formattingLocale) ids as strings ('' when unset), or None if the read
    fails."""
    detail = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        token=token, raise_error=False,
    )
    if not detail:
        return None
    regional = detail.get('regionalSettings', {}) or {}
    return (
        str((regional.get('language') or {}).get('id', '') or ''),
        str((regional.get('greetingLanguage') or {}).get('id', '') or ''),
        str((regional.get('formattingLocale') or {}).get('id', '') or ''),
    )


def set_region(ext_id, language_id, greeting_language_id, formatting_locale_id, token):
    """Set an extension's language settings.

    RingCentral rejects a partial ``regionalSettings`` language update — when any
    of the three language fields is written they must *all* be specified
    together (``language``, ``greetingLanguage``, ``formattingLocale``). So we
    read the extension's current values first and send all three, using the
    operator's chosen value where provided and falling back to the current value
    otherwise. If a field is unset on the extension and not chosen, it defaults
    to the other chosen value so the three stay consistent and valid.

    Returns (ok, message) — message is RingCentral's error text on failure (e.g.
    an unsupported extension type).
    """
    language_id = str(language_id or '').strip()
    greeting_language_id = str(greeting_language_id or '').strip()
    formatting_locale_id = str(formatting_locale_id or '').strip()

    if not (language_id or greeting_language_id or formatting_locale_id):
        return False, 'Nothing to update (no language selected).'

    current = _read_regional_ids(ext_id, token)
    if current is None:
        return False, 'Could not read the extension to merge language settings.'
    cur_lang, cur_greet, cur_fmt = current

    # A sensible fallback for any field that is neither chosen nor currently set,
    # so all three are always populated with a valid id.
    fallback = next((v for v in (language_id, greeting_language_id,
                                 formatting_locale_id, cur_lang, cur_greet,
                                 cur_fmt) if v), '')

    final_lang = language_id or cur_lang or fallback
    final_greet = greeting_language_id or cur_greet or fallback
    final_fmt = formatting_locale_id or cur_fmt or fallback

    if not (final_lang and final_greet and final_fmt):
        return False, 'Could not determine all language settings to apply.'

    regional = {
        'language': {'id': final_lang},
        'greetingLanguage': {'id': final_greet},
        'formattingLocale': {'id': final_fmt},
    }

    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        method='PUT', json={'regionalSettings': regional},
        token=token, return_response=True,
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, 'Language settings updated'
    return False, _error_message(resp)
