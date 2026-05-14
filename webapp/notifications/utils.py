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
        
        self._pause_until = 0
        self._lock = threading.Lock()

    def start_audit_job(self, token):
        job_id = str(uuid.uuid4())
        self._update_job_status(job_id, "running", 0, "Initializing Audit...")
        thread = threading.Thread(target=self._run_audit_background, args=(job_id, token))
        thread.daemon = True
        thread.start()
        return job_id

    def start_update_job(self, df, token):
        job_id = str(uuid.uuid4())
        self._update_job_status(job_id, "running", 0, "Initializing Update...")
        thread = threading.Thread(target=self._run_update_background, args=(job_id, df, token))
        thread.daemon = True
        thread.start()
        return job_id

    def get_job_status(self, job_id):
        status_file = os.path.join(JOB_DIR, f"{job_id}.json")
        if not os.path.exists(status_file):
            return {"status": "error", "message": "Job not found"}
        with open(status_file, 'r') as f:
            return json.load(f)

    def _update_job_status(self, job_id, status, percent, message, filename=None, logs=None):
        data = {
            "status": status,
            "percent": percent,
            "message": message,
            "filename": filename,
            "logs": logs,
            "updated_at": datetime.now().isoformat()
        }
        with open(os.path.join(JOB_DIR, f"{job_id}.json"), 'w') as f:
            json.dump(data, f)

    def _run_audit_background(self, job_id, token):
        try:
            from webapp.rc_api import rc
            
            self._update_job_status(job_id, "running", 5, "Fetching extension list...")
            extensions = []
            page = 1
            
            while True:
                try:
                    resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                        'type': ['User', 'Department', 'Voicemail', 'Limited'], 
                        'perPage': 1000, 
                        'page': page
                    })
                    
                    if resp.status_code == 429:
                        self._handle_429(resp)
                        time.sleep(max(1, self._pause_until - time.time()))
                        continue

                    if resp.status_code != 200:
                        self._update_job_status(job_id, "error", 100, f"API Error {resp.status_code}: {resp.text}")
                        return

                    data = resp.json()
                    for r in data.get('records', []):
                        r_type = r.get('type', '')
                        status = r.get('status', '')
                        
                        if r_type in ['User', 'Limited'] and status not in ['Enabled', 'NotActivated']:
                            continue
                            
                        extensions.append(r)
                    
                    if not data.get('navigation', {}).get('nextPage'): break
                    page += 1
                except Exception as e:
                    self._update_job_status(job_id, "error", 100, f"Fetch Exception: {str(e)}")
                    return

            total_ext = len(extensions)
            if total_ext == 0:
                self._update_job_status(job_id, "error", 100, f"Success but 0 valid extensions found.")
                return

            results = []
            completed_count = 0
            self._pause_until = 0
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_ext = {executor.submit(self._fetch_single_setting, ext, token=token): ext for ext in extensions}
                
                for future in concurrent.futures.as_completed(future_to_ext):
                    results.append(future.result())
                    completed_count += 1
                    
                    if completed_count % 10 == 0:
                        percent = 10 + int((completed_count / total_ext) * 80)
                        self._update_job_status(job_id, "running", percent, f"Processed {completed_count}/{total_ext} extensions...")

            self._update_job_status(job_id, "running", 95, "Saving Excel...")
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
            
            self._update_job_status(job_id, "running", 5, "Mapping extensions...")
            ext_map = {}
            page = 1
            while True:
                resp = rc.get('/restapi/v1.0/account/~/extension', token=token, params={
                    'type': ['User', 'Department', 'Voicemail', 'Limited'], 
                    'perPage': 1000, 'page': page
                })

                if resp.status_code == 429:
                    self._handle_429(resp)
                    time.sleep(max(1, self._pause_until - time.time()))
                    continue

                if resp.status_code != 200:
                    self._update_job_status(job_id, "error", 100, f"API Error during mapping: {resp.status_code}")
                    return
                
                data = resp.json()
                for r in data.get('records', []):
                    r_type = r.get('type', '')
                    status = r.get('status', '')
                    if r_type in ['User', 'Limited'] and status not in ['Enabled', 'NotActivated']:
                        continue
                    if 'extensionNumber' in r: ext_map[str(r['extensionNumber'])] = str(r['id'])
                
                if not data.get('navigation', {}).get('nextPage'): break
                page += 1

            if not ext_map:
                self._update_job_status(job_id, "error", 100, "No extensions found in account to map against.")
                return

            logs = []
            total_rows = len(df)
            processed_rows = 0
            self._pause_until = 0

            for index, row in df.iterrows():
                processed_rows += 1
                if processed_rows % 5 == 0 or processed_rows == 1:
                    percent = 10 + int((processed_rows / total_rows) * 85)
                    self._update_job_status(job_id, "running", percent, f"Updating row {processed_rows}/{total_rows}...")

                if "Example" in str(row.get('ExtensionName', '')): continue

                ext_num = str(row['ExtensionNumber']).strip().replace('.0', '')
                ext_id = ext_map.get(ext_num)
                
                if not ext_id:
                    logs.append(f"⚠️ Ext {ext_num}: Not found. Skipping.")
                    continue

                wait_needed = self._pause_until - time.time()
                if wait_needed > 0: time.sleep(wait_needed)
                time.sleep(random.uniform(0.01, 0.05))

                try:
                    get_resp = rc.get(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", token=token)
                    if get_resp.status_code == 429:
                        self._handle_429(get_resp)
                        logs.append(f"⚠️ Ext {ext_num}: Rate Limit (GET). Skipping.")
                        continue
                    if get_resp.status_code != 200:
                        logs.append(f"❌ Ext {ext_num}: API Error {get_resp.status_code}")
                        continue
                    
                    settings = get_resp.json()
                    
                    if 'EmailAddresses' in row and not pd.isna(row['EmailAddresses']):
                        raw = str(row['EmailAddresses'])
                        settings['emailAddresses'] = [e.strip() for e in raw.split(',') if e.strip()]
                        settings.pop('emailRecipients', None)

                    if 'DisableManagerNotifications' in row:
                        val = str(row['DisableManagerNotifications']).lower()
                        if val in ['true', '1', 'yes']:
                            for cat in ['voicemails', 'inboundFaxes', 'missedCalls']:
                                if cat in settings: settings[cat]['includeManagers'] = False

                    if 'IncludeSms' in row and not pd.isna(row['IncludeSms']):
                        if 'includeSmsRecipients' in settings:
                            settings['includeSmsRecipients'] = bool(row['IncludeSms'])
                    if 'AdvancedMode' in row and not pd.isna(row['AdvancedMode']):
                        if 'advancedMode' in settings:
                            settings['advancedMode'] = bool(row['AdvancedMode'])

                    cats = {
                        'voicemails': ('Voicemails_Email', 'Voicemails_SMS', 'Voicemails_MarkAsRead'),
                        'missedCalls': ('MissedCalls_Email', 'MissedCalls_SMS', None),
                        'inboundTexts': ('InboundTexts_Email', 'InboundTexts_SMS', None),
                        'inboundFaxes': ('InboundFaxes_Email', 'InboundFaxes_SMS', 'InboundFaxes_MarkAsRead'),
                        'outboundFaxes': ('OutboundFaxes_Email', 'OutboundFaxes_SMS', None)
                    }
                    
                    for cat, cols in cats.items():
                        if cat in settings: 
                            if cols[0] in row and not pd.isna(row[cols[0]]): settings[cat]['notifyByEmail'] = bool(row[cols[0]])
                            if cols[1] in row and not pd.isna(row[cols[1]]): settings[cat]['notifyBySms'] = bool(row[cols[1]])
                            if cols[2] and cols[2] in row and not pd.isna(row[cols[2]]): settings[cat]['markAsRead'] = bool(row[cols[2]])

                    put_resp = rc.put(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings", json=settings, token=token)
                    if put_resp.status_code == 200:
                        logs.append(f"✅ Ext {ext_num}: Updated")
                    elif put_resp.status_code == 429:
                        self._handle_429(put_resp)
                        logs.append(f"⚠️ Ext {ext_num}: Rate Limit (PUT).")
                    else:
                        logs.append(f"❌ Ext {ext_num}: Failed {put_resp.text}")

                except Exception as e:
                    logs.append(f"❌ Ext {ext_num}: {str(e)}")

            self._update_job_status(job_id, "complete", 100, "Update Completed", logs=logs)

        except Exception as e:
            self._update_job_status(job_id, "error", 100, f"System Error: {str(e)}")

    def _handle_429(self, resp):
        try:
            retry_after = int(resp.headers.get('Retry-After', 5))
        except:
            retry_after = 5
        retry_after = min(retry_after, 60)
        
        with self._lock:
            new_pause = time.time() + retry_after + 1
            if new_pause > self._pause_until:
                self._pause_until = new_pause

    def _fetch_single_setting(self, ext, token=None):
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
                    return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': "API ERROR / UNASSIGNED"}
            except Exception as e:
                return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': f"Err: {str(e)}"}
        return {'ExtensionNumber': ext.get('extensionNumber'), 'ExtensionName': "Failed: Rate Limit Exceeded"}

    def generate_blank_template(self):
        import pandas as pd
        df = pd.DataFrame(columns=self.columns)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Update_Template')
        return output
