import logging
import time

from flask import session

from webapp.rc_api import rc_api_call

logger = logging.getLogger(__name__)

# The BLF presence write endpoint (PUT .../presence/line) is documented as
# throttling-group "Medium" and the service itself carries x-availability
# "Limited" with an x-failover-strategy of "Hardening". That means 429
# (throttled), 500 and 503 (ServiceNotAvailable) are *expected*, transient
# responses that should be retried rather than treated as hard failures.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RCPresenceError(Exception):
    """Raised when an RC presence call fails with a non-retryable (or
    retry-exhausted) status. Carries the HTTP status and the raw RC error body
    so callers can report exactly why RingCentral rejected the change instead
    of silently counting it as a success."""

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class RCPresenceManager:
    def __init__(self, account_id="~", max_retries=4):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"
        self.max_retries = max_retries

    @staticmethod
    def _pkce_token():
        """BLF/presence deliberately uses ONLY the standard PKCE OAuth token
        (like the analytics module), never the SM impersonation bridge. The
        presence write endpoints require the EditPresence app permission, so
        the operator connects with a client id known to hold it. Passing the
        token explicitly also bypasses rc_api_call's impersonation-first
        selection and its 401 impersonation-refresh path."""
        return session.get('rc_access_token')

    # ------------------------------------------------------------------
    # Core transport with retry/backoff + honest error propagation
    # ------------------------------------------------------------------
    def _call(self, endpoint, method="GET", **kwargs):
        """Perform an RC API call and return the parsed JSON body.

        Retries 429/5xx with exponential backoff (honouring ``Retry-After``
        when present), which the presence service documents as expected under
        its "Hardening" failover strategy. Raises :class:`RCPresenceError` on
        any non-2xx that survives the retries so failures can never be
        misreported as successes.
        """
        kwargs.setdefault('token', self._pkce_token())
        attempt = 0
        while True:
            resp = rc_api_call(endpoint, method=method, return_response=True, **kwargs)
            status = getattr(resp, "status_code", None)

            if resp is not None and getattr(resp, "ok", False):
                if status == 204:
                    return {"success": True}
                try:
                    return resp.json()
                except ValueError:
                    return {}

            if status in RETRYABLE_STATUS and attempt < self.max_retries:
                headers = getattr(resp, "headers", {}) or {}
                try:
                    retry_after = float(headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0
                backoff = retry_after if retry_after > 0 else min(2 ** attempt, 16)
                logger.warning(
                    "Presence %s %s -> %s. Retry %s/%s in %ss",
                    method, endpoint, status, attempt + 1, self.max_retries, backoff,
                )
                time.sleep(backoff)
                attempt += 1
                continue

            try:
                body = resp.text if resp is not None else ""
            except Exception:
                body = ""
            raise RCPresenceError(
                f"RC API {method} {endpoint} failed with status {status}: {body}",
                status_code=status,
                body=body,
            )

    # ------------------------------------------------------------------
    # Read helpers used to populate the UI (soft-fail: never break the page)
    # ------------------------------------------------------------------
    def get_sites(self):
        endpoint = f"{self.base_path}/sites"
        response = rc_api_call(endpoint, method="GET", token=self._pkce_token())
        return response.get('records', []) if response else []

    def get_all_users(self, site_id=None):
        endpoint = f"{self.base_path}/extension"
        params = {"type": ["User"], "perPage": 1000}
        if site_id: params["siteId"] = site_id
        response = rc_api_call(endpoint, method="GET", params=params, token=self._pkce_token())
        return response.get('records', []) if response else []

    def get_all_extensions_raw(self):
        endpoint = f"{self.base_path}/extension"
        params = {"perPage": 1000}
        response = rc_api_call(endpoint, method="GET", params=params, token=self._pkce_token())
        return response.get('records', []) if response else []

    def get_extension_by_number(self, ext_number):
        endpoint = f"{self.base_path}/extension"
        params = {"extensionNumber": ext_number}
        response = rc_api_call(endpoint, method="GET", params=params, token=self._pkce_token())
        records = response.get('records', []) if response else []
        return str(records[0].get('id')) if records else None

    # ------------------------------------------------------------------
    # Presence settings + monitored lines (strict: surface every failure)
    # ------------------------------------------------------------------
    def get_presence_settings(self, extension_id):
        return self._call(
            f"{self.base_path}/extension/{extension_id}/presence", method="GET"
        ) or {}

    def update_presence_settings(self, extension_id, payload):
        return self._call(
            f"{self.base_path}/extension/{extension_id}/presence",
            method="PUT", json=payload,
        )

    def get_monitored_lines(self, extension_id):
        """Return the *complete* monitored-line list, following pagination.

        The monitored-lines PUT is a full replacement, so any line missing from
        the list we read back would be silently dropped. A heavy HUD user can
        have more lines than a single page returns, so we page through every
        record before handing the set to the update logic.
        """
        endpoint = f"{self.base_path}/extension/{extension_id}/presence/line"
        all_records = []
        page = 1
        while True:
            resp = self._call(
                endpoint, method="GET", params={"perPage": 1000, "page": page}
            ) or {}
            records = resp.get('records', []) or []
            all_records.extend(records)

            paging = resp.get('paging', {}) or {}
            total_pages = paging.get('totalPages')
            current_page = paging.get('page', page)
            if not records or not total_pages or current_page >= total_pages:
                break
            page = current_page + 1
        return {"records": all_records}

    def update_monitored_lines(self, extension_id, line_records):
        payload = {"records": line_records}
        return self._call(
            f"{self.base_path}/extension/{extension_id}/presence/line",
            method="PUT", json=payload,
        )
