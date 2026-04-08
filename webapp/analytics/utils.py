import requests
import os
from webapp.rc_api import rc_api_call

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
            print(f"GRANTED SCOPES: {data.get('scope')}")
            return data.get("access_token")
        return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
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

    def delete_extension(self, extension_id):
        """
        OPERABILITY TEST: Deletes a specific extension.
        Endpoint: DELETE /restapi/v1.0/account/{accountId}/extension/{extensionId}
        """
        url = f"{self.base_url}/restapi/v1.0/account/{self.account_id}/extension/{extension_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        print(f"--- ATTEMPTING DELETE: Account={self.account_id} Ext={extension_id} ---")
        response = requests.delete(url, headers=headers)
        
        # DELETE often returns 204 (No Content), so we return status + body if exists
        try:
            body = response.json()
        except:
            body = {"message": "No JSON body returned (standard for 204 No Content)"}
            
        return {
            "status_code": response.status_code,
            "api_response": body,
            "url_attempted": url
        }
