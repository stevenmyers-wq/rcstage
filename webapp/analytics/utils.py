from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    Uses an isolated token to avoid interfering with the global PKCE session.
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
        
        # We ensure perPage is set to 100 for a better data spread
        params = kwargs.get('params', {})
        if 'perPage' not in params:
            params['perPage'] = 100

        # CRITICAL: We pass the isolated token directly to rc_api_call
        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            params=params,
            **kwargs
        )
