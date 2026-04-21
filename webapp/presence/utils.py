import json
import logging
from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_sites(self):
        try:
            # Fetches the list of sites for the UI dropdown filter
            endpoint = f"{self.base_path}/sites"
            response = rc_api_call(endpoint, method="GET")
            return response.get('records', []) if response else []
        except Exception as e:
            logging.error(f"Failed to fetch sites: {e}")
            return []

    def get_all_users(self, site_id=None):
        try:
            endpoint = f"{self.base_path}/extension"
            params = {"type": ["User"], "perPage": 1000} 
            # If the UI passes a site, append it to the API request
            if site_id and str(site_id).strip() != "":
                params["siteId"] = site_id
                
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
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
