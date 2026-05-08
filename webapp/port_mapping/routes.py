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
    loa_url = request.form.get('loa_url')
    
    brd_file = request.files.get('brd_file')
    brd_url = request.form.get('brd_url')

    if not loa_file and not loa_url:
        return jsonify({"error": "LOA (PDF) file or URL is required."}), 400
    
    if not brd_file and not brd_url:
        return jsonify({"error": "BRD (Excel) file or URL is required."}), 400

    loa_bytes = None
    loa_file_id = None
    brd_bytes = None
    brd_file_id = None

    # Handle LOA Input
    if loa_file:
        if not loa_file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "LOA must be a PDF file."}), 400
        loa_bytes = loa_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", loa_url)
        if not match:
            return jsonify({"error": "Invalid LOA Google Drive URL. Ensure it contains '/d/FILE_ID/'"}), 400
        loa_file_id = match.group(1)

    # Handle BRD Input
    if brd_file:
        if not (brd_file.filename.lower().endswith('.xlsx') or brd_file.filename.lower().endswith('.xls')):
            return jsonify({"error": "BRD must be an Excel file."}), 400
        brd_bytes = brd_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", brd_url)
        if not match:
            return jsonify({"error": "Invalid BRD Google Drive URL. Ensure it contains '/d/FILE_ID/'"}), 400
        brd_file_id = match.group(1)

    try:
        output_buffer = utils.process_port_mapping(
            loa_bytes=loa_bytes, 
            loa_file_id=loa_file_id, 
            brd_bytes=brd_bytes, 
            brd_file_id=brd_file_id
        )
        return send_file(
            output_buffer,
            download_name="Processed_Port_Mapping.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
