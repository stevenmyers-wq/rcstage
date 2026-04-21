import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from webapp.presence.utils import RCPresenceManager

presence_bp = Blueprint('presence', __name__)

def parse_bool(val):
    if pd.isna(val) or str(val).strip() == "": return None
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

@presence_bp.route('/api/presence/audit', methods=['POST'])
def generate_audit_report():
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

        for record in records:
            l_id = record.get('id')
            ext_obj = record.get('extension') or {}
            m_id = str(ext_obj.get('id', ''))
            
            # Identify Shared Lines or standard extensions
            master = id_to_ext_map.get(m_id, {})
            name_val = master.get('name') or ext_obj.get('type') or 'Unknown'
            ext_val = master.get('extensionNumber') or ext_obj.get('extensionNumber') or m_id
            
            row[f"Line {l_id} Name"] = f"{name_val} ({master.get('type', 'Unknown')})"
            row[f"Line {l_id} Extension"] = str(ext_val)

        audit_data.append(row)
    
    df = pd.DataFrame(audit_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='BLF_Audit.xlsx')

@presence_bp.route('/api/presence/update', methods=['POST'])
def update_blf():
    file = request.files['file']
    df = pd.read_excel(file, sheet_name=0)
    df.columns = df.columns.str.strip()
    
    manager = RCPresenceManager()
    all_exts = manager.get_all_extensions_raw() or manager.get_all_users()
    ext_map = {str(e.get('extensionNumber')): str(e.get('id')) for e in all_exts if e.get('extensionNumber')}
    
    results = {"success": 0, "errors": []}
    line_cols = [c for c in df.columns if "Line" in c and "Extension" in c]

    for _, row in df.iterrows():
        t_id = str(row["Target Extension ID"]).split('.')[0]
        
        # 1. Update Toggles
        toggles = {}
        for key, field in [("Ring on Monitored Call", "ringOnMonitoredCall"), 
                           ("Enable Me to Pickup a Monitored Line", "pickUpCallsOnHold")]:
            val = parse_bool(row.get(key))
            if val is not None: toggles[field] = val
        if toggles: manager.update_presence_settings(t_id, toggles)

        # 2. Update Lines - Fetch live state first to see what's locked
        live_lines = manager.get_monitored_lines(t_id).get('records', [])
        locked_ids = {str(r['id']) for r in live_lines if r.get('notEditableOnHud')}
        
        new_payload = []
        for col in line_cols:
            l_idx = col.split(' ')[1]
            if l_idx in locked_ids: continue # DISREGARD LOCKED KEYS
            
            val = str(row[col]).split('.')[0].strip() if pd.notna(row[col]) else ""
            if not val: continue
            
            m_id = ext_map.get(val) or manager.get_extension_by_number(val) or val
            new_payload.append({"id": l_idx, "extension": {"id": m_id}})

        try:
            if new_payload:
                print(f"Payload for {t_id} (Locked {locked_ids} excluded): {new_payload}")
                manager.update_monitored_lines(t_id, new_payload)
            results["success"] += 1
        except Exception as e:
            results["errors"].append(f"Ext {t_id}: {str(e)}")

    return jsonify({"status": "completed", "message": f"Updated {results['success']} users", "errors": results["errors"]})
