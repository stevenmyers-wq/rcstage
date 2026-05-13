import os
import queue
import threading
import time
from flask import Blueprint, jsonify, request, session, Response, stream_with_context
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

audio_streaming_bp = Blueprint(
    'audio_streaming_bp', __name__,
    url_prefix='/api/audio_streaming'
)

# --- In-memory transcript store ---
# Keyed by dialog_id, each value is a list of SSE subscriber queues.
# When a transcript event arrives, it's pushed to all subscriber queues for that dialog.
_transcript_subscribers = {}
_subscribers_lock = threading.Lock()

WEBHOOK_SECRET = os.environ.get('RCAU_WEBHOOK_SECRET', '')


def _publish_to_subscribers(dialog_id, data):
    """Push a transcript event to all SSE subscribers for a dialog."""
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
    """Register a new SSE subscriber queue for a dialog."""
    q = queue.Queue(maxsize=100)
    with _subscribers_lock:
        if dialog_id not in _transcript_subscribers:
            _transcript_subscribers[dialog_id] = []
        _transcript_subscribers[dialog_id].append(q)
    return q


def _unsubscribe(dialog_id, q):
    """Remove an SSE subscriber queue when the browser disconnects."""
    with _subscribers_lock:
        subscribers = _transcript_subscribers.get(dialog_id, [])
        if q in subscribers:
            subscribers.remove(q)
        if not subscribers:
            _transcript_subscribers.pop(dialog_id, None)


# --- Existing auth endpoints ---

@audio_streaming_bp.route('/ringcx-token', methods=['POST'])
@require_rc_token
@track_usage('RingCX Streaming - Connect')
def get_ringcx_token():
    """
    Exchanges the session RC token for a RingCX token.
    Stores accessToken, refreshToken, and accountId in session.
    """
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
    """
    Refreshes the RingCX access token using the stored refresh token.
    Called automatically by the frontend every ~4 minutes.
    """
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
    """
    Fetches RingCX accounts using the stored RingCX session token.
    """
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
    """
    Returns current RingCX session connection state.
    Used by the frontend on page load to restore UI state.
    """
    token = session.get('ringcx_access_token')
    account_id = session.get('ringcx_account_id')

    return jsonify({
        'connected': bool(token),
        'accountId': account_id if token else None
    })


@audio_streaming_bp.route('/ringcx-disconnect', methods=['POST'])
def ringcx_disconnect():
    """Clears RingCX session tokens."""
    session.pop('ringcx_access_token', None)
    session.pop('ringcx_refresh_token', None)
    session.pop('ringcx_account_id', None)
    return jsonify({'status': 'success'})


# --- Webhook receiver (called by gRPC streaming service) ---

@audio_streaming_bp.route('/transcript-event', methods=['POST'])
def transcript_event():
    """
    Receives transcript lines from the gRPC streaming service.
    Validates the shared secret, then pushes to SSE subscribers.
    """
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


# --- SSE stream endpoint (browser connects here) ---

@audio_streaming_bp.route('/transcript-stream/<dialog_id>', methods=['GET'])
def transcript_stream(dialog_id):
    """
    Server-Sent Events endpoint. Browser connects here to receive
    live transcript lines for a specific dialog.
    """
    q = _subscribe(dialog_id)

    def generate():
        try:
            # Send a connected confirmation immediately
            yield 'data: {"type": "connected"}\n\n'
            while True:
                try:
                    # Wait up to 20 seconds for a transcript event
                    # then send a keepalive comment to prevent timeout
                    item = q.get(timeout=20)
                    import json
                    yield f'data: {json.dumps(item)}\n\n'
                except queue.Empty:
                    # SSE keepalive
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
