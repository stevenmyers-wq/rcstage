from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    Isolated from the global PKCE session via an explicit token.
    """
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_records(self, dimension, time_settings, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        if not self.token:
            return {"error": "SESSION_EXPIRED", "message": "No token found."}
            
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if kwargs.get('callFilters'):
            payload['callFilters'] = kwargs.get('callFilters')
        
        # Explicitly pass token to rc_api_call. 
        # We use return_response=True to handle API errors without crashing.
        response = rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token,
            return_response=True,
            **kwargs
        )
        
        if response is None:
            return {"error": "CONNECTION_ERROR", "message": "The RingCentral API could not be reached."}

        if not response.ok:
            try:
                return response.json() 
            except:
                return {"error": "API_ERROR", "message": response.text}
                
        return response.json()
