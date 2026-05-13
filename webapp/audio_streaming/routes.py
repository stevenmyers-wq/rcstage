import os
import queue
import threading
import json
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

audio_streaming_bp = Blueprint(
    'audio_streaming_bp', __name__,
    url_prefix='/api/audio_streaming'
)

# --- In-memory stores ---
_transcript_subscribers = {}
_subscribers_lock = threading.Lock()

# Active dialogs: {dialog_id: {ani, dnis, started_at}}
_active_dialogs = {}
_dialogs_lock = threading.Lock()

WEBHOOK_SECRET = os.environ.get('RCAU_WEBHOOK_SECRET', '')
GCP_PROJECT_NUMBER = os.environ.get('GCP_PROJECT_NUMBER', '396158962307')
GRPC_SERVICE_NAME = 'rcau-rcx-grpc-streaming'
GRPC_REGION = 'us-central1'


def _publish_to_subscribers(dialog_id, data):
    with _subscribers_lock:
        subscribers = _transcript_subscribers.get(dialog_id, [])
        dead = []
        for q in subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            subscribers.remove(q)


def _subscribe(dialog_id):
    q = queue.Queue(maxsize=100)
    with _subscribers_lock:
        if dialog_id not in _transcript_subscribers:
            _transcript_subscribers[dialog_id] = []
        _transcript_subscribers[dialog_id].append(q)
    return q


def _unsubscribe(dialog_id, q):
    with _subscribers_lock:
        subscribers = _transcript_subscribers.get(dialog_id, [])
        if q in subscribers:
            subscribers.remove(q)
        if not subscribers:
            _transcript_subscribers.pop(dialog_id, None)


# --- Auth endpoints ---

@audio_streaming_bp.route('/ringcx-token', methods=['POST'])
@require_rc_token
@track_usage('RingCX Streaming - Connect')
def get_ringcx_token():
    rc_token = session.get('rc_access_token')
    try:
        result = utils.exchange_rc_token_for_ringcx(rc_token)
        session['ringcx_access_token'] = result['accessToken']
        session['ringcx_refresh_token'] = result['refreshToken']
        session['ringcx_account_id'] = result['accountId']
        return jsonify({
            'status': 'success',
            'accountId': result['accountId'],
            'agentDetails': result['agentDetails']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@audio_streaming_bp.route('/ringcx-refresh', methods=['POST'])
def refresh_ringcx_token():
    refresh_token = session.get('ringcx_refresh_token')
    if not refresh_token:
        return jsonify({'error': 'No RingCX refresh token in session. Please reconnect.'}), 401
    try:
        result = utils.refresh_ringcx_token(refresh_token)
        session['ringcx_access_token'] = result['accessToken']
        session['ringcx_refresh_token'] = result['refreshToken']
        return jsonify({'status': 'success'})
    except Exception as e:
        session.pop('ringcx_access_token', None)
        session.pop('ringcx_refresh_token', None)
        session.pop('ringcx_account_id', None)
        return jsonify({'error': str(e)}), 401


@audio_streaming_bp.route('/accounts', methods=['GET'])
@require_rc_token
@track_usage('RingCX Streaming - Get Accounts')
def get_accounts():
    ringcx_token = session.get('ringcx_access_token')
    if not ringcx_token:
        return jsonify({'error': 'Not connected to RingCX. Please connect first.'}), 401
    try:
        accounts = utils.get_ringcx_accounts(ringcx_token)
        return jsonify({'status': 'success', 'accounts': accounts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@audio_streaming_bp.route('/ringcx-status', methods=['GET'])
def ringcx_status():
    token = session.get('ringcx_access_token')
    account_id = session.get('ringcx_account_id')
    return jsonify({
        'connected': bool(token),
        'accountId': account_id if token else None
    })


@audio_streaming_bp.route('/ringcx-disconnect', methods=['POST'])
def ringcx_disconnect():
    session.pop('ringcx_access_token', None)
    session.pop('ringcx_refresh_token', None)
    session.pop('ringcx_account_id', None)
    return jsonify({'status': 'success'})


# --- gRPC service URL ---

@audio_streaming_bp.route('/grpc-service-url', methods=['GET'])
def grpc_service_url():
    hostname = f'{GRPC_SERVICE_NAME}-{GCP_PROJECT_NUMBER}.{GRPC_REGION}.run.app'
    url = f'grpc://{hostname}:443'
    return jsonify({'url': url, 'hostname': hostname})


# --- Active dialogs ---

@audio_streaming_bp.route('/dialog-event', methods=['POST'])
def dialog_event():
    """
    Receives dialog_start / dialog_end events from the gRPC streaming service.
    Maintains the in-memory active dialogs list used by both the streaming
    tab and agent form tab.
    """
    secret = request.headers.get('X-Webhook-Secret', '')
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    event = data.get('event')
    dialog_id = data.get('dialog_id')
    if not dialog_id:
        return jsonify({'error': 'Missing dialog_id'}), 400

    with _dialogs_lock:
        if event == 'dialog_start':
            _active_dialogs[dialog_id] = {
                'dialog_id': dialog_id,
                'ani': data.get('ani') or 'Unknown',
                'dnis': data.get('dnis') or 'Unknown',
                'started_at': datetime.now(timezone.utc).isoformat(),
            }
        elif event == 'dialog_end':
            _active_dialogs.pop(dialog_id, None)

    return jsonify({'status': 'ok'})


@audio_streaming_bp.route('/active-dialogs', methods=['GET'])
def active_dialogs():
    """
    Returns currently active dialogs. Polled by streaming tab and agent form tab.
    No auth required — dialog IDs are unguessable UUIDs.
    """
    with _dialogs_lock:
        dialogs = list(_active_dialogs.values())
    return jsonify({'dialogs': dialogs})


# --- Webhook receiver ---

@audio_streaming_bp.route('/transcript-event', methods=['POST'])
def transcript_event():
    secret = request.headers.get('X-Webhook-Secret', '')
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    dialog_id = data.get('dialog_id')
    if not dialog_id:
        return jsonify({'error': 'Missing dialog_id'}), 400

    _publish_to_subscribers(dialog_id, data)
    return jsonify({'status': 'ok'})


# --- SSE stream endpoint ---

@audio_streaming_bp.route('/transcript-stream/<dialog_id>', methods=['GET'])
def transcript_stream(dialog_id):
    q = _subscribe(dialog_id)

    def generate():
        try:
            yield 'data: {"type": "connected"}\n\n'
            while True:
                try:
                    item = q.get(timeout=20)
                    yield f'data: {json.dumps(item)}\n\n'
                except queue.Empty:
                    yield ': keepalive\n\n'
        except GeneratorExit:
            pass
        finally:
            _unsubscribe(dialog_id, q)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
