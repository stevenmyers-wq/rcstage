import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/sites', methods=['GET'])
def get_sites():
    try:
        manager = RCPresenceManager()
        sites = manager.get_sites()
        return jsonify({"status": "success", "sites": sites})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/users', methods=['GET'])
def get_users():
    try:
        manager = RCPresenceManager()
        users = manager.get_all_users()
        return jsonify({"status": "success", "users": users})
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

@presence_bp.route('/api/presence/audit', methods=['POST'])
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
            for i, record in enumerate(records):
                line_idx = i + 1
                ext_obj = record.get('extension') or {}
                m_id = str(ext_obj.get('id', ''))
                master = id_to_ext_map.get(m_id, {})
                row[f"Line {line_idx} Name"] = f"{'[LOCKED] ' if record.get('notEditableOnHud') else ''}{master.get('name') or ext_obj.get('name') or ''}"
                row[f"Line {line_idx} Extension"] = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
            audit_data.append(row)
        df = pd.DataFrame(audit_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='Audit.xlsx')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf():
    try:
        file = request.files['file']
        df = pd.read_excel(file, sheet_name=0)
        df.columns = df.columns.str.strip()
        
        manager = RCPresenceManager()
        all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
        ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
        
        results = {"success": 0, "errors": []}

        for _, row in df.iterrows():
            target_col = next((c for c in df.columns if "target extension id" in c.lower()), None)
            if not target_col: continue
            t_id = str(row.get(target_col, "")).split('.')[0].strip()
            if not t_id or t_id.lower() == 'nan': continue
            
            # 1. Toggles (Always Fire if present)
            toggles = {}
            for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"), 
                               ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold"),
                               ("Allow other users to see my presence status", "allowSeeMyPresence")]:
                val = parse_bool(row.get(key))
                if val is not None: toggles[field] = val
            if toggles: manager.update_presence_settings(t_id, toggles)

            # 2. Get Real Provisioned Slots
            live_resp = manager.get_monitored_lines(t_id)
            live_records = live_resp.get('records', [])
            
            payload_records = []
            seen_extensions = set()
            skipped_slots = []

            # We ONLY update what exists in live_records. 
            # If RingCentral gives 4 records, we can only update 4 lines.
            for i, record in enumerate(live_records):
                line_num = i + 1
                real_id = str(record.get('id'))
                is_locked = record.get('notEditableOnHud', False)
                
                sheet_col = f"Line {line_num} Extension"
                val = row.get(sheet_col) if sheet_col in df.columns else None

                # Keep locked lines exactly as they are
                if is_locked:
                    curr_id = record.get('extension', {}).get('id')
                    if curr_id:
                        payload_records.append({"id": real_id, "extension": {"id": str(curr_id)}})
                        seen_extensions.add(str(curr_id))
                    continue

                # If spreadsheet is blank, keep current
                if pd.isna(val) or str(val).strip() == "":
                    curr_id = record.get('extension', {}).get('id')
                    if curr_id and str(curr_id) not in seen_extensions:
                        payload_records.append({"id": real_id, "extension": {"id": str(curr_id)}})
                        seen_extensions.add(str(curr_id))
                    continue

                val_str = str(val).split('.')[0].strip()
                if val_str.upper() == "CLEAR": continue

                # Map number to ID
                mon_id = ext_map.get(val_str) or manager.get_extension_by_number(val_str) or val_str
                if mon_id not in seen_extensions:
                    payload_records.append({"id": real_id, "extension": {"id": mon_id}})
                    seen_extensions.add(mon_id)

            # Check if user tried to fill lines the system doesn't have slots for
            for i in range(len(live_records) + 1, 101):
                col = f"Line {i} Extension"
                if col in df.columns and not pd.isna(row.get(col)) and str(row.get(col)).strip() != "":
                    skipped_slots.append(str(i))

            if skipped_slots:
                results["errors"].append(f"Ext {t_id}: Lines {', '.join(skipped_slots)} ignored (system only has {len(live_records)} slots).")

            # 3. FORCE THE UPDATE (No more "diff" check)
            if payload_records:
                try:
                    manager.update_monitored_lines(t_id, payload_records)
                    results["success"] += 1
                except Exception as e:
                    results["errors"].append(f"Ext {t_id}: {str(e)}")
            elif toggles:
                results["success"] += 1

        return jsonify({"status": "completed", "message": f"Processed {results['success']} users", "errors": results["errors"]})
    except Exception as e:
        logging.exception("Upload Crash")
        return jsonify({"status": "error", "message": str(e)}), 500
