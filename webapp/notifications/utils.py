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
        # These variables coordinate all threads to stop them from spamming
        # when a single thread hits a limit.
        self._pause_until = 0
        self._lock = threading.Lock()

    # --- JOB MANAGEMENT HELPERS ---
    
    def start_audit_job(self, token):
        """Starts a background thread and returns the Job ID."""
        job_id = str(uuid.uuid4())
        self._update_job_status(job_id, "running", 0, "Initializing...")
        
        thread = threading.Thread(target=self._run_audit_background, args=(job_id, token))
        thread.daemon = True
        thread.start()
        
        return job_id

    def get_job_status(self, job_id):
        """Reads the status JSON file for a given job."""
        status_file = os.path.join(JOB_DIR, f"{job_id}.json")
        if not os.path.exists(status_file):
            return {"status": "error", "message": "Job not found"}
        
        with open(status_file, 'r') as f:
            return json.load(f)

    def _update_job_status(self, job_id, status, percent, message, filename=None):
        """Writes status to disk."""
        data = {
            "status": status,
            "percent": percent,
            "message": message,
            "filename": filename,
            "updated_at": datetime.now().isoformat()
        }
        with open(os.path.join(JOB_DIR, f"{job_id}.json"), 'w') as f:
            json.dump(data, f)

    # --- CORE LOGIC ---

    def _run_audit_background(self, job_id, token):
        """The actual logic running in a separate thread."""
        try:
            from webapp.rc_api import rc
            
            # 1. Fetch Extensions
            self._update_job_status(job_id, "running", 5, "Fetching extension list...")
            
            extensions = []
            page = 1
            while True:
                try:
                    # Fetching ALL statuses to ensure we don't miss anything
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
                except:
                    break

            total_ext = len(extensions)
            if total_ext == 0:
                self._update_job_status(job_id, "error", 100, "No extensions found in account.")
                return

            # 2. Fetch Settings (Parallel with Coordinated Rate Limiting)
            results = []
            completed_count = 0
            
            # Reset global limiter
            self._pause_until = 0
            
            # Increased to 10 workers for speed, relying on Global Limiter to prevent crashes
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
                worksheet.set_column(0, 0, 15) # Ext Num
                worksheet.set_column(1, 1, 30) # Name
                worksheet.set_column(2, 2, 40) # Emails
            
            # 4. Finish
            self._update_job_status(job_id, "complete", 100, "Done", filename=filename)

        except Exception as e:
            self._update_job_status(job_id, "error", 100, f"System Error: {str(e)}")

    def _fetch_single_setting(self, ext, token=None):
        """Fetches a single extension with Global Coordinated Rate Limiting."""
        from webapp.rc_api import rc
        
        max_retries = 10  # Increased retries since we handle them smarter now
        attempt = 0
        
        while attempt < max_retries:
            # 1. GLOBAL CHECK: Is the API "Red Light" on?
            # If another thread hit a limit, we ALL wait here.
            wait_needed = self._pause_until - time.time()
            if wait_needed > 0:
                time.sleep(wait_needed)
            
            try:
                # 2. MICRO-DELAY: Prevent "Burst" traffic (10 threads hitting at exact same ms)
                time.sleep(random.uniform(0.01, 0.05))
                
                url = f"/restapi/v1.0/account/~/extension/{ext['id']}/notification-settings"
                resp = rc.get(url, token=token)
                
                # --- SUCCESS ---
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
                
                # --- RATE LIMIT HIT (429) ---
                elif resp.status_code == 429:
                    attempt += 1
                    
                    # Get wait time from header (Default 5s, Max 60s)
                    try:
                        retry_after = int(resp.headers.get('Retry-After', 5))
                    except:
                        retry_after = 5
                        
                    # Cap at 60s to prevent hanging forever
                    retry_after = min(retry_after, 60)
                    
                    # SET GLOBAL RED LIGHT
                    # Only one thread needs to set this. The lock ensures we don't overwrite a longer wait.
                    with self._lock:
                        new_pause = time.time() + retry_after + 1 # Add 1s buffer
                        if new_pause > self._pause_until:
                            self._pause_until = new_pause
                            # Optional: print(f"🛑 Rate Limit Hit! Pausing all threads for {retry_after}s")

                    # This thread loops back and will sleep at step #1
                    continue

                # --- OTHER ERRORS ---
                else:
                    return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Error {resp.status_code}"}
            
            except Exception as e:
                return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Err: {str(e)}"}
                
        return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': "Failed: Rate Limit Exceeded"}

    def generate_blank_template(self):
        """Creates an Excel template."""
        import pandas as pd
        
        example_data = {
            'ExtensionNumber': ['101'], 'ExtensionName': ['John Doe'],
            'EmailAddresses': ['john@example.com'], 'IncludeSms': [True],
            'AdvancedMode': [True], 'DisableManagerNotifications': [True],
            'Voicemails_Email': [True], 'Voicemails_SMS': [False], 'Voicemails_MarkAsRead': [True],
            'MissedCalls_Email': [True], 'MissedCalls_SMS': [False],
            'InboundTexts_Email': [False], 'InboundTexts_SMS': [True],
            'InboundFaxes_Email': [True], 'InboundFaxes_SMS': [False], 'InboundFaxes_MarkAsRead': [True],
            'OutboundFaxes_Email': [True], 'OutboundFaxes_SMS': [False]
        }
        
        df = pd.DataFrame(example_data, columns=self.columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
        return output

    def process_update_file(self, file_storage, token=None):
        """Reads Excel/CSV upload and updates settings."""
        from webapp.rc_api import rc
        
        logs = []
        try:
            ext_map = self._get_extension_map(token=token)
            if not ext_map: logs.append("⚠️ No extensions found.")
        except Exception as e:
            return [f"❌ Failed to fetch extension list: {str(e)}"]

        try:
            if file_storage.filename.endswith('.csv'):
                df = pd.read_csv(file_storage)
            else:
                df = pd.read_excel(file_storage)
        except Exception:
            return ["❌ Error reading file. Ensure it is valid Excel or CSV."]
        
        # Simple Validation
        if 'ExtensionNumber' not in df.columns:
            return ["❌ Invalid Template. Missing 'ExtensionNumber' column."]

        for index, row in df.iterrows():
            if "Example" in str(row.get('ExtensionName', '')): continue

            ext_num = str(row['ExtensionNumber']).strip().replace('.0', '')
            ext_id = ext_map.get(ext_num)
            
            if not ext_id:
                logs.append(f"⚠️ Ext {ext_num}: Not found. Skipping.")
                continue

            try:
                # Fetch Current (Needed to preserve unchecked categories)
                get_resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", token=token)
                if get_resp.status_code != 200:
                    logs.append(f"❌ Ext {ext_num}: API Error {get_resp.status_code}")
                    continue
                
                settings = get_resp.json()
                
                # --- APPLY UPDATES ---
                # 1. Emails
                if 'EmailAddresses' in row and not pd.isna(row['EmailAddresses']):
                    raw_emails = str(row['EmailAddresses'])
                    settings['emailAddresses'] = [e.strip() for e in raw_emails.split(',') if e.strip()]
                    settings.pop('emailRecipients', None) # Clear legacy

                # 2. Manager Mode Disable
                if 'DisableManagerNotifications' in row:
                    val = row['DisableManagerNotifications']
                    if str(val).lower() in ['true', '1', 'yes']:
                        # Force disable manager notifications in all cats
                        for cat in ['voicemails', 'inboundFaxes', 'missedCalls']:
                            if cat in settings: settings[cat]['includeManagers'] = False

                # 3. Categories
                cats = {
                    'voicemails': ('Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead'),
                    'missedCalls': ('MissedCalls_Email', 'MissedCalls_SMS', None),
                    'inboundTexts': ('InboundTexts_Email', 'InboundTexts_SMS', None),
                    'inboundFaxes': ('InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead'),
                    'outboundFaxes': ('OutboundFaxes_Email', 'OutboundFaxes_SMS', None)
                }
                
                for cat, cols in cats.items():
                    if cat not in settings: settings[cat] = {}
                    
                    if cols[0] in row and not pd.isna(row[cols[0]]):
                        settings[cat]['notifyByEmail'] = bool(row[cols[0]])
                    if cols[1] in row and not pd.isna(row[cols[1]]):
                        settings[cat]['notifyBySms'] = bool(row[cols[1]])
                    if cols[2] and cols[2] in row and not pd.isna(row[cols[2]]):
                        settings[cat]['markAsRead'] = bool(row[cols[2]])

                # 4. Push Update
                put_resp = rc.put(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", json=settings, token=token)
                if put_resp.status_code == 200:
                    logs.append(f"✅ Ext {ext_num}: Updated")
                else:
                    logs.append(f"❌ Ext {ext_num}: Failed {put_resp.text}")

            except Exception as e:
                logs.append(f"❌ Ext {ext_num}: {str(e)}")

        return logs
