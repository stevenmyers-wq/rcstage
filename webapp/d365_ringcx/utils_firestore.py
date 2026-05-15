import logging
from datetime import datetime, timezone
import webapp.firestore_utils as _fs

# db may be None if Firestore credentials are unavailable (e.g. local dev
# without ADC). All functions below check for this and fail gracefully.
db = getattr(_fs, 'db', None)

logger = logging.getLogger(__name__)

# --- Collection names ---
ENVIRONMENTS_COLLECTION = 'D365_DEMO_ENVIRONMENTS'
USER_STATE_COLLECTION   = 'D365_DEMO_USER_STATE'
LEADS_COLLECTION        = 'D365_DEMO_LEADS'


# ---------------------------------------------------------------------------
# Environment management (write operations: admin only — enforced in routes)
# ---------------------------------------------------------------------------

def get_all_environments() -> list:
    """
    Returns all D365 demo environments as a list of dicts.
    Credentials (client_secret) are excluded — safe to send to frontend.
    """
    try:
        docs = db.collection(ENVIRONMENTS_COLLECTION).stream()
        envs = []
        for doc in docs:
            data = doc.to_dict()
            envs.append({
                'id':          doc.id,
                'name':        data.get('name'),
                'env_url':     data.get('env_url'),
                'owner_email': data.get('owner_email'),
                'created_at':  data.get('created_at'),
                'updated_at':  data.get('updated_at'),
            })
        return envs
    except Exception as e:
        logger.error(f'get_all_environments failed: {e}')
        return []


def get_environment(env_id: str) -> dict | None:
    """
    Returns the full environment document including credentials.
    Only used server-side (never sent raw to frontend).
    """
    try:
        doc = db.collection(ENVIRONMENTS_COLLECTION).document(env_id).get()
        if doc.exists:
            return {'id': doc.id, **doc.to_dict()}
        return None
    except Exception as e:
        logger.error(f'get_environment({env_id}) failed: {e}')
        return None


def save_environment(env_id: str, data: dict) -> None:
    """
    Creates or overwrites a D365 demo environment.
    data should include: name, tenant_id, client_id, client_secret,
                         env_url, owner_email
    """
    now = datetime.now(timezone.utc).isoformat()
    payload = {**data, 'updated_at': now}
    if 'created_at' not in payload:
        payload['created_at'] = now
    try:
        db.collection(ENVIRONMENTS_COLLECTION).document(env_id).set(payload)
    except Exception as e:
        logger.error(f'save_environment({env_id}) failed: {e}')
        raise


def delete_environment(env_id: str) -> None:
    """Deletes a D365 demo environment document."""
    try:
        db.collection(ENVIRONMENTS_COLLECTION).document(env_id).delete()
    except Exception as e:
        logger.error(f'delete_environment({env_id}) failed: {e}')
        raise


# ---------------------------------------------------------------------------
# SE personal state  (last environment used, RingCX campaign IDs)
# ---------------------------------------------------------------------------

def get_user_state(user_email: str) -> dict:
    """
    Returns the SE's saved state. Falls back to empty dict if not found.
    Keys: last_env_id, ringcx_account_id, human_campaign_id,
          ai_campaign_id, booking_campaign_id
    """
    try:
        doc = db.collection(USER_STATE_COLLECTION).document(user_email).get()
        if doc.exists:
            return doc.to_dict()
        return {}
    except Exception as e:
        logger.error(f'get_user_state({user_email}) failed: {e}')
        return {}


def save_user_state(user_email: str, state: dict) -> None:
    """
    Merges state into the SE's document (preserves keys not in state).
    """
    try:
        db.collection(USER_STATE_COLLECTION).document(user_email).set(
            state, merge=True
        )
    except Exception as e:
        logger.error(f'save_user_state({user_email}) failed: {e}')
        raise


# ---------------------------------------------------------------------------
# Demo lead tracking  (scoped per environment)
# ---------------------------------------------------------------------------

def get_demo_leads(env_id: str) -> list:
    """Returns all demo leads created in a given environment."""
    try:
        docs = (
            db.collection(LEADS_COLLECTION)
              .document(env_id)
              .collection('leads')
              .order_by('created_at')
              .stream()
        )
        return [{'leadid': doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        logger.error(f'get_demo_leads({env_id}) failed: {e}')
        return []


def get_demo_lead(env_id: str, leadid: str) -> dict | None:
    """Returns a single demo lead document."""
    try:
        doc = (
            db.collection(LEADS_COLLECTION)
              .document(env_id)
              .collection('leads')
              .document(leadid)
              .get()
        )
        if doc.exists:
            return {'leadid': doc.id, **doc.to_dict()}
        return None
    except Exception as e:
        logger.error(f'get_demo_lead({env_id}, {leadid}) failed: {e}')
        return None


def save_demo_lead(env_id: str, leadid: str, data: dict) -> None:
    """
    Records a newly created D365 demo lead.
    data should include: firstname, lastname, phone, created_by
    """
    payload = {
        **data,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'webscore': None,
        'campaign_assigned': None,
        'disposition': None,
    }
    try:
        (
            db.collection(LEADS_COLLECTION)
              .document(env_id)
              .collection('leads')
              .document(leadid)
              .set(payload)
        )
    except Exception as e:
        logger.error(f'save_demo_lead({env_id}, {leadid}) failed: {e}')
        raise


def update_demo_lead(env_id: str, leadid: str, updates: dict) -> None:
    """
    Patches specific fields on a demo lead.
    Used by: scoring (webscore), push (campaign_assigned), webhook (disposition).
    """
    try:
        (
            db.collection(LEADS_COLLECTION)
              .document(env_id)
              .collection('leads')
              .document(leadid)
              .update(updates)
        )
    except Exception as e:
        logger.error(f'update_demo_lead({env_id}, {leadid}) failed: {e}')
        raise


def delete_demo_lead(env_id: str, leadid: str) -> None:
    """Removes a single demo lead from Firestore tracking."""
    try:
        (
            db.collection(LEADS_COLLECTION)
              .document(env_id)
              .collection('leads')
              .document(leadid)
              .delete()
        )
    except Exception as e:
        logger.error(f'delete_demo_lead({env_id}, {leadid}) failed: {e}')
        raise


def find_lead_env(leadid: str) -> str | None:
    """
    Scans all environments to find which one a leadid belongs to.
    Used by the RingCX postback webhook to route dispositions correctly.
    Returns env_id or None.
    """
    try:
        env_docs = db.collection(LEADS_COLLECTION).list_documents()
        for env_ref in env_docs:
            lead_doc = env_ref.collection('leads').document(leadid).get()
            if lead_doc.exists:
                return env_ref.id
        return None
    except Exception as e:
        logger.error(f'find_lead_env({leadid}) failed: {e}')
        return None
