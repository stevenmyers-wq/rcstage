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

    print(f"--- BRIDGE ATTEMPT: Target='{target_account_id}' ---")
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            data = response.json()
            print(f"--- BRIDGE SUCCESS: OWNER={data.get('owner_id')} ---")
            return data.get("access_token"), data.get("scope", "")
        else:
            print(f"--- BRIDGE FAILED: {response.status_code} {response.text} ---")
            return None, ""
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None, ""

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_url = "https://platform.ringcentral.com"

    def get_account_identity_v2(self):
        """Proof of Identity via V2. Returns status code so we can catch 401s."""
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
            # If 401, we flag it so the UI knows the token expired
            if response.status_code == 401:
                body['_status'] = 401
            return body
        except:
            return {"error": "Invalid API Response", "status": response.status_code, "raw": response.text}
