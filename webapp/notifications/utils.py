import io
import os
import json
import uuid
import threading
import concurrent.futures
import pandas as pd
import time
import random
from datetime import datetime

# Global directory for temporary job files
JOB_DIR = os.path.join('static', 'jobs')
REPORT_DIR = os.path.join('static', 'reports')

# Ensure directories exist
os.makedirs(JOB_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

class NotificationManager:
    def __init__(self):
        self.columns = [
            'ExtensionNumber', 'ExtensionName', 'EmailAddresses', 
            'IncludeSms', 'AdvancedMode', 'DisableManagerNotifications',
            'Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead',
            'MissedCalls_Email', 'MissedCalls_SMS',
            'InboundTexts_Email', 'InboundTexts_SMS',
            'InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead',
            'OutboundFaxes_Email', 'OutboundFaxes_SMS'
        ]
        
        # GLOBAL RATE LIMITER STATE
        self._pause_until = 0
        self._lock = threading.Lock()

    # --- JOB MANAGEMENT HELPERS ---
    
    def start_audit_job(self, token):
        """Starts the Audit background thread."""
        job_id = str(uuid.uuid4())
        self._update_job_status(job_id, "running", 0, "Initializing Audit...")
        
        thread = threading.Thread(target=self._run_audit_background, args=(job_id, token))
        thread.daemon = True
        thread.start()
        return job_id

    def start_update_job(self, df, token):
        """Starts the Update background thread."""
        job_id = str(uuid.uuid4())
        self._update_job_status(job_id, "running", 0, "Initializing Update...")
        
        # Pass the DataFrame to the thread
        thread = threading.Thread(target=self._run_update_background, args=(job_id, df, token))
        thread.daemon = True
        thread.start()
        return job_id

    def get_job_status(self, job_id):
        """Reads the status JSON file."""
        status_file = os.path.join(JOB_DIR, f"{job_id}.json")
        if not os.path.exists(status_file):
            return {"status": "error", "message": "Job not found"}
        
        with open(status_file, 'r') as f:
            return json.load(f)

    def _update_job_status(self, job_id, status, percent, message, filename=None, logs=None):
        """Writes status to disk."""
        data = {
            "status": status,
            "percent": percent,
            "message": message,
            "filename": filename,
            "logs": logs, # Optional list of strings
            "updated_at": datetime.now().isoformat()
        }
        with open(os.path.join(JOB_DIR, f"{job_id}.json"), 'w') as f:
            json.dump(data, f)

    # --- BACKGROUND WORKERS ---

    def _run_audit_background(self, job_id, token):
        try:
            from webapp.rc_api import rc
            
            # 1. Fetch Extensions
            self._update_job_status(job_id, "running", 5, "Fetching extension list...")
            extensions = []
            page = 1
            while True:
                try:
                    resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                        'status': ['Enabled', 'Disabled', 'NotActivated'], 
                        'type': ['User', 'Department', 'Voicemail'], 
                        'perPage': 1000, 
                        'page': page
                    })
                    if resp.status_code != 200: break
                    data = resp.json()
                    extensions.extend(data.get('records', []))
                    if not data.get('navigation', {}).get('nextPage'): break
                    page += 1
                except: break

            total_ext = len(extensions)
            if total_ext == 0:
                self._update_job_status(job_id, "error", 100, "No extensions found.")
                return

            # 2. Fetch Settings
            results = []
            completed_count = 0
            self._pause_until = 0 # Reset limiter
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_ext = {executor.submit(self._fetch_single_setting, ext, token=token): ext for ext in extensions}
                
                for future in concurrent.futures.as_completed(future_to_ext):
                    results.append(future.result())
                    completed_count += 1
                    
                    if completed_count % 10 == 0:
                        percent = 10 + int((completed_count / total_ext) * 80)
                        self._update_job_status(job_id, "running", percent, f"Processed {completed_count}/{total_ext}...")

            # 3. Save Excel
            self._update_job_status(job_id, "running", 95, "Saving Excel file...")
            df = pd.DataFrame(results, columns=self.columns)
            filename = f"Notification_Audit_{job_id}.xlsx"
            filepath = os.path.join(REPORT_DIR, filename)
            
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Notifications')
                worksheet = writer.sheets['Notifications']
                worksheet.set_column(0, 0, 15)
                worksheet.set_column(2, 2, 40)
            
            self._update_job_status(job_id, "complete", 100, "Done", filename=filename)

        except Exception as e:
            self._update_job_status(job_id, "error", 100, f"System Error: {str(e)}")

    def _run_update_background(self, job_id, df, token):
        try:
            from webapp.rc_api import rc
            
            # 1. Map Extensions
            self._update_job_status(job_id, "running", 5, "Mapping extensions...")
            try:
                ext_map = self._get_extension_map(token=token)
            except Exception as e:
                self._update_job_status(job_id, "error", 100, f"Failed to map extensions: {str(e)}")
                return

            logs = []
            total_rows = len(df)
            processed_rows = 0
            self._pause_until = 0 # Reset limiter

            # 2. Process Rows
            for index, row in df.iterrows():
                processed_rows += 1
                
                # Update Status every 5 rows
                if processed_rows % 5 == 0 or processed_rows == 1:
                    percent = 10 + int((processed_rows / total_rows) * 85)
                    self._update_job_status(job_id, "running", percent, f"Updating row {processed_rows}/{total_rows}...")

                if "Example" in str(row.get('ExtensionName', '')): continue

                ext_num = str(row['ExtensionNumber']).strip().replace('.0', '')
                ext_id = ext_map.get(ext_num)
                
                if not ext_id:
                    logs.append(f"⚠️ Ext {ext_num}: Not found. Skipping.")
                    continue

                # --- RATE LIMIT CHECK ---
                wait_needed = self._pause_until - time.time()
                if wait_needed > 0: time.sleep(wait_needed)
                time.sleep(random.uniform(0.01, 0.05)) # Micro-delay

                try:
                    # Fetch Current
                    get_resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", token=token)
                    
                    # Handle 429 on GET
                    if get_resp.status_code == 429:
                        self._handle_429(get_resp)
                        logs.append(f"⚠️ Ext {ext_num}: Rate Limit Hit (GET). Skipping row.")
                        continue
                    if get_resp.status_code != 200:
                        logs.append(f"❌ Ext {ext_num}: API Error {get_resp.status_code}")
                        continue
                    
                    settings = get_resp.json()
                    
                    # --- APPLY CHANGES ---
                    # 1. Emails
                    if 'EmailAddresses' in row and not pd.isna(row['EmailAddresses']):
                        raw = str(row['EmailAddresses'])
                        settings['emailAddresses'] = [e.strip() for e in raw.split(',') if e.strip()]
                        settings.pop('emailRecipients', None)

                    # 2. Manager Mode
                    if 'DisableManagerNotifications' in row:
                        val = str(row['DisableManagerNotifications']).lower()
                        if val in ['true', '1', 'yes']:
                            for cat in ['voicemails', 'inboundFaxes', 'missedCalls']:
                                if cat in settings: settings[cat]['includeManagers'] = False

                    # 3. Simple Toggles
                    if 'IncludeSms' in row and not pd.isna(row['IncludeSms']):
                        settings['includeSmsRecipients'] = bool(row['IncludeSms'])
                    if 'AdvancedMode' in row and not pd.isna(row['AdvancedMode']):
                        settings['advancedMode'] = bool(row['AdvancedMode'])

                    # 4. Categories
                    cats = {
                        'voicemails': ('Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead'),
                        'missedCalls': ('MissedCalls_Email', 'MissedCalls_SMS', None),
                        'inboundTexts': ('InboundTexts_Email', 'InboundTexts_SMS', None),
                        'inboundFaxes': ('InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead'),
                        'outboundFaxes': ('OutboundFaxes_Email', 'OutboundFaxes_SMS', None)
                    }
                    for cat, cols in cats.items():
                        if cat not in settings: settings[cat] = {}
                        if cols[0] in row and not pd.isna(row[cols[0]]): settings[cat]['notifyByEmail'] = bool(row[cols[0]])
                        if cols[1] in row and not pd.isna(row[cols[1]]): settings[cat]['notifyBySms'] = bool(row[cols[1]])
                        if cols[2] and cols[2] in row and not pd.isna(row[cols[2]]): settings[cat]['markAsRead'] = bool(row[cols[2]])

                    # 5. PUSH UPDATE
                    put_resp = rc.put(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", json=settings, token=token)
                    
                    if put_resp.status_code == 200:
                        logs.append(f"✅ Ext {ext_num}: Updated")
                    elif put_resp.status_code == 429:
                        self._handle_429(put_resp)
                        logs.append(f"⚠️ Ext {ext_num}: Rate Limit Hit (PUT). Not updated.")
                    else:
                        logs.append(f"❌ Ext {ext_num}: Failed {put_resp.text}")

                except Exception as e:
                    logs.append(f"❌ Ext {ext_num}: {str(e)}")

            # 3. Finish
            self._update_job_status(job_id, "complete", 100, "Update Completed", logs=logs)

        except Exception as e:
            self._update_job_status(job_id, "error", 100, f"System Error: {str(e)}")

    def _handle_429(self, resp):
        """Updates global pause timer."""
        try:
            retry_after = int(resp.headers.get('Retry-After', 5))
        except:
            retry_after = 5
        retry_after = min(retry_after, 60)
        
        with self._lock:
            new_pause = time.time() + retry_after + 1
            if new_pause > self._pause_until:
                self._pause_until = new_pause

    def _get_extension_map(self, token=None):
        """Helper to get ext map."""
        from webapp.rc_api import rc
        ext_map = {}
        page = 1
        while True:
            resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                'status': ['Enabled', 'Disabled', 'NotActivated'], 
                'type': ['User', 'Department', 'Voicemail'], 
                'perPage': 1000, 'page': page
            })
            if resp.status_code != 200: break
            data = resp.json()
            for r in data.get('records', []):
                if 'extensionNumber' in r: ext_map[str(r['extensionNumber'])] = str(r['id'])
            if not data.get('navigation', {}).get('nextPage'): break
            page += 1
        return ext_map

    def _fetch_single_setting(self, ext, token=None):
        """Thread worker for Audit."""
        from webapp.rc_api import rc
        max_retries = 10
        attempt = 0
        while attempt < max_retries:
            wait_needed = self._pause_until - time.time()
            if wait_needed > 0: time.sleep(wait_needed)
            time.sleep(random.uniform(0.01, 0.05))

            try:
                url = f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings"
                resp = rc.get(url, token=token)
                
                if resp.status_code == 200:
                    s = resp.json()
                    vm = s.get('voicemails', {})
                    fax = s.get('inboundFaxes', {})
                    mgr = vm.get('includeManagers', False) or fax.get('includeManagers', False)
                    return {
                        'ExtensionNumber': ext.get('extensionNumber', ''),
                        'ExtensionName': ext.get('name', 'Unknown'),
                        'EmailAddresses': ", ".join(s.get('emailAddresses', [])),
                        'IncludeSms': s.get('includeSmsRecipients', False),
                        'AdvancedMode': s.get('advancedMode', False),
                        'DisableManagerNotifications': not mgr,
                        'Voicemails_Email': vm.get('notifyByEmail', False),
                        'Voicemails_SMS': vm.get('notifyBySms', False),
                        'Voicemails_MarkAsRead': vm.get('markAsRead', False),
                        'MissedCalls_Email': s.get('missedCalls', {}).get('notifyByEmail', False),
                        'MissedCalls_SMS': s.get('missedCalls', {}).get('notifyBySms', False),
                        'InboundTexts_Email': s.get('inboundTexts', {}).get('notifyByEmail', False),
                        'InboundTexts_SMS': s.get('inboundTexts', {}).get('notifyBySms', False),
                        'InboundFaxes_Email': fax.get('notifyByEmail', False),
                        'InboundFaxes_SMS': fax.get('notifyBySms', False),
                        'InboundFaxes_MarkAsRead': fax.get('markAsRead', False),
                        'OutboundFaxes_Email': s.get('outboundFaxes', {}).get('notifyByEmail', False),
                        'OutboundFaxes_SMS': s.get('outboundFaxes', {}).get('notifyBySms', False)
                    }
                elif resp.status_code == 429:
                    attempt += 1
                    self._handle_429(resp)
                    continue
                else:
                    return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Error {resp.status_code}"}
            except Exception as e:
                return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Err: {str(e)}"}
        return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': "Failed: Rate Limit Exceeded"}

    def generate_blank_template(self):
        """Creates template."""
        import pandas as pd
        df = pd.DataFrame(columns=self.columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
        return output
