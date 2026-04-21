from webapp.rc_api import rc_api_call
import logging

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        """Fetches standard user extensions for the UI table."""
        endpoint = f"{self.base_path}/extension"
        params = {"type": ["User"], "perPage": 1000} 
        response = rc_api_call(endpoint, method="GET", params=params)
        return response.get('records', []) if response else []

    def get_all_extensions_raw(self):
        """Fetches ALL extension types to identify Shared Lines, Depts, etc."""
        endpoint = f"{self.base_path}/extension"
        params = {"perPage": 1000}
        response = rc_api_call(endpoint, method="GET", params=params)
        return response.get('records', []) if response else []

    def get_presence_settings(self, extension_id):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="GET") or {}

    def update_presence_settings(self, extension_id, payload):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="PUT", json=payload)

    def get_monitored_lines(self, extension_id):
        """Step 1 of the Update Process: Get current hardware/HUD state."""
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET") or {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        """Step 2: Update only the editable lines."""
        payload = {"records": line_records}
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
        except Exception as e:
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                logging.error(f"RC API 400 Detail for {extension_id}: {e.response.text}")
            raise e
