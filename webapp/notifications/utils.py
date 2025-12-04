import io
import concurrent.futures

class NotificationManager:
    def __init__(self):
        # Excel Headers
        self.columns = [
            'ExtensionId', 'ExtensionName', 'ExtensionNumber', 
            'EmailAddresses', 'IncludeSms', 'AdvancedMode',
            'Voicemails_Email', 'Voicemails_SMS', 
            'MissedCalls_Email', 'MissedCalls_SMS',
            'InboundTexts_Email', 'InboundTexts_SMS'
        ]

    def _get_enabled_extensions(self):
        """Fetch all enabled User extensions."""
        # Import inside function to prevent circular dependency on startup
        from webapp.rc_api import rc
        
        users = []
        page = 1
        while True:
            resp = rc.get('/restapi/v1.0/account/~/extension', params={
                'status': 'Enabled', 'type': 'User', 'perPage': 1000, 'page': page
            })
            if resp.status_code != 200:
                raise Exception(f"Failed to fetch extensions: {resp.text}")

            data = resp.json()
            users.extend(data['records'])
            
            if not data.get('navigation') or not data['navigation'].get('nextPage'):
                break
            page += 1
        return users

    def _fetch_single_setting(self, ext):
        """Fetch settings for a single user (helper for threading)."""
        from webapp.rc_api import rc
        
        try:
            resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings")
            if resp.status_code != 200:
                return {
                    'ExtensionId': str(ext['id']),
                    'ExtensionName': ext.get('name'),
                    'ExtensionNumber': f"Error: {resp.status_code}"
                }

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
                'ExtensionName': "Error",
                'ExtensionNumber': str(e)
            }

    def generate_audit_report(self):
        """Scans all users and builds an Excel file in memory."""
        import pandas as pd
        
        extensions = self._get_enabled_extensions()
        results = []

        # Threaded fetching (Max 5 workers to be safe with rate limits)
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
        """Creates an empty template with correct headers."""
        import pandas as pd
        
        df = pd.DataFrame(columns=self.columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
        return output

    def process_update_file(self, file_storage):
        """Reads Excel and performs API updates."""
        import pandas as pd
        from webapp.rc_api import rc
        
        logs = []
        try:
            df = pd.read_excel(file_storage)
            df.fillna('', inplace=True)
        except Exception as e:
            return ["❌ Error reading Excel file. Ensure it is a valid .xlsx file."]

        # Validate headers
        if not set(self.columns).issubset(df.columns):
            missing = list(set(self.columns) - set(df.columns))
            return [f"❌ Invalid Template. Missing columns: {missing}"]

        for index, row in df.iterrows():
            ext_id = str(row['ExtensionId']).strip()
            if not ext_id:
                continue

            try:
                # Construct Payload based on API specs
                payload = {
                    "emailAddresses": [e.strip() for e in str(row['EmailAddresses']).split(',') if e.strip()],
                    "advancedMode": bool(row['AdvancedMode']),
                    "includeSmsRecipients": bool(row['IncludeSms']),
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

                url = f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings"
                
                # Perform Update
                resp = rc.put(url, json=payload)

                if resp.status_code == 200:
                    logs.append(f"✅ Ext {row['ExtensionNumber']}: Settings Updated")
                else:
                    logs.append(f"❌ Ext {row['ExtensionNumber']}: API Error {resp.status_code} - {resp.text}")

            except Exception as e:
                logs.append(f"❌ Ext {row.get('ExtensionNumber', 'Unknown')}: {str(e)}")

        return logs
