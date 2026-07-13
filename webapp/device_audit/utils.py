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
    # 1. Fetch both devices and extensions
    devices = fetch_all_devices(token)
    extensions = fetch_all_extensions(token)
    
    # 2. Build a lookup map: { 'extension_id': 'Extension_Type' }
    ext_type_map = {str(ext.get('id')): ext.get('type', 'Unknown') for ext in extensions if ext.get('id')}
    
    audit_data = []
    
    # 3. Process devices
    for d in devices:
        ext = d.get('extension')
        if not ext or not ext.get('id'):
            device_type = "Unassigned"
        else:
            ext_id = str(ext.get('id'))
            
            # Look up the actual type using the map we built
            ext_type = ext_type_map.get(ext_id, 'Unknown')
            
            # Translate RC API types to human-readable types
            if ext_type == 'Limited':
                device_type = "Common Area"
            elif ext_type == 'PagingOnly':
                device_type = "Paging"
            else:
                device_type = ext_type # Will pass through "User", "Department", etc.
                
        model_info = d.get('model', {})
        model_name = model_info.get('name', 'Unknown')
        
        name = d.get('name', 'Unnamed Device')
        mac = d.get('serial', '') # 'serial' on HardPhones contains the MAC address
        
        # Retrieve Online / Offline status
        status = d.get('status', 'Offline')
        is_online = "Yes" if status.lower() == "online" else "No"
        
        audit_data.append({
            "Type (User, Common Area, Unassigned etc)": device_type,
            "Model": model_name,
            "Name": name,
            "MAC": mac,
            "Online (Yes/No)": is_online
        })
        
    if not audit_data:
        audit_data.append({
            "Type (User, Common Area, Unassigned etc)": "No Devices Found",
            "Model": "",
            "Name": "",
            "MAC": "",
            "Online (Yes/No)": ""
        })
        
    return audit_data
