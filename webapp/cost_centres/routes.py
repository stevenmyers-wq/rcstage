from datetime import datetime
from flask import Blueprint, jsonify, request, session, send_file
from webapp.auth_utils import require_rc_token, get_rc_access_token
from webapp.usage_tracking import track_usage
from . import utils

cost_centres_bp = Blueprint('cost_centres_bp', __name__, url_prefix='/api/cost_centres')

@cost_centres_bp.route('/data', methods=['GET'])
@require_rc_token
@track_usage('Cost Centres - Fetch Data')
def get_data():
    token = get_rc_access_token()
    try:
        data = utils.get_cost_centres_data(token)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@cost_centres_bp.route('/export', methods=['POST', 'GET'])
@require_rc_token
@track_usage('Cost Centres - Export')
def export_data():
    """Export the Cost Centre objects and per-cost-centre License Inventory as
    a two-sheet .xlsx workbook.

    The client POSTs the current (possibly filtered) view so the download
    mirrors what's on screen. With no body (e.g. a GET), the full account
    dataset is exported."""
    token = get_rc_access_token()
    try:
        payload = request.get_json(silent=True) or {}
        if payload.get('assets') is not None:
            data = {
                'assets': payload.get('assets') or [],
                'license_inventory': payload.get('license_inventory') or [],
            }
        else:
            data = utils.get_cost_centres_data(token)
        output = utils.build_cost_centres_workbook(data)
        return send_file(
            output,
            download_name=f"Cost_Centres_{datetime.now().strftime('%Y%m%d')}.xlsx",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@cost_centres_bp.route('/transfer', methods=['POST'])
@require_rc_token
@track_usage('Cost Centres - Transfer')
def transfer_cost_centres():
    token = get_rc_access_token()
    req_data = request.get_json()
    
    target_cc_id = req_data.get('target_cost_centre_id')
    assets = req_data.get('assets', [])

    if not target_cc_id or not assets:
        return jsonify({'error': 'Missing target cost centre or assets list.'}), 400

    results = {'success': 0, 'errors': []}
    
    for asset in assets:
        try:
            utils.update_asset_cost_centre(token, asset, target_cc_id)
            results['success'] += 1
        except Exception as e:
            results['errors'].append({
                'id': asset.get('id'), 
                'name': asset.get('name'), 
                'error': str(e)
            })

    return jsonify({'success': True, 'results': results})
