import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, jsonify, send_file
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from .utils import fetch_all_users, get_device_ringing_status

device_ringing_audit_bp = Blueprint('device_ringing_audit_bp', __name__, url_prefix='/api/device_ringing_audit')

@device_ringing_audit_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Device Ringing Audit')
def audit_devices():
    try:
        users = fetch_all_users()
        audit_data = []

        for user in users:
            # Skip unprovisioned / disabled users
            if user.get('status') not in ['Enabled', 'NotActivated']:
                continue

            ext_id = str(user['id'])
            ext_name = user.get('name', 'Unknown')
            ext_num = user.get('extensionNumber', '')

            mobile_en, desktop_en, device_map, device_status = get_device_ringing_status(ext_id)

            row = {
                "Username": ext_name,
                "Extension": ext_num,
                "Extension ID": ext_id,
                "Mobile App Ring Enabled": "Yes" if mobile_en else "No",
                "Desktop App Ring Enabled": "Yes" if desktop_en else "No"
            }

            dev_idx = 1
            for did, dname in device_map.items():
                row[f"Device {dev_idx} Name"] = dname
                row[f"Device {dev_idx} Ring Enabled"] = "Yes" if device_status[did] else "No"
                dev_idx += 1

            audit_data.append(row)

        if not audit_data:
            audit_data = [{"Username": "No active users found", "Extension": "", "Extension ID": ""}]

        df = pd.DataFrame(audit_data)

        # Order base columns first, then dynamically append device columns
        base_cols = ["Username", "Extension", "Extension ID", "Mobile App Ring Enabled", "Desktop App Ring Enabled"]
        dev_cols = [c for c in df.columns if c.startswith("Device")]
        
        # Sort device columns logically by device index
        dev_cols.sort(key=lambda x: int(x.split(' ')[1]) if len(x.split(' ')) > 1 and x.split(' ')[1].isdigit() else 999)

        final_cols = base_cols + dev_cols
        df = df[[c for c in final_cols if c in df.columns]]

        # Generate Excel File
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Device Ringing Audit')
            worksheet = writer.sheets['Device Ringing Audit']
            # Auto-adjust column widths
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

        output.seek(0)
        return send_file(
            output,
            download_name=f"Device_Ringing_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500
