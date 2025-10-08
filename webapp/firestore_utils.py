# webapp/firestore_utils.py
from google.cloud import firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot

# Database initialization
try:
    db = firestore.Client()
except Exception as e:
    print(f"WARNING: Could not initialize Firestore client: {e}. Check credentials.")

# --- CONSTANTS ---
PASSCODE_COLLECTION_ID = 'RCAU_APITOOLS_WEBAPP_PASSCODE'
PASSCODE_DOCUMENT_ID = 'passcode'
PASSCODE_FIELD = 'app_passcode'
ADMIN_LIST_FIELD = 'admin_emails'

# --- Firestore Helpers ---
def get_config_from_firestore() -> dict | None:
    """Retrieves the shared passcode and admin list from Firestore."""
    try:
        doc_ref: DocumentSnapshot = db.collection(PASSCODE_COLLECTION_ID).document(PASSCODE_DOCUMENT_ID).get()
        if doc_ref.exists:
            data = doc_ref.to_dict()
            return {'passcode': data.get(PASSCODE_FIELD), 'admin_list': data.get(ADMIN_LIST_FIELD, [])}
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to access Firestore: {e}")
    return None