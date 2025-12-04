import pandas as pd
import io
import concurrent.futures
import json
from ringcentral_client import rc  # Assuming you have a central RC client instance

class NotificationManager:
    def __init__(self):
        # Flattened Excel Headers
        self.columns = [
            'ExtensionId', 'ExtensionName', 'ExtensionNumber', 
            'EmailAddresses', 'IncludeSms', 'AdvancedMode',
            'Voicemails_Email', 'Voicemails_SMS', 
            'MissedCalls_Email', 'MissedCalls_SMS',
            'InboundTexts_Email', 'InboundTexts_SMS'
        ]

    def _get_enabled_extensions(self):
        """Helper: Fetch all enabled User extensions."""
        users = []
        page = 1
        while True:
            resp = rc.get('/restapi/v1.0/account/~/extension', params={
                'status': 'Enabled', 'type': 'User', 'perPage': 1000, 'page': page
            })
            data = resp.json()
            users.extend(data['records'])
            if not data.get('navigation') or not data['navigation'].get('nextPage'):
                break
            page += 1
        return users

    def _fetch_single_setting(self, ext):
        """Helper: Fetch settings for one user (for threading)."""
        try:
            resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings")
            settings = resp.json()
            
            # Extract basic lists
            emails = ", ".join(settings.get('emailAddresses', []))
            
            # Extract Advanced Flags
            voicemails = settings.get('voicemails', {})
            missed_calls = settings.get('missedCalls', {})
            inbound_texts = settings.get('inboundTexts', {})

            return {
                'ExtensionId': str(ext['id']),
                'ExtensionName': ext.get('name', 'Unknown'),
                'ExtensionNumber': ext.get('extensionNumber', ''),
                'EmailAddresses': emails,
                'IncludeSms': settings.get('includeSmsRecipients', False),
                'AdvancedMode': settings.get('advancedMode', False),
                'Voicemails_Email': voicemails.get('notifyByEmail', False),
                'Voicemails_SMS': voicemails.get('notifyBySms', False),
                'MissedCalls_Email': missed_calls.get('notifyByEmail', False),
                'MissedCalls_SMS': missed_calls.get('notifyBySms', False),
                'InboundTexts_Email': inbound_texts.get('notifyByEmail', False),
                'InboundTexts_SMS': inbound_texts.get('notifyBySms', False)
            }
        except Exception as e:
            return {
                'ExtensionId': str(ext['id']),
                'ExtensionName': f"Error: {str(e)}",
                'ExtensionNumber': ext.get('extensionNumber', '')
            }

    def generate_audit_report(self):
        """Scans all users and builds Excel."""
        extensions = self._get_enabled_extensions()
        results = []

        # Threaded fetching for speed (API rate limits apply)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_ext = {executor.submit(self._fetch_single_setting, ext): ext for ext in extensions}
            for future in concurrent.futures.as_completed(future_to_ext):
                results.append(future.result())

        df = pd.DataFrame(results, columns=self.columns)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Notifications')
        return output

    def generate_blank_template(self):
        """Creates an empty template with headers."""
        df = pd.DataFrame(columns=self.columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
        return output

    def process_update_file(self, file_storage):
        """Reads Excel and pushes updates to RC."""
        logs = []
        try:
            df = pd.read_excel(file_storage)
            df.fillna('', inplace=True)
        except Exception as e:
            return ["❌ Error reading Excel file. Check format."]

        # Validate headers
        if not set(self.columns).issubset(df.columns):
            missing = list(set(self.columns) - set(df.columns))
            return [f"❌ Invalid Template. Missing columns: {missing}"]

        for index, row in df.iterrows():
            ext_id = str(row['ExtensionId']).strip()
            if not ext_id:
                continue

            try:
                # Construct Payload
                payload = {
                    "emailAddresses": [e.strip() for e in str(row['EmailAddresses']).split(',') if e.strip()],
                    "advancedMode": bool(row['AdvancedMode']),
                    # Advanced Settings
                    "voicemails": {
                        "notifyByEmail": bool(row['Voicemails_Email']),
                        "notifyBySms": bool(row['Voicemails_SMS'])
                    },
                    "missedCalls": {
                        "notifyByEmail": bool(row['MissedCalls_Email']),
                        "notifyBySms": bool(row['MissedCalls_SMS'])
                    },
                    "inboundTexts": {
                        "notifyByEmail": bool(row['InboundTexts_Email']),
                        "notifyBySms": bool(row['InboundTexts_SMS'])
                    }
                }

                # API Update
                url = f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings"
                resp = rc.put(url, json=payload)

                if resp.status_code == 200:
                    logs.append(f"✅ Ext {row['ExtensionNumber']}: Settings Updated")
                else:
                    logs.append(f"❌ Ext {row['ExtensionNumber']}: API Error {resp.status_code} - {resp.text}")

            except Exception as e:
                logs.append(f"❌ Ext {row.get('ExtensionNumber', 'Unknown')}: {str(e)}")

        return logs
