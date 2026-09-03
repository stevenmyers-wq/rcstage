"""Caller ID (CLI) — business logic and RingCentral calls.

Every RingCentral extension exposes an outbound *caller ID* (CLI) per calling
feature via ``/restapi/v1.0/account/~/extension/{id}/caller-id``. The resource is
a ``byFeature`` list — each entry pairs a feature (RingOut, RingMe, CallLog, the
mobile app, …) with a ``callerId`` that is either a specific ``PhoneNumber``, the
caller's ``CurrentLocation``, or ``Blocked``.

This module lets an operator set the same account phone number as the caller ID
for a set of users, either by:

  * ticking users in-tool and choosing one number to apply, or
  * downloading a spreadsheet (users + a blank ``New Caller ID`` column), editing
    it, and uploading it back for a preview / apply.

When a number is applied to an extension, it is written to the extension's core
outbound calling features (``SETTABLE_FEATURES``) plus any other feature that is
already set to a specific phone number. Features intentionally left on
``CurrentLocation`` / ``Blocked`` (and the non-voice Fax/SMS/Voicemail features)
are not touched unless they already carry a number, so the change is predictable
and never trips the API by writing a number to a feature that can't accept one.
"""
import io
import time

import pandas as pd
from openpyxl.styles import Font

from webapp.rc_api import rc_api_call
from webapp import task_control


# ---------------------------------------------------------------------------
# Which extensions / features we work with
# ---------------------------------------------------------------------------
# Caller ID is a user concept, so the tick-list and template surface user
# extensions: standard Users and Limited extensions (the two types that place
# outbound calls and carry an editable CLI). Other object types either have no
# outbound CLI or are managed elsewhere in the Admin Portal.
USER_LIKE_TYPES = {'User', 'Limited'}

# The outbound calling features a number is always written to when a caller ID is
# applied, even if they are currently Blocked / CurrentLocation. These all accept
# a specific PhoneNumber. Fax / SMS / Voicemail are deliberately excluded and are
# only updated when they already carry a number (see build_caller_id_payload).
SETTABLE_FEATURES = [
    'RingOut', 'RingMe', 'CallLog', 'CommonPhone',
    'MobileApp', 'Alternate', 'AdditionalSoftphone', 'DelegatedCalling',
]

# The alias used for the account's primary ("Main") site.
MAIN_SITE_ID = 'main-site'
MAIN_SITE_NAME = 'Main Site'

# Bulk template layout. The download and the upload share these column names so
# the file the operator downloads is the same file they edit and upload back.
TEMPLATE_SHEET = 'Caller ID'
NUMBERS_SHEET = 'Caller ID Numbers'
TEMPLATE_HEADERS = ['Extension Number', 'Extension Name', 'Site', 'Type', 'New Caller ID']


# ---------------------------------------------------------------------------
# Paged fetching
# ---------------------------------------------------------------------------

def _paged(endpoint, token):
    """Yields every record across all pages of a collection endpoint."""
    page = 1
    while True:
        sep = '&' if '?' in endpoint else '?'
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


def _display_name(record):
    """Best display name for an extension record (contact name, then name)."""
    contact = record.get('contact') or {}
    first = (contact.get('firstName') or '').strip()
    last = (contact.get('lastName') or '').strip()
    combined = f"{first} {last}".strip()
    return combined or (record.get('name') or '').strip() or '—'


def _site_of(record):
    """Effective site name for an extension record, defaulting to Main Site."""
    site = record.get('site') or {}
    return (site.get('name') or '').strip() or MAIN_SITE_NAME


def _digits(value):
    """Comparable form of a phone number — its digits only (drops +, spaces …)."""
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


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


# ---------------------------------------------------------------------------
# Sites, users and available caller ID numbers
# ---------------------------------------------------------------------------

def list_sites(token):
    """Returns an ordered list of {'id','name'} for every site, including the
    Main Site alias. Used to populate the Site filter."""
    sites = [{'id': MAIN_SITE_ID, 'name': MAIN_SITE_NAME}]
    for s in _paged('/restapi/v1.0/account/~/sites', token):
        sid = str(s.get('id'))
        if sid == MAIN_SITE_ID:
            continue
        sites.append({'id': sid, 'name': s.get('name', '') or sid})
    return sites


def list_user_extensions(token):
    """Returns the User-family extensions worth setting a caller ID on.

    Each row is ``{id, extensionNumber, name, type, site, status}``. Only Enabled
    / NotActivated user extensions are returned (disabled ones can't take a CLI).
    """
    rows = []
    for e in _paged('/restapi/v1.0/account/~/extension', token):
        if e.get('type') not in USER_LIKE_TYPES or not e.get('id'):
            continue
        if e.get('status') not in ('Enabled', 'NotActivated', None):
            continue
        rows.append({
            'id': str(e.get('id')),
            'extensionNumber': str(e.get('extensionNumber') or ''),
            'name': _display_name(e),
            'type': e.get('type') or '—',
            'site': _site_of(e),
            'status': e.get('status') or '',
        })

    def _sort_key(r):
        num = r.get('extensionNumber') or ''
        return (0, int(num)) if num.isdigit() else (1, num)
    rows.sort(key=_sort_key)
    return rows


def list_caller_id_numbers(token):
    """Returns the account phone numbers usable as a caller ID.

    Each row is ``{id, phoneNumber, label, usageType}``. These populate the
    'Caller ID to apply' dropdown and the template's reference sheet. RingCentral
    still enforces per-extension eligibility on apply, so an ineligible choice is
    reported per row rather than hidden here.
    """
    numbers = []
    for n in _paged('/restapi/v1.0/account/~/phone-number', token):
        phone = str(n.get('phoneNumber') or '').strip()
        if not phone:
            continue
        numbers.append({
            'id': str(n.get('id')),
            'phoneNumber': phone,
            'label': str(n.get('label') or '').strip(),
            'usageType': str(n.get('usageType') or '').strip(),
        })
    numbers.sort(key=lambda r: r['phoneNumber'])
    return numbers


def build_number_maps(token):
    """Returns (by_id, by_digits) for the account's caller ID numbers so an
    operator-supplied id or typed phone number can be resolved to a real number.
    """
    numbers = list_caller_id_numbers(token)
    by_id = {n['id']: n for n in numbers}
    by_digits = {}
    for n in numbers:
        by_digits.setdefault(_digits(n['phoneNumber']), n)
    return by_id, by_digits


def build_ext_number_map(token):
    """Returns {extensionNumber (str): extensionId (str)} across every extension."""
    ext_map = {}
    for e in _paged('/restapi/v1.0/account/~/extension', token):
        num = str(e.get('extensionNumber') or '').strip()
        if num and e.get('id'):
            ext_map[num] = str(e['id'])
    return ext_map


# ---------------------------------------------------------------------------
# Reading / applying a caller ID
# ---------------------------------------------------------------------------

def get_caller_id(token, ext_id):
    """Returns the raw caller-id resource for an extension, or None."""
    return rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}/caller-id",
        token=token, raise_error=False
    )


def current_caller_id_number(resource):
    """Best single 'current caller ID' number for display: the RingOut feature's
    number when set, else the first feature that carries a specific number."""
    if not resource:
        return ''
    by_feature = resource.get('byFeature') or []
    ringout = next((f for f in by_feature if f.get('feature') == 'RingOut'), None)
    ordered = ([ringout] if ringout else []) + by_feature
    for f in ordered:
        if not f:
            continue
        cid = f.get('callerId') or {}
        if cid.get('type') == 'PhoneNumber':
            info = cid.get('phoneInfo') or {}
            num = str(info.get('phoneNumber') or '').strip()
            if num:
                return num
    return ''


def build_caller_id_payload(resource, number_id):
    """Builds the byFeature PUT payload that sets ``number_id`` as the caller ID.

    A feature is set to the number when it is one of the core outbound features
    (SETTABLE_FEATURES) or it already carries a specific PhoneNumber. Returns
    (payload, feature_names). An empty feature list means there is nothing safe to
    change.
    """
    by_feature = (resource or {}).get('byFeature') or []
    settable = set(SETTABLE_FEATURES)
    features = []
    new_by_feature = []
    for entry in by_feature:
        feat = entry.get('feature')
        if not feat:
            continue
        cid = entry.get('callerId') or {}
        already_number = cid.get('type') == 'PhoneNumber'
        if feat in settable or already_number:
            new_by_feature.append({
                'feature': feat,
                'callerId': {'type': 'PhoneNumber', 'phoneInfo': {'id': str(number_id)}},
            })
            features.append(feat)
    return {'byFeature': new_by_feature}, features


def apply_caller_id(token, ext_id, number_id):
    """Set ``number_id`` as the caller ID for an extension's calling features.

    Returns (ok, message, current_number). ``current_number`` is the extension's
    caller ID before the change, for reporting the from → to transition.
    """
    resource = get_caller_id(token, ext_id)
    if not resource:
        return False, 'Could not read the current caller ID settings.', ''

    current_number = current_caller_id_number(resource)
    payload, features = build_caller_id_payload(resource, number_id)
    if not features:
        return False, 'No caller-ID-capable calling features on this extension.', current_number

    resp = rc_api_call(
        f"/restapi/v1.0/account/~/extension/{ext_id}/caller-id",
        method='PUT', json=payload, token=token, return_response=True
    )
    if resp is not None and getattr(resp, 'ok', False):
        return True, f"Applied to {len(features)} feature(s).", current_number
    return False, _error_message(resp), current_number


# ---------------------------------------------------------------------------
# Bulk XLSX template
# ---------------------------------------------------------------------------

def generate_template(token):
    """Build the bulk-update workbook: one editable sheet listing every user with
    a blank ``New Caller ID`` column, plus a reference sheet of the account's
    available caller ID numbers."""
    users = list_user_extensions(token)
    numbers = list_caller_id_numbers(token)

    df_users = pd.DataFrame(
        [{
            'Extension Number': u['extensionNumber'],
            'Extension Name': u['name'],
            'Site': u['site'],
            'Type': u['type'],
            'New Caller ID': '',
        } for u in users],
        columns=TEMPLATE_HEADERS,
    )
    df_numbers = pd.DataFrame(
        [{
            'Phone Number': n['phoneNumber'],
            'Label': n['label'],
            'Usage Type': n['usageType'],
        } for n in numbers],
        columns=['Phone Number', 'Label', 'Usage Type'],
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_users.to_excel(writer, index=False, sheet_name=TEMPLATE_SHEET)
        df_numbers.to_excel(writer, index=False, sheet_name=NUMBERS_SHEET)

        for sheet_name in (TEMPLATE_SHEET, NUMBERS_SHEET):
            ws = writer.sheets[sheet_name]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for column in ws.columns:
                length = max((len(str(cell.value)) if cell.value is not None else 0)
                             for cell in column)
                ws.column_dimensions[column[0].column_letter].width = min(length + 4, 45)

    output.seek(0)
    return output


def _clean(value):
    """Normalise a spreadsheet cell to a trimmed string (handles NaN / '1001.0')."""
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text


# ---------------------------------------------------------------------------
# Streamed apply — shared by the in-tool selection and the template upload
# ---------------------------------------------------------------------------

def _emit(i, total, is_preview, ext, name, from_num, to_num, status, message):
    return {
        'type': 'row', 'current': i + 1, 'total': total, 'is_preview': is_preview,
        'ext': ext, 'name': name, 'from': from_num, 'to': to_num,
        'status': status, 'message': message,
    }


def apply_selection_batch(targets, token, number_id, is_preview=False, task_id=None):
    """Generator yielding NDJSON-friendly progress dicts while previewing / applying
    one chosen caller ID number to a set of ticked users.

    ``targets`` is a list of ``{id, extensionNumber, name}`` (as sent by the
    tick-list). ``number_id`` is the account phone-number id to apply.
    """
    total = len(targets)
    yield {'type': 'start', 'total': total, 'message': 'Loading caller ID numbers…'}

    by_id, _by_digits = build_number_maps(token)
    number = by_id.get(str(number_id))
    if not number:
        yield {'type': 'error', 'message': 'The selected caller ID number was not found on this account.'}
        return
    to_num = number['phoneNumber']

    updated = skipped = errors = 0
    for i, t in enumerate(targets):
        if not is_preview and task_control.is_stopped(task_id):
            yield {'type': 'cancelled', 'current': i, 'total': total,
                   'message': f"Stopped by user. {i} of {total} user(s) processed; the rest were skipped."}
            task_control.clear(task_id)
            return

        ext_id = str(t.get('id') or '')
        ext_num = str(t.get('extensionNumber') or '')
        name = t.get('name') or ''
        if not ext_id:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', to_num, 'error', 'Missing extension id.')
            continue

        resource = get_caller_id(token, ext_id)
        time.sleep(0.05)
        if not resource:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', to_num, 'error',
                        'Could not read the current caller ID settings.')
            continue

        from_num = current_caller_id_number(resource)
        _payload, features = build_caller_id_payload(resource, number_id)
        if not features:
            skipped += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'skipped',
                        'No caller-ID-capable calling features.')
            continue
        if _digits(from_num) == _digits(to_num):
            skipped += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'skipped',
                        'Already set — no change.')
            continue
        if is_preview:
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'pending', 'Will update.')
            continue

        ok, msg, _cur = apply_caller_id(token, ext_id, number_id)
        time.sleep(0.05)
        if ok:
            updated += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'applied', msg)
        else:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'error', msg)

    if not is_preview:
        task_control.clear(task_id)
    yield {'type': 'done', 'is_preview': is_preview,
           'updated': updated, 'skipped': skipped, 'errors': errors, 'total': total}


def apply_upload_batch(records, token, is_preview=False, task_id=None):
    """Generator yielding NDJSON-friendly progress dicts while previewing / applying
    caller ID changes from an uploaded template file.

    Each row is matched to an extension by ``Extension Number`` and the
    ``New Caller ID`` cell is resolved to an account phone number.
    """
    total = len(records)
    yield {'type': 'start', 'total': total, 'message': 'Loading account directory…'}

    account = rc_api_call('/restapi/v1.0/account/~', token=token, raise_error=False)
    if not account:
        yield {'type': 'error', 'message': 'Unauthorized. Token expired or invalid.'}
        return

    ext_map = build_ext_number_map(token)
    _by_id, by_digits = build_number_maps(token)

    updated = skipped = errors = 0
    for i, row in enumerate(records):
        if not is_preview and task_control.is_stopped(task_id):
            yield {'type': 'cancelled', 'current': i, 'total': total,
                   'message': f"Stopped by user. {i} of {total} row(s) processed; the rest were skipped."}
            task_control.clear(task_id)
            return

        ext_num = _clean(row.get('Extension Number', '')).split('.')[0]
        name = _clean(row.get('Extension Name', ''))
        desired_raw = _clean(row.get('New Caller ID', ''))

        # Fully blank row -> skip silently.
        if not ext_num and not desired_raw:
            continue

        if not ext_num:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', desired_raw, 'error', 'Missing Extension Number.')
            continue
        if not desired_raw:
            skipped += 1
            yield _emit(i, total, is_preview, ext_num, name, '', '', 'skipped', 'No New Caller ID — left unchanged.')
            continue

        ext_id = ext_map.get(ext_num)
        if not ext_id:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', desired_raw, 'error',
                        f"No extension found for number {ext_num}.")
            continue

        number = by_digits.get(_digits(desired_raw))
        if not number:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', desired_raw, 'error',
                        f"'{desired_raw}' is not an available caller ID number on this account.")
            continue
        to_num = number['phoneNumber']

        resource = get_caller_id(token, ext_id)
        time.sleep(0.05)
        if not resource:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, '', to_num, 'error',
                        'Could not read the current caller ID settings.')
            continue

        from_num = current_caller_id_number(resource)
        _payload, features = build_caller_id_payload(resource, number['id'])
        if not features:
            skipped += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'skipped',
                        'No caller-ID-capable calling features.')
            continue
        if _digits(from_num) == _digits(to_num):
            skipped += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'skipped', 'Already set — no change.')
            continue
        if is_preview:
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'pending', 'Will update.')
            continue

        ok, msg, _cur = apply_caller_id(token, ext_id, number['id'])
        time.sleep(0.05)
        if ok:
            updated += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'applied', msg)
        else:
            errors += 1
            yield _emit(i, total, is_preview, ext_num, name, from_num, to_num, 'error', msg)

    if not is_preview:
        task_control.clear(task_id)
    yield {'type': 'done', 'is_preview': is_preview,
           'updated': updated, 'skipped': skipped, 'errors': errors, 'total': total}
