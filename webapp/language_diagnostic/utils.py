import time
from webapp.rc_api import rc_api_call

# RingCentral exposes the set of selectable interface/greeting languages via the
# language dictionary. Each record carries an ``id`` (the numeric locale id used
# when writing ``regionalSettings.language`` / ``regionalSettings.greetingLanguage``
# on an extension) plus a handful of booleans describing where the language may
# be used:
#   - ``ui``                -> selectable as the account/extension interface language
#   - ``greeting``          -> selectable as a greeting language
#   - ``formattingLocale``  -> selectable as the number/date formatting locale
#   - ``isRegionalSupported`` -> regional (per-extension) selection is supported
# The support flags we care about for "can a user's language & greeting be set".
LANGUAGE_SUPPORT_FLAGS = ('ui', 'greeting', 'formattingLocale', 'isRegionalSupported')

LANGUAGE_DICTIONARY_ENDPOINT = '/restapi/v1.0/dictionary/language'

# What "English (AU)" looks like across the fields RingCentral might use. We
# match generously so the diagnostic still finds it if RC labels it "English
# (Australia)", "en-AU", "en_AU", etc.
EN_AU_LOCALE_CODES = {'en-au', 'en_au', 'enau'}
EN_AU_NAME_HINTS = ('australia', 'australian')


def _paged(endpoint, token):
    """Yields every record across all pages of a collection endpoint."""
    page = 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        resp = rc_api_call(
            f"{endpoint}{sep}perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        for rec in resp['records']:
            yield rec
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)


def _is_en_au(record):
    """True if a language dictionary record looks like English (Australia)."""
    locale = str(record.get('localeCode', '')).strip().lower()
    if locale in EN_AU_LOCALE_CODES:
        return True
    name = str(record.get('name', '')).strip().lower()
    # Only treat a name as en-AU when it's an English variant for Australia,
    # so we don't accidentally match an unrelated "Australia" entry.
    if 'english' in name and any(h in name for h in EN_AU_NAME_HINTS):
        return True
    return False


def _slim_language(record):
    """A record trimmed to the fields the diagnostic reports on."""
    slim = {
        'id': str(record.get('id', '')),
        'name': record.get('name', ''),
        'localeCode': record.get('localeCode', ''),
    }
    for flag in LANGUAGE_SUPPORT_FLAGS:
        slim[flag] = record.get(flag)
    return slim


def load_language_dictionary(token):
    """Read-only: fetch the RingCentral language dictionary and highlight any
    English (Australia) entry.

    Answers the first half of the question directly: does RingCentral even offer
    English (AU), and is it flagged as usable for the interface language
    (``ui``) and for greetings (``greeting``)?
    """
    records = list(_paged(LANGUAGE_DICTIONARY_ENDPOINT, token))

    en_au = [_slim_language(r) for r in records if _is_en_au(r)]

    supports_language = any(m.get('ui') for m in en_au)
    supports_greeting = any(m.get('greeting') for m in en_au)

    return {
        'endpoint': LANGUAGE_DICTIONARY_ENDPOINT,
        'total_languages': len(records),
        'english_au': {
            'found': bool(en_au),
            'matches': en_au,
            'supports_language_ui': supports_language,
            'supports_greeting': supports_greeting,
        },
        # Full slim list so the caller can eyeball every option RC actually
        # offers (the dictionary is small).
        'all_languages': [_slim_language(r) for r in records],
    }


def _resolve_extension_id(token, ext):
    """Resolve an extension number *or* id to an extension id. Returns
    (ext_id, ext_label) or (None, None) if it can't be found."""
    ext = str(ext).strip()
    if not ext:
        return None, None

    for e in _paged('/restapi/v1.0/account/~/extension', token):
        eid = str(e.get('id', ''))
        num = str(e.get('extensionNumber', '') or '').strip()
        if ext == eid or ext == num:
            name = e.get('name') or ''
            label = f"{name} (ext {num})" if num else (name or eid)
            return eid, label
    return None, None


def _read_regional(token, ext_id):
    """Return the extension's current regionalSettings.language and
    greetingLanguage as {'id','name'} pairs (empty dicts if unset)."""
    detail = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        token=token, raise_error=False
    )
    if not detail:
        return None
    regional = detail.get('regionalSettings', {}) or {}
    return {
        'language': regional.get('language', {}) or {},
        'greetingLanguage': regional.get('greetingLanguage', {}) or {},
    }


def read_extension_language(token, ext):
    """Read-only: report an extension's current language & greeting language."""
    ext_id, label = _resolve_extension_id(token, ext)
    if not ext_id:
        return {'found': False, 'error': f"No extension found for '{ext}'."}

    current = _read_regional(token, ext_id)
    if current is None:
        return {'found': False, 'error': 'Failed to read the extension settings.'}

    return {
        'found': True,
        'extension_id': ext_id,
        'extension': label,
        'current': current,
    }


def test_english_au(token, ext, fields=None, apply=False, keep=False):
    """The end-to-end diagnostic: can this extension's language and/or greeting
    language actually be set to English (AU)?

    Runs a **dry run** by default — it resolves the en-AU dictionary id, checks
    the support flags, and reports the exact payload it *would* send, without
    writing anything.

    With ``apply=True`` it performs the PUT, reads the extension back to confirm
    the change stuck, and (unless ``keep=True``) restores the original settings
    so the probe is non-destructive.

    ``fields`` selects which settings to test — any of 'language',
    'greetingLanguage'. Defaults to both.
    """
    fields = [f for f in (fields or ['language', 'greetingLanguage'])
              if f in ('language', 'greetingLanguage')]
    if not fields:
        fields = ['language', 'greetingLanguage']

    result = {
        'applied': bool(apply),
        'fields': fields,
    }

    # 1. Is English (AU) in the dictionary at all, and what's its id?
    dict_report = load_language_dictionary(token)
    en_au = dict_report['english_au']
    result['english_au_available'] = en_au
    if not en_au['found']:
        result['success'] = False
        result['message'] = ("RingCentral does not list English (Australia) in "
                             "the language dictionary for this account, so it "
                             "cannot be set.")
        return result

    en_au_entry = en_au['matches'][0]
    en_au_id = en_au_entry['id']
    result['target_language'] = en_au_entry

    # Warn (but don't hard-fail) if RC doesn't advertise the flag for a field
    # we're about to test — the write itself is the real proof.
    warnings = []
    if 'language' in fields and not en_au_entry.get('ui'):
        warnings.append("English (AU) is not flagged as a selectable interface "
                        "language (ui=false); setting it may be rejected.")
    if 'greetingLanguage' in fields and not en_au_entry.get('greeting'):
        warnings.append("English (AU) is not flagged as a selectable greeting "
                        "language (greeting=false); setting it may be rejected.")
    if warnings:
        result['warnings'] = warnings

    # 2. Resolve the extension and read its current settings.
    ext_id, label = _resolve_extension_id(token, ext)
    if not ext_id:
        result['success'] = False
        result['message'] = f"No extension found for '{ext}'."
        return result
    result['extension_id'] = ext_id
    result['extension'] = label

    original = _read_regional(token, ext_id)
    if original is None:
        result['success'] = False
        result['message'] = 'Failed to read the current extension settings.'
        return result
    result['original'] = original

    # 3. Build the payload that would set the requested fields to en-AU.
    regional_payload = {f: {'id': en_au_id} for f in fields}
    payload = {'regionalSettings': regional_payload}
    result['payload'] = payload

    if not apply:
        result['success'] = True
        result['message'] = ("Dry run only — no changes made. English (AU) was "
                             "found in the dictionary and the above payload is "
                             "what would be sent. Re-run with apply=true to "
                             "perform a live write test.")
        return result

    # 4. Live write test.
    put_resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}",
        method='PUT', json=payload, token=token, return_response=True
    )
    time.sleep(0.05)
    put_ok = put_resp is not None and getattr(put_resp, 'ok', False)
    result['write_status'] = getattr(put_resp, 'status_code', None)
    if not put_ok:
        err_text = ''
        try:
            err_text = str(put_resp.json().get('message', '')) if put_resp is not None else ''
        except Exception:
            err_text = getattr(put_resp, 'text', '') if put_resp is not None else ''
        result['success'] = False
        result['message'] = f"RingCentral rejected the change. {err_text}".strip()
        return result

    # 5. Read back to confirm the change actually stuck.
    after = _read_regional(token, ext_id)
    result['after'] = after
    verified = bool(after) and all(
        str((after.get(f) or {}).get('id', '')) == en_au_id for f in fields
    )
    result['verified'] = verified

    # 6. Restore the original values unless the caller asked to keep the change.
    if not keep:
        restore_payload = {'regionalSettings': {}}
        for f in fields:
            orig_id = str((original.get(f) or {}).get('id', ''))
            if orig_id:
                restore_payload['regionalSettings'][f] = {'id': orig_id}
        if restore_payload['regionalSettings']:
            restore_resp = rc_api_call(
                f"/restapi/v1.0/account/~/extension/{ext_id}",
                method='PUT', json=restore_payload, token=token, return_response=True
            )
            result['restored'] = restore_resp is not None and getattr(restore_resp, 'ok', False)
        else:
            # Nothing to restore to (the fields were unset originally); leave as-is.
            result['restored'] = False
            result.setdefault('warnings', []).append(
                "Original had no value for the tested field(s); the en-AU value "
                "was left in place because RC cannot unset it via this call.")

    result['success'] = verified
    result['message'] = ("English (AU) was accepted and verified on the extension."
                         if verified else
                         "The write was accepted but the read-back did not match "
                         "English (AU); RC may have silently ignored or mapped it.")
    return result
