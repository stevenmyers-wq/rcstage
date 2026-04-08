import requests
import os
from webapp.rc_api import rc_api_call

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges the Employee SSO token for a Customer-scoped session token.
    This resolves the 404 (Account Not Found) and 403 (Forbidden) errors.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  # Passing the employee token in this header
    }
    payload = {
        "accountId": str(target_account_id),
        "appName": "brd" # Identifier from the successful internal tool
    }

    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            # The returned token context is now the target customer account
            return response.json().get("access_token")
        else:
            print(f"Exchange Bridge Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Token Exchange Exception: {str(e)}")
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
        With the impersonated token, this will no longer return a 404.
        """
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        res = rc_api_call(endpoint, token=self.token)
        
        # 'operator' is the RC technical term for the primary admin/extension
        if res and 'operator' in res:
            return res['operator'].get('id')
        
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """POST analytics query targeting the identified admin extension."""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }

        # Impersonation: Restrict query to data 'seen' by the Super Admin
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
