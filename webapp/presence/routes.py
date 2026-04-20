from flask import Blueprint, request, jsonify
from webapp.presence.utils import RCPresenceManager
import logging

presence_bp = Blueprint('presence', __name__)

@presence_bp.route('/api/presence/<extension_id>/lines', methods=['GET'])
def get_lines(extension_id):
    try:
        manager = RCPresenceManager()
        result = manager.get_monitored_lines(extension_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@presence_bp.route('/api/presence/<extension_id>/lines', methods=['PUT'])
def update_lines(extension_id):
    try:
        data = request.json
        records = data.get('records', [])
        manager = RCPresenceManager()
        result = manager.update_monitored_lines(extension_id, records)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@presence_bp.route('/api/presence/<extension_id>/permissions', methods=['GET'])
def get_permissions(extension_id):
    try:
        manager = RCPresenceManager()
        result = manager.get_presence_permissions(extension_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
