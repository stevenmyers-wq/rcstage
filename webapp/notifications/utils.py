import io
import concurrent.futures

class NotificationManager:
    def __init__(self):
        # Full list of columns for Audit and Template
        self.columns = [
            'ExtensionNumber',      # Key for lookup
            'ExtensionName',        # For reference only
            'EmailAddresses',       # Comma separated
            'IncludeSms',           # TRUE/FALSE
            'AdvancedMode',         # TRUE/FALSE
            'Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead',
            'MissedCalls_Email', 'MissedCalls_SMS',
            'InboundTexts_Email', 'InboundTexts_SMS',
            'InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead',
            'OutboundFaxes_Email', 'OutboundFaxes_SMS'
        ]

    def _get_extension_map(self, token=None):
        """
        Fetches all extensions and returns a dictionary:
        { '101': '12345678', '102': '87654321' }
        """
        from webapp.rc_api import rc
        
        ext_map = {}
        page = 1
        while True:
            try:
                # Pass token explicitly
                resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                    'status': ['Enabled', 'Disabled', 'NotActivated'], 
                    'type': 'User', 
                    'perPage': 1000, 
                    'page': page
                })
                if resp.status_code != 200:
                    break
                
                data = resp.json()
                for record in data.get('records', []):
                    if 'extensionNumber' in record:
                        ext_map[str(record['extensionNumber'])] = str(record['id'])
                
                if not data.get('navigation') or not data['navigation'].get('nextPage'):
                    break
                page += 1
            except Exception:
                break
        return ext_map

    def _fetch_single_setting(self, ext, token=None):
        """Fetch settings for a single user (helper for threading)."""
        from webapp.rc_api import rc
        
        try:
            # Pass token explicitly to the API call running in the thread
            resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings", token=token)
            
            if resp.status_code != 200:
                return {
                    'ExtensionNumber': ext.get('extensionNumber', ''),
                    'ExtensionName': f"Error: {resp.status_code}"
                }

            settings = resp.json()
            
            # Extract basic lists
            emails = ", ".join(settings.get('emailAddresses', []))
            
            # Extract Settings
            voicemails = settings.get('voicemails', {})
            missed_calls = settings.get('missedCalls', {})
            inbound_texts = settings.get('inboundTexts', {})
            inbound_faxes = settings.get('inboundFaxes', {})
            outbound_faxes = settings.get('outboundFaxes', {})

            return {
                'ExtensionNumber': ext.get('extensionNumber', ''),
                'ExtensionName': ext.get('name', 'Unknown'),
                'EmailAddresses': emails,
                'IncludeSms': settings.get('includeSmsRecipients', False),
                'AdvancedMode': settings.get('advancedMode', False),
                
                'Voicemails_Email': voicemails.get('notifyByEmail', False),
                'Voicemails_SMS': voicemails.get('notifyBySms', False),
                'Voicemails_MarkAsRead': voicemails.get('markAsRead', False),
                
                'MissedCalls_Email': missed_calls.get('notifyByEmail', False),
                'MissedCalls_SMS': missed_calls.get('notifyBySms', False),
                
                'InboundTexts_Email': inbound_texts.get('notifyByEmail', False),
                'InboundTexts_SMS': inbound_texts.get('notifyBySms', False),
                
                'InboundFaxes_Email': inbound_faxes.get('notifyByEmail', False),
                'InboundFaxes_SMS': inbound_faxes.get('notifyBySms', False),
                'InboundFaxes_MarkAsRead': inbound_faxes.get('markAsRead', False),
                
                'OutboundFaxes_Email': outbound_faxes.get('notifyByEmail', False),
                'OutboundFaxes_SMS': outbound_faxes.get('notifyBySms', False)
            }
        except Exception as e:
            return {
                'ExtensionNumber': ext.get('extensionNumber', ''),
                'ExtensionName': f"Error: {str(e)}"
            }

    def generate_audit_report(self, token=None):
        """Scans all users and builds an Excel file."""
        import pandas as pd
        from webapp.rc_api import rc
        
        extensions = []
        page = 1
        while True:
            # Pass token explicitly
            resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                'status': 'Enabled', 'type': 'User', 'perPage': 1000, 'page': page
            })
            if resp.status_code != 200: break
            data = resp.json()
            extensions.extend(data['records'])
            if not data.get('navigation', {}).get('nextPage'): break
            page += 1

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Pass the token into the threaded function
            future_to_ext = {executor.submit(self._fetch_single_setting, ext, token=token): ext for ext in extensions}
            for future in concurrent.futures.as_completed(future_to_ext):
                results.append(future.result())

        df = pd.DataFrame(results, columns=self.columns)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Notifications')
            worksheet = writer.sheets['Notifications']
            worksheet.set_column(2, 2, 40) 
        return output

    def generate_blank_template(self):
        """Creates a template with an example row."""
        import pandas as pd
        
        example_data = {
            'ExtensionNumber': ['101', 'Start data here'],
            'ExtensionName': ['John Doe (Example)', ''],
            'EmailAddresses': ['john@company.com', ''],
            'IncludeSms': [True, ''],
            'AdvancedMode': [True, ''],
            
            'Voicemails_Email': [True, ''],
            'Voicemails_SMS': [False, ''],
            'Voicemails_MarkAsRead': [True, ''],
            
            'MissedCalls_Email': [True, ''],
            'MissedCalls_SMS': [False, ''],
            
            'InboundTexts_Email': [False, ''],
            'InboundTexts_SMS': [True, ''],
            
            'InboundFaxes_Email': [True, ''],
            'InboundFaxes_SMS': [False, ''],
            'InboundFaxes_MarkAsRead': [True, ''],
            
            'OutboundFaxes_Email': [True, ''],
            'OutboundFaxes_SMS': [False, '']
        }

        df = pd.DataFrame(example_data, columns=self.columns)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
            
            instruction_df = pd.DataFrame({
                'Field': ['ExtensionNumber', 'EmailAddresses', 'Flags', 'MarkAsRead'],
                'Instruction': [
                    'The Extension Number (e.g. 101). Logic will look up the ID automatically.',
                    'Comma separated list of emails. Overwrites existing list.',
                    'Use TRUE or FALSE.',
                    'Optional. Set TRUE to mark message as read upon notification.'
                ]
            })
            instruction_df.to_excel(writer, index=False, sheet_name='Instructions')

        return output

    def process_update_file(self, file_storage, token=None):
        """Reads Excel, maps Ext Number to ID, and updates settings."""
        import pandas as pd
        from webapp.rc_api import rc
        
        logs = []
        
        try:
            # Pass token for mapping
            ext_map = self._get_extension_map(token=token)
        except Exception as e:
            return [f"❌ Failed to fetch extension list for mapping: {str(e)}"]

        try:
            df = pd.read_excel(file_storage)
            df.fillna('', inplace=True)
        except Exception as e:
            return ["❌ Error reading Excel file."]

        # Validate Headers (Check for core columns only, allowing optional columns to be missing)
        core_columns = ['ExtensionNumber', 'EmailAddresses']
        if not set(core_columns).issubset(df.columns):
            missing = list(set(core_columns) - set(df.columns))
            return [f"❌ Invalid Template. Missing core columns: {missing}"]

        for index, row in df.iterrows():
            if "Example" in str(row.get('ExtensionName', '')):
                continue

            ext_num = str(row['ExtensionNumber']).strip().replace('.0', '')
            
            if not ext_num:
                continue
                
            ext_id = ext_map.get(ext_num)
            if not ext_id:
                logs.append(f"⚠️ Ext {ext_num}: Not found in account. Skipping.")
                continue

            try:
                # 1. Fetch CURRENT settings first to preserve other fields (like includeAttachment)
                url = f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings"
                get_resp = rc.get(url, token=token)
                
                if get_resp.status_code != 200:
                    logs.append(f"❌ Ext {ext_num}: Failed to fetch current settings ({get_resp.status_code})")
                    continue
                
                settings = get_resp.json()

                # 2. Prepare Helper Functions
                def get_bool(val):
                    return str(val).lower() in ['true', '1', 'yes', 't']
                
                # Safe Get: Returns False if column is missing or cell is blank
                def safe_get_bool(col_name):
                    if col_name in row:
                        return get_bool(row[col_name])
                    return False

                # 3. Modify the settings object (Merging Excel data into existing settings)
                
                # Email Addresses
                settings["emailAddresses"] = [e.strip() for e in str(row['EmailAddresses']).split(',') if e.strip()]
                
                # Top Level Switches
                settings["advancedMode"] = safe_get_bool('AdvancedMode')
                settings["includeSmsRecipients"] = safe_get_bool('IncludeSms')
                
                # Nested Objects - ensure keys exist, then update
                if 'voicemails' not in settings: settings['voicemails'] = {}
                settings["voicemails"]["notifyByEmail"] = safe_get_bool('Voicemails_Email')
                settings["voicemails"]["notifyBySms"] = safe_get_bool('Voicemails_SMS')
                settings["voicemails"]["markAsRead"] = safe_get_bool('Voicemails_MarkAsRead')

                if 'missedCalls' not in settings: settings['missedCalls'] = {}
                settings["missedCalls"]["notifyByEmail"] = safe_get_bool('MissedCalls_Email')
                settings["missedCalls"]["notifyBySms"] = safe_get_bool('MissedCalls_SMS')

                if 'inboundTexts' not in settings: settings['inboundTexts'] = {}
                settings["inboundTexts"]["notifyByEmail"] = safe_get_bool('InboundTexts_Email')
                settings["inboundTexts"]["notifyBySms"] = safe_get_bool('InboundTexts_SMS')

                if 'inboundFaxes' not in settings: settings['inboundFaxes'] = {}
                settings["inboundFaxes"]["notifyByEmail"] = safe_get_bool('InboundFaxes_Email')
                settings["inboundFaxes"]["notifyBySms"] = safe_get_bool('InboundFaxes_SMS')
                settings["inboundFaxes"]["markAsRead"] = safe_get_bool('InboundFaxes_MarkAsRead')

                if 'outboundFaxes' not in settings: settings['outboundFaxes'] = {}
                settings["outboundFaxes"]["notifyByEmail"] = safe_get_bool('OutboundFaxes_Email')
                settings["outboundFaxes"]["notifyBySms"] = safe_get_bool('OutboundFaxes_SMS')

                # 4. PUT the updated object back
                # url is already defined above
                
                resp = rc.put(url, json=settings, token=token)

                if resp.status_code == 200:
                    logs.append(f"✅ Ext {ext_num}: Updated")
                else:
                    logs.append(f"❌ Ext {ext_num}: Error {resp.status_code} - {resp.text}")

            except Exception as e:
                logs.append(f"❌ Ext {ext_num}: {str(e)}")

        return logs
