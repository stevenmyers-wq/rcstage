import copy
import re
import time
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from webapp.rc_api import rc_api_call

notifications_bp = Blueprint('notifications_bp', __name__)


def _pop_param_path(obj, path):
    """Remove a dotted parameter path (e.g. 'voicemails.markAsRead' or
    'outboundFaxes') from a nested settings dict. Returns True if something was
    removed."""
    parts = path.split('.')
    cur = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    if isinstance(cur, dict) and parts[-1] in cur:
        cur.pop(parts[-1], None)
        return True
    return False


def _invalid_param_paths(resp):
    """Extract the parameter path(s) RingCentral flagged as invalid from an
    error response, e.g. 'Parameter [voicemails.markAsRead] value is invalid.'
    Prefers the structured 'parameterName' when present."""
    paths = []
    try:
        body = resp.json()
    except Exception:
        return paths
    for err in (body.get('errors') or []):
        name = err.get('parameterName')
        if name:
            paths.append(name)
            continue
        match = re.search(r'Parameter \[([^\]]+)\]', err.get('message', '') or '')
        if match:
            paths.append(match.group(1))
    if not paths:
        match = re.search(r'Parameter \[([^\]]+)\]', body.get('message', '') or '')
        if match:
            paths.append(match.group(1))
    return paths

def _call_with_retry(endpoint, method='GET', **kwargs):
    """Helper to handle 429 Rate Limits gracefully within the route (server-side pause)."""
    max_retries = 3
    for _ in range(max_retries):
        resp = rc_api_call(endpoint, method=method, return_response=True, **kwargs)
        
        status = getattr(resp, 'status_code', None)
        if status == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 60
            time.sleep(retry_after + 1)
            continue
            
        if resp and getattr(resp, 'ok', False):
            return resp.json()
            
        return None
    return None

# --- 1. GET LIST ---
@notifications_bp.route('/api/notifications/get-targets')
@require_rc_token
def get_targets():
    targets = []
    page = 1
    
    while True:
        # Removed the 'type' API parameter to fetch the full raw list and rely on the Python filter.
        params = {'perPage': 1000, 'page': page}
        resp_data = _call_with_retry('/restapi/v1.0/account/~/extension', params=params)
        
        if not resp_data or 'records' not in resp_data or not resp_data['records']:
            break
            
        for record in resp_data['records']:
            r_type = record.get('type', '')
            if r_type not in ['User', 'Department', 'Voicemail', 'Limited']:
                continue
            
            # Local Filter: Only enforce Enabled/NotActivated on Users and Limited.
            status = record.get('status', '')
            if r_type in ['User', 'Limited'] and status not in ['Enabled', 'NotActivated']:
                continue

            targets.append({
                "id": record['id'],
                "name": record.get('name', 'Unknown'),
                "ext": record.get('extensionNumber', 'N/A'),
                "email": record.get('contact', {}).get('email', ''),
                "type": r_type,
                "site": (record.get('site') or {}).get('name', 'Main Site')
            })
        
        # Pagination Fix: Use paging.totalPages instead of navigation.nextPage
        paging = resp_data.get('paging', {})
        if page >= paging.get('totalPages', 1):
            break
        page += 1
    
    try:
        targets.sort(key=lambda x: int(x['ext']) if x['ext'].isdigit() else 999999)
    except:
        pass 
    
    return jsonify({"targets": targets})


# --- 2. AUDIT SINGLE ---
@notifications_bp.route('/api/notifications/audit-single', methods=['POST'])
@require_rc_token
def audit_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    
    # Use return_response=True so we can pass 429s directly to the frontend for UI pauses
    resp = rc_api_call(endpoint, return_response=True)
    
    if resp and getattr(resp, 'status_code', None) == 429:
        retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 60
        return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        
    if not resp or not getattr(resp, 'ok', False):
        return jsonify({"status": "error", "message": "Settings unavailable or unassigned"})

    settings = resp.json()

    def get_emails(obj, key):
        # In advanced mode each category carries its own recipient list under
        # 'advancedEmailAddresses' (confirmed against the live portal payload);
        # 'emailAddresses' is only a defensive fallback.
        cat = obj.get(key) if obj else None
        if not cat: return ""
        emails = cat.get('advancedEmailAddresses') or cat.get('emailAddresses') or []
        return "; ".join(emails)

    def get_flag(obj, key):
        if not obj or key not in obj: return "FALSE"
        return str(obj[key].get('notifyByEmail', False)).upper()

    def get_flag_optional(obj, key):
        # Some categories (e.g. Fax Transmission Results / Call Notes) only exist
        # on User extensions; Call Queues and other types have no such block.
        # Report N/A when the category is absent so the audit doesn't read as
        # "disabled" for extension types the setting never applies to.
        if not obj or key not in obj: return "N/A"
        return str(obj[key].get('notifyByEmail', False)).upper()

    is_advanced = settings.get('advancedMode', False)
    
    response_data = {
        "Extension ID": ext_id,
        "Advanced Mode": str(is_advanced).upper(),
        "Enable Voicemail": get_flag(settings, 'voicemails'),
        "Enable MissedCalls": get_flag(settings, 'missedCalls'),
        "Enable Faxes": get_flag(settings, 'inboundFaxes'),
        "Enable SMS": get_flag(settings, 'inboundTexts'),
        "Enable FaxResults": get_flag_optional(settings, 'outboundFaxes'),
        "Enable CallNotes": get_flag_optional(settings, 'callNotes'),
        "Global Emails": "; ".join(settings.get('emailAddresses', [])),
        "Voicemail Emails": get_emails(settings, 'voicemails'),
        "Fax Emails": get_emails(settings, 'inboundFaxes'),
        "SMS Emails": get_emails(settings, 'inboundTexts'),
        "MissedCall Emails": get_emails(settings, 'missedCalls'),
        "FaxResults Emails": get_emails(settings, 'outboundFaxes'),
        "CallNotes Emails": get_emails(settings, 'callNotes')
    }

    return jsonify({"status": "success", "data": response_data})


# --- 3. UPDATE SINGLE ---
@notifications_bp.route('/api/notifications/update-single', methods=['POST'])
@require_rc_token
def update_single_extension():
    data = request.get_json()
    ext_id = data.get('id')
    
    def parse_bool(val):
        return str(val).upper() == 'TRUE'
    
    def parse_list(val):
        if not val: return []
        return [e.strip() for e in val.split(';') if e.strip()]

    # Advanced Mode is optional per row. Only when the column carries an explicit
    # value do we change the queue's mode; a blank column must leave the stored
    # mode untouched. Forcing it (blank -> FALSE) silently flipped advanced-mode
    # "specified emails" queues to basic -- which drops the per-category addresses
    # they rely on and makes RingCentral reject the write, so turning a single
    # toggle (e.g. Missed Calls) OFF failed for a change the row never asked for.
    adv_raw = data.get('advanced_mode', None)
    advanced_specified = adv_raw is not None and str(adv_raw).strip() != ''
    requested_advanced = str(adv_raw).upper() == 'TRUE'

    # In advanced mode, notifications are driven entirely by the per-category
    # advanced email addresses (the top-level global list is ignored). If
    # Advanced Mode is TRUE but every advanced email field is blank, the row
    # can't produce a valid, meaningful update — reject it up front with a
    # clear message instead of sending it to RingCentral.
    if advanced_specified and requested_advanced:
        advanced_email_fields = ('vm_emails', 'fax_emails', 'sms_emails', 'missed_emails',
                                 'outfax_emails', 'callnotes_emails')
        if all(not str(data.get(f) or '').strip() for f in advanced_email_fields):
            return jsonify({
                "status": "error",
                "message": "Advanced Mode is TRUE but all advanced email fields "
                           "(Voicemail/Fax/SMS/MissedCall/FaxResults/CallNotes Emails) are blank."
            })

    endpoint = f'/restapi/v1.0/account/~/extension/{ext_id}/notification-settings'
    
    original_resp = rc_api_call(endpoint, return_response=True)
    if original_resp and getattr(original_resp, 'status_code', None) == 429:
        retry_after = int(original_resp.headers.get('Retry-After', 60)) if hasattr(original_resp, 'headers') else 60
        return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        
    if not original_resp or not getattr(original_resp, 'ok', False):
        return jsonify({"status": "error", "message": "Failed to fetch original settings"})

    original = original_resp.json()

    # Full read-modify-write, mirroring the RingCentral portal's own request:
    # echo the whole settings object back and change only the fields this row
    # touches. Sending the complete resource (every category, including any this
    # tool does not manage) is what keeps the API from rejecting the write for a
    # missing/invalid category.
    new_notif = copy.deepcopy(original)

    # 'emailRecipients' is a read-only echo of the recipients RingCentral derives
    # for a queue whose notifications go to its queue managers. Sending it back in
    # the PUT is what made editing any toggle on a manager queue fail (e.g. turning
    # Missed Call notifications OFF). Drop it unconditionally -- RingCentral
    # recomputes it from 'includeManagers'. (extension_uploader and the cq_hours
    # updater drop this same read-only field before writing.)
    new_notif.pop('emailRecipients', None)

    # Only change the mode when the row explicitly asked; otherwise keep whatever
    # the queue already has (see the advanced_specified note above).
    if advanced_specified and 'advancedMode' in new_notif:
        new_notif['advancedMode'] = requested_advanced
    advanced = bool(new_notif.get('advancedMode', False))

    # Advanced mode is incompatible with queue managers selected from the user
    # list (EXT-465, "Advanced mode is not available when queue managers are
    # selected from user list"). When we enable advanced mode, de-select the
    # managers so our uploaded per-category addresses take delivery instead. In
    # basic mode we leave 'includeManagers' as-is, so manager delivery is
    # preserved while the individual category toggles below still apply.
    if advanced and 'includeManagers' in new_notif:
        new_notif['includeManagers'] = False

    # Top-level 'emailAddresses' is the basic-mode global recipient list. In
    # advanced mode the portal leaves it as-is (it is harmless there), so we only
    # touch it in basic mode, from the Global Emails column when a value is given.
    if not advanced:
        global_emails = parse_list(data.get('global_emails'))
        if global_emails:
            new_notif['emailAddresses'] = global_emails

    # Per-category changes: notifyByEmail from the row's enable flag; per-category
    # recipients (advanced mode) written to the category's 'advancedEmailAddresses'
    # list — the field the portal reads and writes. A blank email column leaves
    # the stored list untouched.
    cat_fields = {
        'voicemails':    ('enable_vm',        'vm_emails'),
        'missedCalls':   ('enable_missed',    'missed_emails'),
        'inboundFaxes':  ('enable_fax',       'fax_emails'),
        'inboundTexts':  ('enable_sms',       'sms_emails'),
        'outboundFaxes': ('enable_outfax',    'outfax_emails'),
        'callNotes':     ('enable_callnotes', 'callnotes_emails'),
    }
    for cat, (enable_key, emails_key) in cat_fields.items():
        block = new_notif.get(cat)
        if not isinstance(block, dict):
            continue
        enable_val = data.get(enable_key)
        if enable_val not in (None, ''):
            enabled = parse_bool(enable_val)
            block['notifyByEmail'] = enabled
            # A category's queue managers can't receive a notification type that
            # has been turned off; leaving 'includeManagers' set on a now-disabled
            # category is a conflicting state RingCentral rejects. Clear it for the
            # category being switched off so the toggle actually takes effect.
            if not enabled and block.get('includeManagers'):
                block['includeManagers'] = False
        if advanced:
            emails_val = data.get(emails_key)
            if emails_val not in (None, ''):
                block['advancedEmailAddresses'] = parse_list(emails_val)

    # When advanced mode is on, the portal sends an advancedEmailAddresses list
    # on every notification category (empty where none is set). Ensure the same
    # shape so a basic->advanced flip doesn't leave a category without the field.
    if advanced:
        for cat in ('voicemails', 'missedCalls', 'inboundFaxes', 'inboundTexts', 'outboundFaxes', 'callNotes'):
            block = new_notif.get(cat)
            if isinstance(block, dict) and 'advancedEmailAddresses' not in block:
                block['advancedEmailAddresses'] = []

    # PUT, then iteratively drop any field RingCentral reports as invalid and
    # retry. The GET returns fields (markAsRead, notifyBySms, includeTranscription,
    # ...) that PUT rejects for some extension types; RingCentral names the
    # offending parameter, so we pop exactly that one and resend. Bounded to
    # avoid an unbounded loop.
    put_resp = rc_api_call(endpoint, method='PUT', json=new_notif, return_response=True)
    attempts = 0
    while attempts < 8 and not (put_resp and getattr(put_resp, 'ok', False)):
        if put_resp is not None and getattr(put_resp, 'status_code', None) == 429:
            retry_after = int(put_resp.headers.get('Retry-After', 60)) if hasattr(put_resp, 'headers') else 60
            return jsonify({"error": "Rate limit", "retry_after": retry_after}), 429
        invalid_paths = _invalid_param_paths(put_resp)
        if not invalid_paths:
            break
        removed = False
        for path in invalid_paths:
            if _pop_param_path(new_notif, path):
                removed = True
        if not removed:
            break
        attempts += 1
        put_resp = rc_api_call(endpoint, method='PUT', json=new_notif, return_response=True)

    if put_resp and getattr(put_resp, 'ok', False):
        return jsonify({"status": "success"})

    # Surface the concrete RingCentral error(s) -- code, message, and the parameter
    # RC flagged -- so failures like EXT-414/EXT-465 ("Parameter [...] is not
    # applicable ... queue managers are selected from user list") name the actual
    # parameter instead of a generic "Update failed".
    msg = "Update failed"
    try:
        body = put_resp.json()
        errs = body.get('errors') or []
        parts = []
        for e in errs:
            code = e.get('errorCode') or e.get('code') or ''
            emsg = e.get('message') or ''
            param = e.get('parameterName')
            piece = " ".join(p for p in (code, emsg) if p).strip()
            if param and param not in piece:
                piece = f"{piece} [{param}]".strip()
            if piece:
                parts.append(piece)
        msg = " | ".join(parts) if parts else body.get('message', msg)
    except Exception:
        pass
    return jsonify({"status": "error", "message": msg})
