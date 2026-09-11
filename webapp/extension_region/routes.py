from flask import Blueprint, jsonify, request, session

from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

extension_region_bp = Blueprint(
    'extension_region_bp', __name__, url_prefix='/api/extension_region'
)


def _token():
    """Prefer the SM impersonation (bridge) token, falling back to PKCE."""
    return session.get('sm_isolated_token') or session.get('rc_access_token')


@extension_region_bp.route('/list-extensions', methods=['GET'])
@require_rc_token
@track_usage('Extension Region - List')
def list_extensions():
    """Enumerate every account extension for the selection table. The UI filters
    by Type / Site."""
    token = _token()
    try:
        rows, summary = utils.build_extension_rows(token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if rows is None:
        return jsonify({"error": "Could not load account extensions. Token may be invalid or expired."}), 502

    return jsonify({"records": rows, "summary": summary})


@extension_region_bp.route('/languages', methods=['GET'])
@require_rc_token
@track_usage('Extension Region - Languages')
def languages():
    """Return the RingCentral language dictionary for the two dropdowns, with
    per-language ui / greeting support flags."""
    token = _token()
    try:
        return jsonify({"languages": utils.load_languages(token)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@extension_region_bp.route('/update', methods=['POST'])
@require_rc_token
@track_usage('Extension Region - Update')
def update():
    """Set a single extension's language settings.

    Body: {id, languageId, greetingLanguageId, formattingLocaleId}. At least one
    of the three must be provided. RingCentral requires all three language
    fields together, so utils.set_region reads the current values and fills any
    the operator left unset."""
    token = _token()
    data = request.get_json(silent=True) or {}
    ext_id = data.get('id')
    language_id = str(data.get('languageId', '') or '').strip()
    greeting_language_id = str(data.get('greetingLanguageId', '') or '').strip()
    formatting_locale_id = str(data.get('formattingLocaleId', '') or '').strip()

    if not ext_id:
        return jsonify({"error": "Missing id"}), 400
    if not (language_id or greeting_language_id or formatting_locale_id):
        return jsonify({"error": "Select a language to apply."}), 400

    try:
        ok, msg, request_info = utils.set_region(
            ext_id, language_id, greeting_language_id, formatting_locale_id, token)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not ok:
        # Include the attempted PUT (when one was built) so the UI can show
        # exactly what was sent even on a rejection.
        return jsonify({"error": msg, "request": request_info}), 502
    return jsonify({"success": True, "id": ext_id, "request": request_info})
