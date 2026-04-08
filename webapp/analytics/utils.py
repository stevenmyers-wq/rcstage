import requests
import os
from webapp.rc_api import rc_api_call

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges Employee token for Customer-scoped token.
    Uses 'brd' profile from the working HAR file.
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

    def get_full_account_info(self):
        """
        FETCHING VIA V2 ENDPOINT
        Endpoint: /restapi/v2/accounts/~
        Using '~' ensures we see exactly who the token 'thinks' it is.
        """
        url = "https://platform.ringcentral.com/restapi/v2/accounts/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        response = requests.get(url, headers=headers)
        try:
            # We return the whole thing so you can inspect the v2 structure
            return response.json()
        except:
            return {
                "error": "Invalid JSON response", 
                "status": response.status_code, 
                "body": response.text,
                "url_queried": url
            }
