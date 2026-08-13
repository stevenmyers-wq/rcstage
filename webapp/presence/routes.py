import io
import json
import pandas as pd
from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
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
        # "Answer Line N" columns drive the separate "users allowed to answer my
        # calls" permission list (additive, like the monitored lines above).
        for i in range(1, 51):
            columns.append(f"Answer Line {i} Name")
            columns.append(f"Answer Line {i} Extension")
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
    """Stream per-user audit progress as NDJSON so the UI can show a progress
    bar (each user needs two RC reads, so a large selection is slow). The built
    rows ride along in the ``progress`` chunks; the client accumulates them and
    posts them to /api/presence/audit/report to get the formatted workbook."""
    data = request.json or {}
    selected_users = data.get('users', [])
    task_id = data.get('task_id')

    def generate():
        try:
            manager = RCPresenceManager()
            all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
            id_to_ext_map = {str(e.get('id')): e for e in all_exts if e.get('id')}

            total = len(selected_users)
            yield json.dumps({"type": "start", "total": total,
                              "message": f"Auditing {total} user(s)…"}) + "\n"

            for idx, user in enumerate(selected_users):
                # Cooperative stop: rows already built are still downloadable.
                if task_control.is_stopped(task_id):
                    yield json.dumps({
                        "type": "cancelled", "current": idx, "total": total,
                        "message": f"Stopped by user. {idx} of {total} audited; the rest were skipped."
                    }) + "\n"
                    break

                ext_id = user.get('id')
                try:
                    settings = manager.get_presence_settings(ext_id)
                    lines_resp = manager.get_monitored_lines(ext_id)
                except Exception as e:
                    # One unreadable user shouldn't abort the whole audit now that
                    # results stream incrementally — skip it and keep going.
                    logging.warning("Audit read failed for %s: %s", ext_id, e)
                    yield json.dumps({
                        "type": "progress", "current": idx + 1, "total": total,
                        "result": {"name": user.get('name', '') or str(ext_id),
                                   "status": "error", "message": f"Could not read presence ({e})"}
                    }) + "\n"
                    continue
                # "Users allowed to answer my calls" lives on a separate resource.
                # Soft-fail it: an account/extension without this permission list
                # shouldn't blank out the rest of an otherwise-good audit row.
                try:
                    perms_resp = manager.get_presence_permissions(ext_id)
                except Exception as e:
                    logging.warning("Permission read failed for %s: %s", ext_id, e)
                    perms_resp = {"records": []}
                records = lines_resp.get('records') or []

                row = {
                    "Target Extension Name": user.get('name', ''),
                    "Target Extension Number": user.get('extensionNumber', ''),
                    "Target Extension ID": ext_id,
                    "Ring on Monitored Call": settings.get('ringOnMonitoredCall', False),
                    "Enable Me to Pickup a Monitored Line": settings.get('pickUpCallsOnHold', False),
                    "Allow other users to see my presence status": settings.get('allowSeeMyPresence', False)
                }

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

                # Users allowed to answer this extension's calls (separate list).
                for i, record in enumerate(perms_resp.get('records') or []):
                    ans_idx = i + 1
                    ext_obj = record.get('extension') or {}
                    m_id = str(ext_obj.get('id', ''))

                    master = id_to_ext_map.get(m_id, {})
                    type_label = master.get('type') or ext_obj.get('type') or 'Unknown'
                    name = master.get('name') or ext_obj.get('name') or type_label
                    ext_num = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id

                    row[f"Answer Line {ans_idx} Name"] = f"{name} ({type_label})"
                    row[f"Answer Line {ans_idx} Extension"] = str(ext_num)

                perm_count = len(perms_resp.get('records') or [])
                yield json.dumps({
                    "type": "progress", "current": idx + 1, "total": total, "row": row,
                    "result": {"name": user.get('name', '') or str(ext_id),
                               "status": "success",
                               "message": f"{len(records)} monitored line(s), {perm_count} answer permission(s)"}
                }) + "\n"

            yield json.dumps({"type": "done"}) + "\n"
        except Exception as e:
            logging.exception("Audit Crash")
            yield json.dumps({"type": "error", "message": f"Audit Failed: {str(e)}"}) + "\n"
        finally:
            task_control.clear(task_id)

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp


@presence_bp.route('/api/presence/audit/report', methods=['POST'])
@require_rc_token
def audit_report():
    """Build the formatted audit workbook from the rows streamed by
    /api/presence/audit. Column order follows the first-seen keys across rows,
    exactly as the previous single-shot audit produced it."""
    try:
        data = request.json or {}
        rows = data.get('rows') or []
        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name='BLF_Audit_Detailed.xlsx')
    except Exception as e:
        logging.exception("Audit report crash")
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
@require_rc_token
@track_usage('BLF Presence Update')
def update_blf():
    try:
        file = request.files['file']
        task_id = request.form.get('task_id')
        # Additive is the default behaviour (no UI toggle): add listed extensions
        # to each user's existing lines, never replace/remove. An explicit
        # additive=false can still request positional replacement via the API.
        additive = request.form.get('additive', 'true').lower() == 'true'
        df = pd.read_excel(file, sheet_name=(request.form.get('sheet_name') or 0))
        df.columns = df.columns.str.strip()

        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        valid_ids = {str(e.get('id')) for e in all_exts if e.get('id')}
        # id -> record, so per-target results can carry the friendly extension
        # number/name alongside the internal id RC actually keys on.
        id_to_ext = {str(e.get('id')): e for e in all_exts if e.get('id')}
    except Exception as e:
        # File/parse/directory failures happen before the stream starts, so they
        # can still be reported as a plain JSON error the client shows directly.
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500

    # "details" is a structured, per-target log (name + friendly number + internal
    # id + status + what happened) used for the downloadable results report.
    results = {"success": 0, "errors": [], "details": []}

    target_id_col = next((c for c in df.columns if "target extension id" in c.lower()), None)
    target_num_col = next((c for c in df.columns if "target extension number" in c.lower()), None)

    def _clean(v):
        s = str(v).split('.')[0].strip()
        return "" if s.lower() in ("", "nan", "none") else s

    def generate():
        cancelled = False
        total = len(df)
        yield json.dumps({"type": "start", "total": total,
                          "message": f"Processing {total} row(s)…"}) + "\n"
        try:
            for idx, (_, row) in enumerate(df.iterrows()):
                # Cooperative stop: users already updated stand; the rest skipped.
                if task_control.is_stopped(task_id):
                    cancelled = True
                    yield json.dumps({
                        "type": "cancelled", "current": idx, "total": total,
                        "message": f"Stopped by user. Updated {results['success']} user(s); the rest were skipped."
                    }) + "\n"
                    break

                before = len(results["details"])

                # Prefer Target Extension ID, but fall back to resolving the
                # Target Extension Number so a sheet with only the number works.
                t_id = _clean(row.get(target_id_col)) if target_id_col else ""
                if not t_id and target_num_col:
                    t_num = _clean(row.get(target_num_col))
                    if t_num:
                        t_id = ext_map.get(t_num) or manager.get_extension_by_number(t_num) or ""
                        if not t_id:
                            results["errors"].append(f"Target ext {t_num}: not found in this account.")
                            _add_detail(results, "", t_num, "", "Error",
                                        "Target extension not found in this account.")
                if not t_id:
                    yield json.dumps({"type": "progress", "current": idx + 1, "total": total,
                                      "result": _row_result(results, before, "Skipped blank row")}) + "\n"
                    continue

                try:
                    _process_row(manager, df, row, t_id, ext_map, valid_ids, id_to_ext, results, additive=additive)
                    # "Users allowed to answer my calls" is a separate resource,
                    # applied independently so a permission failure never masks a
                    # successful monitored-line update (and vice-versa).
                    _process_permissions(manager, df, row, t_id, ext_map, valid_ids, id_to_ext, results)
                except RCPresenceError as e:
                    results["errors"].append(f"Ext {t_id}: RC rejected update ({e.status_code}): {e.body}")
                    name, number = _friendly(id_to_ext, t_id)
                    _add_detail(results, name, number, t_id, "Error",
                                f"RC rejected update ({e.status_code}): {e.body}")
                    logging.error(f"RC API Error during update for {t_id}: {e}")
                except Exception as e:
                    results["errors"].append(f"Ext {t_id}: {str(e)}")
                    name, number = _friendly(id_to_ext, t_id)
                    _add_detail(results, name, number, t_id, "Error", str(e))
                    logging.exception(f"Unexpected error during update for {t_id}")

                yield json.dumps({"type": "progress", "current": idx + 1, "total": total,
                                  "result": _row_result(results, before)}) + "\n"
        finally:
            task_control.clear(task_id)

        if cancelled:
            status = "cancelled"
            message = f"Stopped by user. Updated {results['success']} user(s); the rest were skipped."
        else:
            status = "completed" if results["success"] or not results["errors"] else "error"
            message = f"Updated {results['success']} users"
        yield json.dumps({"type": "done", "status": status, "message": message,
                          "cancelled": cancelled, "errors": results["errors"],
                          "details": results["details"]}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp


@presence_bp.route('/api/presence/update/report', methods=['POST'])
@require_rc_token
def update_report():
    """Turn the per-target results from an update run into a downloadable
    Excel report. The client posts back the ``details`` array it received from
    /api/presence/update so the report can be generated without re-running the
    update (and without holding run state on the server)."""
    try:
        data = request.json or {}
        details = data.get('details') or []
        columns = ["Target Extension Name", "Target Extension Number",
                   "Target Extension ID", "Status", "Detail"]
        df = pd.DataFrame(details, columns=columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, download_name='Presence_Update_Results.xlsx')
    except Exception as e:
        logging.exception("Results report crash")
        return jsonify({"status": "error", "message": str(e)}), 500


def _friendly(id_to_ext, t_id):
    """Resolve an internal extension id to its friendly (name, number) pair for
    the results report. Falls back to blanks when the id isn't in the account
    cache (e.g. an id supplied directly in the sheet)."""
    e = id_to_ext.get(str(t_id), {}) if id_to_ext else {}
    return e.get('name', '') or '', str(e.get('extensionNumber', '') or '')


def _row_result(results, before_len, empty_message="No changes"):
    """Shape the newest detail row (appended since ``before_len``) into a compact
    per-row result for the progress stream. Rows that produced no detail (e.g. a
    fully blank sheet row) fall back to a neutral 'skipped' result."""
    new_details = results["details"][before_len:]
    if not new_details:
        return {"name": "", "ext": "", "status": "skipped", "message": empty_message}
    d = new_details[-1]
    st = (d.get("Status") or "").strip().lower()
    kind = "error" if st == "error" else ("skipped" if st in ("no change", "skipped") else "success")
    return {
        "name": d.get("Target Extension Name") or "",
        "ext": d.get("Target Extension Number") or d.get("Target Extension ID") or "",
        "status": kind,
        "message": d.get("Detail") or (d.get("Status") or ""),
    }


def _add_detail(results, name, number, ext_id, status, detail):
    """Append one per-target row to the downloadable results log. Carries both
    the friendly extension number/name and the internal id RC keys on."""
    results.setdefault("details", []).append({
        "Target Extension Name": name,
        "Target Extension Number": number,
        "Target Extension ID": str(ext_id) if ext_id else "",
        "Status": status,
        "Detail": detail,
    })


def _process_row(manager, df, row, t_id, ext_map, valid_ids, id_to_ext, results, additive=False):
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

    name, number = _friendly(id_to_ext, t_id)
    unresolved_note = f" Unresolved extension(s): {', '.join(unresolved)}." if unresolved else ""

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
            detail = f"Monitored lines updated ({len(current_exts)} -> {len(payload_exts)} line(s))."
            if toggles_applied:
                detail = "Presence settings updated; " + detail
            _add_detail(results, name, number, t_id, "Updated", detail + unresolved_note)
        except RCPresenceError as e:
            results["errors"].append(
                f"Ext {t_id}: RC rejected ({e.status_code}): {e.body} | sent: {json.dumps(payload_records)}"
            )
            _add_detail(results, name, number, t_id, "Error",
                        f"RC rejected ({e.status_code}): {e.body}")
        return
    elif toggles_applied:
        results["success"] += 1
        _add_detail(results, name, number, t_id, "Updated",
                    "Presence settings updated; monitored lines unchanged." + unresolved_note)
    elif unresolved:
        # Nothing applied and the only sheet action was extensions we couldn't resolve.
        _add_detail(results, name, number, t_id, "Error",
                    "No changes applied." + unresolved_note)
    else:
        results["errors"].append(f"Ext {t_id}: No changes detected.")
        _add_detail(results, name, number, t_id, "No Change", "No changes detected.")


def _process_permissions(manager, df, row, t_id, ext_map, valid_ids, id_to_ext, results):
    """Apply the "users allowed to answer my calls" list from the sheet's
    'Answer Line N' columns to the presence/permission resource.

    Additive and position-agnostic, exactly like the monitored-line handling:
    every current permission is re-sent and only extensions not already present
    are appended — nothing is removed. Runs independently of the monitored-line
    update and records its own result row (added only when something actually
    changes or an extension can't be resolved) so it never overwrites the
    monitored-line outcome. Raises nothing: RC failures are captured as errors."""
    ans_cols = [c for c in df.columns
                if c.strip().lower().startswith("answer line") and "extension" in c.strip().lower()]
    if not ans_cols:
        return  # sheet doesn't drive answer permissions

    def resolve(val_str):
        if val_str in ext_map:
            return ext_map[val_str]
        if val_str in valid_ids:
            return val_str
        looked = manager.get_extension_by_number(val_str)
        if looked:
            return str(looked)
        if manager.extension_exists(val_str):
            return val_str
        return None

    # A failed read must abort: the PUT is a full replacement, so acting on an
    # empty/partial list would wipe the user's existing permissions.
    live_records = manager.get_presence_permissions(t_id).get('records', [])

    payload_records = []
    seen = set()
    for record in live_records:
        current_ext_id = str(record.get('extension', {}).get('id', ''))
        if current_ext_id and current_ext_id not in seen:
            payload_records.append({"extension": {"id": current_ext_id}})
            seen.add(current_ext_id)

    unresolved = []
    for col in ans_cols:
        val = row.get(col)
        if pd.isna(val) or str(val).strip() == "" or str(val).strip().upper() == "CLEAR":
            continue
        val_str = str(val).split('.')[0].strip()
        resolved_id = resolve(val_str)
        if not resolved_id:
            unresolved.append(val_str)
            continue
        if resolved_id in seen:
            continue
        payload_records.append({"extension": {"id": resolved_id}})
        seen.add(resolved_id)

    # Assign contiguous slot ids, mirroring the monitored-line PUT contract for
    # this Limited endpoint. Permission records carry no reserved/locked flag.
    for i, rec in enumerate(payload_records):
        rec["id"] = str(i + 1)

    name, number = _friendly(id_to_ext, t_id)
    note = f" Unresolved answer extension(s): {', '.join(unresolved)}." if unresolved else ""

    current = [str(r.get('extension', {}).get('id', '')) for r in live_records]
    updated = [str(p.get('extension', {}).get('id', '')) for p in payload_records]

    if current != updated:
        logging.info("Presence permission PUT ext=%s payload=%s", t_id, json.dumps(payload_records))
        try:
            manager.update_presence_permissions(t_id, payload_records)
            _add_detail(results, name, number, t_id, "Updated",
                        f"Users allowed to answer calls updated "
                        f"({len(current)} -> {len(updated)}).{note}")
        except RCPresenceError as e:
            results["errors"].append(
                f"Ext {t_id} (answer permissions): RC rejected ({e.status_code}): {e.body}"
            )
            _add_detail(results, name, number, t_id, "Error",
                        f"RC rejected answer-permission update ({e.status_code}): {e.body}")
    elif unresolved:
        results["errors"].append(
            f"Ext {t_id}: skipped unresolved answer extension(s): {', '.join(unresolved)}"
        )
        _add_detail(results, name, number, t_id, "Error",
                    "No answer-permission changes applied." + note)


# ==========================================
# THE DIAGNOSTICS SANDBOX (admin-only)
# ==========================================
@presence_bp.route('/api/presence/sandbox/<extension_id>', methods=['POST'])
@require_rc_token
def presence_sandbox(extension_id):
    """Fire a RAW GET/PUT straight at a presence sub-resource — admin only.

    Generalised from the old line-only PUT so the exact request shape for the
    'users allowed to answer my calls' list (presence/permission) can be probed
    against a live extension: GET to read the current body, then PUT candidate
    payloads and read back RC's real status/body. Goes through ``rc`` directly
    (no retry/raise wrapper) so the untouched HTTP status and response are
    returned verbatim, and echoes the request that was sent for the record.

    Body: {"resource": "line"|"permission", "method": "GET"|"PUT", "payload": …}.
    A bare/legacy body (no these keys) is treated as a presence/line PUT payload.
    """
    if not is_admin_user():
        return jsonify({"status": "error", "message": "Admin privileges required."}), 403

    from webapp.rc_api import rc

    body = request.json
    if isinstance(body, dict) and any(k in body for k in ("resource", "method", "payload")):
        resource = (body.get("resource") or "line").strip().lower()
        method = (body.get("method") or "PUT").strip().upper()
        payload = body.get("payload")
    else:
        resource, method, payload = "line", "PUT", body

    sub = "presence/permission" if resource in ("permission", "permissions") else "presence/line"
    manager = RCPresenceManager()
    endpoint = f"{manager.base_path}/extension/{extension_id}/{sub}"
    req_info = {"method": method, "endpoint": endpoint,
                "body": payload if method == "PUT" else None}

    try:
        if method == "GET":
            resp = rc.get(endpoint)
        elif method == "PUT":
            resp = rc.put(endpoint, json=payload)
        else:
            return jsonify({"status": "error", "request": req_info,
                            "message": f"Unsupported method '{method}' (use GET or PUT)."}), 400

        return jsonify({
            "status": "success" if getattr(resp, "ok", False) else "error",
            "request": req_info,
            "status_code": getattr(resp, "status_code", None),
            "body": _safe_json(resp),
        })
    except Exception as e:
        return jsonify({"status": "error", "request": req_info, "message": str(e)}), 400


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
