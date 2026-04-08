import requests
import os
from webapp.rc_api import rc_api_call

# Internal Routing based on Entry Points Document
COMMON_API_URL = "https://api.ringcentral.com"
INTERNAL_BACKEND_URL = "https://extapi.ringcentral.com"

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges an Employee SSO token for a Customer-scoped session token.
    Uses 'brd' (Build) profile as identified in the working HAR file.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    
    payload = {
        "accountId": str(target_account_id),
        "appName": "brd" 
    }

    print(f"--- BRIDGE ATTEMPT: AppName='brd' Target='{target_account_id}' ---")
    
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            data = response.json()
            print(f"--- BRIDGE SUCCESS: GRANTED SCOPES: {data.get('scope')} ---")
            return data.get("access_token")
        else:
            print(f"--- BRIDGE FAILED: {response.status_code} {response.text} ---")
            return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token

    def get_account_identity(self):
        """
        Diagnostic: Hits the 'Common API' subdomain to pull legal contact info.
        Uses '~' to prove exactly whose token this is.
        """
        url = f"{COMMON_API_URL}/restapi/v1.0/account/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        response = requests.get(url, headers=headers)
        return response.json()

    def get_super_admin_extension(self):
        """Resolves Operator extension for the account."""
        # Using rc_api_call which defaults to platform.ringcentral.com
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        if res and 'operator' in res:
            return res['operator'].get('id')
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None):
        """
        POST analytics query via 'extapi.ringcentral.com'.
        This is the internal route for backend apps to bypass public 403s.
        """
        url = f"{INTERNAL_BACKEND_URL}/analytics/calls/v1/accounts/{self.account_id}/records/fetch"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if admin_extension_id:
            payload["callFilters"] = {
                "extensionFilters": [{"extensionId": str(admin_extension_id)}]
            }

        response = requests.post(url, headers=headers, json=payload)
        return response.json()
