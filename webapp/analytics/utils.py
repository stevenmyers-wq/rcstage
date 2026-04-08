from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API.
    """
    def __init__(self, account_id):
        # account_id is required; no default to "~"
        self.account_id = account_id
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_aggregation(self, grouping, time_settings, response_options, **kwargs):
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        params = {"page": kwargs.get('page', 1), "perPage": kwargs.get('per_page', 200)}
        return rc_api_call(f"{self.base_path}/aggregation/fetch", method="POST", json=payload, params=params)

    def fetch_records(self, dimension, time_settings, **kwargs):
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        # Optional filters
        for key in ['callFilters', 'ids', 'searchString']:
            if kwargs.get(key):
                payload[key] = kwargs[key]
        
        params = {"page": kwargs.get('page', 1), "perPage": kwargs.get('per_page', 100)}
        return rc_api_call(f"{self.base_path}/records/fetch", method="POST", json=payload, params=params)
