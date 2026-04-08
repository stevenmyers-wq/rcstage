from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    """
    def __init__(self, account_id):
        # account_id is mandatory for impersonation
        self.account_id = account_id
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_aggregation(self, grouping, time_settings, response_options, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/aggregation/fetch"""
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        params = {"page": kwargs.get('page', 1), "perPage": kwargs.get('per_page', 200)}
        return rc_api_call(f"{self.base_path}/aggregation/fetch", method="POST", json=payload, params=params)

    def fetch_timeline(self, interval, grouping, time_settings, response_options, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/timeline/fetch"""
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        params = {"interval": interval, "page": kwargs.get('page', 1), "perPage": kwargs.get('per_page', 20)}
        return rc_api_call(f"{self.base_path}/timeline/fetch", method="POST", json=payload, params=params)

    def fetch_records(self, dimension, time_settings, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        # Include optional filters if provided
        for key in ['callFilters', 'ids', 'searchString']:
            if kwargs.get(key):
                payload[key] = kwargs[key]
        
        params = {"page": kwargs.get('page', 1), "perPage": kwargs.get('per_page', 100)}
        return rc_api_call(f"{self.base_path}/records/fetch", method="POST", json=payload, params=params)
