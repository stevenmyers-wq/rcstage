import re
import traceback
from flask import Blueprint, jsonify, request, send_file
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

port_mapping_bp = Blueprint('port_mapping_bp', __name__, url_prefix='/api/port_mapping')

@port_mapping_bp.route('/process', methods=['POST'])
@require_rc_token
@track_usage('Port Mapping')
def process_mapping():
    loa_file = request.files.get('loa_file')
    brd_url = request.form.get('brd_url')

    if not loa_file:
        return jsonify({"error": "LOA (PDF) file is required."}), 400
    
    if not brd_url:
        return jsonify({"error": "BRD Google Drive link is required."}), 400

    if not loa_file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "LOA must be a PDF file."}), 400

    # Extract the File ID from the Google Drive URL
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", brd_url)
    if not match:
        return jsonify({"error": "Invalid Google Drive URL. Ensure it contains '/d/FILE_ID/'"}), 400
    
    file_id = match.group(1)

    try:
        output_buffer = utils.process_port_mapping(loa_file.read(), file_id)
        return send_file(
            output_buffer,
            download_name="Processed_Port_Mapping.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
