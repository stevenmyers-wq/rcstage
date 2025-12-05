import io
import concurrent.futures
import pandas as pd
import json

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
                # EXPANDED SEARCH: Include 'Department' (Call Queues) and 'Voicemail' (Msg Only)
                resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                    'status': ['Enabled', 'Disabled', 'NotActivated'], 
                    'type': ['User', 'Department', 'Voicemail'], 
                    'perPage': 1000, 
                    'page': page
                })
                
                # CRITICAL FIX: If the first page fails (e.g. 401 Unauthorized), 
                # raise an error immediately instead of returning an empty map.
                if resp.status_code != 200:
                    if page == 1:
                        raise Exception(f"API Error fetching extensions: {resp.status_code}")
                    else:
                        break
                
                data = resp.json()
                records = data.get('records', [])
                
                for record in records:
                    if 'extensionNumber' in record:
                        ext_map[str(record['extensionNumber'])] = str(record['id'])
                
                # Navigation check
                if not data.get('navigation') or not data['navigation'].get('nextPage'):
                    break
                
                page += 1
                
            except Exception as e:
                # Re-raise explicit exceptions
                if page == 1:
                    raise e
                break
                
        return ext_map

    def _fetch_single_setting(self, ext, token=None):
        """Fetch settings for a single user (helper for threading)."""
        from webapp.rc_api import rc
        
        try:
            # Pass token explicitly to the API call running in the thread
            resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings", token=token)
            
            if resp.status_code != 200:
                # Some extensions (like limited ones) might not have notification settings.
                # Return a basic error row or just skip? 
                # Better to return error row so user knows it was skipped.
                return {
                    'ExtensionNumber': ext.get('extensionNumber', ''),
                    'ExtensionName': f"{ext.get('name', 'Unknown')} (No Settings Found - {resp.status_code})"
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
            # EXPANDED SEARCH: Include 'Department' and 'Voicemail' here too
            resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                'status': ['Enabled', 'Disabled', 'NotActivated'], 
                'type': ['User', 'Department', 'Voicemail'], 
                'perPage': 1000, 
                'page': page
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
            logs.append(f"ℹ️ Debug: Successfully mapped {len(ext_map)} extensions.")
            
            if not ext_map:
                 logs.append("⚠️ Warning: No extensions found. Check your permissions or connection.")
        except Exception as e:
            return [f"❌ Failed to fetch extension list: {str(e)}"]

        try:
            df = pd.read_excel(file_storage)
        except Exception as e:
            return ["❌ Error reading Excel file."]

        # --- AUTO-CLEAN QUEUES START ---
        # Detect Call Queues - Log only, we will enforce in loop for safety.
        if 'ExtensionName' in df.columns:
             queue_mask = df['ExtensionName'].str.contains('Queue', case=False, na=False)
             if queue_mask.any():
                 count = queue_mask.sum()
                 logs.append(f"ℹ️ Auto-Fix: Detected {count} Call Queues. Enforcing strict compatibility in payload.")
        # --- AUTO-CLEAN QUEUES END ---

        # Validate Headers
        core_columns = ['ExtensionNumber']
        if not set(core_columns).issubset(df.columns):
            missing = list(set(core_columns) - set(df.columns))
            return [f"❌ Invalid Template. Missing core columns: {missing}"]

        for index, row in df.iterrows():
            if "Example" in str(row.get('ExtensionName', '')):
                continue

            ext_num = str(row['ExtensionNumber']).strip().replace('.0', '')
            ext_name = str(row.get('ExtensionName', '')).lower()
            
            # Determine if this specific row is a Queue
            is_queue = 'queue' in ext_name
            
            if not ext_num or ext_num.lower() == 'nan':
                continue
                
            ext_id = ext_map.get(ext_num)
            if not ext_id:
                logs.append(f"⚠️ Ext {ext_num}: Not found in account. Skipping.")
                continue

            try:
                # 1. Fetch CURRENT settings first to preserve other fields
                url = f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings"
                get_resp = rc.get(url, token=token)
                
                if get_resp.status_code != 200:
                    logs.append(f"❌ Ext {ext_num}: Failed to fetch current settings ({get_resp.status_code})")
                    continue
                
                settings = get_resp.json()

                # 2. Prepare Helper Functions
                def get_bool_or_none(col_name):
                    if col_name not in row: return None
                    val = row[col_name]
                    if pd.isna(val) or str(val).strip() == '': return None
                    s = str(val).lower().strip()
                    if s in ['true', '1', '1.0', 'yes', 't', 'on']: return True
                    if s in ['false', '0', '0.0', 'no', 'f', 'off']: return False
                    return None

                # 3. Modify the settings object
                # IMPORTANT: Always populate the main email list. 
                # This overrides "Notify Manager" logic for simple settings.
                if 'EmailAddresses' in row and not pd.isna(row['EmailAddresses']):
                      email_raw = str(row['EmailAddresses'])
                      email_list = [e.strip() for e in email_raw.split(',') if e.strip()]
                      settings["emailAddresses"] = email_list
                else:
                      email_list = settings.get("emailAddresses", [])

                val = get_bool_or_none('AdvancedMode')
                # Explicitly capture user intent
                user_wants_advanced = val
                if val is not None: settings["advancedMode"] = val
                
                val = get_bool_or_none('IncludeSms')
                if val is not None: settings["includeSmsRecipients"] = val
                
                is_advanced = settings.get("advancedMode", False)

                categories = {
                    'voicemails': ('Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead'),
                    'missedCalls': ('MissedCalls_Email', 'MissedCalls_SMS', None),
                    'inboundTexts': ('InboundTexts_Email', 'InboundTexts_SMS', None),
                    'inboundFaxes': ('InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead'),
                    'outboundFaxes': ('OutboundFaxes_Email', 'OutboundFaxes_SMS', None)
                }

                for cat, cols in categories.items():
                    if cat not in settings: settings[cat] = {}
                    
                    val_email = get_bool_or_none(cols[0])
                    if val_email is not None: settings[cat]["notifyByEmail"] = val_email
                    
                    val_sms = get_bool_or_none(cols[1])
                    if val_sms is not None: settings[cat]["notifyBySms"] = val_sms
                    
                    if cols[2]:
                        val_mark = get_bool_or_none(cols[2])
                        # Check existence before setting
                        if val_mark is not None and 'markAsRead' in settings[cat]:
                            settings[cat]["markAsRead"] = val_mark
                            
                            if val_mark is True and 'includeAttachment' in settings[cat]:
                                settings[cat]["includeAttachment"] = True
                    
                    # Advanced Mode Population
                    notify_email = settings[cat].get("notifyByEmail", False)
                    notify_sms = settings[cat].get("notifyBySms", False)

                    if is_advanced:
                         if notify_email:
                             settings[cat]["advancedEmailAddresses"] = email_list
                         if cat == 'missedCalls' and notify_sms:
                             settings[cat]["advancedSmsEmailAddresses"] = email_list

                # --- QUEUE SAFETY OVERRIDE START ---
                # Call Queues throw "400 InvalidParameter: emailRecipients" if you try to set 
                # custom emails for features that are locked to Manager (Texts/MissedCalls), 
                # or features that don't exist (OutboundFax).
                # To fix this, we strictly CLEAN the payload for Queues.
                if is_queue:
                    # 1. Force Advanced Mode OFF
                    settings["advancedMode"] = False
                    user_wants_advanced = False 
                    
                    # 2. DELETE potentially conflicting keys entirely.
                    # We do NOT send updates for texts/missed calls for queues to avoid validation errors.
                    keys_to_remove = ['outboundFaxes', 'inboundTexts', 'missedCalls', 'includeSmsRecipients']
                    for k in keys_to_remove:
                        if k in settings:
                            del settings[k]
                
                # Global cleanup: If Advanced Mode is FALSE (either naturally or forced by queue),
                # we MUST remove lingering "advancedEmailAddresses" keys from all categories.
                if not settings.get("advancedMode", False):
                    for cat in categories:
                        if cat in settings:
                            settings[cat].pop('advancedEmailAddresses', None)
                            settings[cat].pop('advancedSmsEmailAddresses', None)
                # --- QUEUE SAFETY OVERRIDE END ---

                # 4. PUT with Retry Logic (Handle MarkAsRead & SMS Validation Errors)
                attempt = 0
                max_attempts = 4 # Increased to handle potential multiple failures sequentially
                
                while attempt < max_attempts:
                    resp = rc.put(url, json=settings, token=token)
                    
                    if resp.status_code == 200:
                        # VERIFY: Did the setting actually stick?
                        final_settings = resp.json()
                        final_adv = final_settings.get('advancedMode', False)
                        
                        if user_wants_advanced is True and final_adv is False:
                            logs.append(f"⚠️ Ext {ext_num}: Update success, BUT API ignored 'AdvancedMode' (reverted to False).")
                        else:
                            logs.append(f"✅ Ext {ext_num}: Updated")
                        break
                    
                    err_text = resp.text
                    fixed_something = False
                    
                    # Fix 1: MarkAsRead
                    if resp.status_code == 400 and "markAsRead" in err_text:
                         logs.append(f"⚠️ Ext {ext_num}: Retrying with 'markAsRead=False' (API Rejected Value)...")
                         for cat in categories:
                           if cat in settings and 'markAsRead' in settings[cat]:
                               settings[cat]['markAsRead'] = False
                         fixed_something = True

                    # Fix 2: IncludeSmsRecipients (CMN-451)
                    if resp.status_code == 400 and "includeSmsRecipients" in err_text:
                         logs.append(f"⚠️ Ext {ext_num}: Retrying without 'includeSmsRecipients' (API Forbidden Update)...")
                         # Remove the key entirely
                         if 'includeSmsRecipients' in settings:
                             del settings['includeSmsRecipients']
                         fixed_something = True

                    if fixed_something:
                        attempt += 1
                        continue # Restart loop with modified settings
                    
                    # Real failure
                    logs.append(f"❌ Ext {ext_num}: Error {resp.status_code} - {resp.text}")
                    break

            except Exception as e:
                logs.append(f"❌ Ext {ext_num}: {str(e)}")

        return logs
