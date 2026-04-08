from webapp.rc_api import rc_api_call

class RCBusinessAnalytics:
    """
    Client for the Analytics API.
    Identifies the Super Admin via the 'operator' field in Account info.
    """
    def __init__(self, account_id, token):
        self.account_id = account_id
        self.token = token
        self.base_path = f"/analytics/calls/v1/accounts/{self.account_id}"

    def get_super_admin_extension(self):
        """
        Resolves the Operator extension ID for the account.
        Requires 'ReadAccounts' scope.
        """
        endpoint = f"/restapi/v1.0/account/{self.account_id}"
        # We call the main account endpoint
        res = rc_api_call(endpoint, token=self.token)
        
        # 'operator' is the RC technical term for the primary admin/extension
        if res and 'operator' in res:
            return res['operator'].get('id')
        
        return None

    def fetch_records(self, dimension, time_settings, admin_extension_id=None, **kwargs):
        """POST analytics query targeting the identified admin extension."""
        payload = {
            "dimension": dimension,
            "timeSettings": time_settings
        }

        # Impersonation: Restrict query to data 'seen' by the Super Admin
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
