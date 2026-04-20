from webapp.rc_api import rc_api_call

class RCPresenceManager:
    def __init__(self, account_id="~"):
        self.account_id = account_id
        self.base_path = f"/restapi/v1.0/account/{self.account_id}"

    # --- 1. Monitored Lines (BLF Keys) ---
    def get_monitored_lines(self, extension_id="~"):
        """
        GET /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/line
        Returns the list of BLF lines configured for the extension.
        """
        return rc_api_call(f"{self.base_path}/extension/{extensionId}/presence/line", method="GET")

    def update_monitored_lines(self, extension_id, line_records):
        """
        PUT /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/line
        Updates the list of BLF lines. 
        Note: The first two lines are always the user's own extension and cannot be changed.
        """
        payload = {"records": line_records}
        return rc_api_call(f"{self.base_path}/extension/{extensionId}/presence/line", method="PUT", json=payload)

    # --- 2. Presence Permissions ---
    def get_presence_permissions(self, extension_id="~"):
        """
        GET /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/permission
        Returns the list of extensions that are allowed to monitor this extension.
        """
        return rc_api_call(f"{self.base_path}/extension/{extensionId}/presence/permission", method="GET")

    def update_presence_permissions(self, extension_id, extension_ids):
        """
        PUT /restapi/v1.0/account/{accountId}/extension/{extensionId}/presence/permission
        Updates the list of extensions allowed to monitor this extension.
        """
        payload = {"extensions": [{"id": ext_id} for ext_id in extension_ids]}
        return rc_api_call(f"{self.base_path}/extension/{extensionId}/presence/permission", method="PUT", json=payload)
