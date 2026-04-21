from webapp.rc_api import rc_api_call
import logging

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
        """Fetches ALL extensions to build the Number-to-ID Translator."""
        endpoint = f"{self.base_path}/extension"
        params = {"perPage": 1000}
        try:
            response = rc_api_call(endpoint, method="GET", params=params)
            return response.get('records', []) if response else []
        except Exception:
            return []

    def get_extension_by_number(self, ext_number):
        """Targeted lookup if the bulk dictionary misses an extension."""
        endpoint = f"{self.base_path}/extension"
        params = {"extensionNumber": ext_number}
        try:
            response = rc_api_call(endpoint, method="GET", params=params)
            records = response.get('records', [])
            if records:
                return str(records[0].get('id'))
        except Exception:
            pass
        return None

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
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
        except Exception as e:
            # --- DEBUG TRAP: Catch exact RC Error Body ---
            error_details = str(e)
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                error_details += f" | RC API RESPONSE BODY: {e.response.text}"
            
            print(f"\n{'='*50}\nRC API ERROR DETECTED\nEndpoint: /presence/line\nDetails: {error_details}\n{'='*50}\n")
            logging.error(f"RC API Error Details: {error_details}")
            
            raise Exception(error_details)
