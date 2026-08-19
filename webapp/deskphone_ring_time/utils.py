import io
import os
import time
import base64
from datetime import datetime
import requests
import pandas as pd
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from webapp.rc_api import rc_api_call
from webapp.auth_utils import get_impersonation_token
from webapp import task_control
from . import storage

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

# The default call-handling state on the LEGACY V1 answering-rule endpoint uses
# this well-known id. NOTE: the modern V2 comm-handling endpoint does NOT — its
# state rules are keyed by ids like 'work-hours' / 'business-hours' (varies per
# account), so the V2 path must LIST the rules and pick the default at runtime
# rather than GET a fixed id (see _v2_pick_default_rule).
DEFAULT_STATE_RULE_ID = 'business-hours-rule'

V2_STATE_RULE_BASE = "/restapi/v2/accounts/~/extensions/{ext_id}/comm-handling/voice/state-rules"

# V2 state-rule ids that are availability TOGGLES, not the scheduled ring state
# a deskphone ring time belongs to. Excluded when picking the default rule.
V2_SYSTEM_RULE_IDS = {'forward-all-calls', 'dnd', 'agent'}

# Columns for the audit export AND the accepted upload layout — a row per
# deskphone / generic-SIP device. Editing the sheet in place and re-uploading is
# the intended workflow, so the two share one schema.
AUDIT_COLS = [
    'Ext Number', 'Ext Name', 'Site', 'Device Name', 'Device Type',
    'Device ID', 'Schema', 'Current Ring Time (Secs)', 'New Ring Time (Secs)',
]

# The one data-validated column: an operator may only pick an API-accepted ring
# time (5s steps up to 60s). Blank means "leave this device unchanged".
RING_DROPDOWN_FORMULA = '"' + ','.join(str(s) for s in ACCEPTED_RING_SECONDS) + '"'


# --- BACKGROUND TASK STORE -------------------------------------------------
#
# The audit runs in a background thread (see run_ring_time_audit_background) so
# it survives past a single HTTP request's Cloud Run timeout. The browser starts
# the task, then polls status and finally downloads the workbook. This in-memory
# store holds per-task progress and the finished file; it resets on container
# restart, which is fine for a transient audit (same model as Device Ringing
# Audit). Keys: status, current, total, message, file_data, rows, error.
ring_time_progress_store = {}

# The APPLY (upload) run uses the same background-thread + poll model as the
# audit, so a bulk apply can outlast Cloud Run's per-request timeout (a streaming
# apply request was being killed at the 3600s request limit) and so account-lock
# waits happen server-side without holding an HTTP connection open. Keys: status,
# current, total, message, logs (list of {level, message}), written, cancelled,
# error.
apply_progress_store = {}


# --- RESILIENT API WRAPPER -------------------------------------------------
#
# Two call modes share one wrapper:
#   * Request context (apply / debug): auth_data is None → the call runs under
#     the session token via rc_api_call, which already self-heals a 401 by
#     refreshing and retrying once.
#   * Background thread (audit): there is no request/session, so the caller
#     passes an explicit `auth_data` bundle captured from the session. A 401 is
#     healed here by refreshing the token in place (standard OAuth or the SM
#     impersonation bridge), mirroring Device Ringing Audit's safe_api_call.
# Either way we honour Retry-After on a 429 and back off on 5xx, and return the
# response object so callers can tell a real failure from an empty-but-OK page.

_MAX_ATTEMPTS = 6            # hard failures: 5xx, network error, repeated 401
_RETRY_AFTER_FALLBACK = 5
_RETRY_AFTER_CAP = 60
_MAX_RATE_LIMIT_WAITS = 30  # 429 pauses (don't burn the hard-failure budget)

# Account/resource-lock handling. When another change is being applied to the
# same account (e.g. someone applies a template or edits call handling in the
# admin portal), RingCentral rejects concurrent writes with 409 Conflict (or
# 423 Locked). We wait with escalating backoff and retry the SAME write so the
# apply resumes on its own once the portal operation releases the lock.
_CONFLICT_STATUSES = {409, 423}
_MAX_CONFLICT_WAITS = 40    # up to ~30-40 min of patience for a long template apply
_CONFLICT_WAIT_STEP = 15
_CONFLICT_WAIT_CAP = 60
# Lock errors that arrive as a 400/500 but are really "busy, try again".
_LOCK_BODY_MARKERS = ('lock', 'in progress', 'try again', 'another operation',
                      'concurrent', 'being modified', 'temporarily unavailable')


def _set_message(task_id, msg):
    """Surface a transient status line (rate-limit / lock waits) on whichever
    background store owns this task — the audit store or the apply store."""
    if not task_id:
        return
    for store in (ring_time_progress_store, apply_progress_store):
        if task_id in store:
            store[task_id]['message'] = msg


def _looks_locked(resp):
    """True when a response reads like a transient account lock/conflict, whether
    by status (409/423) or a recognisable 'busy, try again' error body."""
    status = getattr(resp, 'status_code', None)
    if status in _CONFLICT_STATUSES:
        return True
    if status in (400, 500):
        body = (getattr(resp, 'text', '') or '').lower()
        return any(m in body for m in _LOCK_BODY_MARKERS)
    return False


def _retry_after_seconds(resp):
    ra = _RETRY_AFTER_FALLBACK
    headers = getattr(resp, 'headers', None)
    if headers is not None:
        try:
            ra = int(headers.get('Retry-After', _RETRY_AFTER_FALLBACK))
        except (TypeError, ValueError):
            ra = _RETRY_AFTER_FALLBACK
    return min(ra + 1, _RETRY_AFTER_CAP)


def _heal_bg_token(auth_data, task_id=None):
    """Refreshes the background task's access token in place. Handles both the
    SM partner-impersonation bridge and a standard PKCE refresh. Returns True
    when a fresh access token was obtained."""
    server_url = auth_data.get('server_url', 'https://platform.ringcentral.com')

    # A. Impersonation bridge: refresh the underlying employee token (if we can)
    #    and re-mint the customer token.
    if auth_data.get('sm_employee_token') and auth_data.get('sm_target_id'):
        if auth_data.get('sm_employee_refresh_token'):
            client_id = os.getenv('SM_CLIENT_ID')
            client_secret = os.getenv('SM_CLIENT_SECRET')
            token_url = f"{server_url}/restapi/oauth/token"
            data = {'grant_type': 'refresh_token',
                    'refresh_token': auth_data['sm_employee_refresh_token']}
            headers = {'Content-Type': 'application/x-www-form-urlencoded',
                       'Accept': 'application/json'}
            if client_secret:
                auth_str = f"{client_id}:{client_secret}"
                headers['Authorization'] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
            else:
                data['client_id'] = client_id
            try:
                r = requests.post(token_url, data=data, headers=headers)
                if not r.ok:
                    return False
                nt = r.json()
                auth_data['sm_employee_token'] = nt.get('access_token')
                auth_data['sm_employee_refresh_token'] = nt.get('refresh_token')
            except Exception:
                return False
        new_token = get_impersonation_token(auth_data['sm_employee_token'],
                                            auth_data['sm_target_id'])
        if new_token:
            auth_data['access_token'] = new_token
            _set_message(task_id, "Impersonation token refreshed — resuming…")
            return True
        return False

    # B. Standard OAuth refresh.
    if auth_data.get('refresh_token') and auth_data.get('client_id'):
        token_url = f"{server_url}/restapi/oauth/token"
        payload = {'grant_type': 'refresh_token',
                   'refresh_token': auth_data['refresh_token'],
                   'client_id': auth_data['client_id']}
        try:
            r = requests.post(token_url, data=payload,
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
            if r.ok:
                nt = r.json()
                auth_data['access_token'] = nt.get('access_token')
                auth_data['refresh_token'] = nt.get('refresh_token')
                _set_message(task_id, "Access token refreshed — resuming…")
                return True
        except Exception:
            return False
    return False


def _request(endpoint, method='GET', params=None, json_body=None,
             auth_data=None, task_id=None, max_attempts=_MAX_ATTEMPTS):
    """Resilient RC call. See module notes above. Returns a response object
    (real or MockResponse); never raises for HTTP status.

    Bounded retries with separate budgets so a busy account doesn't burn the
    hard-failure budget: 5xx / network / repeated-401 count against max_attempts;
    429 rate limits and 409/423 account locks each have their own generous,
    capped budget and back off before retrying the SAME request."""
    resp = None
    hard_attempts = 0
    rate_waits = 0
    conflict_waits = 0
    while True:
        token = auth_data.get('access_token') if auth_data else None
        # (connect, read) timeout so a stalled RC connection in the background
        # thread can't hang the whole audit forever — it surfaces as an error the
        # retry loop handles instead of a permanent "working" state. rc_api_call
        # can raise (e.g. requests.Timeout) rather than return a response, so
        # guard the call and treat a raise as a retryable transient failure.
        try:
            resp = rc_api_call(endpoint, params=params, method=method,
                               json=json_body, return_response=True, token=token,
                               timeout=(10, 60))
        except Exception as e:
            hard_attempts += 1
            if hard_attempts >= max_attempts:
                return None
            _set_message(task_id, f"Network error ({e}) — retrying…")
            time.sleep(min(2 * hard_attempts, 30))
            resp = None
            continue
        status = getattr(resp, 'status_code', None)

        if status == 429:
            rate_waits += 1
            if rate_waits > _MAX_RATE_LIMIT_WAITS:
                return resp
            wait = _retry_after_seconds(resp)
            _set_message(task_id, f"Rate limit reached — pausing {wait}s…")
            time.sleep(wait)
            continue

        # Account/resource lock (concurrent admin-portal change): wait it out and
        # retry the same write so the run resumes once the lock releases.
        if _looks_locked(resp):
            conflict_waits += 1
            if conflict_waits > _MAX_CONFLICT_WAITS:
                return resp
            wait = min(_CONFLICT_WAIT_STEP * conflict_waits, _CONFLICT_WAIT_CAP)
            _set_message(task_id, f"Account busy — another change is being applied; "
                                  f"waiting {wait}s and retrying…")
            time.sleep(wait)
            continue

        # Background mode heals a 401 here; session mode already healed it
        # inside rc_api_call before returning, so we only act when auth_data set.
        if status == 401 and auth_data is not None:
            hard_attempts += 1
            if hard_attempts < max_attempts and _heal_bg_token(auth_data, task_id):
                continue
            return resp

        if status is not None and status >= 500:
            hard_attempts += 1
            if hard_attempts >= max_attempts:
                return resp
            time.sleep(min(2 * hard_attempts, 30))
            continue

        # Success or a non-retryable 4xx (e.g. 404 → fall through to caller).
        break
    return resp


def _json(endpoint, params=None, auth_data=None, task_id=None):
    """GET helper: returns parsed JSON on success, else None."""
    resp = _request(endpoint, params=params, auth_data=auth_data, task_id=task_id)
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

def _fetch_paginated(endpoint, extra_params, what, auth_data=None, task_id=None):
    """Walks every page of a list endpoint, retrying rate limits / 5xx per page.

    Raises on a genuine page failure rather than returning a partial list — a
    silent truncation here is what made large audits appear to stop early."""
    records = []
    page = 1
    while True:
        params = dict(extra_params or {})
        params.update({'perPage': 1000, 'page': page})
        resp = _request(endpoint, params=params, auth_data=auth_data, task_id=task_id)
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


def fetch_all_users(auth_data=None, task_id=None):
    """Fetches every active/pending User extension across all pages."""
    records = _fetch_paginated('/restapi/v1.0/account/~/extension',
                               {'type': 'User'}, 'User list',
                               auth_data=auth_data, task_id=task_id)
    return [u for u in records if u.get('status') in ('Enabled', 'NotActivated')]


def fetch_all_devices(auth_data=None, task_id=None):
    """Fetches every device on the account (all pages), grouped by owning
    extension id, so the audit needs one bulk call instead of one per user."""
    by_ext = {}
    for d in _fetch_paginated('/restapi/v1.0/account/~/device', {}, 'Device list',
                              auth_data=auth_data, task_id=task_id):
        ext_id = str((d.get('extension') or {}).get('id') or '')
        if ext_id:
            by_ext.setdefault(ext_id, []).append(d)
    return by_ext


def fetch_extension_devices(ext_id, auth_data=None):
    """Fetches a single extension's devices (used on the apply path to validate
    device ids and to resolve a blank Device ID to all deskphone/SIP devices)."""
    data = _json(f'/restapi/v1.0/account/~/extension/{ext_id}/device', auth_data=auth_data)
    return (data or {}).get('records', []) if data else []


def get_extension_id(extension_number, auth_data=None):
    """Resolves an extension number to its internal id."""
    ext_num = str(extension_number).strip()
    if ext_num.endswith('.0'):
        ext_num = ext_num[:-2]
    data = _json('/restapi/v1.0/account/~/extension',
                 params={'extensionNumber': ext_num}, auth_data=auth_data)
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
    dispatching. Only DeviceRingTarget entries inside an enabled RingGroupAction
    carry a device-level ring time; the group's `duration` is that ring time.
    Disabled actions/targets are skipped (a disabled group's device doesn't ring,
    so its stale duration must not be reported as the current ring time)."""
    out = {}
    dispatching = rule.get('dispatching') or {}
    for action in dispatching.get('actions', []):
        if action.get('type') != 'RingGroupAction':
            continue
        if action.get('enabled') is False:
            continue
        duration = action.get('duration')
        for t in action.get('targets', []):
            if t.get('enabled') is False:
                continue
            if t.get('type') == 'DeviceRingTarget':
                did = str((t.get('device') or {}).get('id') or '')
                if did:
                    out[did] = duration
    return out


def _v2_rule_rings_devices(rule):
    """True when a V2 state rule has at least one enabled RingGroupAction that
    rings a specific device (as opposed to only mobile/desktop or terminating)."""
    for action in (rule.get('dispatching') or {}).get('actions', []):
        if action.get('type') != 'RingGroupAction' or action.get('enabled') is False:
            continue
        for t in action.get('targets', []):
            if t.get('type') == 'DeviceRingTarget' and t.get('enabled') is not False:
                return True
    return False


def _v2_rule_enabled(rule):
    """A V2 state rule's on/off, tolerating the flag living on the rule or its
    nested `state` object."""
    state = rule.get('state') or {}
    return state.get('enabled', rule.get('enabled', True))


def _v2_pick_default_rule(records):
    """Chooses the default 'business / work hours' ring rule from the V2
    state-rule list: an enabled, non-system rule that actually rings a device.
    Prefers a rule whose name/id reads like business/work hours over an
    after-hours / holiday one; returns the raw rule dict, or None."""
    candidates = []
    for r in records:
        if str(r.get('id') or '') in V2_SYSTEM_RULE_IDS:
            continue
        if not _v2_rule_enabled(r):
            continue
        candidates.append(r)

    if not candidates:
        return None

    def score(r):
        label = (str(r.get('displayName') or '') + ' ' +
                 str((r.get('state') or {}).get('displayName') or '') + ' ' +
                 str(r.get('id') or '')).lower()
        name_rank = 1
        if 'business' in label or 'work' in label:
            name_rank = 0
        elif 'after' in label or 'night' in label or 'holiday' in label or 'closed' in label:
            name_rank = 2
        # Prefer rules that actually ring a device (rank 0) over those that don't.
        return (0 if _v2_rule_rings_devices(r) else 1, name_rank)

    candidates.sort(key=score)
    return candidates[0]


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


def get_default_ring_config(ext_id, auth_data=None, task_id=None):
    """Reads an extension's DEFAULT-state ring configuration.

    Tries the V2 comm-handling state rule first (source of truth on migrated /
    new-call-handling accounts); falls back to the V1 answering rule. Returns
    (schema, device_durations, rule) where:
      - schema is 'V2', 'V1' or None,
      - device_durations maps device id (str) -> ring seconds (int or None),
      - rule is the raw rule body (used by the apply path to mutate + PUT).

    V2 has no fixed 'business-hours-rule' id, so we LIST the state rules and pick
    the default ring rule at runtime. New-call-handling accounts also 403 the V1
    endpoints, so once V2 returns a usable rule we never fall through to V1.
    """
    list_data = _json(V2_STATE_RULE_BASE.format(ext_id=ext_id),
                      auth_data=auth_data, task_id=task_id)
    if list_data is not None:
        records = list_data.get('records', []) or []
        rule = _v2_pick_default_rule(records)
        if rule is not None:
            # The list already inlines `dispatching.actions` on this endpoint, but
            # re-GET by the rule's real id if a summary ever omits them, so the
            # apply path always mutates the authoritative body.
            rid = str(rule.get('id') or '')
            if rid and not (rule.get('dispatching') or {}).get('actions'):
                full = _json(V2_STATE_RULE_BASE.format(ext_id=ext_id) + f"/{rid}",
                             auth_data=auth_data, task_id=task_id)
                if full is not None:
                    rule = full
            return 'V2', _extract_v2_device_durations(rule), rule
        # A V2 account that returned rules but no ringing default: nothing to
        # audit/apply on V2, and V1 is 403 here — report V2 with no durations.
        if records:
            return 'V2', {}, None
        # Empty list: likely not a V2 account after all — try V1 below.

    v1_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{DEFAULT_STATE_RULE_ID}"
    v1_rule = _json(v1_url, params={'view': 'Detailed'}, auth_data=auth_data, task_id=task_id)
    if v1_rule is not None:
        return 'V1', _extract_v1_device_durations(v1_rule), v1_rule

    return None, {}, None


# --- WRITE: apply a new ring time on the default state --------------------

def _apply_v2(ext_id, rule, device_targets, auth_data=None):
    """Sets the ring duration (seconds) on each RingGroupAction of a V2 state
    rule that contains one of the target devices, then PATCHes the rule's
    dispatching back.

    The comm-handling state-rule endpoint updates via PATCH (a PUT returns
    AGW-404 "resource not found"); we send just the modified `dispatching` object
    so the rule's state/schedule/conditions are left untouched.

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

    # PATCH just the modified dispatching to the rule's OWN id (e.g. 'work-hours';
    # V2 has no fixed 'business-hours-rule' id). PATCH — not PUT — is what the
    # comm-handling state-rule endpoint accepts; a PUT returns AGW-404.
    rule_id = str(rule.get('id') or DEFAULT_STATE_RULE_ID)
    url = V2_STATE_RULE_BASE.format(ext_id=ext_id) + f"/{rule_id}"
    resp = _request(url, method='PATCH', json_body={'dispatching': dispatching},
                    auth_data=auth_data)
    if resp is not None and getattr(resp, 'ok', False):
        note = " (multiple ring times requested in one ring group — used the largest)" if conflict else ""
        return True, f"Updated {changed} ring group(s){note}."
    if _looks_locked(resp):
        return False, ("account still locked by another change after retrying — skipped; "
                       "re-run once the admin-portal change finishes.")
    status = getattr(resp, 'status_code', '?')
    text = ((getattr(resp, 'text', '') or '').strip())[:300]
    return False, f"V2 PATCH failed [{status}] {text or '(no body)'}"


def _apply_v1(ext_id, rule, device_targets, auth_data=None):
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
    resp = _request(url, method='PUT', json_body=body, auth_data=auth_data)
    if resp is not None and getattr(resp, 'ok', False):
        return True, f"Updated {changed} forwarding ring group(s) (V1)."
    if _looks_locked(resp):
        return False, ("account still locked by another change after retrying — skipped; "
                       "re-run once the admin-portal change finishes.")
    status = getattr(resp, 'status_code', '?')
    text = ((getattr(resp, 'text', '') or '').strip())[:300]
    return False, f"V1 PUT failed [{status}] {text or '(no body)'}"


def apply_ring_time(ext_id, device_targets, auth_data=None):
    """Applies new ring times to an extension's default state rule.

    `device_targets` maps device id (str) -> desired seconds (int). Returns
    (ok, schema, message)."""
    schema, _, rule = get_default_ring_config(ext_id, auth_data=auth_data)
    if schema == 'V2':
        ok, msg = _apply_v2(ext_id, rule, device_targets, auth_data=auth_data)
        return ok, 'V2', msg
    if schema == 'V1':
        ok, msg = _apply_v1(ext_id, rule, device_targets, auth_data=auth_data)
        return ok, 'V1', msg
    return False, None, "No default call-handling rule found (neither V2 nor V1)."


# --- BACKGROUND APPLY ------------------------------------------------------
#
# `groups` maps ext_key -> {'devices': {device_id: secs}, 'apply_all_secs': secs
# or None}. Runs in a daemon thread (see the /apply route) so a bulk apply — and
# any account-lock waits inside it — can outlast the Cloud Run request timeout.

def _apply_log(store, level, message):
    store['logs'].append({'level': level, 'message': message})


def run_ring_time_apply_background(app, task_id, groups, auth_data, user_email=None):
    """Applies ring times to every edited extension, recording per-extension log
    lines and progress into apply_progress_store[task_id] for the browser to
    poll. Honours a cooperative stop via task_control."""
    store = apply_progress_store.get(task_id)
    if store is None:
        return
    try:
        with app.app_context():
            total = len(groups)
            store['total'] = total
            written = 0
            current = 0
            for ext_key, group in groups.items():
                if task_control.is_stopped(task_id):
                    _apply_log(store, 'info', '■ Stopped by user — remaining extensions were skipped.')
                    store['cancelled'] = True
                    break
                current += 1
                store['current'] = current
                store['message'] = f"Applying Ext {ext_key} ({current}/{total})…"
                try:
                    ext_id = get_extension_id(ext_key, auth_data=auth_data)
                    if not ext_id:
                        _apply_log(store, 'error', f"Ext {ext_key}: ⚠️ extension not found.")
                        continue

                    device_targets = dict(group['devices'])
                    if group['apply_all_secs'] is not None:
                        ring_devs = ring_devices_for_ext(
                            fetch_extension_devices(ext_id, auth_data=auth_data))
                        if not ring_devs:
                            _apply_log(store, 'error', f"Ext {ext_key}: ⚠️ no deskphone / generic-SIP devices found.")
                            if not device_targets:
                                continue
                        for d in ring_devs:
                            device_targets.setdefault(str(d.get('id')), group['apply_all_secs'])

                    if not device_targets:
                        _apply_log(store, 'info', f"Ext {ext_key}: ⚠️ nothing to apply.")
                        continue

                    ok, schema, msg = apply_ring_time(ext_id, device_targets, auth_data=auth_data)
                    if ok:
                        secs_shown = sorted(set(device_targets.values()))
                        secs_label = ', '.join(f"{s}s" for s in secs_shown)
                        _apply_log(store, 'success', f"✅ Ext {ext_key}: ring time → {secs_label} [{schema}] — {msg}")
                        written += 1
                    else:
                        tag = f"[{schema}] " if schema else ""
                        _apply_log(store, 'error', f"❌ Ext {ext_key}: {tag}{msg}")
                except Exception as e:
                    _apply_log(store, 'error', f"❌ Ext {ext_key}: {e}")

            store['written'] = written
            store['status'] = 'completed'
            store['message'] = (f"Stopped — {written} extension(s) updated."
                                if store.get('cancelled')
                                else f"Finished — {written} extension(s) updated.")
    except Exception as e:
        store['status'] = 'error'
        store['error'] = str(e)
        _apply_log(store, 'error', f"Apply failed: {e}")
        store['message'] = f"Apply failed: {e}"
    finally:
        task_control.clear(task_id)


# --- WORKBOOK BUILD --------------------------------------------------------

def add_ring_dropdown(ws, columns, max_row=2000):
    """Attaches the accepted-seconds dropdown to the New Ring Time column."""
    if 'New Ring Time (Secs)' not in columns:
        return
    letter = get_column_letter(columns.index('New Ring Time (Secs)') + 1)
    dv = DataValidation(type="list", formula1=RING_DROPDOWN_FORMULA, allow_blank=True)
    dv.error = 'Pick an accepted ring time in seconds (5s steps, up to 60).'
    dv.errorTitle = 'Invalid ring time'
    ws.add_data_validation(dv)
    dv.add(f"{letter}2:{letter}{max_row}")


def autosize(ws):
    for column in ws.columns:
        length = max(len(str(cell.value) or "") for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(length + 5, 55)


def build_ring_time_workbook(audit_data):
    """Builds the audit .xlsx from a list of row dicts. Returns (bytes, rows)."""
    df = pd.DataFrame(audit_data)
    for c in AUDIT_COLS:
        if c not in df.columns:
            df[c] = ''
    df = df[AUDIT_COLS]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Ring Times')
        ws = writer.sheets['Ring Times']
        add_ring_dropdown(ws, AUDIT_COLS)
        autosize(ws)
    return output.getvalue(), len(df)


# --- BACKGROUND AUDIT ------------------------------------------------------
#
# A light pace between users keeps us comfortably under RingCentral's rate
# limits on big accounts; the reactive 429 backoff in _request is the safety
# net. Because this runs in a background thread (not a request), the total wall
# time no longer counts against a Cloud Run request timeout.
_AUDIT_PACE_SECONDS = 0.2


def _finalise_storage(task_id, user_email):
    """Uploads the finished workbook to durable storage (GCS + Firestore index)
    so it can be downloaded from any instance and after the tab is closed. Best
    effort — an in-memory copy is always kept as the fast/fallback path."""
    store = ring_time_progress_store.get(task_id) or {}
    if not store.get('file_data') or not storage.storage_enabled():
        return
    try:
        storage.save_result(task_id, store['file_data'],
                            store.get('filename') or f"{task_id}.xlsx",
                            store.get('rows', 0), user_email)
    except Exception as e:
        print(f"deskphone_ring_time: durable save failed: {e}")


def run_ring_time_audit_background(app, task_id, auth_data, user_email=None):
    """Crawls the account for deskphone / generic-SIP ring times and builds the
    audit workbook into ring_time_progress_store[task_id]. Runs in a daemon
    thread; wraps its work in an app context so rc_api_call resolves the right
    RC_SERVER_URL and config even without a request. On completion the workbook
    is also persisted to durable storage (GCS/Firestore) when configured."""
    store = ring_time_progress_store.get(task_id)
    if store is None:
        return
    try:
        with app.app_context():
            storage.record_status(task_id, 'running', user_email=user_email)
            store['message'] = 'Loading users…'
            users = fetch_all_users(auth_data=auth_data, task_id=task_id)

            store['message'] = 'Loading account devices…'
            devices_by_ext = fetch_all_devices(auth_data=auth_data, task_id=task_id)

            # Only audit users that actually own a deskphone / generic-SIP device.
            targets = []
            for u in users:
                ext_id = str(u.get('id'))
                ring_devs = ring_devices_for_ext(devices_by_ext.get(ext_id, []))
                if ring_devs:
                    targets.append((u, ring_devs))

            store['total'] = len(targets)

            filename = f"Deskphone_Ring_Time_{datetime.now().strftime('%Y%m%d')}.xlsx"
            store['filename'] = filename

            if not targets:
                store['file_data'], store['rows'] = build_ring_time_workbook(
                    [{'Ext Number': 'No Data',
                      'Device Name': 'No deskphone / generic-SIP devices found'}])
                store['status'] = 'completed'
                store['message'] = 'No deskphone / generic-SIP devices found.'
                _finalise_storage(task_id, user_email)
                return

            audit_data = []
            for i, (user, ring_devs) in enumerate(targets):
                ext_id = str(user.get('id'))
                ext_num = user.get('extensionNumber', '')
                ext_name = user.get('name', 'Unknown')
                site = (user.get('site') or {}).get('name') or 'Main Site'

                store['current'] = i + 1
                store['message'] = f"Auditing Ext {ext_num} ({ext_name})…"

                try:
                    schema, durations, _ = get_default_ring_config(
                        ext_id, auth_data=auth_data, task_id=task_id)
                except Exception:
                    schema, durations = None, {}

                for d in ring_devs:
                    did = str(d.get('id'))
                    current = durations.get(did)
                    audit_data.append({
                        'Ext Number': ext_num,
                        'Ext Name': ext_name,
                        'Site': site,
                        'Device Name': device_display_name(d),
                        'Device Type': DEVICE_TYPE_LABELS.get(d.get('type'), d.get('type')),
                        'Device ID': did,
                        'Schema': schema or 'Unknown',
                        'Current Ring Time (Secs)': current if current is not None else '',
                        'New Ring Time (Secs)': '',
                    })

                time.sleep(_AUDIT_PACE_SECONDS)

            store['message'] = 'Compiling spreadsheet…'
            store['file_data'], store['rows'] = build_ring_time_workbook(audit_data)
            store['status'] = 'completed'
            store['message'] = f"Audit complete — {store['rows']} device row(s)."
            _finalise_storage(task_id, user_email)
    except Exception as e:
        store['status'] = 'error'
        store['error'] = str(e)
        store['message'] = f"Audit failed: {e}"
        try:
            storage.record_status(task_id, 'error', user_email=user_email, error=str(e))
        except Exception:
            pass
