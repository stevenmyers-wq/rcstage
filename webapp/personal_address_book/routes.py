# webapp/personal_address_book/routes.py
from flask import Blueprint, jsonify, request
from webapp.auth_utils import require_rc_token
from . import utils
import traceback

personal_address_book_bp = Blueprint(
    'personal_address_book_bp', __name__,
    url_prefix='/api/pab'
)

# ... (keep existing routes: /users, /contacts, /contacts/delete) ...

@personal_address_book_bp.route('/users', methods=['GET'])
@require_rc_token
def get_user_list():
    """Fetches a list of all enabled users in the account."""
    try:
        users = utils.fetch_all_users()
        return jsonify(users)
    except Exception as e:
        print(f"Error fetching users: {e}")
        traceback.print_exc()
        return jsonify({"error": "An internal error occurred while fetching users."}), 500

@personal_address_book_bp.route('/contacts', methods=['POST'])
@require_rc_token
def get_contacts_for_users():
    """
    Fetches and aggregates personal address book contacts for a list of selected user IDs.
    """
    data = request.get_json()
    user_ids = data.get('userIds')

    if not user_ids or not isinstance(user_ids, list):
        return jsonify({"error": "Request body must include a 'userIds' array."}), 400

    try:
        aggregated_contacts = utils.fetch_and_aggregate_contacts(user_ids)
        return jsonify(aggregated_contacts)
    except Exception as e:
        print(f"Error fetching contacts: {e}")
        traceback.print_exc()
        return jsonify({"error": "An internal error occurred while fetching contacts."}), 500


@personal_address_book_bp.route('/contacts/upload', methods=['POST'])
@require_rc_token
def upload_contacts():
    """
    Handles adding, removing, or syncing contacts from a CSV for selected users.
    Returns a task ID for the frontend to monitor.
    """
    data = request.get_json()
    user_ids = data.get('userIds')
    contacts = data.get('contacts')
    action = data.get('action')

    if not all([user_ids, contacts, action]):
        return jsonify({"error": "Request body must include 'userIds', 'contacts', and 'action'."}), 400
    
    if action.lower() not in ['add', 'remove', 'update']:
        return jsonify({"error": "Invalid action specified. Must be 'add', 'remove', or 'update'."}), 400

    try:
        results = utils.process_contact_upload(user_ids, contacts, action.lower())
        return jsonify(results)
    except Exception as e:
        print(f"Error processing contact upload: {e}")
        traceback.print_exc()
        return jsonify({"error": "An internal error occurred during the upload process."}), 500


@personal_address_book_bp.route('/contacts/delete', methods=['POST'])
@require_rc_token
def delete_contacts():
    """
    Deletes specific contacts for specific users.
    """
    data = request.get_json()
    contacts_to_delete = data.get('contacts')

    if not contacts_to_delete or not isinstance(contacts_to_delete, list):
        return jsonify({"error": "Request body must include a 'contacts' array."}), 400
    
    try:
        results = utils.delete_selected_contacts(contacts_to_delete)
        return jsonify(results)
    except Exception as e:
        print(f"Error deleting contacts: {e}")
        traceback.print_exc()
        return jsonify({"error": "An internal error occurred while deleting contacts."}), 500

# --- NEW ROUTE FOR POLLING ---
@personal_address_book_bp.route('/contacts/task-status/<task_id>', methods=['GET'])
@require_rc_token
def get_task_status(task_id):
    """Polls the status of a bulk upload task."""
    if not task_id:
        return jsonify({"error": "Task ID is required."}), 400
    
    try:
        status = utils.check_bulk_upload_status(task_id)
        return jsonify(status)
    except Exception as e:
        print(f"Error fetching task status: {e}")
        return jsonify({"error": "An internal error occurred while fetching task status."}), 500
