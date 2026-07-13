import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, jsonify, send_file, session
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from . import utils

device_audit_bp = Blueprint('device_audit_bp', __name__, url_prefix='/api/device_audit')

@device_audit_bp.route('/export', methods=['POST'])
@require_rc_token
@track_usage('Device Audit - Export')
def export_device_audit():
    token = get_rc_access_token()
    if not token:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = utils.generate_device_audit(token)
        df = pd.DataFrame(data)
        
        # Sort the DataFrame exactly as requested
        if not df.empty and "Type (User, Common Area, Unassigned etc)" in df.columns:
            df.sort_values(by=["Type (User, Common Area, Unassigned etc)", "Model", "Name"], inplace=True)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Device Audit')
            worksheet = writer.sheets['Device Audit']
            # Auto-adjust column widths for readability
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
                
        output.seek(0)
        filename = f"Device_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output, 
            download_name=filename, 
            as_attachment=True, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
