import re
import traceback
from flask import Blueprint, jsonify, request, send_file, session
from webapp.usage_tracking import track_usage
from . import utils

port_mapping_bp = Blueprint('port_mapping_bp', __name__, url_prefix='/api/port_mapping')

@port_mapping_bp.route('/process', methods=['POST'])
@track_usage('Port Mapping (Bridged)')
def process_mapping():
    # Using the central SM token
    token = session.get('sm_isolated_token')
    if not token:
        return jsonify({"error": "Unauthorized: Please Bridge the connection first."}), 401

    loa_file = request.files.get('loa_file')
    loa_url = request.form.get('loa_url')
    brd_file = request.files.get('brd_file')
    brd_url = request.form.get('brd_url')
    # Per-user Google Drive OAuth token from the browser (Google Identity
    # Services), same as the message-management bulk upload. Required to read
    # any Drive link, since public/anyone-with-link access is now restricted.
    drive_token = (request.form.get('drive_token') or '').strip() or None

    if not loa_file and not loa_url: return jsonify({"error": "LOA (PDF) file or URL is required."}), 400
    if not brd_file and not brd_url: return jsonify({"error": "BRD (Excel) file or URL is required."}), 400

    loa_bytes = loa_file_id = brd_bytes = brd_file_id = None

    if loa_file:
        if not loa_file.filename.lower().endswith('.pdf'): return jsonify({"error": "LOA must be a PDF file."}), 400
        loa_bytes = loa_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", loa_url)
        if not match: return jsonify({"error": "Invalid LOA Google Drive URL."}), 400
        loa_file_id = match.group(1)

    if brd_file:
        if not (brd_file.filename.lower().endswith('.xlsx') or brd_file.filename.lower().endswith('.xls')):
            return jsonify({"error": "BRD must be an Excel file."}), 400
        brd_bytes = brd_file.read()
    else:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", brd_url)
        if not match: return jsonify({"error": "Invalid BRD Google Drive URL."}), 400
        brd_file_id = match.group(1)

    # A Drive link can only be read with the user's authorization now.
    if (loa_file_id or brd_file_id) and not drive_token:
        return jsonify({"error": "Google Drive authorization is required to read Drive links. Please authorize Drive access and try again."}), 400

    try:
        output_buffer = utils.process_port_mapping(
            token=token,
            loa_bytes=loa_bytes,
            loa_file_id=loa_file_id,
            brd_bytes=brd_bytes,
            brd_file_id=brd_file_id,
            drive_token=drive_token
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
