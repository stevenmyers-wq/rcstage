import io
import time
import pandas as pd
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from webapp.rc_api import rc_api_call

# Global store for background task progress
template_progress_store = {}

def safe_api_call(endpoint, method='GET', json_payload=None, token=None):
    """Helper to safely request API data while respecting 429 Rate Limits."""
    for attempt in range(4):
        resp = rc_api_call(endpoint, method=method, json=json_payload, return_response=True, token=token)
        status_code = getattr(resp, 'status_code', None)
        
        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60)) if hasattr(resp, 'headers') else 10
            time.sleep(retry_after + 1)
            continue
            
        if resp and getattr(resp, 'ok', False):
            try:
                return resp.json() or {"success": True}
            except:
                return {"success": True}
                
        if status_code and status_code >= 500:
            time.sleep(3)
            continue
            
        return None
    return None

def fetch_all_templates(token):
    """Fetches all user templates with pagination."""
    templates = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/templates?perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp: 
            break
        templates.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return templates

def fetch_all_users(token):
    """Fetches all active users."""
    users = []
    page = 1
    while True:
        resp = safe_api_call(f"/restapi/v1.0/account/~/extension?type=User&perPage=1000&page={page}", token=token)
        if not resp or 'records' not in resp: 
            break
        for u in resp['records']:
            if u.get('status') in ['Enabled', 'NotActivated']:
                users.append(u)
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return users

def generate_audit_spreadsheet(token):
    """Generates an Excel file with User, Site, and a Template dropdown."""
    users = fetch_all_users(token)
    templates = fetch_all_templates(token)
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Template Assignment"

    # Removed the "Current Template" column
    headers = ["Extension ID", "Extension Number", "Name", "Site", "Template to Apply"]
    ws.append(headers)

    # Note: openpyxl data validation formula must be a comma-separated string
    template_names = [t.get('name', '').replace(',', '') for t in templates]
    dropdown_formula = f'"{",".join(template_names)}"'
    
    # We must allow blank so the user doesn't have to assign a template to EVERY row
    dv = DataValidation(type="list", formula1=dropdown_formula, allow_blank=True)
    ws.add_data_validation(dv)

    for index, user in enumerate(users, start=2):
        ext_id = str(user.get('id', ''))
        ext_num = user.get('extensionNumber', '')
        name = user.get('name', 'Unknown')
        site = user.get('site', {}).get('name', 'Main Site')
        
        ws.append([ext_id, ext_num, name, site, ""])
        
        # Add the validation to the 'Template to Apply' column (now Column E / 5)
        dv.add(ws.cell(row=index, column=5)) 

    # Auto-adjust column widths
    for column in ws.columns:
        length = max(len(str(cell.value) or "") for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(length + 5, 50)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def process_upload_background(task_id, file_bytes, token):
    """Parses the Excel file, chunks the requests, and pushes via Bulk Apply."""
    template_progress_store[task_id] = {
        'status': 'running', 'current': 0, 'total': 0, 'message': 'Parsing Excel file...'
    }

    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
        templates = fetch_all_templates(token)
        template_map = {t.get('name', '').replace(',', ''): t['id'] for t in templates}
        
        application_batches = {}
        
        # Group extension IDs by the template they need applied
        for _, row in df.iterrows():
            template_name = row.get("Template to Apply")
            ext_id = row.get("Extension ID")
            
            if pd.notna(template_name) and str(template_name).strip() != "":
                t_name_clean = str(template_name).strip()
                if t_name_clean in template_map:
                    t_id = template_map[t_name_clean]
                    if t_id not in application_batches:
                        application_batches[t_id] = []
                    application_batches[t_id].append(str(ext_id).split('.')[0].strip())

        # Flatten into tasks for the progress bar (Chunking by 100)
        CHUNK_SIZE = 100
        tasks = []
        for t_id, ext_ids in application_batches.items():
            for i in range(0, len(ext_ids), CHUNK_SIZE):
                tasks.append((t_id, ext_ids[i:i + CHUNK_SIZE]))

        total_tasks = len(tasks)
        template_progress_store[task_id]['total'] = total_tasks

        if total_tasks == 0:
            template_progress_store[task_id]['status'] = 'completed'
            template_progress_store[task_id]['message'] = 'No templates assigned in the uploaded file.'
            return

        for idx, (t_id, chunked_ext_ids) in enumerate(tasks):
            template_progress_store[task_id]['current'] = idx
            template_progress_store[task_id]['message'] = f'Applying template to batch {idx + 1} of {total_tasks}...'
            
            endpoint = f'/restapi/v1.0/account/~/templates/{t_id}/bulk-apply'
            body = {
                "extensionIds": chunked_ext_ids,
                "notifyUsers": False,
                "overrideAll": True
            }
            
            safe_api_call(endpoint, method='POST', json_payload=body, token=token)
            time.sleep(1.5) # Buffer between heavy chunks to avoid backend queuing limits

        template_progress_store[task_id]['current'] = total_tasks
        template_progress_store[task_id]['status'] = 'completed'
        template_progress_store[task_id]['message'] = 'All templates applied successfully!'

    except Exception as e:
        template_progress_store[task_id]['status'] = 'error'
        template_progress_store[task_id]['error'] = str(e)
