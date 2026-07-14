import time
from webapp.rc_api import rc_api_call

def fetch_all_devices(token):
    devices = []
    page = 1
    while True:
        resp = rc_api_call(f"/restapi/v1.0/account/~/device?perPage=1000&page={page}", token=token, raise_error=False)
        if not resp or 'records' not in resp: 
            break
        devices.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return devices

def fetch_all_extensions(token):
    extensions = []
    page = 1
    while True:
        resp = rc_api_call(f"/restapi/v1.0/account/~/extension?perPage=1000&page={page}", token=token, raise_error=False)
        if not resp or 'records' not in resp: 
            break
        extensions.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'): 
            break
        page += 1
        time.sleep(0.05)
    return extensions

def generate_device_audit(token):
    devices = fetch_all_devices(token)
    extensions = fetch_all_extensions(token)
    
    # Build a robust lookup map for extension details
    ext_map = {
        str(ext.get('id')): {
            'type': ext.get('type', 'Unknown'),
            'name': ext.get('name', ''),
            'extensionNumber': ext.get('extensionNumber', ''),
            'site': ext.get('site', {}).get('name', '')
        } 
        for ext in extensions if ext.get('id')
    }
    
    audit_data = []
    
    for d in devices:
        ext = d.get('extension')
        ext_name = ""
        ext_num = ""
        site_name = ""
        
        if not ext or not ext.get('id'):
            device_type = "Unassigned"
            # Unassigned devices can still belong to a site
            site_name = d.get('site', {}).get('name', 'Main Site')
        else:
            ext_id = str(ext.get('id'))
            ext_info = ext_map.get(ext_id, {})
            
            ext_type = ext_info.get('type', 'Unknown')
            ext_name = ext_info.get('name', '')
            ext_num = ext_info.get('extensionNumber', '')
            
            # Inherit the site from the assigned extension, fallback to device site if missing
            site_name = ext_info.get('site') or d.get('site', {}).get('name', 'Main Site')
            
            if ext_type == 'Limited':
                device_type = "Common Area"
            elif ext_type == 'PagingOnly':
                device_type = "Paging"
            else:
                device_type = ext_type
                
        # Ensure we don't have blank site names
        if not site_name:
            site_name = "Main Site"
                
        model_info = d.get('model', {})
        model_name = model_info.get('name', 'Unknown')
        name = d.get('name', 'Unnamed Device')
        mac = d.get('serial', '') 
        
        status = d.get('status', 'Offline')
        is_online = "Yes" if status.lower() == "online" else "No"
        
        audit_data.append({
            "Site": site_name,
            "Type (User, Common Area, Unassigned etc)": device_type,
            "Assigned Ext Name": ext_name,
            "Assigned Ext Number": ext_num,
            "Model": model_name,
            "Name": name,
            "MAC": mac,
            "Online (Yes/No)": is_online
        })
        
    if not audit_data:
        audit_data.append({
            "Site": "",
            "Type (User, Common Area, Unassigned etc)": "No Devices Found",
            "Assigned Ext Name": "",
            "Assigned Ext Number": "",
            "Model": "",
            "Name": "",
            "MAC": "",
            "Online (Yes/No)": ""
        })
        
    return audit_data
