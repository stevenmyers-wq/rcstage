import requests
import os
from webapp.rc_api import rc_api_call

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges an Employee SSO token for a Customer-scoped session token.
    Uses 'brd' (Build) as the stable internal profile for impersonation.
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
            print(f"--- BRIDGE SUCCESS: OWNER={data.get('owner_id')} SCOPES={data.get('scope')} ---")
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
        self.platform_url = "https://platform.ringcentral.com"

    def get_account_identity_proof(self):
        """
        Directly queries the identity of the token to provide burden of proof.
        Using '~' tells the API: 'Tell me who I am impersonating right now.'
        """
        url = f"{self.platform_url}/restapi/v1.0/account/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        # Use direct requests to bypass any custom logic in rc_api_call
        response = requests.get(url, headers=headers)
        return response.status_code, response.json()

    def get_super_admin_extension(self):
        """Resolves the Operator/Super Admin extension ID."""
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        if res and 'operator' in res:
            return res['operator'].get('id')
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None):
        """
        POST analytics query. Uses platform.ringcentral.com as per the HAR.
        If this returns 403, it means the 'brd' bridge profile lacks Analytics scopes.
        """
        url = f"{self.platform_url}/analytics/calls/v1/accounts/{self.account_id}/records/fetch"
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
