from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    def get_all_users(self):
        """Fetches all user extensions in the account for the checkbox table."""
        endpoint = f"{self.base_path}/extension"
        # We fetch Users, but if you need to audit other types like Departments, add them here.
        params = {"type": ["User"], "perPage": 1000} 
        
        users = []
        try:
            response = rc_api_call(endpoint, method="GET", params=params)
            if response and 'records' in response:
                users.extend(response['records'])
            return users
        except Exception as e:
            raise Exception(f"Failed to fetch users: {str(e)}")

    def get_monitored_lines(self, extension_id):
        """Returns the BLF lines for a specific extension."""
        try:
            return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="GET")
        except Exception:
            # Return an empty structure if the user has no lines or an error occurs
            return {"records": []}

    def update_monitored_lines(self, extension_id, line_records):
        """Updates the array of BLF lines for the extension."""
        payload = {"records": line_records}
        return rc_api_call(f"{self.base_path}/extension/{extension_id}/presence/line", method="PUT", json=payload)
