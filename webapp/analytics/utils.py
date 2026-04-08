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
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if kwargs.get('callFilters'):
            payload['callFilters'] = kwargs.get('callFilters')
        
        # We pass the token explicitly to the existing rc_api_call utility
        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            **kwargs
        )
