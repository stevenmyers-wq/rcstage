from webapp.rc_api import rc_api_call
import logging

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        endpoint = f"{self.base_path}/extension"
        params = {"type": ["User"], "perPage": 1000} 
        response = rc_api_call(endpoint, method="GET", params=params)
        return response.get('records', []) if response else []

    def get_all_extensions_raw(self):
        endpoint = f"{self.base_path}/extension"
        params = {"perPage": 1000}
        response = rc_api_call(endpoint, method="GET", params=params)
        return response.get('records', []) if response else []

    def get_extension_by_number(self, ext_number):
        endpoint = f"{self.base_path}/extension"
        params = {"extensionNumber": ext_number}
        response = rc_api_call(endpoint, method="GET", params=params)
        records = response.get('records', [])
        return str(records[0].get('id')) if records else None

    def get_presence_settings(self, extension_id):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="GET") or {}

    def update_presence_settings(self, extension_id, payload):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence", method="PUT", json=payload)

    def get_monitored_lines(self, extension_id):
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET") or {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        payload = {"records": line_records}
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
        except Exception as e:
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                print(f"RC API Error Body: {e.response.text}")
            raise e
