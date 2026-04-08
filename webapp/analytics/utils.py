from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    Uses an explicit token to support the independent analytics auth flow.
    """
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_records(self, dimension, time_settings, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        if not self.token:
            return {"error": "No analytics token provided to client."}
            
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if kwargs.get('callFilters'):
            payload['callFilters'] = kwargs.get('callFilters')
        
        # Explicitly pass the isolated token override to your rc_api_call utility
        # Using raise_error=True so our route can catch and JSONify the error
        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token,
            raise_error=True,
            **kwargs
        )
