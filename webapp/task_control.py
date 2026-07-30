"""Shared cooperative-cancellation registry for long-running write tools.

Every UC tool that writes to an account in bulk (item by item, in batches, or as
a streamed job) should be stoppable mid-run. Rather than each tool re-inventing
its own cancel flag, they all share this one in-memory registry keyed by a
``task_id``.

Why a plain module-level dict/set is safe here: the app is deployed as
``gunicorn --workers 1 --threads 8`` (see the project dockerfile) -- a SINGLE
process with thread-based concurrency. So the worker/loop doing the writes and
the separate ``/cancel`` request that asks it to stop run in the *same* process
and see the *same* memory. (This is the same assumption the original bespoke
`user_templates` progress store already relied on.) If the app is ever moved to
multiple worker processes, this registry would need to move to a shared backend
(Firestore/Redis) instead.

Cancellation is cooperative and best-effort: a worker checks ``is_stopped()``
between items and bails out cleanly. Items already sent to RingCentral cannot be
recalled -- only not-yet-sent items are skipped.

Typical usage
-------------
Worker loop (background thread, generator, or synchronous request)::

    from webapp import task_control
    for item in items:
        if task_control.is_stopped(task_id):
            break                      # record a 'stopped' outcome, then stop
        ... write item ...
    task_control.clear(task_id)        # always clear when the task ends

Blueprint (one line to expose a cancel endpoint)::

    bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])
"""
import time
import threading

from flask import jsonify, request

# task_ids with a pending stop request. A set (not a dict) -- membership is all
# we need. Guarded by _lock for the mutating operations.
_lock = threading.Lock()
_stopped = set()

# Safety cap so a long-lived process can't accumulate stop flags forever if a
# caller ever forgets to clear(). Well above any realistic concurrent job count.
_MAX_FLAGS = 2000


def request_stop(task_id):
    """Ask the worker owning ``task_id`` to stop at its next item check.

    Returns True if a (non-empty) id was recorded. Safe to call more than once.
    """
    if not task_id:
        return False
    with _lock:
        _stopped.add(str(task_id))
    return True


def is_stopped(task_id):
    """True if a stop has been requested for ``task_id``. Cheap; call freely
    inside tight per-item loops."""
    if not task_id:
        return False
    return str(task_id) in _stopped


def clear(task_id):
    """Forget any stop flag for ``task_id``.

    Call this whenever a task finishes -- success, error, OR cancel -- so a
    future task that happens to reuse the id doesn't inherit a stale stop.
    """
    if not task_id:
        return
    with _lock:
        _stopped.discard(str(task_id))
        # Defensive: if the set somehow grew unbounded (a caller never cleared),
        # reset it rather than leak memory. Worst case a couple of in-flight
        # cancels are missed, which is far better than an OOM.
        if len(_stopped) > _MAX_FLAGS:
            _stopped.clear()


def interruptible_sleep(task_id, seconds, slice_seconds=1.0):
    """Sleep up to ``seconds``, but wake early if the task is stopped.

    Waits in short slices so a stop request is honoured promptly instead of only
    after a multi-minute wait (used by tools that wait out RingCentral rate
    limits or account-wide locks). Returns True if a stop was requested.
    """
    end = time.time() + seconds
    while time.time() < end:
        if is_stopped(task_id):
            return True
        time.sleep(min(slice_seconds, max(0.05, end - time.time())))
    return is_stopped(task_id)


def cancel_view():
    """Ready-made Flask view for a tool's ``/cancel`` endpoint.

    Reads ``task_id`` from the query string or JSON body and flags it for stop.
    Register it per blueprint with::

        bp.add_url_rule('/cancel', 'cancel', task_control.cancel_view, methods=['POST'])
    """
    task_id = request.args.get('task_id') or (request.get_json(silent=True) or {}).get('task_id')
    if not task_id:
        return jsonify({"error": "Missing task_id."}), 400
    request_stop(task_id)
    return jsonify({"success": True})
