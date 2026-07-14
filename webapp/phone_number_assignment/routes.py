import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, session
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

phone_number_assignment_bp = Blueprint('phone_number_assignment_bp', __name__, url_prefix='/api/phone_number_assignment')

@phone_number_assignment_bp.route('/template', methods=['GET'])
@require_rc_token
@track_usage('Phone Number Assignment - Template')
def get_template():
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    try:
        output = utils.generate_template(token)
        filename = f"Phone_Number_Assignment_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@phone_number_assignment_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Phone Number Assignment - Upload')
def upload():
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
            
        records = df.to_dict('records')
        results = utils.process_assignments(records, token)
        return jsonify({"logs": results})
    except Exception as e:
        return jsonify({"error": f"File processing error: {str(e)}"}), 500
