import requests
import os
from webapp.rc_api import rc_api_call

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges an Employee SSO token for a Customer-scoped session token.
    This resolves the 404 (Account Not Found) and 403 (Forbidden) errors.
    """
    # Endpoint identified in the working tool's HAR file
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    
    # CRITICAL: The bridge requires the token in an 'access_token' header, 
    # not the standard 'Authorization' header.
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    
    # 'brd' is the appName used by build.ps.ringcentral.com
    payload = {
        "accountId": str(target_account_id),
        "appName": "analytics" 
    }

    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            data = response.json()
            # LOGGING: Verify that 'Analytics' appears in this string
            print(f"--- BRIDGE SUCCESS ---")
            print(f"TARGET ACCOUNT: {data.get('owner_id')}")
            print(f"GRANTED SCOPES: {data.get('scope')}")
            return data.get("access_token")
        else:
            print(f"--- BRIDGE ERROR: {response.status_code} ---")
            print(f"RESPONSE: {response.text}")
            return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None

class RCBusinessAnalytics:
    """
    Client for the Analytics API.
    Identifies the Super Admin via the 'operator' field in Account info.
    """
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def get_super_admin_extension(self):
        """
        Resolves the Operator extension ID for the account.
        Using the impersonated token fixes the 404 error here.
        """
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        
        if res and 'operator' in res:
            return res['operator'].get('id')
        
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """
        POST analytics query.
        Using the impersonated token fixes the 403 error here.
        """
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }

        if admin_extension_id:
            payload["callFilters"] = {
                "extensionFilters": [
                    {"extensionId": str(admin_extension_id)}
                ]
            }

        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            **kwargs
        )
