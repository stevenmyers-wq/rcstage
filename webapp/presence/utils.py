import json
import logging
from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        try:
            endpoint = f"{self.base_path}/extension"
            params = {"type": ["User"], "perPage": 1000} 
            response = rc_api_call(endpoint, method="GET", params=params)
            return response.get('records', []) if response else []
        except Exception as e:
            logging.error(f"Failed to fetch users: {e}")
            return []

    def get_all_extensions_raw(self):
        try:
            endpoint = f"{self.base_path}/extension"
            params = {"perPage": 1000}
            response = rc_api_call(endpoint, method="GET", params=params)
            return response.get('records', []) if response else []
        except Exception as e:
            logging.error(f"Failed to fetch raw extensions: {e}")
            return []

    def get_extension_by_number(self, ext_number):
        try:
            endpoint = f"{self.base_path}/extension"
            params = {"extensionNumber": ext_number}
            response = rc_api_call(endpoint, method="GET", params=params)
            records = response.get('records', [])
            return str(records[0].get('id')) if records else None
        except Exception:
            return None

    def get_presence_settings(self, extension_id):
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="GET") or {}
        except Exception:
            return {}

    def update_presence_settings(self, extension_id, payload):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="PUT", json=payload)

    def get_monitored_lines(self, extension_id):
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET") or {"records": []}
        except Exception:
            return {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        payload = {"records": line_records}
        payload_str = json.dumps(payload)
        
        # DEFINITIVE PROOF LOGGING: Print exactly what we are about to send to GCP
        logging.warning(f"=== OUTGOING PAYLOAD FOR {extension_id} ===")
        logging.warning(payload_str)
        
        try:
            result = rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
            
            # If the wrapper swallowed a 400 and returned None
            if result is None:
                raise Exception(f"Your wrapper hid the error. Sent Payload: {payload_str}")
                
            return result
            
        except Exception as e:
            # Attempt to rip the raw RingCentral error body out of the Exception object
            rc_error_reason = "Could not extract raw RC body."
            if hasattr(e, 'response') and e.response is not None:
                try:
                    rc_error_reason = e.response.text
                except:
                    pass
            
            # Throw everything back to the UI so you can read it directly
            definitive_error = f"SENT: {payload_str} || RC_RESPONSE: {rc_error_reason}"
            logging.error(f"DEFINITIVE FAILURE: {definitive_error}")
            raise Exception(definitive_error)
