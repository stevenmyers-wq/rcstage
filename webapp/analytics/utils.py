import requests
import os

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges Employee token for Customer-scoped token.
    Uses your custom App Name to force the bridge to grant the Analytics scope.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    
    # Trying your specific App Names that own the Analytics scope
    app_names = ["SM RC PS", "SM AU PS", "brd"]
    
    last_token = None
    last_scopes = ""
    last_profile = ""

    for app_name in app_names:
        payload = {"accountId": str(target_account_id), "appName": app_name}
        print(f"--- BRIDGE ATTEMPT: AppName='{app_name}' ---")
        
        try:
            response = requests.post(exchange_url, json=payload, headers=headers)
            if response.ok:
                data = response.json()
                scopes = data.get("scope", "")
                print(f"--- BRIDGE SUCCESS ({app_name}): SCOPES={scopes} ---")
                
                last_token = data.get("access_token")
                last_scopes = scopes
                last_profile = app_name
                
                # If we successfully pulled the Analytics scope, break the loop!
                if "Analytics" in scopes:
                    return last_token, last_scopes, last_profile
            else:
                print(f"--- BRIDGE REJECTED ({app_name}): {response.status_code} ---")
        except Exception as e:
            print(f"--- BRIDGE EXCEPTION ({app_name}): {str(e)} ---")
            
    # Return whatever worked last, even if it failed the scope check
    return last_token, last_scopes, last_profile

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_url = "https://platform.ringcentral.com"

    def get_account_identity_v2(self):
        """Proof of Identity via V2. Returns status code to catch 401s."""
        url = f"{self.base_url}/restapi/v2/accounts/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        res = requests.get(url, headers=headers)
        return res.status_code, res.json()

    def fetch_records(self, dimension, time_settings):
        """POST analytics query using the ~ path bypass (Fixes ANL-102)."""
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
            body = response.json()
            if response.status_code == 401:
                body['_status'] = 401
            return body
        except:
            return {"error": "Invalid API Response", "status": response.status_code, "raw": response.text}
