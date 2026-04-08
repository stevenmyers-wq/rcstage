import requests
import os

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges an Employee SSO token for a Customer-scoped session token.
    Uses 'brd' (Build) profile.
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

    print(f"--- BRIDGE ATTEMPT: Target='{target_account_id}' ---")
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            data = response.json()
            print(f"--- BRIDGE SUCCESS: OWNER={data.get('owner_id')} ---")
            print(f"GRANTED SCOPES: {data.get('scope')}")
            return data.get("access_token")
        else:
            print(f"--- BRIDGE FAILED: {response.status_code} {response.text} ---")
            return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None

class RCOperabilityTest:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_url = "https://platform.ringcentral.com"

    def delete_extension(self, extension_id):
        """
        DESTRUCTIVE TEST: Deletes a specific extension.
        API: DELETE /restapi/v1.0/account/{accountId}/extension/{extensionId}
        """
        url = f"{self.base_url}/restapi/v1.0/account/{self.account_id}/extension/{extension_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }
        
        print(f"--- EXECUTING DELETE: Account={self.account_id} Ext={extension_id} ---")
        response = requests.delete(url, headers=headers)
        
        # RingCentral DELETE usually returns 204 No Content on success.
        # We capture the status and any body (errors) returned.
        try:
            body = response.json()
        except:
            body = {"info": "No JSON body (Standard for 204 Success)"}
            
        return {
            "status_code": response.status_code,
            "api_response": body,
            "url_attempted": url
        }
