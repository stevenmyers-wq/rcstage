from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Python Client for the RingCentral Business Analytics API (Beta).
    Based on OpenAPI Spec version 1.0.44.
    """
    
    def __init__(self, account_id="~"):
        """
        Initializes the Analytics client.
        :param account_id: Internal RC account ID, defaults to "~" (current session account).
        """
        self.account_id = account_id
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_aggregation(self, grouping, time_settings, response_options, call_filters=None, page=1, per_page=200):
        """POST /analytics/calls/v1/accounts/{accountId}/aggregation/fetch"""
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        if call_filters:
            payload["callFilters"] = call_filters
            
        params = {"page": page, "perPage": per_page}
        return rc_api_call(f"{self.base_path}/aggregation/fetch", method="POST", json=payload, params=params)

    def fetch_timeline(self, interval, grouping, time_settings, response_options, call_filters=None, page=1, per_page=20):
        """POST /analytics/calls/v1/accounts/{accountId}/timeline/fetch"""
        payload = {
            "grouping": grouping,
            "timeSettings": time_settings,
            "responseOptions": response_options
        }
        if call_filters:
            payload["callFilters"] = call_filters
            
        params = {"interval": interval, "page": page, "perPage": per_page}
        return rc_api_call(f"{self.base_path}/timeline/fetch", method="POST", json=payload, params=params)

    def fetch_records(self, dimension, time_settings, call_filters=None, ids=None, search_string=None, page=1, per_page=100):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        if call_filters: payload["callFilters"] = call_filters
        if ids: payload["ids"] = ids
        if search_string: payload["searchString"] = search_string
        
        params = {"page": page, "perPage": per_page}
        return rc_api_call(f"{self.base_path}/records/fetch", method="POST", json=payload, params=params)
