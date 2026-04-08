from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    Uses the underlying rc_api_call for authenticated requests.
    """
    def __init__(self, account_id):
        # account_id is mandatory; we no longer default to "~"
        self.account_id = account_id
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_aggregation(self, grouping, time_settings, response_options, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/aggregation/fetch"""
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        # Passing json=payload into kwargs for rc_api_call to handle
        return rc_api_call(
            f"{self.base_path}/aggregation/fetch", 
            method='POST', 
            json=payload, 
            **kwargs
        )

    def fetch_records(self, dimension, time_settings, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        # Include optional filters if provided in the call
        if kwargs.get('callFilters'):
            payload['callFilters'] = kwargs.get('callFilters')
        
        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            **kwargs
        )
