import io
import json
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager, RCPresenceError
from webapp.auth_utils import require_rc_token, is_admin_user
from webapp.usage_tracking import track_usage
import logging

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/sites', methods=['GET'])
@require_rc_token
def get_sites():
    try:
        manager = RCPresenceManager()
        sites = manager.get_sites()
        return jsonify({"status": "success", "sites": sites})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/template', methods=['GET'])
def get_template():
    try:
        columns = ["Target Extension Name", "Target Extension Number", "Target Extension ID", 
                   "Ring on Monitored Call", "Enable Me to Pickup a Monitored Line", 
                   "Allow other users to see my presence status"]
        for i in range(1, 101):
            columns.append(f"Line {i} Name")
            columns.append(f"Line {i} Extension")
        df_template = pd.DataFrame(columns=columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_template.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='Template.xlsx')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/users', methods=['GET'])
@require_rc_token
def get_users():
    try:
        manager = RCPresenceManager()
        users = manager.get_all_users()
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/audit', methods=['POST'])
@require_rc_token
@track_usage('BLF Presence Audit')
def generate_audit_report():
    try:
        data = request.json
        selected_users = data.get('users', [])
        manager = RCPresenceManager()
        
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}
        
        audit_data = []
        for user in selected_users:
            ext_id = user.get('id')
            settings = manager.get_presence_settings(ext_id)
            lines_resp = manager.get_monitored_lines(ext_id)
            records = lines_resp.get('records') or []

            row = {
                "Target Extension Name": user.get('name', ''),
                "Target Extension Number": user.get('extensionNumber', ''),
                "Target Extension ID": ext_id,
                "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
            }

            assigned_map = {str(r.get('id')): r for r in records}
            
            for i, record in enumerate(records):
                line_idx = i + 1
                ext_obj = record.get('extension') or {}
                m_id = str(ext_obj.get('id', ''))
                
                master = id_to_ext_map.get(m_id, {})
                type_label = master.get('type') or ext_obj.get('type') or 'Unknown'
                name = master.get('name') or ext_obj.get('name') or type_label
                ext_num = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
                
                lock_status = "[LOCKED] " if record.get('notEditableOnHud') else ""
                row[f"Line {line_idx} Name"] = f"{lock_status}{name} ({type_label})"
                row[f"Line {line_idx} Extension"] = str(ext_num)
            
            audit_data.append(row)
            
        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Audit_Detailed.xlsx')
    except Exception as e:
        logging.exception("Audit Crash")
        return jsonify({"status": "error", "message": f"Audit Failed: {str(e)}"}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
@require_rc_token
@track_usage('BLF Presence Update')
def update_blf():
    try:
        file = request.files['file']
        df = pd.read_excel(file, sheet_name=0)
        df.columns = df.columns.str.strip()

        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}

        results = {"success": 0, "errors": []}

        target_col = next((c for c in df.columns if "target extension id" in c.lower()), None)

        for _, row in df.iterrows():
            if not target_col: continue

            t_id = str(row.get(target_col, "")).split('.')[0].strip()
            if not t_id or t_id.lower() == 'nan': continue

            try:
                _process_row(manager, df, row, t_id, ext_map, results)
            except RCPresenceError as e:
                results["errors"].append(f"Ext {t_id}: RC rejected update ({e.status_code}): {e.body}")
                logging.error(f"RC API Error during update for {t_id}: {e}")
            except Exception as e:
                results["errors"].append(f"Ext {t_id}: {str(e)}")
                logging.exception(f"Unexpected error during update for {t_id}")

        status = "completed" if results["success"] or not results["errors"] else "error"
        return jsonify({"status": status, "message": f"Updated {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500


def _process_row(manager, df, row, t_id, ext_map, results):
    """Apply presence toggles + monitored-line changes for a single target
    extension. Raises RCPresenceError if RingCentral rejects a write so the
    caller records a real failure instead of a false success."""
    toggles = {}
    for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"),
                       ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold"),
                       ("Allow other users to see my presence status", "allowSeeMyPresence")]:
        val = parse_bool(row.get(key))
        if val is not None: toggles[field] = val

    toggles_applied = False
    if toggles:
        manager.update_presence_settings(t_id, toggles)
        toggles_applied = True

    # A failed read must abort this row: the PUT is a full replacement, so
    # acting on an empty/partial list would wipe or corrupt the user's lines.
    live_resp = manager.get_monitored_lines(t_id)
    live_records = live_resp.get('records', [])

    payload_records = []
    seen_extensions = set()

    for i, record in enumerate(live_records):
        real_slot_id = str(record.get('id'))
        is_locked = record.get('notEditableOnHud', False)
        current_ext_id = str(record.get('extension', {}).get('id', ''))

        sheet_col = f"Line {i + 1} Extension"
        val = row.get(sheet_col) if sheet_col in df.columns else None

        # notEditableOnHud lines (the user's own presence on lines 1 & 2, plus
        # any system-managed slot such as a monitored Department) cannot be
        # changed, but they MUST be re-sent verbatim in their positions — the
        # PUT is a full-list replacement and dropping a locked slot is rejected.
        if is_locked:
            if current_ext_id:
                payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                seen_extensions.add(current_ext_id)
            continue

        if pd.isna(val) or str(val).strip() == "":
            if current_ext_id and current_ext_id not in seen_extensions:
                payload_records.append({"id": real_slot_id, "extension": {"id": current_ext_id}})
                seen_extensions.add(current_ext_id)
            continue

        val_str = str(val).split('.')[0].strip()

        if val_str.upper() == "CLEAR":
            continue

        monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
        if monitored_id in seen_extensions:
            continue

        payload_records.append({
            "id": real_slot_id,
            "extension": {"id": monitored_id}
        })
        seen_extensions.add(monitored_id)

    for i in range(len(live_records), 100):
        sheet_col = f"Line {i + 1} Extension"
        val = row.get(sheet_col) if sheet_col in df.columns else None

        if pd.isna(val) or str(val).strip() == "" or str(val).strip().upper() == "CLEAR":
            continue

        val_str = str(val).split('.')[0].strip()
        monitored_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str

        if monitored_id in seen_extensions:
            continue

        payload_records.append({
            "extension": {"id": monitored_id}
        })
        seen_extensions.add(monitored_id)

    # Defensive: never send a record whose extension id (or slot id) is empty
    # or a non-numeric placeholder like "None"/"nan". RC rejects these with a
    # generic "InvalidParameter" and it corrupts the whole full-replacement PUT.
    def _valid_id(v):
        return bool(v) and str(v).strip().lower() not in ('', 'none', 'nan') and str(v).strip().isdigit()

    cleaned_records = []
    dropped = []
    for p in payload_records:
        ext_id = str(p.get('extension', {}).get('id', '')).strip()
        slot_id = p.get('id')
        if not _valid_id(ext_id) or (slot_id is not None and not _valid_id(slot_id)):
            dropped.append(p)
            continue
        cleaned_records.append(p)
    payload_records = cleaned_records

    # Payload mirrors the full live list (locked lines re-sent verbatim), so
    # compare against the full live extension list to detect real changes.
    current_exts = [str(r.get('extension', {}).get('id', '')) for r in live_records]
    payload_exts = [str(p.get('extension', {}).get('id', '')) for p in payload_records]

    if current_exts != payload_exts:
        sent = {"records": payload_records}
        logging.info("Presence PUT ext=%s payload=%s", t_id, json.dumps(sent))
        try:
            manager.update_monitored_lines(t_id, payload_records)
            results["success"] += 1
        except RCPresenceError as e:
            drop_note = f" | dropped {len(dropped)} invalid record(s)" if dropped else ""
            results["errors"].append(
                f"Ext {t_id}: RC rejected ({e.status_code}): {e.body} | sent: {json.dumps(sent)}{drop_note}"
            )
        return
    elif toggles_applied:
        results["success"] += 1
    else:
        results["errors"].append(f"Ext {t_id}: No changes detected.")


# ==========================================
# THE DIAGNOSTICS SANDBOX (admin-only)
# ==========================================
@presence_bp.route('/api/presence/sandbox/<extension_id>', methods=['POST'])
@require_rc_token
def presence_sandbox(extension_id):
    # Fires an arbitrary raw PUT straight at RC — restrict to admins.
    if not is_admin_user():
        return jsonify({"status": "error", "message": "Admin privileges required."}), 403
    try:
        raw_payload = request.json
        manager = RCPresenceManager()
        endpoint = f"{manager.base_path}/extension/{extension_id}/presence/line"

        # Surface the real HTTP status/body from RC instead of swallowing it.
        response = manager._call(endpoint, method="PUT", json=raw_payload)
        return jsonify({"status": "success", "data": response})
    except RCPresenceError as e:
        return jsonify({"status": "error", "status_code": e.status_code, "message": e.body or str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ==========================================
# PRESENCE DIAGNOSTICS (admin-only)
# Answers: who is the token, what presence permissions it has, whether a
# presence/line WRITE succeeds at all (self vs. target), and lets us probe
# arbitrary RC read endpoints — all without another deploy cycle.
# Usage:
#   GET /api/presence/diag                      -> identity + permissions + read checks
#   GET /api/presence/diag?target=233306125     -> also raw-read that extension's lines
#   GET /api/presence/diag?selfwrite=1          -> also attempt a no-op PUT to self
#   GET /api/presence/diag?probe=/restapi/...   -> raw GET of any RC path
# ==========================================
@presence_bp.route('/api/presence/diag', methods=['GET'])
@require_rc_token
def presence_diag():
    if not is_admin_user():
        return jsonify({"status": "error", "message": "Admin privileges required."}), 403

    from flask import session
    from webapp.rc_api import rc

    # Probe with the SAME token selection the presence module uses: no explicit
    # token, so rc_api_call picks the SM impersonation bridge first (falling
    # back to PKCE). This tests the real path.
    out = {}
    out['token_type'] = (
        'sm_isolated_token (impersonation)' if session.get('sm_isolated_token')
        else 'rc_access_token (pkce)' if session.get('rc_access_token')
        else 'none'
    )
    out['bridge_target'] = session.get('sm_target_id')

    # Arbitrary read probe (safe: GET only).
    probe = request.args.get('probe')
    if probe:
        r = rc.get(probe)
        return jsonify({
            "probe": probe,
            "status_code": getattr(r, 'status_code', None),
            "body": _safe_json(r),
        })

    # Who does this token represent?
    self_resp = rc.get('/restapi/v1.0/account/~/extension/~')
    self_ext = _safe_json(self_resp) if getattr(self_resp, 'ok', False) else {}
    self_id = str(self_ext.get('id')) if self_ext.get('id') else None
    out['self'] = {k: self_ext.get(k) for k in ('id', 'extensionNumber', 'name', 'type')}
    out['self_read_status'] = getattr(self_resp, 'status_code', None)

    # What presence-related permissions does the user hold?
    authz = rc.get('/restapi/v1.0/account/~/extension/~/authz-profile')
    authz_body = _safe_json(authz) if getattr(authz, 'ok', False) else {}
    perm_ids = [
        (p.get('permission') or {}).get('id')
        for p in (authz_body.get('permissions') or [])
    ]
    out['all_permission_ids'] = sorted([p for p in perm_ids if p])
    out['presence_permissions'] = sorted([p for p in perm_ids if p and ('presence' in p.lower() or 'hud' in p.lower())])

    # Raw read of a target's presence lines (shape confirmation).
    target = request.args.get('target')
    if target:
        tr = rc.get(f'/restapi/v1.0/account/~/extension/{target}/presence/line')
        out['target_line_read'] = {"status_code": getattr(tr, 'status_code', None), "body": _safe_json(tr)}

    # Does a presence/line WRITE succeed on the token's OWN extension?
    # No-op echo of the current lines; gated behind ?selfwrite=1 since any PUT
    # is a real (here idempotent) write.
    if request.args.get('selfwrite') == '1' and self_id:
        sr = rc.get(f'/restapi/v1.0/account/~/extension/{self_id}/presence/line')
        recs = (_safe_json(sr) or {}).get('records', []) if getattr(sr, 'ok', False) else []
        # Full-list echo as a BARE ARRAY (the shape this endpoint actually
        # requires), locked lines included, so it is a valid no-op self-write.
        echo = [
            {"id": str(r.get('id')), "extension": {"id": str(r.get('extension', {}).get('id', ''))}}
            for r in recs
            if r.get('extension', {}).get('id')
        ]
        wr = rc.put(f'/restapi/v1.0/account/~/extension/{self_id}/presence/line', json=echo)
        out['self_write'] = {
            "sent": echo,
            "status_code": getattr(wr, 'status_code', None),
            "body": _safe_json(wr),
        }

    return jsonify(out)


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return getattr(resp, 'text', '')[:1000]
