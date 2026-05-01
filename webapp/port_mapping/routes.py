from flask import Blueprint, jsonify, request, send_file
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
import traceback
from . import utils

port_mapping_bp = Blueprint('port_mapping_bp', __name__, url_prefix='/api/port_mapping')

@port_mapping_bp.route('/process', methods=['POST'])
@require_rc_token
@track_usage('Port Mapping')
def process_mapping():
    if 'loa_file' not in request.files or 'brd_file' not in request.files:
        return jsonify({"error": "Both LOA (PDF) and BRD (Excel) files are required."}), 400

    loa_file = request.files['loa_file']
    brd_file = request.files['brd_file']

    if not loa_file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "LOA must be a PDF file."}), 400
    
    if not (brd_file.filename.lower().endswith('.xlsx') or brd_file.filename.lower().endswith('.xls')):
        return jsonify({"error": "BRD must be an Excel file."}), 400

    try:
        output_buffer = utils.process_port_mapping(loa_file.read(), brd_file.read())
        return send_file(
            output_buffer,
            download_name="Processed_Port_Mapping.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
