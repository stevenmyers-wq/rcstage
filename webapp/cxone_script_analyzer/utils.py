# webapp/cxone_script_analyzer/utils.py
import requests
import json
import time
import markdown
from google import genai
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime

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
    
    if "\\" in folder_list: folder_list.remove("\\")
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

def format_cxone_date(date_str):
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y %I:%M:%S %p")
        return dt.strftime("%d-%b-%Y").upper(), dt.strftime("%I:%M:%S %p")
    except ValueError:
        return date_str, "Unknown Time"

def create_pdf(md_text):
    html = markdown.markdown(md_text, extensions=['tables'])
    styled_html = f"""
    <html><head><style>
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
    </style></head><body>{html}</body></html>
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
                if "404" in error_str or "NOT_FOUND" in error_str: continue
                if "429" in error_str or "QUOTA" in error_str or "RATE" in error_str:
                    time.sleep(2 ** attempt + 2)
                    break 
                continue
    raise Exception("Gemini API Error: Max retries exceeded or fatal error.")