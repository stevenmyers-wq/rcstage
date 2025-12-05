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
            'DisableManagerNotifications',  # NEW: TRUE/FALSE - explicitly disable manager mode
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
            
            # Check if manager notifications are enabled
            has_manager_notify = (
                voicemails.get('includeManagers', False) or 
                inbound_faxes.get('includeManagers', False)
            )

            return {
                'ExtensionNumber': ext.get('extensionNumber', ''),
                'ExtensionName': ext.get('name', 'Unknown'),
                'EmailAddresses': emails,
                'IncludeSms': settings.get('includeSmsRecipients', False),
                'AdvancedMode': settings.get('advancedMode', False),
                'DisableManagerNotifications': not has_manager_notify,  # Show current state
                
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
            'DisableManagerNotifications': [True, ''],
            
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
                'Field': ['ExtensionNumber', 'EmailAddresses', 'DisableManagerNotifications', 'Flags', 'MarkAsRead'],
                'Instruction': [
                    'The Extension Number (e.g. 101). Logic will look up the ID automatically.',
                    'Comma separated list of emails. Overwrites existing list.',
                    'Set TRUE to switch from "Notify Manager" mode to specified emails. Required for Call Queues.',
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

                # 3. Handle Email Addresses and Manager Notification Override
                if 'EmailAddresses' in row and not pd.isna(row['EmailAddresses']):
                    email_raw = str(row['EmailAddresses'])
                    email_list = [e.strip() for e in email_raw.split(',') if e.strip()]
                    settings["emailAddresses"] = email_list
                    
                    # CRITICAL FIX: Check if user wants to disable manager notifications
                    disable_manager = get_bool_or_none('DisableManagerNotifications')
                    
                    # If explicitly set to TRUE, or if emails are provided for a Queue, disable manager mode
                    if disable_manager is True or (is_queue and email_list):
                        logs.append(f"ℹ️ Ext {ext_num}: Disabling manager notifications, switching to specified emails...")
                        
                        # Remove emailRecipients at ROOT level (this is key!)
                        settings.pop('emailRecipients', None)
                        
                        # For queues: REMOVE includeManagers entirely (they don't support it)
                        # For users: Set to False
                        for cat in ['voicemails', 'inboundFaxes', 'missedCalls']:
                            if cat not in settings:
                                settings[cat] = {}
                            
                            if is_queue:
                                # Queues: DELETE the field entirely
                                settings[cat].pop('includeManagers', None)
                            else:
                                # Users: Set to False
                                settings[cat]["includeManagers"] = False
                            
                            # Remove manager-specific email recipients from category
                            settings[cat].pop('emailRecipients', None)
                            
                            # Ensure notifyByEmail is enabled if we're providing emails
                            if email_list and get_bool_or_none(f'{cat.capitalize()}_Email') is None:
                                settings[cat]["notifyByEmail"] = True
                else:
                    email_list = settings.get("emailAddresses", [])

                # 4. Handle Advanced Mode
                val = get_bool_or_none('AdvancedMode')
                user_wants_advanced = val
                if val is not None: 
                    settings["advancedMode"] = val
                
                val = get_bool_or_none('IncludeSms')
                if val is not None: 
                    settings["includeSmsRecipients"] = val
                
                is_advanced = settings.get("advancedMode", False)
                
                # IMMEDIATE CLEANUP: If Advanced Mode is False, remove advanced keys now
                if not is_advanced:
                    for key in list(settings.keys()):
                        if isinstance(settings[key], dict):
                            settings[key].pop('advancedEmailAddresses', None)
                            settings[key].pop('advancedSmsEmailAddresses', None)
                
                # IMMEDIATE QUEUE CLEANUP: Remove unsupported categories and root fields BEFORE processing
                if is_queue:
                    # Remove entire unsupported categories from fetched settings
                    settings.pop('outboundFaxes', None)
                    settings.pop('inboundTexts', None)
                    settings.pop('emailRecipients', None)
                    settings.pop('includeSmsRecipients', None)
                    
                    # CRITICAL: Remove root-level includeManagers (this is the manager notification toggle!)
                    settings.pop('includeManagers', None)
                    
                    # Remove forbidden fields from all existing categories
                    for cat in list(settings.keys()):
                        if isinstance(settings[cat], dict):
                            settings[cat].pop('markAsRead', None)
                            settings[cat].pop('includeAttachment', None)
                            settings[cat].pop('includeManagers', None)
                            settings[cat].pop('emailRecipients', None)
                            settings[cat].pop('advancedEmailAddresses', None)
                            settings[cat].pop('advancedSmsEmailAddresses', None)
                            settings[cat].pop('includeTranscription', None)  # Also forbidden for queues

                # 5. Process Category-Specific Settings
                categories = {
                    'voicemails': ('Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead'),
                    'missedCalls': ('MissedCalls_Email', 'MissedCalls_SMS', None),
                    'inboundTexts': ('InboundTexts_Email', 'InboundTexts_SMS', None),
                    'inboundFaxes': ('InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead'),
                    'outboundFaxes': ('OutboundFaxes_Email', 'OutboundFaxes_SMS', None)
                }

                for cat, cols in categories.items():
                    # Skip categories that queues don't support - DON'T PROCESS THEM AT ALL
                    if is_queue and cat in ['outboundFaxes', 'inboundTexts']:
                        continue  # Don't even look at these categories
                    
                    if cat not in settings: 
                        settings[cat] = {}
                    
                    val_email = get_bool_or_none(cols[0])
                    if val_email is not None: 
                        settings[cat]["notifyByEmail"] = val_email
                    
                    val_sms = get_bool_or_none(cols[1])
                    if val_sms is not None: 
                        settings[cat]["notifyBySms"] = val_sms
                    
                    # Handle markAsRead and includeAttachment
                    if is_queue:
                        # For queues: REMOVE these fields entirely (they don't support them)
                        settings[cat].pop('markAsRead', None)
                        settings[cat].pop('includeAttachment', None)
                    elif cols[2]:
                        # For non-queues: Only set if user provided a value
                        val_mark = get_bool_or_none(cols[2])
                        if val_mark is not None:
                            settings[cat]["markAsRead"] = val_mark
                            
                            if val_mark is True:
                                settings[cat]["includeAttachment"] = True
                        else:
                            # User didn't provide a value - keep whatever was fetched, unless it's false
                            # Actually, let's be safe and remove markAsRead if not explicitly set
                            pass
                    
                    # Advanced Mode Population (only if advanced is enabled and not a queue)
                    if is_advanced and not is_queue:
                        notify_email = settings[cat].get("notifyByEmail", False)
                        notify_sms = settings[cat].get("notifyBySms", False)

                        if notify_email:
                            settings[cat]["advancedEmailAddresses"] = email_list
                        if cat == 'missedCalls' and notify_sms:
                            settings[cat]["advancedSmsEmailAddresses"] = email_list
                    else:
                        # Remove advanced fields if not in advanced mode or if queue
                        settings[cat].pop('advancedEmailAddresses', None)
                        settings[cat].pop('advancedSmsEmailAddresses', None)

                # 6. FINAL QUEUE SAFETY CLEANUP (runs AFTER all processing)
                # NOTE: This is now redundant but kept as a safety net
                if is_queue:
                    # Remove unsupported root-level fields one last time
                    root_forbidden = ['outboundFaxes', 'inboundTexts', 'emailRecipients', 
                                     'includeSmsRecipients', 'includeManagers']
                    for field in root_forbidden:
                        settings.pop(field, None)
                    
                    # Clean all categories one last time
                    category_forbidden = ['markAsRead', 'includeAttachment', 'includeManagers', 
                                         'emailRecipients', 'advancedEmailAddresses', 
                                         'advancedSmsEmailAddresses', 'includeTranscription']
                    for key, value in list(settings.items()):
                        if isinstance(value, dict):
                            for field in category_forbidden:
                                value.pop(field, None)
                    
                    # Debug: Log what categories remain
                    remaining_cats = [k for k in settings.keys() if isinstance(settings[k], dict)]
                    logs.append(f"ℹ️ Ext {ext_num}: After final cleanup, categories: {', '.join(remaining_cats)}")

                # 7. PUT with Retry Logic
                attempt = 0
                max_attempts = 4
                
                # FINAL AGGRESSIVE CLEANUP RIGHT BEFORE PUT (for queues)
                if is_queue:
                    # Remove unsupported root-level fields
                    root_forbidden = ['outboundFaxes', 'inboundTexts', 'emailRecipients', 
                                     'includeSmsRecipients', 'includeManagers']
                    for field in root_forbidden:
                        settings.pop(field, None)
                    
                    # Aggressively clean all dict values
                    category_forbidden = ['markAsRead', 'includeAttachment', 'includeManagers', 
                                         'emailRecipients', 'advancedEmailAddresses', 
                                         'advancedSmsEmailAddresses', 'includeTranscription']
                    for key, value in list(settings.items()):
                        if isinstance(value, dict):
                            for field in category_forbidden:
                                value.pop(field, None)
                
                # Debug: Show what we're about to send for queues
                if is_queue and attempt == 0:
                    import json as json_lib
                    
                    problem_fields = []
                    
                    # Check root level
                    if 'outboundFaxes' in settings:
                        problem_fields.append('outboundFaxes')
                    if 'inboundTexts' in settings:
                        problem_fields.append('inboundTexts')
                    
                    # Check all categories
                    for cat_key, cat_value in settings.items():
                        if isinstance(cat_value, dict):
                            for field in ['markAsRead', 'includeAttachment']:
                                if field in cat_value:
                                    problem_fields.append(f"{cat_key}.{field}")
                    
                    if problem_fields:
                        logs.append(f"🚨 Ext {ext_num}: CRITICAL - Payload STILL has forbidden fields after cleanup: {', '.join(problem_fields)}")
                    
                    # Log the FULL JSON payload so we can see everything
                    payload_str = json_lib.dumps(settings, indent=2)
                    logs.append(f"📤 Ext {ext_num}: Full payload ({len(payload_str)} chars): {payload_str}")
                
                while attempt < max_attempts:
                    # Create a deep copy for the API call
                    import copy
                    payload = copy.deepcopy(settings)
                    
                    # For queues: Include the forbidden categories but disable them
                    if is_queue:
                        # For queues, manager notifications are controlled differently:
                        # Just provide emailAddresses and DON'T send includeManagers at all
                        payload.pop('includeManagers', None)
                        payload.pop('emailRecipients', None)
                        
                        # Set forbidden categories to disabled state (not removed)
                        if 'outboundFaxes' not in payload:
                            payload['outboundFaxes'] = {}
                        payload['outboundFaxes']['notifyByEmail'] = False
                        payload['outboundFaxes']['notifyBySms'] = False
                        
                        if 'inboundTexts' not in payload:
                            payload['inboundTexts'] = {}
                        payload['inboundTexts']['notifyByEmail'] = False
                        payload['inboundTexts']['notifyBySms'] = False
                        
                        # For supported categories, set forbidden sub-fields to False
                        for cat in ['voicemails', 'inboundFaxes', 'missedCalls']:
                            if cat in payload and isinstance(payload[cat], dict):
                                payload[cat]['markAsRead'] = False
                                payload[cat]['includeAttachment'] = False
                                # Remove category-level manager fields
                                payload[cat].pop('includeManagers', None)
                                payload[cat].pop('emailRecipients', None)
                    
                    resp = rc.put(url, json=payload, token=token)
                    
                    if resp.status_code == 200:
                        # VERIFY: Did the setting actually stick?
                        final_settings = resp.json()
                        final_adv = final_settings.get('advancedMode', False)
                        
                        if user_wants_advanced is True and final_adv is False:
                            logs.append(f"⚠️ Ext {ext_num}: Update success, BUT API ignored 'AdvancedMode' (reverted to False).")
                        else:
                            logs.append(f"✅ Ext {ext_num}: Updated successfully")
                        break
                    
                    err_text = resp.text
                    err_json = None
                    try:
                        err_json = resp.json()
                    except:
                        pass
                    
                    fields_removed = []
                    
                    # Parse all errors from the response and collect them
                    if err_json and 'errors' in err_json:
                        for error in err_json.get('errors', []):
                            param = error.get('parameterName', '')
                            
                            # Handle nested parameters like "voicemails.markAsRead"
                            if '.' in param:
                                cat_name, field_name = param.split('.', 1)
                                if cat_name in settings and isinstance(settings[cat_name], dict):
                                    if field_name in settings[cat_name]:
                                        settings[cat_name].pop(field_name, None)
                                        fields_removed.append(f"{cat_name}.{field_name}")
                            
                            # Handle top-level invalid categories
                            elif param in settings:
                                settings.pop(param, None)
                                fields_removed.append(param)
                            
                            # Handle specific known issues
                            elif 'includeManagers' in param:
                                for cat in list(settings.keys()):
                                    if isinstance(settings[cat], dict) and 'includeManagers' in settings[cat]:
                                        settings[cat].pop('includeManagers', None)
                                        fields_removed.append(f"{cat}.includeManagers")
                            
                            elif 'emailRecipients' in param:
                                if 'emailRecipients' in settings:
                                    settings.pop('emailRecipients', None)
                                    fields_removed.append('emailRecipients')
                                for cat in list(settings.keys()):
                                    if isinstance(settings[cat], dict) and 'emailRecipients' in settings[cat]:
                                        settings[cat].pop('emailRecipients', None)
                                        fields_removed.append(f"{cat}.emailRecipients")
                        
                        # If we removed any fields, log once and retry
                        if fields_removed:
                            logs.append(f"⚠️ Ext {ext_num}: Removed {len(fields_removed)} invalid fields: {', '.join(fields_removed[:3])}{'...' if len(fields_removed) > 3 else ''}")
                            attempt += 1
                            if attempt >= max_attempts:
                                logs.append(f"❌ Ext {ext_num}: Max retry attempts reached - {resp.text}")
                                break
                            continue
                    
                    # Fallback: old logic for text-based errors
                    if resp.status_code == 400 and "includeSmsRecipients" in err_text:
                        logs.append(f"⚠️ Ext {ext_num}: Removing 'includeSmsRecipients'...")
                        settings.pop('includeSmsRecipients', None)
                        attempt += 1
                        continue
                    
                    # Real failure - no fix possible
                    logs.append(f"❌ Ext {ext_num}: Error {resp.status_code} - {resp.text}")
                    break

            except Exception as e:
                logs.append(f"❌ Ext {ext_num}: {str(e)}")

        return logs
