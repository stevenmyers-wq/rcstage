import requests
import os
from webapp.rc_api import rc_api_call

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges an Employee SSO token for a Customer-scoped session token.
    Uses the 'rcau' profile which is authorized for Analytics.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    
    # Try 'rcau' as the primary appName for this service
    payload = {
        "accountId": str(target_account_id),
        "appName": "rcau" 
    }

    print(f"--- ATTEMPTING BRIDGE: Account={target_account_id}, AppName={payload['appName']} ---")
    
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        
        if response.ok:
            data = response.json()
            print(f"--- BRIDGE SUCCESS ---")
            print(f"GRANTED SCOPES: {data.get('scope')}") # Look for 'Analytics' here
            return data.get("access_token")
        else:
            # This is where your 'Invalid appName' 400 error was caught
            print(f"--- BRIDGE ERROR {response.status_code} ---")
            print(f"PAYLOAD SENT: {payload}")
            print(f"RESPONSE BODY: {response.text}")
            return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def get_super_admin_extension(self):
        """Resolves the Operator extension ID. Fixes 404 error."""
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        if res and 'operator' in res:
            return res['operator'].get('id')
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """POST analytics query. Fixes 403 error."""
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
