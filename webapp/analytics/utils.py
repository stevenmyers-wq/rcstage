from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def get_super_admin_extension(self):
        """
        Searches the target account for the default Super Admin.
        Typically Extension 101 or the extension with the 'Admin' role.
        """
        # Query extensions for the target account
        # We look for extensionNumber '101' as the standard default
        endpoint = f"/restapi/v1.0/account/{self.account_id}/extension"
        params = {"extensionNumber": "101"} 
        
        res = rc_api_call(endpoint, token=self.token, params=params)
        
        if res and 'records' in res and len(res['records']) > 0:
            return res['records'][0]['id']
        
        # Fallback: Search for any extension with the 'Main' or 'Admin' status if 101 isn't found
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """POST /analytics/calls/v1/accounts/{accountId}/records/fetch"""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }

        # If we found the Super Admin, we apply them as a filter to the query
        # to ensure we are seeing data 'on behalf' of their routing.
        if admin_extension_id:
            payload["callFilters"] = {
                "extensionFilters": [
                    {"extensionId": str(admin_extension_id)}
                ]
            }

        return rc_api_call(
            f"{self.base_path}/records/fetch", 
            method='POST', 
            json=payload, 
            token=self.token, 
            **kwargs
        )
