import requests
import os

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges Employee token for Customer-scoped token.
    Loops through internal profiles to find one that allows the 'Analytics' scope.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    
    # We want Analytics, so we try reporting profiles first.
    # 'brd' is the ultimate fallback because we know it at least grants identity access.
    app_profiles = ["reporting", "analytics", "rcau", "brd"]
    
    last_token = None
    last_scopes = ""
    last_profile = ""

    for profile in app_profiles:
        payload = {"accountId": str(target_account_id), "appName": profile}
        print(f"--- BRIDGE ATTEMPT: AppName='{profile}' ---")
        
        try:
            response = requests.post(exchange_url, json=payload, headers=headers)
            if response.ok:
                data = response.json()
                scopes = data.get("scope", "")
                print(f"--- BRIDGE SUCCESS ({profile}): SCOPES={scopes} ---")
                
                last_token = data.get("access_token")
                last_scopes = scopes
                last_profile = profile
                
                # If we got the golden ticket (Analytics), stop looking and return it
                if "Analytics" in scopes:
                    return last_token, last_scopes, last_profile
            else:
                print(f"--- BRIDGE REJECTED ({profile}): {response.status_code} ---")
        except Exception as e:
            print(f"--- BRIDGE EXCEPTION ({profile}): {str(e)} ---")
            
    # Return the last successful token (usually 'brd') even if it lacks Analytics
    return last_token, last_scopes, last_profile

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_url = "https://platform.ringcentral.com"

    def get_account_identity_v2(self):
        """Proof of Identity via the V2 Endpoint."""
        url = f"{self.base_url}/restapi/v2/accounts/~"
        headers = {"Authorization": f"Bearer {self.token}"}
        res = requests.get(url, headers=headers)
        return res.json()

    def fetch_records(self, dimension, time_settings):
        """POST analytics query using the ~ ANL-102 bypass."""
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
