import json
from flask import Blueprint, jsonify, request, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

emergency_locations_bp = Blueprint(
    'emergency_locations_bp', __name__,
    url_prefix='/api/emergency_locations'
)
emergency_locations_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _ndjson_stream(chunks, on_error):
    """Wrap a generator of dict chunks as a streaming NDJSON Response.

    Each chunk is emitted as one JSON line, flushed immediately so the browser
    can advance its progress bar in real time. ``on_error`` builds the error
    chunk yielded if the generator raises mid-stream."""
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


@emergency_locations_bp.route('/locations', methods=['GET'])
@require_rc_token
def get_locations():
    """Streams account emergency response locations as flat spreadsheet rows.

    Optional query param ``siteId`` scopes the audit to a single site.
    """
    site_id = (request.args.get('siteId') or '').strip() or None
    return _ndjson_stream(
        utils.fetch_locations(site_id=site_id),
        ("An internal error occurred while fetching locations.", "Error fetching locations: {}"),
    )


@emergency_locations_bp.route('/raw', methods=['GET'])
@require_rc_token
def get_raw_example():
    """DEBUG: raw ERL JSON for inspecting the (esp. international) address format.

    Query params:
      locationId — return the single-resource GET body for that ERL.
      limit      — when locationId is omitted, how many locations to detail (default 3).
    """
    location_id = (request.args.get('locationId') or '').strip() or None
    try:
        limit = int(request.args.get('limit', 3))
    except (TypeError, ValueError):
        limit = 3
    try:
        return jsonify(utils.fetch_raw_examples(location_id=location_id, limit=limit))
    except Exception as e:
        print(f"Error fetching raw ERL example: {e}")
        return jsonify({"error": "An internal error occurred while fetching the raw example."}), 500


@emergency_locations_bp.route('/dictionary', methods=['GET'])
@require_rc_token
def get_dictionary():
    """DEBUG: look up / snapshot the coded values behind ERL fields.

    Query params:
      kind      — snapshot | formats | format
      countryId — optional filter for kind=formats
      formatId  — required when kind=format
      path      — raw passthrough of any /restapi/… path (overrides kind)
    """
    kind = (request.args.get('kind') or '').strip() or None
    country_id = (request.args.get('countryId') or '').strip() or None
    format_id = (request.args.get('formatId') or '').strip() or None
    path = (request.args.get('path') or '').strip() or None
    try:
        return jsonify(utils.explore_dictionary(
            kind=kind, country_id=country_id, path=path, format_id=format_id))
    except Exception as e:
        print(f"Error exploring ERL dictionary: {e}")
        return jsonify({"error": "An internal error occurred while exploring the dictionary."}), 500


@emergency_locations_bp.route('/reference', methods=['GET'])
@require_rc_token
def get_reference():
    """Baked-in reference for the UI (template picker + reference sheet).

    No params  → the list of countries you can create an ERL for (id, iso, name,
                 primaryFormatId), for the template country picker.
    ?countryId → that country's emergency formats (field specs) + state list, for
                 building the template columns and the Reference sheet.
    """
    from . import reference
    country_id = (request.args.get('countryId') or '').strip() or None
    try:
        if country_id:
            return jsonify(reference.ui_country_detail(country_id))
        return jsonify({"countries": reference.ui_countries()})
    except Exception as e:
        print(f"Error building ERL reference: {e}")
        return jsonify({"error": "An internal error occurred while building the reference."}), 500


@emergency_locations_bp.route('/template.xlsx', methods=['GET'])
@require_rc_token
def download_template():
    """Download the format-aware 'new location' template workbook (all countries).

    Real Excel dropdowns for Action/Visibility/Country/Site and dependent
    state/street-type lists; required fields noted on the Ref Countries sheet.
    """
    from datetime import date
    from . import template
    try:
        data = template.build_template_workbook()
    except Exception as e:
        print(f"Error building ERL template: {e}")
        return jsonify({"error": "An internal error occurred while building the template."}), 500
    resp = Response(
        data,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp.headers['Content-Disposition'] = (
        f'attachment; filename="RC_ERL_Template_{date.today().isoformat()}.xlsx"')
    return resp


@emergency_locations_bp.route('/test-write', methods=['POST'])
@require_rc_token
def test_write():
    """DEBUG: send one exact body to the ERL endpoint and return the round-trip.

    Body: {"body": {...RC location body...}, "locationId": "optional — PUT if set,
    POST (create) if omitted"}. Use to iterate on the exact structured-address
    body that makes buildingNumber / streetType persist.
    """
    data = request.get_json(silent=True) or {}
    body = data.get('body')
    location_id = (data.get('locationId') or '').strip() or None
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "Request must include a non-empty 'body' object."}), 400
    try:
        return jsonify(utils.test_write(body, location_id=location_id))
    except Exception as e:
        print(f"Error during ERL test write: {e}")
        return jsonify({"error": "An internal error occurred during the test write."}), 500


@emergency_locations_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Emergency Locations')
def upload_locations():
    """Creates/updates/deletes emergency locations from rows, streaming progress."""
    data = request.get_json()
    if not data or 'records' not in data:
        return jsonify({"error": "Request body must include 'records'."}), 400

    records = data['records']
    address_columns = data.get('addressColumns') or []
    task_id = data.get('task_id')

    def chunks():
        try:
            yield from utils.apply_locations_from_records(records, address_columns, task_id=task_id)
        finally:
            # Always clear the stop flag when the run ends (success, error, cancel).
            task_control.clear(task_id)

    return _ndjson_stream(
        chunks(),
        ("An internal error occurred during the update process.", "Error during upload process: {}"),
    )
