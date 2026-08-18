import time
import pandas as pd
from webapp.rc_api import rc_api_call

# --- CONSTANTS -------------------------------------------------------------
#
# This tool sets how long a user's PHYSICAL desk phone (HardPhone) or generic /
# BYO SIP phone (OtherPhone) rings on the DEFAULT call-handling state before the
# call moves on (voicemail / next action). The modern RingCentral model
# (V2 comm-handling) expresses this as a `duration` in SECONDS on the
# RingGroupAction that contains the device's ring target; the legacy V1
# answering-rule model expresses it as a `ringCount` (number of rings) on the
# matching forwarding rule. We lead with V2 and fall back to V1, mirroring the
# Custom Rules module's V1/V2 fallback strategy.

# Only physical endpoints carry a meaningful "deskphone" ring time. Softphones
# (desktop / mobile apps) are deliberately excluded — they are governed by the
# app-ring settings, not the deskphone ring time.
RING_DEVICE_TYPES = {'HardPhone', 'OtherPhone'}

# Human label for the audit's Device Type column.
DEVICE_TYPE_LABELS = {
    'HardPhone': 'Desk Phone (HardPhone)',
    'OtherPhone': 'Generic SIP (OtherPhone)',
}

# RingCentral rings last ~5 seconds each, so seconds <-> rings convert on a
# factor of 5. The RingEX admin console offers the deskphone ring time as a
# dropdown in 5-second steps up to 60 seconds (12 rings); we mirror exactly that
# set as the spreadsheet's data-validation list so an operator can only enter an
# API-accepted value.
SECONDS_PER_RING = 5
ACCEPTED_RING_SECONDS = list(range(5, 61, 5))  # 5,10,15,...,60

# The default call-handling state. On both the V2 comm-handling and the V1
# answering-rule endpoints the default (business-hours / 24-7) rule shares this
# well-known id.
DEFAULT_STATE_RULE_ID = 'business-hours-rule'

V2_STATE_RULE_BASE = "/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"


# --- RESILIENT API WRAPPER -------------------------------------------------
#
# rc_api_call already self-heals an expired token (it refreshes on a 401 and
# retries the call once), but it does NOT retry rate limits (429) or transient
# gateway errors (5xx) — it just returns None. On a large account a 429 that
# lands mid-pagination would otherwise be read as "no more records" and silently
# truncate the crawl, which is exactly what makes a big audit look like it
# "stopped in the middle". This wrapper honours Retry-After on a 429, backs off
# on a 5xx, and returns the response object so callers can distinguish a real
# failure from an empty-but-successful page.

_MAX_ATTEMPTS = 6
_RETRY_AFTER_FALLBACK = 5
_RETRY_AFTER_CAP = 60


def safe_request(endpoint, method='GET', params=None, json_body=None,
                 max_attempts=_MAX_ATTEMPTS):
    """Calls rc_api_call with 429 (Retry-After) and 5xx backoff. Returns the
    response object (real or MockResponse); never raises for HTTP status."""
    resp = None
    for attempt in range(max_attempts):
        resp = rc_api_call(endpoint, params=params, method=method,
                           json=json_body, return_response=True)
        status = getattr(resp, 'status_code', None)

        # Rate limited: wait the server-advised window, then retry.
        if status == 429:
            retry_after = _RETRY_AFTER_FALLBACK
            headers = getattr(resp, 'headers', None)
            if headers is not None:
                try:
                    retry_after = int(headers.get('Retry-After', _RETRY_AFTER_FALLBACK))
                except (TypeError, ValueError):
                    retry_after = _RETRY_AFTER_FALLBACK
            time.sleep(min(retry_after + 1, _RETRY_AFTER_CAP))
            continue

        # Transient server / gateway error: exponential-ish backoff, then retry.
        if status is not None and status >= 500:
            time.sleep(min(2 * (attempt + 1), 30))
            continue

        # Success or a non-retryable 4xx (e.g. 404 → fall through to caller).
        break
    return resp


def _safe_json(endpoint, params=None):
    """GET helper: returns parsed JSON on success, else None."""
    resp = safe_request(endpoint, params=params)
    if resp is not None and getattr(resp, 'ok', False):
        try:
            return resp.json()
        except Exception:
            return {}
    return None


# --- VALUE HELPERS ---------------------------------------------------------

def coerce_ring_seconds(value):
    """Validates a spreadsheet cell into an API-accepted ring time in seconds.

    Returns an int from ACCEPTED_RING_SECONDS, or None when the cell is blank or
    cannot be read as a positive number. A number that isn't already on the 5s
    grid is rounded to the nearest accepted value (and clamped into range) so a
    hand-typed '22' still lands on a value RingCentral accepts rather than being
    rejected."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    # Tolerate values like '20 secs', '20s', '4 rings' — pull the leading number.
    is_rings = 'ring' in raw
    digits = ''.join(ch for ch in raw if ch.isdigit() or ch == '.')
    if not digits:
        return None
    try:
        num = float(digits)
    except ValueError:
        return None
    if num <= 0:
        return None
    secs = num * SECONDS_PER_RING if is_rings else num
    # Snap to the nearest accepted 5s step, then clamp to [min, max].
    snapped = int(round(secs / SECONDS_PER_RING)) * SECONDS_PER_RING
    snapped = max(ACCEPTED_RING_SECONDS[0], min(ACCEPTED_RING_SECONDS[-1], snapped))
    return snapped


def seconds_to_ringcount(secs):
    """Converts a ring time in seconds to a V1 ringCount (min 1)."""
    if not secs:
        return None
    return max(1, int(round(secs / SECONDS_PER_RING)))


def ringcount_to_seconds(ring_count):
    """Converts a V1 ringCount back to seconds for display."""
    if not ring_count:
        return None
    return int(ring_count) * SECONDS_PER_RING


# --- ACCOUNT FETCH ---------------------------------------------------------

def _fetch_paginated(endpoint, extra_params, what):
    """Walks every page of a list endpoint, retrying rate limits / 5xx per page.

    Raises on a genuine page failure rather than returning a partial list — a
    silent truncation here is what made large audits appear to stop early."""
    records = []
    page = 1
    while True:
        params = dict(extra_params or {})
        params.update({'perPage': 1000, 'page': page})
        resp = safe_request(endpoint, params=params)
        if resp is None or not getattr(resp, 'ok', False):
            status = getattr(resp, 'status_code', '?')
            text = ((getattr(resp, 'text', '') or '').strip())[:200]
            raise Exception(f"{what} fetch failed at page {page} [{status}] {text or '(no body)'}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        records.extend(data.get('records', []) or [])
        if not (data.get('navigation') or {}).get('nextPage'):
            break
        page += 1
    return records


def fetch_all_users():
    """Fetches every active/pending User extension across all pages."""
    records = _fetch_paginated('/restapi/v1.0/account/~/extension',
                               {'type': 'User'}, 'User list')
    return [u for u in records if u.get('status') in ('Enabled', 'NotActivated')]


def fetch_all_devices():
    """Fetches every device on the account (all pages), grouped by owning
    extension id, so the audit needs one bulk call instead of one per user."""
    by_ext = {}
    for d in _fetch_paginated('/restapi/v1.0/account/~/device', {}, 'Device list'):
        ext_id = str((d.get('extension') or {}).get('id') or '')
        if ext_id:
            by_ext.setdefault(ext_id, []).append(d)
    return by_ext


def fetch_extension_devices(ext_id):
    """Fetches a single extension's devices (used on the apply path to validate
    device ids and to resolve a blank Device ID to all deskphone/SIP devices)."""
    data = _safe_json(f'/restapi/v1.0/account/~/extension/{ext_id}/device')
    return (data or {}).get('records', []) if data else []


def get_extension_id(extension_number):
    """Resolves an extension number to its internal id."""
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'):
        ext_num = ext_num[:-2]
    data = _safe_json('/restapi/v1.0/account/~/extension',
                      params={'extensionNumber': ext_num})
    if data and data.get('records'):
        return data['records'][0]['id']
    return None


def device_display_name(device):
    """Best-effort friendly name for a device."""
    return (device.get('name')
            or (device.get('model') or {}).get('name')
            or device.get('serial')
            or 'Unknown Device')


def ring_devices_for_ext(devices):
    """Filters an extension's device list to the physical deskphone / generic
    SIP endpoints this tool operates on."""
    return [d for d in devices if d.get('type') in RING_DEVICE_TYPES]


# --- READ: current ring time on the default state -------------------------

def _extract_v2_device_durations(rule):
    """Maps device id -> ring duration (seconds) from a V2 state rule's
    dispatching. Only DeviceRingTarget entries inside a RingGroupAction carry a
    device-level ring time; the group's `duration` is that ring time."""
    out = {}
    dispatching = rule.get('dispatching') or {}
    for action in dispatching.get('actions', []):
        if action.get('type') != 'RingGroupAction':
            continue
        duration = action.get('duration')
        for t in action.get('targets', []):
            if t.get('type') == 'DeviceRingTarget':
                did = str((t.get('device') or {}).get('id') or '')
                if did:
                    out[did] = duration
    return out


def _extract_v1_device_durations(rule):
    """Maps device id -> ring duration (seconds) from a V1 answering rule's
    forwarding config, converting each forwarding rule's ringCount to seconds."""
    out = {}
    forwarding = rule.get('forwarding') or {}
    for r in forwarding.get('rules', []):
        secs = ringcount_to_seconds(r.get('ringCount'))
        for fn in r.get('forwardingNumbers', []):
            did = str((fn.get('device') or {}).get('id') or '')
            if did:
                out[did] = secs
    return out


def get_default_ring_config(ext_id):
    """Reads an extension's DEFAULT-state ring configuration.

    Tries the V2 comm-handling state rule first (source of truth on migrated /
    new-call-handling accounts); falls back to the V1 answering rule. Returns
    (schema, device_durations, rule) where:
      - schema is 'V2', 'V1' or None,
      - device_durations maps device id (str) -> ring seconds (int or None),
      - rule is the raw rule body (used by the apply path to mutate + PUT).
    """
    v2_url = V2_STATE_RULE_BASE.format(ext_id=ext_id) + f"/{DEFAULT_STATE_RULE_ID}"
    v2_rule = _safe_json(v2_url)
    if v2_rule is not None:
        return 'V2', _extract_v2_device_durations(v2_rule), v2_rule

    v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{DEFAULT_STATE_RULE_ID}"
    v1_rule = _safe_json(v1_url, params={'view': 'Detailed'})
    if v1_rule is not None:
        return 'V1', _extract_v1_device_durations(v1_rule), v1_rule

    return None, {}, None


# --- WRITE: apply a new ring time on the default state --------------------

def _apply_v2(ext_id, rule, device_targets):
    """Sets the ring duration (seconds) on each RingGroupAction of a V2 state
    rule that contains one of the target devices, then PUTs the rule back.

    `device_targets` maps device id -> desired seconds. RingCentral holds one
    `duration` per ring group, so if several targeted devices share a group with
    conflicting requested times, the largest requested value wins (documented in
    the returned message)."""
    dispatching = rule.get('dispatching') or {}
    actions = dispatching.get('actions', [])
    target_ids = set(device_targets.keys())

    changed = 0
    conflict = False
    for action in actions:
        if action.get('type') != 'RingGroupAction':
            continue
        group_device_ids = {
            str((t.get('device') or {}).get('id') or '')
            for t in action.get('targets', [])
            if t.get('type') == 'DeviceRingTarget'
        }
        hit = group_device_ids & target_ids
        if not hit:
            continue
        wanted = {device_targets[d] for d in hit}
        new_secs = max(wanted)
        if len(wanted) > 1:
            conflict = True
        if action.get('duration') != new_secs:
            action['duration'] = new_secs
            changed += 1

    if changed == 0:
        return False, ("Target device(s) were not found in an enabled ring group "
                       "of the default rule — nothing changed.")

    body = dict(rule)
    body.pop('uri', None)  # read-only; RC rejects it on PUT
    url = V2_STATE_RULE_BASE.format(ext_id=ext_id) + f"/{DEFAULT_STATE_RULE_ID}"
    resp = safe_request(url, method='PUT', json_body=body)
    if resp is not None and getattr(resp, 'ok', False):
        note = " (multiple ring times requested in one ring group — used the largest)" if conflict else ""
        return True, f"Updated {changed} ring group(s){note}."
    status = getattr(resp, 'status_code', '?')
    text = ((getattr(resp, 'text', '') or '').strip())[:300]
    return False, f"V2 PUT failed [{status}] {text or '(no body)'}"


def _apply_v1(ext_id, rule, device_targets):
    """Sets ringCount on each V1 forwarding rule that references a target device,
    then PUTs the answering rule back. Best-effort: on classic accounts a user's
    own phones are not always represented as forwarding rules, so when no
    forwarding rule references the device we report it instead of guessing."""
    forwarding = rule.get('forwarding') or {}
    rules = forwarding.get('rules', [])
    changed = 0
    for r in rules:
        rule_device_ids = {
            str((fn.get('device') or {}).get('id') or '')
            for fn in r.get('forwardingNumbers', [])
        }
        hit = rule_device_ids & set(device_targets.keys())
        if not hit:
            continue
        wanted = {device_targets[d] for d in hit}
        new_secs = max(wanted)
        new_count = seconds_to_ringcount(new_secs)
        if r.get('ringCount') != new_count:
            r['ringCount'] = new_count
            changed += 1

    if changed == 0:
        return False, ("V1 account: target device(s) not present in the default "
                       "rule's forwarding config — left unchanged. Inspect with Debug.")

    body = dict(rule)
    body.pop('uri', None)
    url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{DEFAULT_STATE_RULE_ID}"
    resp = safe_request(url, method='PUT', json_body=body)
    if resp is not None and getattr(resp, 'ok', False):
        return True, f"Updated {changed} forwarding ring group(s) (V1)."
    status = getattr(resp, 'status_code', '?')
    text = ((getattr(resp, 'text', '') or '').strip())[:300]
    return False, f"V1 PUT failed [{status}] {text or '(no body)'}"


def apply_ring_time(ext_id, device_targets):
    """Applies new ring times to an extension's default state rule.

    `device_targets` maps device id (str) -> desired seconds (int). Returns
    (ok, schema, message)."""
    schema, _, rule = get_default_ring_config(ext_id)
    if schema == 'V2':
        ok, msg = _apply_v2(ext_id, rule, device_targets)
        return ok, 'V2', msg
    if schema == 'V1':
        ok, msg = _apply_v1(ext_id, rule, device_targets)
        return ok, 'V1', msg
    return False, None, "No default call-handling rule found (neither V2 nor V1)."
