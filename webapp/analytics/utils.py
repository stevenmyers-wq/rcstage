import requests
import os

def get_impersonation_token(employee_token, target_account_id):
    """Exchanges Employee token for Customer-scoped token using 'brd'."""
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
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
        self.base_url = "https://platform.ringcentral.com"

    def get_account_identity_v2(self):
        """Proof of Identity via V2."""
        url = f"{self.base_url}/restapi/v2/accounts/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        res = requests.get(url, headers=headers)
        return res.json()

    def fetch_records(self, dimension, time_settings):
        """
        POST analytics query.
        CRITICAL FIX: Used '~' in the path instead of self.account_id to resolve ANL-102 403 Error.
        """
        # The magic fix is right here: /accounts/~/records
        url = f"{self.base_url}/analytics/calls/v1/accounts/~/records/fetch"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }

        response = requests.post(url, headers=headers, json=payload)
        
        try:
            return response.json()
        except:
            return {"error": "Invalid API Response", "status": response.status_code, "raw": response.text}
