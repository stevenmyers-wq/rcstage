import io
import json
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager, RCPresenceError
from webapp.auth_utils import require_rc_token, is_admin_user
from webapp.usage_tracking import track_usage
from webapp import task_control
import logging

presence_bp = Blueprint('presence', __name__)
presence_bp.add_url_rule('/api/presence/cancel', 'presence_cancel', task_control.cancel_view, methods=['POST'])

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
        task_id = request.form.get('task_id')
        additive = request.form.get('additive', 'false').lower() == 'true'
        df = pd.read_excel(file, sheet_name=0)
        df.columns = df.columns.str.strip()

        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        valid_ids = {str(e.get('id')) for e in all_exts if e.get('id')}

        results = {"success": 0, "errors": []}
        cancelled = False

        target_id_col = next((c for c in df.columns if "target extension id" in c.lower()), None)
        target_num_col = next((c for c in df.columns if "target extension number" in c.lower()), None)

        def _clean(v):
            s = str(v).split('.')[0].strip()
            return "" if s.lower() in ("", "nan", "none") else s

        try:
            for _, row in df.iterrows():
                # Cooperative stop: users already updated stand; the rest skipped.
                if task_control.is_stopped(task_id):
                    cancelled = True
                    break

                # Prefer Target Extension ID, but fall back to resolving the
                # Target Extension Number so a sheet with only the number works.
                t_id = _clean(row.get(target_id_col)) if target_id_col else ""
                if not t_id and target_num_col:
                    t_num = _clean(row.get(target_num_col))
                    if t_num:
                        t_id = ext_map.get(t_num) or manager.get_extension_by_number(t_num) or ""
                        if not t_id:
                            results["errors"].append(f"Target ext {t_num}: not found in this account.")
                if not t_id: continue

                try:
                    _process_row(manager, df, row, t_id, ext_map, valid_ids, results, additive=additive)
                except RCPresenceError as e:
                    results["errors"].append(f"Ext {t_id}: RC rejected update ({e.status_code}): {e.body}")
                    logging.error(f"RC API Error during update for {t_id}: {e}")
                except Exception as e:
                    results["errors"].append(f"Ext {t_id}: {str(e)}")
                    logging.exception(f"Unexpected error during update for {t_id}")
        finally:
            task_control.clear(task_id)

        if cancelled:
            status = "cancelled"
            message = f"Stopped by user. Updated {results['success']} user(s); the rest were skipped."
        else:
            status = "completed" if results["success"] or not results["errors"] else "error"
            message = f"Updated {results['success']} users"
        return jsonify({"status": status, "message": message, "cancelled": cancelled, "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500


def _process_row(manager, df, row, t_id, ext_map, valid_ids, results, additive=False):
    """Apply presence toggles + monitored-line changes for a single target
    extension. Raises RCPresenceError if RingCentral rejects a write so the
    caller records a real failure instead of a false success.

    additive=True: keep every current line (reserved + monitored) and only add
    extensions listed in the sheet that aren't already monitored — never replace
    or remove. This is position-agnostic: it trusts each live record's own
    notEditableOnHud flag, so it is safe regardless of which/how many slots are
    reserved (which cannot be predicted up front)."""
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

    def resolve(val_str):
        """Resolve a sheet cell to a real internal extension id, or None if it
        can't be resolved (so we skip + report it rather than sending a bogus
        id that RC rejects with CMN-102, failing the entire PUT)."""
        if val_str in ext_map:            # extension number -> id
            return ext_map[val_str]
        if val_str in valid_ids:          # already a known internal id
            return val_str
        looked = manager.get_extension_by_number(val_str)   # query by number
        if looked:
            return str(looked)
        if manager.extension_exists(val_str):   # an id not in our page cache
            return val_str
        return None

    # A failed read must abort this row: the PUT is a full replacement, so
    # acting on an empty/partial list would wipe or corrupt the user's lines.
    live_resp = manager.get_monitored_lines(t_id)
    live_records = live_resp.get('records', [])

    payload_records = []      # each is {"extension": {"id": ...}}; ids assigned below
    seen_extensions = set()
    unresolved = []

    def keep_line(record):
        """Re-send an existing line verbatim. Reserved (notEditableOnHud) lines
        keep their EXACT slot id via _lid so RC never sees them 'modified'."""
        current_ext_id = str(record.get('extension', {}).get('id', ''))
        if not current_ext_id:
            return
        if record.get('notEditableOnHud', False):
            payload_records.append({"extension": {"id": current_ext_id}, "_lid": str(record.get('id'))})
            seen_extensions.add(current_ext_id)
        elif current_ext_id not in seen_extensions:
            payload_records.append({"extension": {"id": current_ext_id}})
            seen_extensions.add(current_ext_id)

    if additive:
        # Additive: preserve every current line (reserved + monitored), then add
        # any sheet extension not already monitored. Position-agnostic — we never
        # assume which slots are locked; we trust the live notEditableOnHud flags.
        for record in live_records:
            keep_line(record)

        ext_cols = [c for c in df.columns
                    if c.strip().lower().startswith("line ") and "extension" in c.strip().lower()]
        for col in ext_cols:
            val = row.get(col)
            if pd.isna(val) or str(val).strip() == "" or str(val).strip().upper() == "CLEAR":
                continue
            val_str = str(val).split('.')[0].strip()
            monitored_id = resolve(val_str)
            if not monitored_id:
                unresolved.append(val_str)
                continue
            if monitored_id in seen_extensions:
                continue
            payload_records.append({"extension": {"id": monitored_id}})
            seen_extensions.add(monitored_id)
    else:
        # Positional replacement: sheet "Line N" maps to slot N. Reserved slots
        # are re-sent verbatim (sheet value ignored); editable slots take the
        # sheet value or keep current when blank; CLEAR removes; extra lines add.
        for i, record in enumerate(live_records):
            is_locked = record.get('notEditableOnHud', False)
            current_ext_id = str(record.get('extension', {}).get('id', ''))

            sheet_col = f"Line {i + 1} Extension"
            val = row.get(sheet_col) if sheet_col in df.columns else None

            if is_locked:
                keep_line(record)
                continue

            if pd.isna(val) or str(val).strip() == "":
                keep_line(record)
                continue

            val_str = str(val).split('.')[0].strip()

            if val_str.upper() == "CLEAR":
                continue

            monitored_id = resolve(val_str)
            if not monitored_id:
                unresolved.append(val_str)
                keep_line(record)   # keep current rather than dropping on a bad cell
                continue
            if monitored_id in seen_extensions:
                continue

            payload_records.append({"extension": {"id": monitored_id}})
            seen_extensions.add(monitored_id)

        for i in range(len(live_records), 100):
            sheet_col = f"Line {i + 1} Extension"
            val = row.get(sheet_col) if sheet_col in df.columns else None

            if pd.isna(val) or str(val).strip() == "" or str(val).strip().upper() == "CLEAR":
                continue

            val_str = str(val).split('.')[0].strip()
            monitored_id = resolve(val_str)
            if not monitored_id:
                unresolved.append(val_str)
                continue
            if monitored_id in seen_extensions:
                continue

            payload_records.append({"extension": {"id": monitored_id}})
            seen_extensions.add(monitored_id)

    # Assign slot ids. Reserved (locked) records keep their EXACT id so RC never
    # sees a reserved line "modified" (BLF-101); every other record takes the
    # next free contiguous id that isn't a reserved one. RC requires an id on
    # every record and rejects a non-self extension on a reserved slot.
    reserved_ids = {int(r["_lid"]) for r in payload_records if r.get("_lid")}
    next_free = 1
    for rec in payload_records:
        lid = rec.pop("_lid", None)
        if lid is not None:
            rec["id"] = str(lid)
        else:
            while next_free in reserved_ids:
                next_free += 1
            rec["id"] = str(next_free)
            next_free += 1
    payload_records.sort(key=lambda r: int(r["id"]))

    if unresolved:
        results["errors"].append(
            f"Ext {t_id}: skipped unresolved extension(s): {', '.join(unresolved)}"
        )

    # Payload mirrors the full live list (locked lines re-sent verbatim), so
    # compare against the full live extension list to detect real changes.
    current_exts = [str(r.get('extension', {}).get('id', '')) for r in live_records]
    payload_exts = [str(p.get('extension', {}).get('id', '')) for p in payload_records]

    if current_exts != payload_exts:
        logging.info("Presence PUT ext=%s payload=%s", t_id, json.dumps(payload_records))
        try:
            manager.update_monitored_lines(t_id, payload_records)
            results["success"] += 1
        except RCPresenceError as e:
            results["errors"].append(
                f"Ext {t_id}: RC rejected ({e.status_code}): {e.body} | sent: {json.dumps(payload_records)}"
            )
        return
    elif toggles_applied:
        results["success"] += 1
    elif not unresolved:
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
