import os
import uuid
import json
import random
import logging
from datetime import datetime, timedelta
from queue import Queue, Empty
from flask import Blueprint, jsonify, request, session, Response
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from webapp.audio_streaming.utils import exchange_rc_token_for_ringcx
from . import utils_firestore as fs
from . import utils_d365 as d365

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory live feed  (per env_id — reset on container restart, PoC only)
# ---------------------------------------------------------------------------
_live_feed_subscribers: dict = {}   # {env_id: [Queue, ...]}

d365_ringcx_bp = Blueprint(
    'd365_ringcx_bp', __name__,
    url_prefix='/api/d365_ringcx'
)

AI_CAMPAIGN_SCORE_THRESHOLD = 60  # leads scoring below this go to AI campaign

# ---------------------------------------------------------------------------
# Fake lead generation data  (eastern AU only — avoids WA dialing restrictions)
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    'James', 'Oliver', 'William', 'Jack', 'Noah', 'Thomas', 'Ethan', 'Lucas',
    'Mason', 'Liam', 'Emma', 'Olivia', 'Charlotte', 'Ava', 'Sophia', 'Mia',
    'Isla', 'Grace', 'Chloe', 'Sophie', 'Daniel', 'Ryan', 'Nathan', 'Jessica',
    'Zoe', 'Hannah', 'Lily', 'Ruby', 'Ella', 'Madison',
]

_LAST_NAMES = [
    'Smith', 'Jones', 'Williams', 'Brown', 'Taylor', 'Wilson', 'Johnson',
    'White', 'Martin', 'Anderson', 'Thompson', 'Davis', 'Robinson', 'Clark',
    'Lewis', 'Walker', 'Hall', 'Young', 'Allen', 'Harris', 'Mitchell', 'Kelly',
]

_EAST_SUBURBS = [
    # NSW
    ('Parramatta', 'NSW'), ('Bondi', 'NSW'), ('Chatswood', 'NSW'),
    ('Newtown', 'NSW'), ('Randwick', 'NSW'), ('Manly', 'NSW'),
    ('Strathfield', 'NSW'), ('Penrith', 'NSW'), ('Liverpool', 'NSW'),
    ('Hornsby', 'NSW'),
    # VIC
    ('Richmond', 'VIC'), ('Fitzroy', 'VIC'), ('Hawthorn', 'VIC'),
    ('St Kilda', 'VIC'), ('Doncaster', 'VIC'), ('Frankston', 'VIC'),
    ('Clayton', 'VIC'), ('Box Hill', 'VIC'), ('Ringwood', 'VIC'),
    ('Moonee Ponds', 'VIC'),
    # QLD
    ('Chermside', 'QLD'), ('Carindale', 'QLD'), ('Toowong', 'QLD'),
    ('Southport', 'QLD'), ('Broadbeach', 'QLD'), ('Indooroopilly', 'QLD'),
    ('Woolloongabba', 'QLD'), ('Sunnybank', 'QLD'),
]

# crd67_movetype picklist: integer value → display label
_MOVE_TYPES = {
    322750000: 'Residential Local',
    322750001: 'Residential Interstate',
    322750002: 'Commercial Local',
    322750003: 'Commercial Interstate',
}

# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------
# Base points by move type (job size signal)
_MOVETYPE_SCORES = {
    322750003: 40,  # Commercial Interstate — largest job
    322750002: 30,  # Commercial Local
    322750001: 25,  # Residential Interstate
    322750000: 15,  # Residential Local — smallest job
}

# Urgency points by days until move date
_URGENCY_BANDS = [
    (14,  40),   # < 14 days  → +40
    (30,  30),   # < 30 days  → +30
    (60,  20),   # < 60 days  → +20
    (90,  10),   # < 90 days  → +10
    (None, 5),   # 90+ days   → +5
]


def _calculate_raw_score(movetype, movedate_str: str) -> int:
    """
    Calculates a raw WebScore (0–80) from move type and move date.
    Raw scores are then adjusted into human (≥60) or AI (<60) ranges
    by score_leads() based on the SE's chosen split.
    """
    base = _MOVETYPE_SCORES.get(movetype, 20)
    urgency = 5
    if movedate_str:
        try:
            move_dt = datetime.fromisoformat(movedate_str.replace('Z', ''))
            days_until = (move_dt - datetime.now()).days
            for threshold, pts in _URGENCY_BANDS:
                if threshold is None or days_until < threshold:
                    urgency = pts
                    break
        except Exception:
            pass
    return base + urgency


def _generate_fake_lead(phone: str) -> dict:
    """Generates realistic fake AU lead data for demo purposes."""
    first = random.choice(_FIRST_NAMES)
    last = random.choice(_LAST_NAMES)
    origin = random.choice(_EAST_SUBURBS)
    dest = random.choice([s for s in _EAST_SUBURBS if s != origin])
    move_type_val = random.choice(list(_MOVE_TYPES.keys()))
    move_type_label = _MOVE_TYPES[move_type_val]
    move_date_iso = (datetime.now() + timedelta(days=random.randint(14, 120))).strftime('%Y-%m-%dT00:00:00Z')

    return {
        'firstname':                first,
        'lastname':                 last,
        'mobilephone':              phone,
        'subject':                  f'{move_type_label} – {origin[0]} to {dest[0]}',
        'description':              'Demo lead created for RingCX campaign testing.',
        'address1_city':            origin[0],
        'address1_stateorprovince': origin[1],
        'leadsourcecode':           10,   # Web
        'crd67_originsuburb':       origin[0],
        'crd67_destinationsuburb':  dest[0],
        'crd67_movedate':           move_date_iso,
        'crd67_movetype':           move_type_val,
        # crd67_webscore set during scoring phase, not on create
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AU_STATE_TIMEZONE = {
    'NSW': 'Australia/Sydney',
    'ACT': 'Australia/Sydney',
    'VIC': 'Australia/Melbourne',
    'QLD': 'Australia/Brisbane',
    'SA':  'Australia/Adelaide',
    'WA':  'Australia/Perth',
    'TAS': 'Australia/Hobart',
    'NT':  'Australia/Darwin',
}


def _to_local_au_phone(phone: str) -> str:
    """
    Converts an E.164 AU number to local format for RingCX lead loader.
    +61412345678 → 0412345678
    61412345678  → 0412345678
    Already local (0412345678) → unchanged
    """
    phone = (phone or '').strip()
    if phone.startswith('+61'):
        return '0' + phone[3:]
    if phone.startswith('61') and len(phone) >= 10:
        return '0' + phone[2:]
    return phone


_AU_STATE_RINGCX_TZ = {
    'NSW': 'EAST', 'ACT': 'EAST', 'VIC': 'EAST',
    'QLD': 'EAST', 'TAS': 'EAST',
    'SA':  'CENTRAL', 'NT': 'CENTRAL',
    'WA':  'WEST',
}


def _state_to_ringcx_tz(state: str) -> str:
    """Maps AU state abbreviation to RingCX timezone code (EAST / CENTRAL / WEST)."""
    return _AU_STATE_RINGCX_TZ.get((state or '').strip().upper(), 'EAST')


def _get_d365_token_for_env(env_id: str):
    """
    Fetches the environment config from Firestore and returns a D365 token.
    Raises Exception if environment not found or auth fails.
    """
    env = fs.get_environment(env_id)
    if not env:
        raise Exception(f'Environment {env_id} not found')
    return (
        d365.get_d365_token(
            env['tenant_id'],
            env['client_id'],
            env['client_secret'],
            env['env_url'],
        ),
        env['env_url']
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/status', methods=['GET'])
@require_rc_token
def status():
    """Returns config status for the current user — used to drive the UI state."""
    user_email = session.get('user_email', '')
    user_state = fs.get_user_state(user_email)
    environments = fs.get_all_environments()   # safe list, no secrets

    ringcx_connected = bool(session.get('ringcx_access_token'))

    return jsonify({
        'status':           'ok',
        'user_email':       user_email,
        'is_admin':         session.get('is_admin', False),
        'ringcx_connected': ringcx_connected,
        'ringcx_account_id':     user_state.get('ringcx_account_id'),
        'human_campaign_id':     user_state.get('human_campaign_id'),
        'ai_campaign_id':        user_state.get('ai_campaign_id'),
        'booking_campaign_id':   user_state.get('booking_campaign_id'),
        'last_env_id':           user_state.get('last_env_id'),
        'environments':          environments,
    })


# ---------------------------------------------------------------------------
# D365 Environment management
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/test-connection', methods=['GET'])
@require_rc_token
def test_connection():  # noqa: E302
    """
    Verifies D365 credentials for an environment by obtaining a token
    and fetching one record.
    Query param: env_id (required)
    """
    env_id = request.args.get('env_id')
    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400

    try:
        token, env_url = _get_d365_token_for_env(env_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    try:
        info = d365.whoami(token, env_url)
        org_id = info.get('OrganizationId', 'unknown')
        return jsonify({'status': 'ok', 'message': f'Connected. Org ID: {org_id}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@d365_ringcx_bp.route('/environments', methods=['GET'])
@require_rc_token
def list_environments():
    """Returns all D365 demo environments (no credentials — safe for frontend)."""
    return jsonify({'environments': fs.get_all_environments()})


@d365_ringcx_bp.route('/environments', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Save Environment')
def save_environment():
    """
    Creates or updates a D365 demo environment. Admin only.
    Body: { name, tenant_id, client_id, client_secret, env_url }
    Optionally include env_id to update an existing environment.
    """
    if not session.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    required = ['name', 'tenant_id', 'client_id', 'client_secret', 'env_url']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

    env_id = data.get('env_id') or str(uuid.uuid4())[:8]

    payload = {
        'name':         data['name'],
        'tenant_id':    data['tenant_id'],
        'client_id':    data['client_id'],
        'client_secret': data['client_secret'],
        'env_url':      data['env_url'].rstrip('/'),
        'owner_email':  session.get('user_email', ''),
    }

    try:
        fs.save_environment(env_id, payload)
        return jsonify({'status': 'saved', 'env_id': env_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@d365_ringcx_bp.route('/environments/<env_id>', methods=['DELETE'])
@require_rc_token
def delete_environment(env_id):
    """Deletes a D365 demo environment. Admin only."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        fs.delete_environment(env_id)
        return jsonify({'status': 'deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# SE personal state  (RingCX IDs + last env)
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/user-state', methods=['GET'])
@require_rc_token
def get_user_state():
    user_email = session.get('user_email', '')
    return jsonify(fs.get_user_state(user_email))


@d365_ringcx_bp.route('/user-state', methods=['POST'])
@require_rc_token
def save_user_state():
    """
    Saves the SE's RingCX campaign IDs and last selected environment.
    Body: { ringcx_account_id, human_campaign_id, ai_campaign_id,
            booking_campaign_id, last_env_id }
    """
    user_email = session.get('user_email', '')
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    allowed = [
        'ringcx_account_id', 'human_campaign_id',
        'ai_campaign_id', 'booking_campaign_id', 'last_env_id'
    ]
    state = {k: data[k] for k in allowed if k in data}

    try:
        fs.save_user_state(user_email, state)
        return jsonify({'status': 'saved'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# RingCX auth  (token exchange — keeps SE on this tab)
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/ringcx-connect', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - RingCX Connect')
def ringcx_connect():
    """
    Exchanges the SE's RingEX PKCE token for a RingCX session token.
    Identical to audio_streaming but lives here so the SE never has to
    leave this tab to authenticate.
    """
    rc_token = session.get('rc_access_token')
    try:
        result = exchange_rc_token_for_ringcx(rc_token)
        session['ringcx_access_token']  = result['accessToken']
        session['ringcx_refresh_token'] = result['refreshToken']
        session['ringcx_account_id']    = result['accountId']
        return jsonify({
            'status':     'connected',
            'accountId':  result['accountId'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@d365_ringcx_bp.route('/ringcx-disconnect', methods=['POST'])
def ringcx_disconnect():
    session.pop('ringcx_access_token', None)
    session.pop('ringcx_refresh_token', None)
    session.pop('ringcx_account_id', None)
    return jsonify({'status': 'disconnected'})


# ---------------------------------------------------------------------------
# RingCX campaigns
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/ringcx-campaigns', methods=['GET'])
@require_rc_token
def get_ringcx_campaigns():
    """Returns RingCX campaigns for the connected account. Used to populate campaign dropdowns."""
    ringcx_token = session.get('ringcx_access_token')
    if not ringcx_token:
        return jsonify({'error': 'Not connected to RingCX. Please connect first.'}), 401
    account_id = session.get('ringcx_account_id')
    if not account_id:
        return jsonify({'error': 'No RingCX account ID in session. Please reconnect.'}), 401
    try:
        campaigns = d365.get_ringcx_campaigns(ringcx_token, account_id)
        return jsonify({'campaigns': campaigns})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Leads — fetch from D365
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/leads', methods=['GET'])
@require_rc_token
@track_usage('D365 Demo - Get Leads')
def get_leads():
    """
    Fetches leads from the selected D365 environment.
    Query param: env_id (required)
    Cross-references with Firestore to mark which leads are demo leads
    and to surface our tracked state (webscore, campaign, disposition).
    """
    env_id = request.args.get('env_id')
    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400

    try:
        token, env_url = _get_d365_token_for_env(env_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    try:
        leads = d365.get_leads(token, env_url, filter_expr='statecode eq 0', limit=10)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Build a lookup of our tracked demo leads for this environment
    demo_leads = {dl['leadid']: dl for dl in fs.get_demo_leads(env_id)}

    # Merge D365 data with our Firestore tracking state
    for lead in leads:
        lid = lead.get('leadid')
        tracked = demo_leads.get(lid)
        lead['_is_demo']          = tracked is not None
        lead['_created_by']       = tracked.get('created_by') if tracked else None
        lead['_webscore_tracked'] = tracked.get('webscore') if tracked else None
        lead['_campaign']         = tracked.get('campaign_assigned') if tracked else None
        lead['_disposition']      = tracked.get('disposition') if tracked else None

    # Save last used env for this SE
    fs.save_user_state(session.get('user_email', ''), {'last_env_id': env_id})

    return jsonify({'leads': leads, 'total': len(leads)})


# ---------------------------------------------------------------------------
# Demo lead creation & cleanup  (approval gate enforced in frontend)
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/leads/create-demo', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Create Demo Leads')
def create_demo_leads():
    """
    Creates fake demo leads in D365 with generated names and real phone numbers.
    Body: { env_id, phone_numbers: ['+61412345678', ...] }
    Returns created count and any per-number errors.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    env_id = data.get('env_id')
    phone_numbers = data.get('phone_numbers', [])

    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400
    if not phone_numbers:
        return jsonify({'error': 'At least one phone number is required'}), 400

    try:
        token, env_url = _get_d365_token_for_env(env_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    user_email = session.get('user_email', '')
    created = []
    errors = []

    for phone in phone_numbers:
        phone = phone.strip()
        if not phone:
            continue
        lead_data = _generate_fake_lead(phone)
        try:
            result = d365.create_lead(token, env_url, lead_data)
            leadid = result.get('leadid')
            if leadid:
                fs.save_demo_lead(env_id, leadid, {
                    'firstname':   lead_data['firstname'],
                    'lastname':    lead_data['lastname'],
                    'phone':       phone,
                    'state':       lead_data.get('address1_stateorprovince', ''),
                    'origin':      lead_data.get('crd67_originsuburb', ''),
                    'destination': lead_data.get('crd67_destinationsuburb', ''),
                    'movetype':    _MOVE_TYPES.get(lead_data.get('crd67_movetype'), ''),
                    'created_by':  user_email,
                })
            created.append({
                'leadid':    leadid,
                'firstname': lead_data['firstname'],
                'lastname':  lead_data['lastname'],
                'phone':     phone,
            })
        except Exception as e:
            logger.error(f'create_demo_leads: failed for {phone}: {e}')
            errors.append({'phone': phone, 'error': str(e)})

    return jsonify({'created': len(created), 'leads': created, 'errors': errors})


@d365_ringcx_bp.route('/leads/delete-demo', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Delete Demo Leads')
def delete_demo_leads():
    """
    Deletes all demo leads for a given environment from both D365 and Firestore.
    Gracefully handles leads already deleted from D365 (e.g. via web interface).
    Body: { env_id }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    env_id = data.get('env_id')
    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400

    try:
        token, env_url = _get_d365_token_for_env(env_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    demo_leads = fs.get_demo_leads(env_id)
    if not demo_leads:
        return jsonify({'deleted': 0, 'message': 'No demo leads found for this environment.'})

    deleted = 0
    errors = []

    ringcx_token      = session.get('ringcx_access_token')
    ringcx_account_id = session.get('ringcx_account_id')

    for lead in demo_leads:
        leadid = lead.get('leadid')
        if not leadid:
            continue

        # Remove from RingCX campaign if we have the internal lead ID stored
        ringcx_lead_id  = lead.get('ringcx_lead_id')
        ringcx_camp_id  = lead.get('ringcx_campaign_id')
        if ringcx_lead_id and ringcx_camp_id and ringcx_token and ringcx_account_id:
            try:
                d365.delete_lead_from_ringcx(ringcx_token, ringcx_account_id, ringcx_camp_id, ringcx_lead_id)
            except Exception as e:
                if '404' in str(e):
                    logger.info(f'delete_demo_leads: RingCX lead {ringcx_lead_id} already gone')
                else:
                    logger.warning(f'delete_demo_leads: RingCX delete failed for {ringcx_lead_id}: {e}')
                    # Non-blocking — continue with D365 + Firestore cleanup

        # Delete from D365 — ignore 404 (already deleted via web interface)
        try:
            d365.delete_lead(token, env_url, leadid)
        except Exception as e:
            if '404' in str(e) or 'Does Not Exist' in str(e):
                logger.info(f'delete_demo_leads: lead {leadid} already gone from D365, removing from Firestore only')
            else:
                logger.error(f'delete_demo_leads: D365 delete failed for {leadid}: {e}')
                errors.append({'leadid': leadid, 'error': str(e)})
                continue  # leave Firestore record intact if D365 delete failed unexpectedly

        # Remove from Firestore tracking
        try:
            fs.delete_demo_lead(env_id, leadid)
            deleted += 1
        except Exception as e:
            logger.error(f'delete_demo_leads: Firestore delete failed for {leadid}: {e}')
            errors.append({'leadid': leadid, 'error': f'Firestore: {e}'})

    return jsonify({'deleted': deleted, 'total': len(demo_leads), 'errors': errors})


# ---------------------------------------------------------------------------
# Score & route
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/leads/score', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Score Leads')
def score_leads():
    """
    Scores all demo leads for an environment and writes crd67_webscore back to D365.

    Body: { env_id, human_count }
      human_count: how many leads should score ≥ 60 (go to human agent campaign).
      The remainder are scored < 60 (go to AI agent campaign).

    Scoring process:
      1. Fetch each demo lead from D365 to get move type + move date.
      2. Calculate a raw score from those two signals (see _calculate_raw_score).
      3. Rank leads by raw score (best leads naturally float to the top).
      4. Top human_count leads → final score clamped to 60–95.
         Remaining leads → final score clamped to 15–55.
      5. Write crd67_webscore to D365 and update Firestore tracking.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    env_id = data.get('env_id')
    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400

    human_count = int(data.get('human_count', 0))

    try:
        token, env_url = _get_d365_token_for_env(env_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    demo_leads = fs.get_demo_leads(env_id)
    if not demo_leads:
        return jsonify({'error': 'No demo leads found. Create some first.'}), 400

    total = len(demo_leads)
    human_count = max(0, min(human_count, total))

    # Step 1 & 2: fetch from D365 and calculate raw score
    scored = []
    for fl in demo_leads:
        leadid = fl['leadid']
        try:
            lead = d365.get_lead(token, env_url, leadid)
            movetype  = lead.get('crd67_movetype')
            movedate  = lead.get('crd67_movedate', '')
            raw_score = _calculate_raw_score(movetype, movedate)
            scored.append({
                'leadid':    leadid,
                'firstname': fl.get('firstname', ''),
                'lastname':  fl.get('lastname', ''),
                'movetype':  movetype,
                'movedate':  movedate,
                'raw_score': raw_score,
            })
        except Exception as e:
            logger.error(f'score_leads: skipping lead {leadid}: {e}')

    # Step 3: rank by raw score descending (naturally best leads first)
    scored.sort(key=lambda x: x['raw_score'], reverse=True)

    # Steps 4 & 5: assign final scores and write back
    results = []
    for i, lead in enumerate(scored):
        is_human = i < human_count

        if is_human:
            # Clamp into human range 60–95, preserving relative ordering
            raw = lead['raw_score']
            final = max(60, min(95, raw + random.randint(0, 10)))
        else:
            # Clamp into AI range 15–55
            raw = lead['raw_score']
            final = max(15, min(55, raw - random.randint(5, 15)))

        leadid = lead['leadid']

        try:
            d365.update_lead(token, env_url, leadid, {'crd67_webscore': final})
        except Exception as e:
            logger.error(f'score_leads: D365 update failed for {leadid}: {e}')

        try:
            fs.update_demo_lead(env_id, leadid, {'webscore': final})
        except Exception as e:
            logger.error(f'score_leads: Firestore update failed for {leadid}: {e}')

        results.append({
            'leadid':    leadid,
            'firstname': lead['firstname'],
            'lastname':  lead['lastname'],
            'movetype':  lead['movetype'],
            'movedate':  lead['movedate'],
            'score':     final,
            'bucket':    'human' if is_human else 'ai',
        })

    return jsonify({'scored': len(results), 'human': human_count, 'ai': total - human_count, 'results': results})


@d365_ringcx_bp.route('/leads/push', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Push to RingCX')
def push_leads():
    """
    Pushes scored demo leads into the appropriate RingCX campaigns via Lead Loader API.
    Leads with WebScore >= 60 go to the human campaign; < 60 go to the AI campaign.

    Body: { env_id }

    Each lead is pushed with externId = D365 leadId so the postback webhook can match
    the RingCX disposition back to the correct D365 record.

    The returned RingCX internal leadId is stored in Firestore so uncalled leads can be
    removed from campaigns during demo cleanup.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    env_id = data.get('env_id')
    if not env_id:
        return jsonify({'error': 'env_id is required'}), 400

    ringcx_token = session.get('ringcx_access_token')
    ringcx_account_id = session.get('ringcx_account_id')
    if not ringcx_token:
        return jsonify({'error': 'Not connected to RingCX. Please reconnect.'}), 401

    user_email = session.get('user_email', '')
    user_state = fs.get_user_state(user_email)
    human_campaign_id = user_state.get('human_campaign_id')
    ai_campaign_id    = user_state.get('ai_campaign_id')

    if not human_campaign_id or not ai_campaign_id:
        return jsonify({'error': 'Human and AI campaign IDs not saved. Configure them in the Campaign IDs card first.'}), 400

    demo_leads = fs.get_demo_leads(env_id)
    if not demo_leads:
        return jsonify({'error': 'No demo leads found for this environment.'}), 400

    unscored = [l for l in demo_leads if l.get('webscore') is None]
    if unscored:
        return jsonify({'error': f'{len(unscored)} leads have no score. Run "Score Leads" first.'}), 400

    # Group leads by campaign so we make one bulk upload per campaign (one email per group)
    human_leads = []
    ai_leads    = []
    for fl in demo_leads:
        score    = fl.get('webscore', 0)
        is_human = score >= AI_CAMPAIGN_SCORE_THRESHOLD
        entry = {
            'leadid':       fl['leadid'],
            'firstName':    fl.get('firstname', ''),
            'lastName':     fl.get('lastname', ''),
            'phone1':       fl.get('phone', ''),
            'externId':     fl['leadid'],
            'leadTimezone': _state_to_ringcx_tz(fl.get('state', '')),
            'webscore':     str(fl.get('webscore', '')),
            'movetype':     fl.get('movetype', ''),
            'origin':       fl.get('origin', ''),
            'destination':  fl.get('destination', ''),
            'is_human':     is_human,
        }
        if is_human:
            human_leads.append(entry)
        else:
            ai_leads.append(entry)

    pushed_human = 0
    pushed_ai    = 0
    errors       = []

    def _push_group(leads_group, campaign_id, bucket_label):
        nonlocal pushed_human, pushed_ai
        if not leads_group:
            return
        try:
            result = d365.push_leads_to_ringcx(
                ringcx_token, ringcx_account_id, campaign_id,
                leads_group,
            )
            for fl in leads_group:
                leadid = fl['leadid']
                fs.update_demo_lead(env_id, leadid, {
                    # No per-lead RingCX ID available from batch upload —
                    # transactionId is an upload reference, not a deletable lead ID.
                    # RingCX cleanup must be done manually via the campaign UI.
                    'ringcx_campaign_id': campaign_id,
                    'campaign_assigned':  bucket_label,
                })
            if bucket_label == 'human':
                pushed_human += len(leads_group)
            else:
                pushed_ai += len(leads_group)
        except Exception as e:
            logger.error(f'push_leads: batch failed for {bucket_label} campaign {campaign_id}: {e}')
            for fl in leads_group:
                errors.append({
                    'leadid': fl['leadid'],
                    'name':   f"{fl.get('firstName','')} {fl.get('lastName','')}".strip(),
                    'error':  str(e),
                })

    _push_group(human_leads, human_campaign_id, 'human')
    _push_group(ai_leads,    ai_campaign_id,    'ai')

    return jsonify({
        'human':  pushed_human,
        'ai':     pushed_ai,
        'errors': errors,
    })


@d365_ringcx_bp.route('/ringcx-campaign-leads', methods=['GET'])
@require_rc_token
@track_usage('D365 Demo - Check RingCX Campaign Leads')
def check_ringcx_campaign_leads():
    """
    Queries both configured campaigns in RingCX and returns a lead count + sample.
    Used for debugging/verification after a push.

    Query params: env_id (used only to confirm which environment we're in)
    """
    ringcx_token      = session.get('ringcx_access_token')
    ringcx_account_id = session.get('ringcx_account_id')
    if not ringcx_token:
        return jsonify({'error': 'Not connected to RingCX. Please reconnect.'}), 401

    user_email = session.get('user_email', '')
    user_state = fs.get_user_state(user_email)
    human_campaign_id = user_state.get('human_campaign_id')
    ai_campaign_id    = user_state.get('ai_campaign_id')

    if not human_campaign_id or not ai_campaign_id:
        return jsonify({'error': 'Campaign IDs not configured.'}), 400

    import requests as req
    headers = {'Authorization': f'Bearer {ringcx_token}'}
    results = {}

    # POST /campaignLeads/leadSearch — search across one campaign at a time
    search_url = f'{d365.ENGAGE_BASE_URL}/voice/api/v1/admin/accounts/{ringcx_account_id}/campaignLeads/leadSearch'

    for label, campaign_id in [('human', human_campaign_id), ('ai', ai_campaign_id)]:
        try:
            resp = req.post(
                search_url,
                headers={**headers, 'Content-Type': 'application/json'},
                params={'page': 1, 'maxRows': 50},
                json={'campaignIds': [int(campaign_id)]},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.warning(f'RingCX leadSearch campaign {campaign_id} ({label}) response: {str(data)[:500]}')
            # Response shape: int (count only), list, or {totalCount: N, searchResults: [...]}
            if isinstance(data, int):
                # API returned just a count — no leads detail available this way
                leads = []
                total = data
            elif isinstance(data, list):
                leads = data
                total = len(data)
            else:
                leads = data.get('searchResults', data.get('leads', data.get('uploadedLeads', [])))
                total = data.get('totalCount', data.get('total', len(leads)))
            results[label] = {
                'campaign_id': campaign_id,
                'total':       total,
                'leads':       [
                    {
                        'leadId':    l.get('leadId') or l.get('id'),
                        'firstName': l.get('firstName', ''),
                        'lastName':  l.get('lastName', ''),
                        'phone1':    l.get('phone1', ''),
                        'externId':  l.get('externId', ''),
                        'leadState': l.get('leadState', l.get('state', '')),
                    }
                    for l in (leads or [])[:10]
                ],
            }
        except Exception as e:
            logger.error(f'check_ringcx_campaign_leads({label} campaign {campaign_id}): {e}')
            results[label] = {'campaign_id': campaign_id, 'error': str(e)}

    return jsonify(results)


# ---------------------------------------------------------------------------
# RingCX postback webhook
# ---------------------------------------------------------------------------

def _broadcast_live_event(env_id: str, event: dict) -> None:
    """Push a disposition event to all SSE subscribers for this environment."""
    queues = _live_feed_subscribers.get(env_id, [])
    dead = []
    for q in queues:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        queues.remove(q)


@d365_ringcx_bp.route('/postback', methods=['POST'])
def ringcx_postback():
    """
    Receives Agent Termination postbacks from RingCX web services.
    Auth: ?secret=<RCAU_WEBHOOK_SECRET> query parameter.

    RingCX web service configuration:
        URL:  POST https://<host>/api/d365_ringcx/postback?secret=<secret>
        Body: FORM format, fields:
            externId      → #extern_id#
            disposition   → #agent_disposition#
            agentId       → #agent_id#
            recordingUrl  → #recording_url#
            notes         → #agent_notes#  (optional)
            uii           → #uii#          (optional, call session ID)

    Uses externId (= D365 leadId) to look up env and lead in Firestore,
    then writes disposition back to D365 and broadcasts to live feed SSE.
    """
    # --- Auth ---
    WEBHOOK_SECRET  = os.environ.get('D365_POSTBACK_SECRET', 'rcau-demo-2026')
    provided_secret = request.args.get('secret', '')
    if provided_secret != WEBHOOK_SECRET:
        logger.warning(f'ringcx_postback: invalid secret from {request.remote_addr}')
        return jsonify({'error': 'forbidden'}), 403

    # --- Extract fields (accept multiple naming conventions) ---
    form = request.form
    data = request.get_json(silent=True) or {}

    def get_field(*names):
        for n in names:
            v = form.get(n) or data.get(n)
            if v:
                return str(v).strip()
        return ''

    extern_id    = get_field('externId', 'extern_id', 'externalId', 'external_id')
    disposition  = get_field('disposition', 'agent_disposition', 'agentDisposition', 'Disposition')
    agent_id     = get_field('agentId', 'agent_id', 'AgentId')
    recording_url= get_field('recordingUrl', 'recording_url', 'RecordingUrl')
    notes        = get_field('notes', 'agentNotes', 'agent_notes')
    uii          = get_field('uii', 'UII')

    logger.info(f'ringcx_postback: externId={extern_id} disposition={disposition} agentId={agent_id}')

    if not extern_id:
        logger.warning('ringcx_postback: missing externId — ignoring')
        return jsonify({'status': 'ignored', 'reason': 'no externId'}), 200

    # --- Look up env from Firestore ---
    env_id = fs.find_lead_env(extern_id)
    if not env_id:
        logger.warning(f'ringcx_postback: externId {extern_id} not found in any env')
        return jsonify({'status': 'ignored', 'reason': 'lead not found'}), 200

    # --- Fetch current lead data from Firestore ---
    lead = fs.get_demo_lead(env_id, extern_id) or {}
    campaign_assigned = lead.get('campaign_assigned', 'unknown')
    firstname = lead.get('firstname', '')
    lastname  = lead.get('lastname', '')
    phone     = lead.get('phone', '')
    disposed_at = datetime.utcnow().isoformat() + 'Z'

    # --- Update Firestore ---
    fs.update_demo_lead(env_id, extern_id, {
        'disposition':   disposition,
        'agent_id':      agent_id,
        'recording_url': recording_url,
        'notes':         notes,
        'uii':           uii,
        'disposed_at':   disposed_at,
    })

    # --- Write disposition back to D365 ---
    try:
        env_doc = fs.get_environment(env_id)
        if env_doc:
            d365_token = d365.get_d365_token(
                env_doc['tenant_id'], env_doc['client_id'],
                env_doc['client_secret'], env_doc['env_url']
            )
            d365.update_lead(d365_token, env_doc['env_url'], extern_id, {
                'crd67_rcdisposition': disposition,
            })
            # For AI campaign calls, also create a Phone Call activity
            if campaign_assigned == 'ai' and recording_url:
                d365.create_phone_call_activity(d365_token, env_doc['env_url'], extern_id, {
                    'disposition':    disposition,
                    'notes':          notes,
                    'recording_url':  recording_url,
                    'queue':          'AI Agent',
                })
    except Exception as e:
        logger.error(f'ringcx_postback: D365 write failed for {extern_id}: {e}')
        # Don't fail the webhook — RingCX retries on non-200

    # --- Broadcast to live feed SSE ---
    event = {
        'leadid':           extern_id,
        'firstname':        firstname,
        'lastname':         lastname,
        'phone':            phone,
        'campaign':         campaign_assigned,
        'disposition':      disposition,
        'recording_url':    recording_url,
        'notes':            notes,
        'disposed_at':      disposed_at,
        'booking_pushed':   False,
    }
    _broadcast_live_event(env_id, event)

    return jsonify({'status': 'ok'}), 200


@d365_ringcx_bp.route('/live-feed', methods=['GET'])
@require_rc_token
def live_feed_stream():
    """
    SSE stream of real-time disposition events for a given environment.
    Query param: env_id
    Sends keepalive comments every 30s to prevent connection timeout.
    """
    env_id = request.args.get('env_id', '')
    if not env_id:
        return jsonify({'error': 'env_id required'}), 400

    def event_stream():
        q = Queue()
        _live_feed_subscribers.setdefault(env_id, []).append(q)
        yield ': connected\n\n'   # flush headers immediately so onopen fires
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    yield f'data: {json.dumps(event)}\n\n'
                except Empty:
                    yield ': keepalive\n\n'
        finally:
            subs = _live_feed_subscribers.get(env_id, [])
            if q in subs:
                subs.remove(q)

    return Response(event_stream(), mimetype='text/event-stream',
                    headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


# ---------------------------------------------------------------------------
# Push lead to booking campaign
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/leads/push-booking', methods=['POST'])
@require_rc_token
@track_usage('D365 Demo - Push to Booking')
def push_to_booking():
    """
    Pushes a single HOT (or manually selected) lead into the booking campaign.
    Body: { env_id, leadid }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    env_id = data.get('env_id')
    leadid = data.get('leadid')
    if not env_id or not leadid:
        return jsonify({'error': 'env_id and leadid are required'}), 400

    ringcx_token      = session.get('ringcx_access_token')
    ringcx_account_id = session.get('ringcx_account_id')
    if not ringcx_token:
        return jsonify({'error': 'Not connected to RingCX. Please reconnect.'}), 401

    user_email = session.get('user_email', '')
    user_state = fs.get_user_state(user_email)
    booking_campaign_id = user_state.get('booking_campaign_id')
    if not booking_campaign_id:
        return jsonify({'error': 'Booking campaign not configured. Set it in the Campaign IDs card.'}), 400

    lead = fs.get_demo_lead(env_id, leadid)
    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    try:
        d365.push_leads_to_ringcx(
            ringcx_token,
            ringcx_account_id,
            booking_campaign_id,
            [{
                'firstName':    lead.get('firstname', ''),
                'lastName':     lead.get('lastname', ''),
                'phone1':       lead.get('phone', ''),
                'externId':     leadid,
                'leadTimezone': _state_to_ringcx_tz(lead.get('state', '')),
            }],
        )
        fs.update_demo_lead(env_id, leadid, {'booking_pushed': True})
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f'push_to_booking: failed for {leadid}: {e}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Outcomes dashboard
# ---------------------------------------------------------------------------

@d365_ringcx_bp.route('/outcomes', methods=['GET'])
@require_rc_token
def get_outcomes():
    """
    Returns lead outcome summary + full lead list for the live dashboard.
    Query param: env_id
    """
    env_id = request.args.get('env_id', '')
    if not env_id:
        return jsonify({'error': 'env_id required'}), 400

    leads = fs.get_demo_leads(env_id)

    def is_hot(l):
        # Matches "HOT", "HOT LEAD", etc. but not "NOT HOT"
        return (l.get('disposition') or '').upper().strip().startswith('HOT')

    total   = len(leads)
    called  = sum(1 for l in leads if l.get('disposition'))
    hot     = sum(1 for l in leads if is_hot(l))
    not_hot = called - hot
    pending = total - called

    # AI vs Human split
    def split_stats(group):
        g_called = sum(1 for l in group if l.get('disposition'))
        g_hot    = sum(1 for l in group if is_hot(l))
        return {'total': len(group), 'called': g_called, 'hot': g_hot, 'not_hot': g_called - g_hot}

    ai_stats    = split_stats([l for l in leads if l.get('campaign_assigned') == 'ai'])
    human_stats = split_stats([l for l in leads if l.get('campaign_assigned') == 'human'])

    # Automation speed: avg seconds from created_at → pushed_at
    speed_samples = []
    for l in leads:
        try:
            c, p = l.get('created_at'), l.get('pushed_at')
            if c and p:
                ct = datetime.fromisoformat(c.replace('Z', '+00:00'))
                pt = datetime.fromisoformat(p.replace('Z', '+00:00'))
                secs = (pt - ct).total_seconds()
                if 0 <= secs < 3600:
                    speed_samples.append(secs)
        except Exception:
            pass
    avg_speed = round(sum(speed_samples) / len(speed_samples), 1) if speed_samples else None

    all_leads = [
        {
            'leadid':         l['leadid'],
            'firstname':      l.get('firstname', ''),
            'lastname':       l.get('lastname', ''),
            'phone':          l.get('phone', ''),
            'campaign':       l.get('campaign_assigned', ''),
            'disposition':    l.get('disposition', ''),
            'recording_url':  l.get('recording_url', ''),
            'notes':          l.get('notes', ''),
            'disposed_at':    l.get('disposed_at', ''),
            'booking_pushed': l.get('booking_pushed', False),
            'webscore':       l.get('webscore'),
            'movetype':       l.get('movetype', ''),
            'origin':         l.get('origin', ''),
            'destination':    l.get('destination', ''),
            'created_at':     l.get('created_at', ''),
            'pushed_at':      l.get('pushed_at', ''),
        }
        for l in leads
    ]

    return jsonify({
        'total':                     total,
        'called':                    called,
        'hot':                       hot,
        'not_hot':                   not_hot,
        'pending':                   pending,
        'avg_automation_speed_secs': avg_speed,
        'ai':                        ai_stats,
        'human':                     human_stats,
        'leads':                     all_leads,
    })
