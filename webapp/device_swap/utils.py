import time
from webapp.rc_api import get_rc_client

def get_all_devices():
    """Fetch all devices to map MACs to target device IDs and Extensions to current device IDs."""
    client = get_rc_client()
    devices = []
    
    # Using v1.0 to easily grab the full device inventory in one shot
    endpoint = '/restapi/v1.0/account/~/device'
    response = client.get(endpoint).json()
    devices.extend(response.get('records', []))
    
    mac_to_device_id = {}
    ext_to_device_data = {}
    
    for device in devices:
        # Map MAC to the physical deviceId
        mac = device.get('mac')
        if mac:
            clean_mac = str(mac).replace(':', '').replace('-', '').lower()
            mac_to_device_id[clean_mac] = str(device['id'])
            
        # Map Extension Number to its current HardPhone data
        ext = device.get('extension')
        if ext and device.get('type') == 'HardPhone':
            ext_num = str(ext.get('extensionNumber'))
            if ext_num not in ext_to_device_data:
                ext_to_device_data[ext_num] = []
            
            # Store BOTH the device ID and the internal extension ID
            ext_to_device_data[ext_num].append({
                'device_id': str(device['id']),
                'ext_id': str(ext['id'])
            })
            
    return mac_to_device_id, ext_to_device_data

def process_bulk_device_swap(records):
    client = get_rc_client()
    results = []
    
    mac_to_device_id, ext_to_device_data = get_all_devices()
    
    for row in records:
        ext_num = str(row.get('Extension')).split('.')[0] 
        raw_mac = str(row.get('MAC Address')).strip()
        target_mac = raw_mac.replace(':', '').replace('-', '').lower()
        
        result_entry = {
            'extension': ext_num,
            'mac': raw_mac,
            'status': 'Failed',
            'reason': ''
        }
        
        if target_mac not in mac_to_device_id:
            result_entry['reason'] = f"MAC {raw_mac} not found in the account inventory."
            results.append(result_entry)
            continue
            
        if ext_num not in ext_to_device_data:
            result_entry['reason'] = f"Extension {ext_num} has no current HardPhone to swap."
            results.append(result_entry)
            continue
            
        # Defaulting to the first HardPhone found on the extension
        current_device_data = ext_to_device_data[ext_num][0]
        current_device_id = current_device_data['device_id']
        ext_id = current_device_data['ext_id']
        target_device_id = mac_to_device_id[target_mac]
        
        if current_device_id == target_device_id:
            result_entry['reason'] = "The target MAC is already assigned to this exact device."
            results.append(result_entry)
            continue
            
        try:
            payload = {
                "targetDeviceId": target_device_id
            }
            # v2 endpoint requires the internal extension ID
            endpoint = f'/restapi/v2/accounts/~/extensions/{ext_id}/devices/{current_device_id}/replace'
            client.post(endpoint, json=payload)
            
            result_entry['status'] = 'Success'
            result_entry['reason'] = 'Device successfully swapped.'
            
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg = e.response.json().get('message', error_msg)
                except:
                    pass
            result_entry['reason'] = f"API Error: {error_msg}"
            
        results.append(result_entry)
        time.sleep(0.5) # Modest rate-limiting
        
    return results
