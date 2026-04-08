from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    Uses an explicit token to remain independent of the global PKCE session.
    """
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_records(self, dimension, time_settings, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        if not self.token:
            return {"error": "AUTH_REQUIRED", "message": "No analytics token found."}
            
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        
        # We use return_response=True so we can manually handle 403/500 errors
        # without triggering the global Flask exception handler.
        response = rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            return_response=True,
            **kwargs
        )
        
        if response is None:
            return {"error": "NETWORK_ERROR", "message": "Could not connect to RingCentral."}

        if not response.ok:
            try:
                # Try to get the specific error from RC (e.g., 'Forbidden' or 'Parameter Invalid')
                return response.json()
            except:
                return {"error": "API_ERROR", "message": f"Status {response.status_code}: {response.text[:100]}"}
                
        return response.json()
