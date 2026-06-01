# webapp/cxone_script_analyzer/utils.py
# NOTE (Jun 2026): google-genai bumped from 0.3.0 → >=1.11.0. If Gemini script analysis breaks,
# contact Riyaz Mohammed (riyaz.mohammed@ringcentral.com) before changing SDK usage here.
import requests
import json
import time
import markdown
import base64
import gzip
import zlib
import re
import urllib3
import xml.etree.ElementTree as ET
from google import genai
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime

# Suppress the harmless InsecureRequestWarning when making verify=False requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def handle_cxone_response(response):
    """Intercepts 401s to provide a human-readable token expiration warning."""
    if response.status_code == 401:
        raise Exception("CXone session expired or unauthorized. Please navigate to the Authentication tab, click 'Disconnect CXone', and reconnect.")
    response.raise_for_status()

def get_cxone_token(access_key, secret_key, region):
    auth_url = f"https://{region}.nice-incontact.com/authentication/v1/token/access-key"
    headers = {"Content-Type": "application/json"}
    payload = {"accessKeyId": access_key, "accessKeySecret": secret_key}
    
    response = requests.post(auth_url, headers=headers, json=payload, verify=False)
    handle_cxone_response(response)
    data = response.json()
    
    token = data.get("access_token")
    base_uri = data.get("resource_server_base_uri") or data.get("server_base_uri") or data.get("domain")
    
    if not base_uri:
        base_uri = f"https://api-{region}.niceincontact.com"
        
    return token, base_uri.rstrip("/")

def fetch_cxone_bu_name(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/business-unit"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(endpoint, headers=headers, verify=False)
        if response.ok:
            data = response.json()
            bu = data.get('businessUnits', data.get('businessUnit', data))
            if isinstance(bu, list) and len(bu) > 0:
                bu = bu[0]
            if isinstance(bu, dict):
                return bu.get('businessUnitName', bu.get('name', 'Unknown BU'))
    except Exception:
        pass
    return "Unknown BU"

def fetch_cxone_folders(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/script-folders"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    response = requests.get(endpoint, headers=headers, verify=False)
    handle_cxone_response(response)
    data = response.json()
    
    def hunt_for_keys(json_data, target_keys):
        found_values = []
        if isinstance(json_data, dict):
            for k, v in json_data.items():
                if k in target_keys and isinstance(v, str):
                    found_values.append(v)
                else:
                    found_values.extend(hunt_for_keys(v, target_keys))
        elif isinstance(json_data, list):
            for item in json_data:
                found_values.extend(hunt_for_keys(item, target_keys))
        return found_values

    possible_folders = hunt_for_keys(data, ["scriptName", "folderName", "name"])
    folder_list = list(set(possible_folders))
    folder_list.sort()
    
    if "\\" in folder_list:
        folder_list.remove("\\")
    folder_list.insert(0, "\\")
    
    return folder_list

def fetch_cxone_scripts(base_uri, token, folder_name):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/script-folders"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    params = {}
    if folder_name and folder_name != "\\":
        params["folder"] = folder_name
        
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    return response.json().get("scriptList", [])

def fetch_script_history(base_uri, token, script_path):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/scripts/historyByName"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"scriptPath": script_path}
    
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    return response.json().get("versions", [])

def fetch_script_content(base_uri, token, specific_script_id):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/scripts"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"scriptId": specific_script_id}
    
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    data = response.json()
    
    script_data = data
    if "scriptList" in data and isinstance(data["scriptList"], list) and len(data["scriptList"]) > 0:
        script_data = data["scriptList"][0]
    elif "scripts" in data and isinstance(data["scripts"], list) and len(data["scripts"]) > 0:
        script_data = data["scripts"][0]
        
    return json.dumps(script_data, indent=2)

def fetch_cxone_skills(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/skills"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"isDeleted": "false"} 
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    return response.json()

def fetch_cxone_hours(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/hours-of-operation"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"isDeleted": "false"} 
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    return response.json()

def fetch_cxone_hours_detail(base_uri, token, profile_id):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/hours-of-operation/{profile_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    response = requests.get(endpoint, headers=headers, verify=False)
    
    if not response.ok:
        if response.status_code == 401:
            raise Exception("CXone session expired. Please disconnect and reconnect.")
        return {"_fetch_error": f"HTTP {response.status_code}: {response.text}"}
        
    return response.json()

def fetch_cxone_teams(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/teams"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"isDeleted": "false"} 
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    handle_cxone_response(response)
    return response.json()

def fetch_cxone_pocs(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/points-of-contact"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(endpoint, headers=headers, verify=False)
        handle_cxone_response(response)
        return response.json()
    except Exception as e:
        return {"_fetch_error": f"Failed to fetch Standard POCs: {str(e)}"}

def fetch_cxone_digital_channels(base_uri, token):
    endpoint = f"{base_uri}/dfo/3.0/channels"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(endpoint, headers=headers, verify=False)
        handle_cxone_response(response)
        return response.json()
    except Exception as e:
        return {"_fetch_error": f"Failed to fetch Digital Channels: {str(e)}"}

def get_environment_config_md(base_uri, token):
    """Fetches all environment config components and builds the combined Markdown output."""
    raw_skills = fetch_cxone_skills(base_uri, token)
    raw_hours = fetch_cxone_hours(base_uri, token)
    raw_teams = fetch_cxone_teams(base_uri, token)
    raw_pocs = fetch_cxone_pocs(base_uri, token)
    raw_dfo_channels = fetch_cxone_digital_channels(base_uri, token)
    
    skills_list = raw_skills.get('businessUnitSkills', raw_skills.get('skills', raw_skills)) if isinstance(raw_skills, dict) else raw_skills
    hours_list = raw_hours.get('hoursOfOperationProfiles', raw_hours.get('hoursOfOperation', raw_hours)) if isinstance(raw_hours, dict) else raw_hours
    teams_list = raw_teams.get('teams', raw_teams) if isinstance(raw_teams, dict) else raw_teams
    pocs_list = raw_pocs.get('pointsOfContact', raw_pocs) if isinstance(raw_pocs, dict) else raw_pocs
    dfo_channels_list = raw_dfo_channels.get('data', raw_dfo_channels.get('channels', raw_dfo_channels)) if isinstance(raw_dfo_channels, dict) else raw_dfo_channels
    
    if isinstance(pocs_list, dict) and "_fetch_error" in pocs_list:
        pocs_list = [pocs_list]
        
    if isinstance(dfo_channels_list, dict) and "_fetch_error" in dfo_channels_list:
        dfo_channels_list = [dfo_channels_list]
        
    detailed_hours_list = []
    if isinstance(hours_list, list):
        for profile in hours_list:
            profile_id = profile.get('hoursOfOperationProfileId') or profile.get('profileId') or profile.get('id')
            
            if profile_id:
                details = fetch_cxone_hours_detail(base_uri, token, profile_id)
                
                if "_fetch_error" in details:
                    profile["_fetch_error"] = details["_fetch_error"]
                else:
                    unwrapped = details.get('hoursOfOperationProfile', details)
                    profile['days'] = unwrapped.get('days', [])
                    profile['holidays'] = unwrapped.get('holidays', [])
                    
                detailed_hours_list.append(profile)
            else:
                profile["_fetch_error"] = "No Profile ID found in raw JSON object."
                detailed_hours_list.append(profile)
    else:
        detailed_hours_list = hours_list
    
    return generate_config_markdown(skills_list, detailed_hours_list, teams_list, pocs_list, dfo_channels_list)

def generate_script_graph(script_json_str):
    try:
        data = json.loads(script_json_str)
    except Exception:
        data = {}

    nodes = []
    edges_list = []
    node_ids = set()

    script_payload = None
    is_base64 = False
    for key, val in data.items():
        if key.lower() in ('scriptdata', 'script_data', 'filedata', 'script_content') and isinstance(val, str):
            script_payload = val
            is_base64 = True
            break
    if not script_payload:
        script_payload = script_json_str

    decoded = script_payload
    if is_base64:
        try:
            s = script_payload.strip()
            s += '=' * (-len(s) % 4) 
            decoded_bytes = base64.b64decode(s)
            unzipped = None
            try: unzipped = gzip.decompress(decoded_bytes)
            except Exception:
                try: unzipped = zlib.decompress(decoded_bytes)
                except Exception:
                    try: unzipped = zlib.decompress(decoded_bytes, -15)
                    except Exception: pass
            
            if unzipped:
                try: decoded = unzipped.decode('utf-8')
                except UnicodeDecodeError: decoded = unzipped.decode('utf-16', errors='ignore')
            else:
                decoded = decoded_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"Base64 Decode Error (Ignored): {e}")

    decoded = decoded.strip().lstrip('\xef\xbb\xbf') 

    if decoded.startswith('<'):
        try:
            clean_xml = decoded
            if clean_xml.startswith('<?xml'):
                clean_xml = clean_xml.split('?>', 1)[-1].strip()
            clean_xml = re.sub(r'\sxmlns="[^"]+"', '', clean_xml, count=1)
            root = ET.fromstring(clean_xml)

            for el in root.iter():
                tag = el.tag.lower()
                if 'action' in tag and 'actions' not in tag and 'targetaction' not in tag:
                    attrs = {k.lower(): v for k, v in el.attrib.items()}
                    act_id = str(attrs.get('actionid') or attrs.get('id') or '')
                    if not act_id: continue
                    
                    lbl = attrs.get('label') or attrs.get('caption') or ''
                    name = attrs.get('name') or attrs.get('pluginname') or attrs.get('type') or 'Unknown'
                    plugin = attrs.get('pluginname') or attrs.get('type') or name
                    
                    display_label = f"{lbl}\n[{name}]" if lbl else f"[{name}]"
                    
                    if act_id not in node_ids:
                        nodes.append({
                            "data": {
                                "id": act_id, 
                                "label": display_label, 
                                "type": str(plugin).lower(),
                                "properties": dict(el.attrib)
                            }
                        })
                        node_ids.add(act_id)

            for el in root.iter():
                tag = el.tag.lower()
                if 'action' in tag and 'actions' not in tag and 'targetaction' not in tag:
                    attrs = {k.lower(): v for k, v in el.attrib.items()}
                    act_id = str(attrs.get('actionid') or attrs.get('id') or '')
                    if not act_id: continue

                    for k, v in attrs.items():
                        if k in ['actionid', 'id', 'sourceactionid']: continue
                        if k.endswith('actionid') or k.endswith('targetid') or k in ['target', 'next', 'to', 'destination']:
                            tgt = str(v)
                            if tgt and tgt != act_id and tgt in node_ids:
                                lbl = k.replace('actionid', '').replace('targetid', '').capitalize()
                                if not lbl or lbl in ['Target', 'Dest']: lbl = 'Next'
                                edges_list.append({"source": act_id, "target": tgt, "label": lbl})

                    for child in el.iter():
                        if child == el: continue
                        c_attrs = {k.lower(): v for k, v in child.attrib.items()}
                        for k, v in c_attrs.items():
                            if k in ['actionid', 'id', 'sourceactionid']: continue
                            if k.endswith('actionid') or k.endswith('targetid') or k in ['target', 'next', 'to', 'destination']:
                                tgt = str(v)
                                if tgt and tgt != act_id and tgt in node_ids:
                                    cond = str(c_attrs.get('condition') or c_attrs.get('label') or c_attrs.get('outcome') or '')
                                    if not cond:
                                        cond = k.replace('actionid', '').replace('targetid', '').capitalize()
                                        if not cond or cond in ['Target', 'Dest']: cond = 'Next'
                                    edges_list.append({"source": act_id, "target": tgt, "label": cond})
        except Exception as e:
            print(f"XML Parse Error: {e}")

    if not nodes and (decoded.startswith('{') or decoded.startswith('[')):
        try:
            parsed_json = json.loads(decoded)
            actions = []

            def extract_nodes(obj):
                if isinstance(obj, dict):
                    ci_dict = {str(k).lower(): v for k, v in obj.items()}
                    a_id = str(ci_dict.get('actionid') or ci_dict.get('id') or '')
                    
                    is_script = False
                    for v in [ci_dict.get('pluginname'), ci_dict.get('type'), ci_dict.get('name')]:
                        if str(v).lower() == 'script': is_script = True
                        
                    if a_id and not is_script and len(ci_dict) >= 3:
                        lbl = str(ci_dict.get('label') or ci_dict.get('caption') or '')
                        name = str(ci_dict.get('name') or ci_dict.get('pluginname') or ci_dict.get('type') or 'Unknown')
                        plugin = str(ci_dict.get('pluginname') or ci_dict.get('type') or ci_dict.get('actiontype') or name)
                        
                        if a_id not in node_ids:
                            props = {str(k): str(v) for k, v in obj.items() if not isinstance(v, (dict, list))}
                            display_label = f"{lbl}\n[{name}]" if lbl else f"[{name}]"
                            
                            node_obj = {
                                "data": {
                                    "id": a_id, 
                                    "label": display_label, 
                                    "type": plugin.lower(),
                                    "properties": props
                                }
                            }
                            x_val = ci_dict.get('x') or ci_dict.get('left')
                            y_val = ci_dict.get('y') or ci_dict.get('top')
                            if x_val is not None and y_val is not None:
                                try: node_obj["position"] = {"x": float(x_val), "y": float(y_val)}
                                except Exception: pass

                            nodes.append(node_obj)
                            node_ids.add(a_id)
                            actions.append(obj)
                        
                    for v in obj.values():
                        if isinstance(v, (dict, list)): extract_nodes(v)
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, (dict, list)): extract_nodes(item)
                        
            extract_nodes(parsed_json)

            for act in actions:
                ci_dict = {str(k).lower(): v for k, v in act.items()}
                current_src = str(ci_dict.get('actionid') or ci_dict.get('id') or '')
                if not current_src: continue

                for k, v in ci_dict.items():
                    if not v or k in ['actionid', 'id', 'sourceactionid', 'scriptactionid']: continue
                    tgt = str(v)
                    
                    if isinstance(v, (int, str)) and len(tgt) < 20: 
                        is_pointer = k.endswith('actionid') or k.endswith('targetid') or k in ['target', 'next', 'to', 'destination']
                        if is_pointer and tgt and tgt != current_src:
                            cond = str(ci_dict.get('condition') or ci_dict.get('label') or ci_dict.get('outcome') or '')
                            if not cond:
                                cond = k.replace('actionid', '').replace('targetid', '').capitalize()
                                if cond in ['Target', 'Dest', 'Next', '']: cond = 'Next'
                            edges_list.append({"source": current_src, "target": tgt, "label": cond})

            def extract_explicit_branches(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if str(k).lower() == 'branches' and isinstance(v, dict):
                            for src_id, targets in v.items():
                                if isinstance(targets, list):
                                    for t in targets:
                                        if isinstance(t, dict):
                                            tgt_id = str(t.get('to') or '')
                                            if tgt_id:
                                                e_type = str(t.get('type') or '').lower()
                                                if e_type in ['custom', 'branch']:
                                                    lbl = str(t.get('label') or '')
                                                else:
                                                    lbl = '' 
                                                
                                                edges_list.append({
                                                    "source": str(src_id), 
                                                    "target": tgt_id, 
                                                    "label": lbl
                                                })
                    for v in obj.values():
                        if isinstance(v, (dict, list)): extract_explicit_branches(v)
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, (dict, list)): extract_explicit_branches(item)
                        
            extract_explicit_branches(parsed_json)

            if not edges_list:
                def extract_inline_edges(obj, current_src=None):
                    if isinstance(obj, dict):
                        ci_dict = {str(k).lower(): v for k, v in obj.items()}
                        a_id = str(ci_dict.get('actionid') or ci_dict.get('id') or '')
                        if a_id and len(ci_dict) >= 3 and str(ci_dict.get('pluginname', '')).lower() != 'script':
                            current_src = a_id
                            
                        if current_src:
                            for k, v in ci_dict.items():
                                if not v or k in ['actionid', 'id', 'sourceactionid', 'scriptactionid']: continue
                                tgt = str(v)
                                if isinstance(v, (int, str)) and len(tgt) < 20: 
                                    if k.endswith('actionid') or k.endswith('targetid') or k in ['target', 'next', 'to', 'destination']:
                                        if tgt and tgt != current_src:
                                            lbl = str(ci_dict.get('condition') or ci_dict.get('label') or ci_dict.get('outcome') or '')
                                            edges_list.append({"source": current_src, "target": tgt, "label": lbl})
                                        
                        for v in obj.values():
                            if isinstance(v, (dict, list)): extract_inline_edges(v, current_src)
                    elif isinstance(obj, list):
                        for item in obj:
                            if isinstance(item, (dict, list)): extract_inline_edges(item, current_src)
                extract_inline_edges(parsed_json)

        except Exception as e:
            print(f"JSON Parse Error: {e}")

    unique_edges = []
    seen_edges = set()
    for e in edges_list:
        if e['source'] == e['target']: continue
        ekey = f"{e['source']}->{e['target']}::{e['label']}"
        if ekey not in seen_edges:
            seen_edges.add(ekey)
            
            if e['target'] not in node_ids:
                nodes.append({
                    "data": {
                        "id": e['target'], 
                        "label": f"End/Unknown\n[{e['target']}]", 
                        "type": "end",
                        "properties": {"note": "Target action not found in script"}
                    }
                })
                node_ids.add(e['target'])
                
            unique_edges.append({"data": e})

    edges = unique_edges

    try:
        formatted_decoded = json.dumps(json.loads(decoded), indent=2)
    except Exception:
        formatted_decoded = decoded

    if not nodes:
        preview = formatted_decoded[:1500] if formatted_decoded else "Empty Payload"
        nodes.append({"data": {"id": "error_node", "label": f"Parser could not find actions.\n\nRAW DECODED PREVIEW:\n{preview}", "type": "end"}})
    elif nodes and not edges:
        nodes.append({"data": {"id": "edge_error_node", "label": "Found nodes but NO EDGES.\n\nSee JSON below.", "type": "end"}})

    return {
        "nodes": nodes, 
        "edges": edges,
        "raw_decoded": formatted_decoded
    }

def format_cxone_date(date_str):
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y %I:%M:%S %p")
        return dt.strftime("%d-%b-%Y").upper(), dt.strftime("%I:%M:%S %p")
    except ValueError:
        return date_str, "Unknown Time"

def create_pdf(md_text):
    html = markdown.markdown(md_text, extensions=['tables'])
    
    # CSS adjusted to condense table layouts and minimize line padding
    styled_html = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Helvetica, Arial, sans-serif; font-size: 12px; line-height: 1.3; color: #333; }}
                h1, h2, h3 {{ color: #1f497d; margin-top: 12px; margin-bottom: 8px; }}
                h3 {{ border-bottom: 1px solid #1f497d; padding-bottom: 3px; }}
                code {{ background-color: #f4f4f4; padding: 2px 4px; font-family: monospace; font-size: 10px; }}
                pre {{ background-color: #f4f4f4; padding: 8px; border-left: 3px solid #ccc; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 8px; margin-bottom: 16px; }}
                th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: left; vertical-align: top; font-size: 11px; }}
                th {{ background-color: #f8f9fa; font-weight: bold; color: #1f497d; }}
                ul {{ margin-top: 2px; margin-bottom: 2px; padding-left: 15px; }}
                li {{ margin-bottom: 1px; }}
            </style>
        </head>
        <body>
            {html}
        </body>
    </html>
    """
    
    pdf_buffer = BytesIO()
    pisa.CreatePDF(src=styled_html, dest=pdf_buffer)
    return pdf_buffer.getvalue()

def generate_config_markdown(skills_data, hours_data, teams_data, pocs_data, dfo_channels_data):
    """Directly converts CXone JSON dictionaries into Markdown text without using LLM."""
    md = ""

    # Check if there's any meaningful description to display at all
    has_hours_desc = False
    if isinstance(hours_data, list):
        for h in hours_data:
            if isinstance(h, dict):
                d_val = str(h.get('description', '')).strip()
                if d_val and d_val.lower() not in ['none', 'null', 'n/a', '']:
                    has_hours_desc = True
                    break

    # --- HOURS OF OPERATION ---
    md += "## Hours of Operation\n\n"
    if has_hours_desc:
        md += "| Profile Name | Profile ID | Description | Schedule | Holidays |\n"
        md += "|---|---|---|---|---|\n"
    else:
        md += "| Profile Name | Profile ID | Schedule | Holidays |\n"
        md += "|---|---|---|---|\n"
        
    for h in hours_data:
        name = str(h.get('hoursOfOperationProfileName', h.get('profileName', h.get('name', 'N/A')))).replace('|', ' ').strip()
        pid = str(h.get('hoursOfOperationProfileId', h.get('profileId', h.get('id', 'N/A')))).replace('|', ' ').strip()
        desc = str(h.get('description', '')).strip().replace('\n', ' ').replace('|', ' ')
        if desc.lower() in ['none', 'null', 'n/a', '']:
            desc = ''
            
        if "_fetch_error" in h:
            sched = f"<ul><li>{h['_fetch_error']}</li></ul>"
            hols = ""
        else:
            days = h.get('days', [])
            if isinstance(days, dict) and 'day' in days: 
                days = days['day']
            if not isinstance(days, list): days = [days] if days else []
            
            sched_items = []
            for d in days:
                dname = str(d.get('dayName', d.get('day', 'Unknown'))).replace('|', ' ').strip()
                if str(d.get('isClosedAllDay', '')).lower() == 'true':
                    sched_items.append(f"<li>{dname}: Closed All Day</li>")
                else:
                    ot = str(d.get('openTime', '00:00:00')).replace('|', ' ').strip()
                    ct = str(d.get('closeTime', '00:00:00')).replace('|', ' ').strip()
                    sched_items.append(f"<li>{dname}: {ot} to {ct}</li>")
            sched = "<ul>" + "".join(sched_items) + "</ul>" if sched_items else "No schedule defined"
            
            hols_data = h.get('holidays', [])
            if isinstance(hols_data, dict) and 'holiday' in hols_data:
                hols_data = hols_data['holiday']
            if not isinstance(hols_data, list): hols_data = [hols_data] if hols_data else []
            
            hol_items = []
            for hd in hols_data:
                hname = str(hd.get('holidayName', hd.get('name', 'Unknown'))).replace('|', ' ').strip()
                if str(hd.get('isClosedAllDay', '')).lower() == 'true':
                    hol_items.append(f"<li>{hname} (Closed All Day)</li>")
                else:
                    ot = str(hd.get('openTime', '00:00:00')).replace('|', ' ').strip()
                    ct = str(hd.get('closeTime', '00:00:00')).replace('|', ' ').strip()
                    hol_items.append(f"<li>{hname}: {ot} to {ct}</li>")
            hols = "<ul>" + "".join(hol_items) + "</ul>" if hol_items else "No holidays defined"
            
        if has_hours_desc:
            md += f"| {name} | {pid} | {desc} | {sched} | {hols} |\n"
        else:
            md += f"| {name} | {pid} | {sched} | {hols} |\n"
        
    # --- SKILLS ---
    md += "\n## Skills\n\n"
    md += "| Skill Name | Skill ID | Media Type | Campaign | Direction | Dispositions | SLA Threshold | SLA Goal (%) |\n"
    md += "|---|---|---|---|---|---|---|---|\n"
    for s in skills_data:
        name = str(s.get('skillName', 'N/A')).replace('|', ' ').strip()
        sid = str(s.get('skillId', 'N/A')).replace('|', ' ').strip()
        media = str(s.get('mediaTypeName', s.get('mediaType', 'N/A'))).replace('|', ' ').strip()
        camp = str(s.get('campaignName', s.get('campaignId', 'N/A'))).replace('|', ' ').strip()
        
        direction = "Outbound" if str(s.get('isOutbound', 'false')).lower() == 'true' else "Inbound"
        
        disp = "None"
        if str(s.get('requireDisposition', '')).lower() == 'true':
            disp = "Required"
        elif s.get('requireDisposition') is not None:
            disp = "Optional" 
            
        slat = str(s.get('slaThreshold', 'N/A')).replace('|', ' ').strip()
        slag = str(s.get('serviceLevelGoal', 'N/A')).replace('|', ' ').strip()
        
        md += f"| {name} | {sid} | {media} | {camp} | {direction} | {disp} | {slat} | {slag} |\n"
        
    # --- TEAMS ---
    md += "\n## Teams\n\n"
    md += "| Team Name | Team ID | Unavailable Codes | Default Contact Handling |\n"
    md += "|---|---|---|---|\n"
    for t in teams_data:
        name = str(t.get('teamName', 'N/A')).replace('|', ' ').strip()
        tid = str(t.get('teamId', 'N/A')).replace('|', ' ').strip()
        
        u_codes_raw = t.get('unavailableCodes', [])
        
        def flatten_list(lst):
            result = []
            for i in lst:
                if isinstance(i, list):
                    result.extend(flatten_list(i))
                else:
                    result.append(i)
            return result

        u_codes = []
        if isinstance(u_codes_raw, list):
            u_codes = flatten_list(u_codes_raw)
        elif isinstance(u_codes_raw, dict):
            inner = u_codes_raw.get('unavailableCode', u_codes_raw)
            if isinstance(inner, list):
                u_codes = flatten_list(inner)
            else:
                u_codes.append(inner)
        
        u_items = []
        for uc in u_codes:
            if isinstance(uc, dict):
                uname = 'Unknown'
                for k, v in uc.items():
                    clean_k = str(k).lower()
                    if clean_k in ['outstatename', 'unavailablecodename', 'name']:
                        if v: 
                            uname = str(v).replace('|', ' ').strip()
                        break
                u_items.append(f"<li>{uname}</li>")
                
        uc_str = "<ul>" + "".join(u_items) + "</ul>" if u_items else "None"
        
        ch = []
        ch.append(f"<li>Voice: {t.get('maxConcurrentVoice', t.get('voice', 1))}</li>")
        ch.append(f"<li>Chats: {t.get('maxConcurrentChats', t.get('chats', 1))}</li>")
        ch.append(f"<li>SMS: {t.get('maxConcurrentSms', t.get('sms', 1))}</li>")
        ch.append(f"<li>Emails: {t.get('maxConcurrentEmails', t.get('emails', 1))}</li>")
        ch.append(f"<li>Work Items: {t.get('maxConcurrentWorkItems', t.get('workItems', 1))}</li>")
        ch.append(f"<li>Digital: {t.get('maxConcurrentDigital', t.get('digital', 1))}</li>")
        
        req_contact = str(t.get('requireContact', t.get('reqContact', 'False'))).capitalize()
        if req_contact == 'None': req_contact = 'False'
        ch.append(f"<li>Req Contact: {req_contact}</li>")
        
        chan_lock = str(t.get('channelLock', 'False')).capitalize()
        if chan_lock == 'None': chan_lock = 'False'
        ch.append(f"<li>Channel Lock: {chan_lock}</li>")
        
        auto_foc = str(t.get('autoFocus', 'False')).capitalize()
        if auto_foc == 'None': auto_foc = 'False'
        ch.append(f"<li>Auto-Focus: {auto_foc}</li>")
        
        tot_contacts = t.get('maxRoutingThreshold', t.get('totalContactCount', t.get('maxContacts', 1)))
        ch.append(f"<li>Total Contact Count: {tot_contacts}</li>")
        
        ch_str = "<ul>" + "".join(ch) + "</ul>"
        
        md += f"| {name} | {tid} | {uc_str} | {ch_str} |\n"

    # --- POINTS OF CONTACT ---
    md += "\n## Points of Contact\n\n"
    
    # Filter Standard POCs to exclude 'Digital' media types
    valid_standard_pocs = []
    for p in pocs_data:
        if "_fetch_error" in p:
            valid_standard_pocs.append(p)
            continue
        media = p.get('mediaTypeName', p.get('mediaType', 'Unknown'))
        if str(media).lower() == 'digital':
            continue
        valid_standard_pocs.append(p)

    if valid_standard_pocs:
        md += "### Standard Points of Contact\n\n"
        md += "| Media Type | Point of Contact | Name | Script |\n"
        md += "|---|---|---|---|\n"
        for p in valid_standard_pocs:
            if "_fetch_error" in p:
                md += f"| API Error | {p['_fetch_error']} | N/A | N/A |\n"
            else:
                media = str(p.get('mediaTypeName', p.get('mediaType', 'Unknown'))).replace('|', ' ').strip()
                poc = str(p.get('contactAddress', p.get('pointOfContact', str(p.get('pointOfContactId', 'Unknown'))))).replace('|', ' ').strip()
                name = str(p.get('contactDescription', p.get('pointOfContactName', p.get('name', 'Unknown')))).replace('|', ' ').strip()
                script = str(p.get('scriptName', 'None')).replace('|', ' ').strip()
                md += f"| {media} | {poc} | {name} | {script} |\n"

    # NEW DIGITAL CHANNELS LOGIC
    valid_digital_channels = []
    for d in dfo_channels_data:
        if "_fetch_error" in d:
            valid_digital_channels.append(d)
            continue
        
        ctype = 'Unknown'
        if isinstance(d, dict):
            for k, v in d.items():
                clean_k = str(k).lower()
                if clean_k in ['channeltype', 'integrationtype', 'realchanneltype', 'type', 'channel', 'providername']:
                    if v:
                        ctype = str(v)
                        break
        
        # Omit Voice channels
        if ctype.lower() == 'voice':
            continue
            
        if ctype.lower() in ['in-contact-email', 'incontact-email', 'in-contact email']:
            ctype = 'E-Mail'
        else:
            ctype = ctype[0].upper() + ctype[1:] if ctype else 'Unknown'
            
        d['_processed_ctype'] = ctype
        valid_digital_channels.append(d)

    # Only show the section and table if there are valid digital channels to display
    if valid_digital_channels:
        md += "\n### Digital Channels\n\n"
        md += "| Channel Type | Point of Contact | Name | Studio Script |\n"
        md += "|---|---|---|---|\n"
        for d in valid_digital_channels:
            if "_fetch_error" in d:
                md += f"| API Error | {d['_fetch_error']} | N/A | N/A |\n"
            else:
                ctype = str(d.get('_processed_ctype', 'Unknown')).replace('|', ' ').strip()
                poc = str(d.get('idOnExternalPlatform', d.get('channelId', d.get('id', 'Unknown')))).replace('|', ' ').strip()
                name = str(d.get('name', 'Unknown')).replace('|', ' ').strip()
                script = str(d.get('routingScript', d.get('scriptName', d.get('studioScriptName', d.get('acdScript', 'None'))))).replace('|', ' ').strip()
                md += f"| {ctype} | {poc} | {name} | {script} |\n"

    return md

def build_analysis_prompt(current_script, previous_script):
    """Generates the prompt for comparing two script versions."""
    return f"""
    You are an expert NICE CXone contact center architect with deep knowledge of Studio scripting, routing logic, and action configuration.

Compare the two JSON versions of a CXone Studio Script provided below and produce a detailed, evidence-based changelog of all **functional differences** between them. Your audience is contact center developers and Studio script builders, as well as technically familiar IT managers and project leads.

Focus exclusively on:

1. **Studio Actions** — any actions that were added, removed, or modified (e.g., `SNIPPET`, `MENU`, `REQAGENT`, `PLAYLOG`, `ASSIGN`, etc.), including changes to action properties or parameters
2. **Routing branches and connection paths** — changes to how actions connect, branch conditions, success/failure paths, or call flow sequencing
3. **Variables and Snippet logic** — new, removed, or altered variable declarations, assignments, and any code-level changes within Snippet blocks

**Critical constraint:** Ignore all visual/cosmetic metadata — canvas coordinates (X, Y), position offsets, z-order, UI labels unrelated to logic, or any other purely presentational property. If a change has no functional impact on script behavior, exclude it entirely.

## Analysis Requirements

For each identified change, provide a **per-action breakdown** that includes:
- The specific action name or identifier affected
- What changed (property, parameter, connection, logic)
- The before and after values or behavior, cited directly from the JSON (evidence)
- The functional impact on script behavior

Structure the output as a professional changelog using clear headings and bullet points. Do not include a main title — begin directly with the analysis. Each entry should be specific enough that a Studio developer could locate and understand the change without opening the files.

--- PREVIOUS VERSION (JSON) ---
{previous_script}

--- CURRENT VERSION (JSON) ---
{current_script}
    """

def build_as_built_prompt(bu_name, scripts_content, author="Unknown Author"):
    """Generates the prompt for building as-built documentation from a script."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return f"""
    You are an expert NICE CXone (inContact) contact center engineer and technical writer.
    Your task is to generate a professional, handover-ready "As-Built" document for the customer '{bu_name}'.
    
    I will provide you with the raw JSON/XML payload(s) of the Studio Script(s) deployed in their environment.
    
    The document MUST include the following sections in this exact order:
    
    1. Document Control
       - Customer Name: {bu_name}
       - Document Title: CXone As-Built Documentation
       - Version: 1.0
       - Date: {date_str}
       - Author: {author}
       - Revision History Table (with a single entry for Initial Draft)
    2. Executive Summary
       - A concise overview of the delivered solution, its business purpose, and the scope of what was implemented (based on the scripts).
    3. Solution Overview
       - A description of the overall contact center architecture and how the components fit together (high-level call flow, supporting systems).
    4. Integrations
       - List any CRM, database, API, or third-party integrations configured within the scripts, with their purpose and touchpoints. (If none, state that no external integrations were identified).
    5. Environment Configuration
       - You MUST output exactly this placeholder text for this section: [INJECT_ENVIRONMENT_CONFIGURATION_HERE]
    6. Script Analysis
       - For each script provided, provide a high-level summary and list the key Actions (plugins) used, grouped logically (e.g., Media, Routing, Logic).
    7. Glossary
       - Definitions of technical terms used in the document.
       
    Guidelines:
    - Use a formal documentation tone — precise, factual, and neutral.
    - Do not invent configuration details that are not present in the source scripts.
    - Do not include any information regarding Email gateways.
    - The Author name must be exactly as provided. Do not alter it or invent a job title.
    - Format the output cleanly in Markdown. Do NOT use markdown code blocks (```markdown ... ```) to wrap the entire response, just output the raw markdown text.
    - Use Australian English spelling for all generated prose (e.g., categorise, colour, standardise).
    - Do NOT include phrases such as "not provided in JSON, but referenced" or similar conversational disclaimers.
    
    Here are the Studio Script payloads:
    {scripts_content}
    """

def analyze_script_changes_api(prompt, gemini_api_key, max_retries=4):
    client = genai.Client(api_key=gemini_api_key)
    models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-pro']
    
    last_error = "Unknown API Error"
    
    for attempt in range(max_retries):
        for model_name in models_to_try:
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception as e:
                last_error = str(e)
                error_str = last_error.upper()
                
                print(f"Gemini API Attempt Failed ({model_name}): {last_error}")
                
                if "404" in error_str or "NOT_FOUND" in error_str:
                    continue
                if "429" in error_str or "QUOTA" in error_str or "RATE" in error_str:
                    time.sleep(2 ** attempt + 2)
                    break 
                
                continue
                
    raise Exception(f"Gemini API Error: {last_error}")