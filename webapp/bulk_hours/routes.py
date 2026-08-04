import json
from flask import Blueprint, jsonify, request, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

bulk_hours_bp = Blueprint(
    'bulk_hours_bp', __name__,
    url_prefix='/api/bulk_hours'
)
bulk_hours_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _ndjson_stream(chunks, on_error):
    """Wrap a generator of dict chunks as a streaming NDJSON Response.

    Each chunk is emitted as one JSON line, flushed immediately so the browser
    can advance its progress bar in real time. ``on_error`` builds the error
    chunk yielded if the generator raises mid-stream (a non-200 status is no
    longer possible once streaming has begun)."""
    def generate():
        try:
            for chunk in chunks:
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            print(on_error[1].format(e))
            yield json.dumps({"type": "error", "message": on_error[0]}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'   # disable proxy buffering so chunks flush live
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@bulk_hours_bp.route('/hours/<entity_type>', methods=['GET'])
@require_rc_token
def get_hours_list(entity_type):
    """Streams the business hours for 'sites' or 'queues' with live progress."""
    if entity_type.lower() not in ['sites', 'queues']:
        return jsonify({"error": "Invalid entity type for hours."}), 400

    formatted_entity_type = "Site" if entity_type.lower() == 'sites' else "Queue"
    return _ndjson_stream(
        utils.fetch_operating_hours(formatted_entity_type),
        ("An internal error occurred while fetching hours.", "Error fetching hours: {}"),
    )

@bulk_hours_bp.route('/rules/<entity_type>', methods=['GET'])
@require_rc_token
def get_rules_list(entity_type):
    """Streams answering rules for 'sites' or 'queues' with live progress."""
    if entity_type.lower() not in ['sites', 'queues']:
        return jsonify({"error": "Invalid entity type for rules."}), 400

    formatted_entity_type = "Site" if entity_type.lower() == 'sites' else "Queue"
    category = request.args.get('category', 'all').lower() # 'default', 'custom', or 'all'
    return _ndjson_stream(
        utils.fetch_rules(formatted_entity_type, category),
        ("An internal error occurred while fetching rules.", "Error fetching rules: {}"),
    )

@bulk_hours_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Bulk Open Hours')
def upload_data():
    """
    Receives either Hours or Rules data and streams per-record progress back.
    The frontend specifies the data type in the payload.
    """
    data = request.get_json()

    if not data or 'records' not in data or 'dataType' not in data:
        return jsonify({"error": "Request body must include 'dataType' and 'records'."}), 400

    records = data['records']
    data_type = data['dataType']
    task_id = data.get('task_id')

    if data_type == 'Hours':
        source = utils.update_hours_from_records(records, task_id=task_id)
    elif data_type == 'Rules':
        source = utils.update_rules_from_records(records, task_id=task_id)
    else:
        return jsonify({"error": f"Invalid dataType '{data_type}' specified."}), 400

    def chunks():
        try:
            yield from source
        finally:
            # Always clear the stop flag when the run ends (success, error, cancel).
            task_control.clear(task_id)

    return _ndjson_stream(
        chunks(),
        ("An internal error occurred during the update process.", "Error during upload process: {}"),
    )
