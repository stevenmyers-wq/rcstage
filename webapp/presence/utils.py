from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        """Fetches all user extensions in the account."""
        endpoint = f"{self.base_path}/extension"
        params = {"type": ["User"], "perPage": 1000} # Get Users only
        
        users = []
        try:
            response = rc_api_call(endpoint, params=params)
            if response and 'records' in response:
                users.extend(response['records'])
            return users
        except Exception as e:
            raise Exception(f"Failed to fetch users: {str(e)}")

    def get_monitored_lines(self, extension_id):
        """
        GET /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/line
        Returns the BLF lines for a specific extension.
        """
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET")
        except Exception:
            # If the user has no lines or an error occurs, return an empty structure
            return {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        """
        PUT /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/line
        """
        payload = {"records": line_records}
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
