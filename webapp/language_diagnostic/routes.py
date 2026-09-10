from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from . import utils

language_diagnostic_bp = Blueprint(
    'language_diagnostic_bp', __name__, url_prefix='/api/language_diagnostic'
)


@language_diagnostic_bp.route('/dictionary', methods=['GET'])
@require_rc_token
@track_usage('Language Diagnostic - Dictionary')
def dictionary():
    """Diagnostic (read-only): dump the RingCentral language dictionary and flag
    whether English (Australia) is offered, and whether it's usable as an
    interface language (ui) and/or a greeting language (greeting)."""
    token = get_rc_access_token()
    try:
        return jsonify({'success': True, 'data': utils.load_language_dictionary(token)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@language_diagnostic_bp.route('/extension', methods=['GET'])
@require_rc_token
@track_usage('Language Diagnostic - Read Extension')
def extension():
    """Diagnostic (read-only): report an extension's current language and
    greeting language. Pass ?ext=<extension number or id>."""
    token = get_rc_access_token()
    ext = request.args.get('ext', '').strip()
    if not ext:
        return jsonify({'error': "Missing 'ext' query parameter (extension number or id)."}), 400
    try:
        return jsonify({'success': True, 'data': utils.read_extension_language(token, ext)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@language_diagnostic_bp.route('/test', methods=['POST'])
@require_rc_token
@track_usage('Language Diagnostic - Test English AU')
def test():
    """Diagnostic: can an extension's language and/or greeting language actually
    be set to English (AU)?

    Body (JSON):
      - ext     (required): extension number or id.
      - fields  (optional): list of 'language' / 'greetingLanguage'. Default both.
      - apply   (optional): false (default) = dry run, no writes. true = perform
                            a live PUT, verify via read-back, then restore.
      - keep    (optional): with apply=true, keep the en-AU value instead of
                            restoring the original (default false).
    """
    token = get_rc_access_token()
    body = request.get_json(silent=True) or {}
    ext = str(body.get('ext', '')).strip()
    if not ext:
        return jsonify({'error': "Missing 'ext' (extension number or id)."}), 400

    fields = body.get('fields')
    if fields is not None and not isinstance(fields, list):
        fields = [fields]
    apply = bool(body.get('apply', False))
    keep = bool(body.get('keep', False))

    try:
        data = utils.test_english_au(token, ext, fields=fields, apply=apply, keep=keep)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
