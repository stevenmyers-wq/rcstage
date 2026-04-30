# webapp/cxone_script_analyzer/utils.py
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

def get_cxone_token(access_key, secret_key, region):
    auth_url = f"https://{region}.nice-incontact.com/authentication/v1/token/access-key"
    headers = {"Content-Type": "application/json"}
    payload = {"accessKeyId": access_key, "accessKeySecret": secret_key}
    
    response = requests.post(auth_url, headers=headers, json=payload, verify=False)
    response.raise_for_status()
    data = response.json()
    
    token = data.get("access_token")
    base_uri = data.get("resource_server_base_uri") or data.get("server_base_uri") or data.get("domain")
    
    if not base_uri:
        base_uri = f"https://api-{region}.niceincontact.com"
        
    return token, base_uri.rstrip("/")

def fetch_cxone_folders(base_uri, token):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/script-folders"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/json"}
    
    response = requests.get(endpoint, headers=headers, verify=False)
    response.raise_for_status()
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
    headers = {"Authorization": f"bearer {token}", "Accept": "application/json"}
    
    params = {}
    if folder_name and folder_name != "\\":
        params["folder"] = folder_name
        
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    response.raise_for_status()
    return response.json().get("scriptList", [])

def fetch_script_history(base_uri, token, script_path):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/scripts/historyByName"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/json"}
    params = {"scriptPath": script_path}
    
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    response.raise_for_status()
    return response.json().get("versions", [])

def fetch_script_content(base_uri, token, specific_script_id):
    endpoint = f"{base_uri}/incontactapi/services/v34.0/scripts"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/json"}
    params = {"scriptId": specific_script_id}
    
    response = requests.get(endpoint, headers=headers, params=params, verify=False)
    response.raise_for_status()
    data = response.json()
    
    script_data = data
    if "scriptList" in data and isinstance(data["scriptList"], list) and len(data["scriptList"]) > 0:
        script_data = data["scriptList"][0]
    elif "scripts" in data and isinstance(data["scripts"], list) and len(data["scripts"]) > 0:
        script_data = data["scripts"][0]
        
    return json.dumps(script_data, indent=2)

def generate_script_graph(script_json_str):
    """Deeply parses CXone Script Base64 XML/JSON into Cytoscape Nodes and Edges with dynamic edge detection."""
    try:
        data = json.loads(script_json_str)
    except Exception:
        data = {}

    nodes = []
    edges_list = []
    node_ids = set()

    # 1. Locate the hidden payload
    script_payload = None
    is_base64 = False
    for key, val in data.items():
        if key.lower() in ('scriptdata', 'script_data', 'filedata', 'script_content') and isinstance(val, str):
            script_payload = val
            is_base64 = True
            break
    if not script_payload:
        script_payload = script_json_str

    # 2. Decode Base64 and attempt Decompression
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

    # 3. Try to parse as XML
    if decoded.startswith('<'):
        try:
            clean_xml = decoded
            if clean_xml.startswith('<?xml'):
                clean_xml = clean_xml.split('?>', 1)[-1].strip()
            clean_xml = re.sub(r'\sxmlns="[^"]+"', '', clean_xml, count=1)
            root = ET.fromstring(clean_xml)

            # Node Scraper
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

            # Edge Scraper (XML)
            for el in root.iter():
                tag = el.tag.lower()
                if 'action' in tag and 'actions' not in tag and 'targetaction' not in tag:
                    attrs = {k.lower(): v for k, v in el.attrib.items()}
                    act_id = str(attrs.get('actionid') or attrs.get('id') or '')
                    if not act_id: continue

                    for child in el.iter():
                        if child == el: continue
                        c_attrs = {k.lower(): v for k, v in child.attrib.items()}
                        for k, v in c_attrs.items():
                            if k in ['actionid', 'id', 'sourceactionid']: continue
                            if k.endswith('actionid') or k.endswith('targetid') or k in ['target', 'next', 'to', 'destination']:
                                tgt = str(v)
                                if tgt and tgt != act_id:
                                    cond = str(c_attrs.get('condition') or c_attrs.get('label') or c_attrs.get('outcome') or '')
                                    if not cond:
                                        cond = k.replace('actionid', '').replace('targetid', '').capitalize()
                                        if not cond or cond in ['Target', 'Dest']: cond = 'Next'
                                    edges_list.append({"source": act_id, "target": tgt, "label": cond})
        except Exception as e:
            print(f"XML Parse Error: {e}")

    # 4. JSON Parser
    if not nodes and (decoded.startswith('{') or decoded.startswith('[')):
        try:
            parsed_json = json.loads(decoded)
            
            # Step 4a: Extract Nodes
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
                            # Optional: Extract XY positioning
                            x_val = ci_dict.get('x') or ci_dict.get('left')
                            y_val = ci_dict.get('y') or ci_dict.get('top')
                            if x_val is not None and y_val is not None:
                                try: node_obj["position"] = {"x": float(x_val), "y": float(y_val)}
                                except Exception: pass

                            nodes.append(node_obj)
                            node_ids.add(a_id)
                        
                    for v in obj.values():
                        if isinstance(v, (dict, list)): extract_nodes(v)
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, (dict, list)): extract_nodes(item)
                        
            extract_nodes(parsed_json)

            # Step 4b: Extract Edges strictly from the "branches" dictionary section
            def extract_explicit_branches(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        # Hunt for the global "branches" dictionary
                        if str(k).lower() == 'branches' and isinstance(v, dict):
                            for src_id, targets in v.items():
                                if isinstance(targets, list):
                                    for t in targets:
                                        if isinstance(t, dict):
                                            tgt_id = str(t.get('to') or '')
                                            if tgt_id:
                                                e_type = str(t.get('type') or '').lower()
                                                # User requirement: Only label if type is custom or branch
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

            # Step 4c: Fallback check for inline edges in case a different script uses the old format
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

    # 5. Assemble and Deduplicate Edges
    unique_edges = []
    seen_edges = set()
    for e in edges_list:
        if e['source'] == e['target']: continue # Prevent self loops
        ekey = f"{e['source']}->{e['target']}::{e['label']}"
        if ekey not in seen_edges:
            seen_edges.add(ekey)
            
            # CRITICAL: Missing Node Prevention
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

    # Return Graph + Raw Payload
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
    
    styled_html = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Helvetica, Arial, sans-serif; font-size: 13px; line-height: 1.6; color: #333; }}
                h1, h2, h3 {{ color: #1f497d; margin-top: 15px; margin-bottom: 10px; }}
                h3 {{ border-bottom: 2px solid #1f497d; padding-bottom: 5px; }}
                code {{ background-color: #f4f4f4; padding: 2px 4px; font-family: monospace; font-size: 11px; }}
                pre {{ background-color: #f4f4f4; padding: 10px; border-left: 3px solid #ccc; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 10px; margin-bottom: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; vertical-align: top; }}
                th {{ background-color: #f8f9fa; font-weight: bold; color: #1f497d; }}
                ul {{ margin-top: 5px; margin-bottom: 5px; padding-left: 20px; }}
                li {{ margin-bottom: 3px; }}
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

def build_analysis_prompt(current_script, previous_script):
    return f"""You are an expert NICE CXone contact center architect with deep knowledge of Studio scripting, routing logic, and action configuration.

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

def build_as_built_prompt(script_json):
    return f"""You are an expert NICE CXone contact center architect. 
Analyze this CXone Studio Script JSON and generate a comprehensive "As-Built" documentation guide.

Please evaluate the logic and focus on:
1. Primary Purpose: What is the high-level function of this script based on its actions?
2. Key Routing Functions: Identify menus, hours of operation checks, and skill routing branches.
3. Integrations & Complex Logic: Detail any SNIPPETs, API calls (e.g., REST), database queries, or external integrations. Do not make assumptions as to what a snippet may do, rely only on the information available in the snippet
4. Variables: List the key variables identified and their apparent purpose.

Output the result as a professional document using clear headings and bullet points. Do not include a main title, just start directly with the analysis.

--- SCRIPT VERSION (JSON) ---
{script_json}
"""

def analyze_script_changes_api(prompt, gemini_api_key, max_retries=4):
    client = genai.Client(api_key=gemini_api_key)
    models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-pro']
    
    for attempt in range(max_retries):
        for model_name in models_to_try:
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception as e:
                error_str = str(e).upper()
                if "404" in error_str or "NOT_FOUND" in error_str:
                    continue
                if "429" in error_str or "QUOTA" in error_str or "RATE" in error_str:
                    time.sleep(2 ** attempt + 2)
                    break 
                continue
    raise Exception("Gemini API Error: Max retries exceeded or encountered a fatal error.")