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
            return {"error": "AUTH_REQUIRED", "message": "Analytics token missing."}
            
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        
        # CRITICAL: We pass token=self.token so rc_api_call ignores the session
        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, # Manual token override
            return_response=False, 
            **kwargs
        )
