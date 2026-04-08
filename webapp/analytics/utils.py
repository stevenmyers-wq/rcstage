import requests
import os
from webapp.rc_api import rc_api_call

# Based on the Entry Points doc: api is for Common API, extapi is for Backend OAuth
COMMON_API_URL = "https://api.ringcentral.com"
INTERNAL_API_URL = "https://extapi.ringcentral.com"

def get_impersonation_token(employee_token, target_account_id):
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "access_token": employee_token}
    payload = {"accountId": str(target_account_id), "appName": "brd"}
    
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            return response.json().get("access_token")
        return None
    except Exception:
        return None

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token

    def get_account_identity(self):
        """
        Using the 'Common API' subdomain (api.ringcentral.com) 
        to get the specific Company Name for proof.
        """
        endpoint = f"{COMMON_API_URL}/restapi/v1.0/account/~"
        # We manually call requests here to specify the subdomain
        headers = {"Authorization": f"Bearer {self.token}"}
        return requests.get(endpoint, headers=headers).json()

    def fetch_records(self, dimension, time_settings, admin_extension_id=None):
        """
        Using the 'Internal/Backend' subdomain (extapi.ringcentral.com)
        to attempt to bypass 403 restrictions on the public platform.
        """
        url = f"{INTERNAL_API_URL}/analytics/calls/v1/accounts/{self.account_id}/records/fetch"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        
        payload = {"dimension": dimension, "timeSettings": time_settings}
        if admin_extension_id:
            payload["callFilters"] = {"extensionFilters": [{"extensionId": str(admin_extension_id)}]}

        response = requests.post(url, headers=headers, json=payload)
        return response.json()
