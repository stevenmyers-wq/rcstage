import time
import io
import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font
from webapp.rc_api import rc

DEVICE_TYPES = {"SPA-1001": "1", "SPA-3000": "2", "PAP2-NA": "3", "SPA-921": "4", "Other phone": "0", "Softphone": "-1", "PAP2T": "5", "SPIP300": "20", "SPIP301": "21", "SPIP320": "22", "SPIP330": "24", "SPIP331": "25", "SPIP430": "26", "SPIP500": "28", "SPIP501": "29", "SPIP550": "30", "SPIP560": "31", "SPIP600": "32", "SPIP601": "33", "SSIP4000": "36", "SSIP6000": "37", "SSIP7000": "38", "VVX1500": "39", "SPA-2102": "7", "SPA-3102": "6", "SPA501G": "13", "SPA502G": "14", "SPA504G": "15", "SPA508G": "16", "SPA-2000": "8", "SPA-942": "11", "SSIP5000": "41", "SPA525G2": "19", "SPA303": "51", "SPA122": "52", "VVX500": "53", "VVX150": "76", "VVX450": "79", "J139": "85", "J179": "86", "B199": "87", "CP-6821-3PCC": "84", "W52P": "57", "CP-8861-3PCC": "74", "CP-7841-3PCC": "73", "T48S": "80", "W60P": "81", "OBI302": "82", "J169": "88", "VVX501": "64", "VVX411": "65", "VVX311": "66", "VVX601": "67", "VVX301": "68", "VVX310": "54", "VVX410": "55", "SPA514G": "56", "T21P": "60", "TRIO8500": "75", "J159": "89", "J189": "90", "VVX400": "58", "VVX300": "59", "VVX600": "61", "VVX101": "62", "VVX201": "63", "W56P": "70", "TRIO8800": "69", "T42S": "71", "T46S": "72", "CCX700": "129", "TRIOC60": "134", "ALEM3": "130", "ALEM5": "131", "ALEM7": "132", "ALE8078S": "133", "T43U": "135", "T46U": "136", "T48U": "137", "VVXD230": "141", "T31P": "142", "CP700X": "145", "T58WPRO": "146", "W76P": "149", "W79P": "150", "CP205": "91", "CP400": "92", "CP600": "93", "CP200": "94", "SPIP335": "40", "CP925": "147", "CP965": "148", "6920": "155", "6930": "156", "6940": "157", "6905": "161", "6910": "162", "6970": "163", "6863I": "164", "6865I": "165", "6867I": "166", "6869I": "167", "6873I": "168", "SNOM715": "169", "SNOMD717": "170", "SNOM725": "171", "SNOMD735": "172", "SNOMD765": "173", "SNOMD785": "174", "IP480": "187", "IP480G": "188", "CP110": "184", "CP210": "185", "CP-8841-3PCC": "186", "EDGEE100": "189", "EDGEE300": "190", "CP935WB": "193", "EDGEE500": "192", "CP410": "194", "ROVE20": "196", "J129": "197", "T34W": "199", "VP59": "152", "ROVE30": "143", "ROVE40": "144", "POLYEDGEB10": "153", "POLYEDGEB30": "154", "VVX250": "77", "SPIP450": "27", "SPIP650": "34", "VVX350": "78", "CP-6861-3PCC": "158", "ATA191-MPP": "159", "ATA192-MPP": "160", "EDGEE220": "178", "EDGEE320": "179", "EDGEE350": "180", "EDGEE450": "181", "EDGEE550": "182", "6920W": "175", "6930W": "176", "6940W": "177", "IP485G": "183", "SPA525G": "18", "J189A": "195", "CP710": "191", "T57W": "95", "CP100": "96", "ALE8008": "42", "ALE8008G": "43", "ALE8018": "44", "ALE8058S": "45", "ALE8068S": "46", "ALE8028S": "47", "T40P": "97", "CP700": "98", "SPA-922": "9", "SPA-962": "12", "SPA301": "50", "CP-8851-3PCC": "83", "VVX401": "99", "CP-7821-3PCC": "49", "CP-8811-3PCC": "48", "TRIO8300": "100", "T33G": "101", "CP930WB": "102", "SPIP321": "23", "SPIP670": "35", "SPA-941": "10", "CP920": "103", "T49G": "104", "T19P_E2": "105", "T23G": "106", "T23P": "107", "T27G": "108", "T27P": "109", "T29G": "110", "T40G": "111", "T41P": "112", "T41S": "113", "T42G": "114", "T46G": "115", "T48G": "116", "T52S": "117", "T53": "118", "T53W": "119", "T54S": "120", "T54W": "121", "T56A": "122", "T58": "123", "CP960": "124", "W69P": "125", "CCX400": "126", "CCX500": "127", "CCX600": "128"}

def generate_device_swap_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Device Swap Template"
    
    # Write headers and format them
    headers = ["Extension", "Device Type", "MAC Address", "Device Name"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        
    # Adjust column widths
    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['B'].width = 35
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 25
        
    # Create the hidden DeviceList sheet
    ws_devices = wb.create_sheet(title="DeviceList")
    device_names = sorted(list(DEVICE_TYPES.keys()))
    
    for i, name in enumerate(device_names, start=1):
        ws_devices.cell(row=i, column=1, value=name)
    
    ws_devices.sheet_state = 'hidden'
    
    # Create and apply the dropdown validation rule to Column B
    formula = f"DeviceList!$A$1:$A${len(device_names)}"
    dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    dv.error = 'Please select a valid Device Type from the dropdown list.'
    dv.errorTitle = 'Invalid Device'
    
    ws.add_data_validation(dv)
    # Apply to the first 1000 rows to keep the file perfectly optimized
    dv.add('B2:B1000')
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def process_bulk_device_update(records):
    results = []
    devices_to_update = []
    ext_map = {}
    
    # 1. Fetch all extensions to map extNumber -> extId
    endpoint = '/restapi/v1.0/account/~/extension'
    params = {'perPage': 1000}
    while True:
        response = rc.get(endpoint, params=params).json()
        for ext in response.get('records', []):
            if ext.get('status') == 'Enabled' and ext.get('type') == 'User':
                ext_num = str(ext.get('extensionNumber', ''))
                ext_map[ext_num] = str(ext.get('id'))
        
        nav = response.get('navigation', {})
        if 'nextPage' in nav:
            params['page'] = params.get('page', 1) + 1
        else:
            break

    # 2. Build the bulk update payload
    for row in records:
        ext_num = str(row.get('Extension')).split('.')[0]
        raw_mac = str(row.get('MAC Address')).strip()
        target_mac = raw_mac.replace(':', '').replace('-', '').lower()
        device_type_name = str(row.get('Device Type'))
        device_name = str(row.get('Device Name', ''))
        
        result_entry = {'extension': ext_num, 'mac': raw_mac, 'status': 'Failed', 'reason': ''}
        
        if ext_num not in ext_map:
            result_entry['reason'] = f"Extension {ext_num} not found or not an enabled user."
            results.append(result_entry)
            continue
            
        model_id = DEVICE_TYPES.get(device_type_name)
        if not model_id:
            result_entry['reason'] = f"Invalid Device Type '{device_type_name}'."
            results.append(result_entry)
            continue

        try:
            ext_id = ext_map[ext_num]
            devices_data = rc.get(f'/restapi/v1.0/account/~/extension/{ext_id}/device').json()
            target_device = next((d for d in devices_data.get('records', []) if d.get('type') in ['HardPhone', 'OtherPhone', 'SoftPhone']), None)
            
            if not target_device:
                result_entry['reason'] = "No target device found to update."
                results.append(result_entry)
                continue
                
            update_obj = {
                "id": str(target_device['id']),
                "serial": target_mac,
                "model": {"id": model_id}
            }
            if device_name and device_name.lower() != 'nan':
                update_obj['name'] = device_name
                
            devices_to_update.append(update_obj)
            result_entry['status'] = 'Pending'
            result_entry['reason'] = 'Added to bulk update payload.'
            result_entry['device_id'] = str(target_device['id'])
            results.append(result_entry)
            
        except Exception as e:
            result_entry['reason'] = f"Failed mapping device: {str(e)}"
            results.append(result_entry)
            
    # 3. Fire the bulk update request
    if not devices_to_update:
        return results
        
    try:
        bulk_payload = {"records": devices_to_update}
        bulk_response = rc.post('/restapi/v1.0/account/~/device/bulk-update', json=bulk_payload).json()
        
        # 4. Map the bulk results back to our tracking list
        for api_result in bulk_response.get('records', []):
            dev_id = str(api_result.get('id', ''))
            for r in results:
                if r.get('device_id') == dev_id:
                    if api_result.get('successful'):
                        r['status'] = 'Success'
                        r['reason'] = 'Successfully updated.'
                    else:
                        r['status'] = 'Failed'
                        error_info = api_result.get('error', {})
                        r['reason'] = f"API Error: {error_info.get('message', 'Unknown Error')} (Code: {error_info.get('errorCode', 'N/A')})"
    except Exception as e:
        for r in results:
            if r['status'] == 'Pending':
                r['status'] = 'Failed'
                r['reason'] = f"Bulk update request failed: {str(e)}"
                
    return results
