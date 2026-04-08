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
            print(f"--- BRIDGE SUCCESS WITH 'brd' ---")
            print(f"GRANTED SCOPES: {data.get('scope')}")
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
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def get_account_info(self):
        """
        Diagnostic: Fetches full account metadata.
        Endpoint: /restapi/v1.0/account/{accountId}
        """
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        return rc_api_call(endpoint, token=self.token)

    def get_super_admin_extension(self):
        """Resolves the Operator extension ID."""
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        if res and 'operator' in res:
            return res['operator'].get('id')
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """POST analytics query for aggregate call records."""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if admin_extension_id:
            payload["callFilters"] = {
                "extensionFilters": [{"extensionId": str(admin_extension_id)}]
            }

        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            **kwargs
        )
