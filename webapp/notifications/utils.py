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

    # --- JOB MANAGEMENT HELPERS ---
    
    def start_audit_job(self, token):
        """Starts a background thread and returns the Job ID."""
        job_id = str(uuid.uuid4())
        
        # Create initial status file
        self._update_job_status(job_id, "running", 0, "Initializing...")
        
        # Start background thread
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
            "filename": filename
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
                self._update_job_status(job_id, "error", 100, "No extensions found.")
                return

            # 2. Fetch Settings (Parallel)
            results = []
            completed_count = 0
            
            # Using 8 workers. Since this is a background thread, it won't block the server.
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                future_to_ext = {executor.submit(self._fetch_single_setting, ext, token=token): ext for ext in extensions}
                
                for future in concurrent.futures.as_completed(future_to_ext):
                    results.append(future.result())
                    completed_count += 1
                    
                    # Update progress every 10 items to reduce file I/O
                    if completed_count % 10 == 0:
                        percent = 10 + int((completed_count / total_ext) * 80) # Scale 10-90%
                        self._update_job_status(job_id, "running", percent, f"Processed {completed_count}/{total_ext} extensions...")

            # 3. Save Excel
            self._update_job_status(job_id, "running", 95, "Generating Excel file...")
            
            df = pd.DataFrame(results, columns=self.columns)
            filename = f"Notification_Audit_{job_id}.xlsx"
            filepath = os.path.join(REPORT_DIR, filename)
            
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Notifications')
                worksheet = writer.sheets['Notifications']
                worksheet.set_column(2, 2, 40)
            
            # 4. Finish
            self._update_job_status(job_id, "complete", 100, "Ready to download", filename=filename)

        except Exception as e:
            self._update_job_status(job_id, "error", 100, f"System Error: {str(e)}")

    def _fetch_single_setting(self, ext, token=None):
        """Fetches a single extension with retry logic."""
        from webapp.rc_api import rc
        
        max_retries = 5
        attempt = 0
        
        while attempt < max_retries:
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
                    wait = int(resp.headers.get('Retry-After', 5)) + random.uniform(0.5, 2.0)
                    time.sleep(wait)
                    continue
                else:
                    return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Error {resp.status_code}"}
            except Exception as e:
                return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Err: {str(e)}"}
        return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': "Rate Limit Failed"}

    # ... (Keep generate_blank_template and process_update_file as they were) ...
    def generate_blank_template(self):
        # [Paste previous generate_blank_template code here]
        # Just creating a placeholder so the code runs
        return io.BytesIO() 

    def process_update_file(self, file_storage, token=None):
        # [Paste previous process_update_file code here]
        return ["Update functionality unchanged"]
