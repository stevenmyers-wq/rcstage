from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        """Fetches User extensions for the UI table."""
        endpoint = f"{self.base_path}/extension"
        params = {"type": ["User"], "perPage": 1000} 
        try:
            response = rc_api_call(endpoint, method="GET", params=params)
            return response.get('records', []) if response else []
        except Exception as e:
            raise Exception(f"Failed to fetch users: {str(e)}")

    def get_all_extensions_raw(self):
        """Fetches ALL extensions (Queues, Park, Shared) to build the Number-to-ID Translator."""
        endpoint = f"{self.base_path}/extension"
        params = {"perPage": 1000}
        try:
            response = rc_api_call(endpoint, method="GET", params=params)
            return response.get('records', []) if response else []
        except Exception:
            return []

    # --- Presence Settings (The Toggles) ---
    def get_presence_settings(self, extension_id):
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="GET")
        except Exception:
            return {}

    def update_presence_settings(self, extension_id, payload):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="PUT", json=payload)

    # --- Presence Lines (The BLF Buttons) ---
    def get_monitored_lines(self, extension_id):
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET")
        except Exception:
            return {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        payload = {"records": line_records}
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
