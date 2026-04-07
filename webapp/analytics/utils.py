from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def fetch_records(self, dimension, time_settings, page=1, per_page=100):
        """
        POST /analytics/calls/v1/accounts/{accountId}/records/fetch
        """
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }
        params = {"page": page, "perPage": per_page}
        # Ensure this returns the raw result or an empty dict, never None
        result = rc_api_call(f"{self.base_path}/records/fetch", method="POST", json=payload, params=params)
        return result if result is not None else {"data": []}
