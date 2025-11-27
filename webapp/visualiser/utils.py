# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

extension_cache = {}

def get_extension_info(ext_id):
    if ext_id in extension_cache:
        return extension_cache[ext_id]
    
    retries = 3
    for i in range(retries):
        try:
            info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']: 
                return {'type': 'Unknown', 'name': 'Unknown/Deleted', 'extensionNumber': '???'}
        except Exception:
            time.sleep(0.5)
    return None

def escape_mermaid(text):
    if not text: return ""
    return json.dumps(text).strip('"').replace('"', "'")

def generate_mermaid_flow(start_ext_id):
    extension_cache.clear()
    
    # Updated Styles: Added a specific style for 'Rule Lists' to make them distinct
    mermaid_graph_lines = [
        '---', 'title: Call Flow Diagram', '---', 'graph TD',
        'classDef siteStyle fill:#e8f5e9,stroke:#4caf50,stroke-width:2px,color:#1b5e20',
        'classDef ivrStyle fill:#e3f2fd,stroke:#2196f3,stroke-width:2px,color:#0d47a1',
        'classDef queueStyle fill:#fff3e0,stroke:#ff9800,stroke-width:2px,color:#e65100',
        'classDef userStyle fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px,color:#424242',
        'classDef infoStyle fill:#fff,stroke:#b0bec5,stroke-width:1px,stroke-dasharray: 5 5,color:#546e7a,font-size:12px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    ext_to_node_id_map = {}
    node_counter = 0

    def _trace_recursive(ext_id, parent_node_id=None, link_text="", path_history=None):
        nonlocal node_counter
        if path_history is None: path_history = []
            
        # --- 1. Cycle & Duplicate Check ---
        if ext_id in path_history:
            if ext_id in ext_to_node_id_map and parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)} (Loop)" --> {ext_to_node_id_map[ext_id]}')
            return

        if ext_id in ext_to_node_id_map:
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {ext_to_node_id_map[ext_id]}')
            return

        # --- 2. Create Node ---
        ext_info = get_extension_info(ext_id)
        node_id = f"node{node_counter}"
        node_counter += 1
        ext_to_node_id_map[ext_id] = node_id
        new_history = path_history + [ext_id]

        if not ext_info:
            mermaid_graph_lines.append(f'{node_id}["❓ Unknown ID: {ext_id}"]:::missingStyle')
            if parent_node_id: mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return

        ext_type = ext_info.get('type', 'Unknown')
        # Fix Department vs Queue
        if ext_type == 'Department':
            if rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"): ext_type = 'CallQueue'

        icon = {'Site': '🏢', 'IvrMenu': '🤖', 'CallQueue': '👥', 'User': '👤'}.get(ext_type, '📞')
        label = f"{icon} {ext_type}<br/><b>{escape_mermaid(ext_info.get('name'))}</b><br/>{ext_info.get('extensionNumber', '')}"
        
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(ext_type, 'userStyle')
        mermaid_graph_lines.append(f'{node_id}["{label}"]:::{style}')
        
        if parent_node_id:
            mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')

        # --- 3. Queue Members (Details) ---
        if ext_type == 'CallQueue':
            try:
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    # Just show count if too many, or list if few
                    members = m_resp['records']
                    m_txt = []
                    for m in members[:6]: # Show top 6
                        mi = get_extension_info(m['id'])
                        if mi: m_txt.append(f"- {escape_mermaid(mi.get('name'))}")
                    
                    if len(members) > 6: m_txt.append(f"... +{len(members)-6} more")
                    
                    if m_txt:
                        info_id = f"info_{node_counter}"
                        node_counter += 1
                        mermaid_graph_lines.append(f'{info_id}["👥 <b>Agents:</b><br/>{ "<br/>".join(m_txt) }"]:::infoStyle')
                        mermaid_graph_lines.append(f'{node_id} -.-> {info_id}')
            except: pass

        # --- 4. Answering Rules (The Missing Piece!) ---
        # We now check rules for EVERYONE (Sites, Users, Queues), not just Users.
        # Note: IvrMenus usually don't have answering rules, they have 'actions'.
        if ext_type in ['User', 'CallQueue', 'Site', 'Department', 'ApplicationExtension']:
            try:
                # Fetch ALL rules (Business Hours, After Hours, Custom)
                rules_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false"
                rules_resp = rc_api_call(rules_url)
                
                if rules_resp and rules_resp.get('records'):
                    non_transfer_rules = []

                    for rule in rules_resp['records']:
                        if not rule.get('enabled'): continue

                        r_name = rule.get('name', 'Custom Rule')
                        if rule.get('type') == 'BusinessHours': r_name = "🌞 Business Hours"
                        if rule.get('type') == 'AfterHours': r_name = "🌙 After Hours"
                        
                        action = rule.get('callHandlingAction')
                        target_id = None
                        
                        # Check for transfers
                        if action == 'TransferToExtension':
                            target_id = rule.get('transfer', {}).get('extension', {}).get('id')
                        elif action == 'UnconditionalForwarding':
                            target_id = rule.get('unconditionalForwarding', {}).get('extension', {}).get('id')
                        
                        # If it transfers, DRAW THE LINE
                        if target_id:
                            _trace_recursive(target_id, node_id, r_name, new_history)
                        else:
                            # If it DOESN'T transfer (e.g. Play Announcement, Take Message), list it so user sees it exists
                            detail = action
                            if action == 'TakeMessagesOnly': detail = "Voicemail"
                            elif action == 'PlayAnnouncementOnly': detail = "Announcement"
                            non_transfer_rules.append(f"{r_name}: <b>{detail}</b>")

                    # Show non-transfer rules in a little attached box
                    if non_transfer_rules:
                        info_id = f"rules_{node_counter}"
                        node_counter += 1
                        mermaid_graph_lines.append(f'{info_id}["⚙️ <b>Config:</b><br/>{ "<br/>".join(non_transfer_rules) }"]:::infoStyle')
                        mermaid_graph_lines.append(f'{node_id} -.-> {info_id}')
            except Exception as e:
                print(f"Error fetching rules for {ext_id}: {e}")

        # --- 5. IVR Actions ---
        if ext_type == 'IvrMenu':
            try:
                ivr_data = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr_data and ivr_data.get('actions'):
                    for act in ivr_data['actions']:
                        if act.get('extension', {}).get('id'):
                            key = act.get('input', '?')
                            # Handle special keys
                            label = f"Key {key}" if key not in ['0', '*'] else f"Key {key}"
                            _trace_recursive(act['extension']['id'], node_id, label, new_history)
            except: pass

    _trace_recursive(start_ext_id)
    return "\n".join(mermaid_graph_lines)
