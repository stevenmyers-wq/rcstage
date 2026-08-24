import json
from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp import task_control
from . import utils

extension_deletion_bp = Blueprint('extension_deletion_bp', __name__, url_prefix='/api/extension_deletion')
extension_deletion_bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])


def _token():
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@extension_deletion_bp.route('/filters', methods=['GET'])
@require_rc_token
def get_filters():
    """Distinct Sites and deletion Categories present in the account."""
    try:
        sites, categories = utils.available_filters(_token())
        return jsonify({"sites": sites, "categories": categories})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@extension_deletion_bp.route('/find', methods=['POST'])
@require_rc_token
@track_usage('Extension Deletion - Find')
def find_extensions():
    """Return the live list of extensions matching the selected filters.

    This is the preview the operator reviews (and ticks) before anything is
    deleted -- nothing is changed here.
    """
    data = request.get_json() or {}
    try:
        rows = utils.build_rows(
            _token(),
            sites=data.get('sites', []),
            categories=data.get('categories', []),
        )
        return jsonify({"extensions": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@extension_deletion_bp.route('/delete', methods=['POST'])
@require_rc_token
@track_usage('Extension Deletion - Delete')
def delete_extensions():
    """Permanently delete the chosen extensions, streaming progress as NDJSON.

    Requires an explicit ``confirm == 'DELETE'`` gate on the server too, not
    just in the UI, so this endpoint can never delete without the deliberate
    confirmation phrase.
    """
    data = request.get_json() or {}
    confirm = str(data.get('confirm', '')).strip()
    if confirm != 'DELETE':
        return jsonify({"error": "Confirmation phrase missing. Type DELETE to confirm."}), 400

    items = data.get('extensions', [])
    if not items:
        return jsonify({"error": "No extensions selected for deletion."}), 400

    task_id = data.get('task_id')
    token = _token()

    def generate():
        try:
            for chunk in utils.delete_batch(items, token, task_id=task_id):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Connection'] = 'keep-alive'
    return resp
