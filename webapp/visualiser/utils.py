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
                return {'type': 'Unknown', 'name': 'Deleted/Unknown', 'extensionNumber': '???'}
        except Exception:
            time.sleep(0.5)
    return None

def escape_mermaid(text):
    if not text: return ""
    return json.dumps(text).strip('"').replace('"', "'")

def generate_mermaid_flow(start_ext_id):
    extension_cache.clear()
    
    # Professional, flat styling (No emojis)
    mermaid_graph_lines = [
        '---', 'title: Call Flow Diagram', '---', 'graph TD',
        'classDef siteStyle fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20',
        'classDef ivrStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1',
        'classDef queueStyle fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100',
        'classDef userStyle fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121',
        'classDef externalStyle fill:#fff8e1,stroke:#ffc107,stroke-width:2px,color:#ff6f00',
        'classDef termStyle fill:#fce4ec,stroke:#c2185b,stroke-width:2px,color:#880e4f',
        'classDef infoStyle fill:#ffffff,stroke:#b0bec5,stroke-width:1px,stroke-dasharray: 5 5,color:#37474f,font-size:12px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    ext_to_node_id_map = {}
    node_counter = 0

    def _trace_recursive(ext_id, parent_node_id=None, link_text="", path_history=None):
        nonlocal node_counter
        if path_history is None: path_history = []
        
        # --- 1. Cycle Protection ---
        if ext_id in path_history:
            if ext_id in ext_to_node_id_map and parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)} (Loop)" --> {ext_to_node_id_map[ext_id]}')
            return

        # --- 2. Existing Node Check ---
        if ext_id in ext_to_node_id_map:
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {ext_to_node_id_map[ext_id]}')
            return

        # --- 3. Create Node ---
        # Handle "Special" IDs (External numbers, Voicemail)
        if str(ext_id).startswith("ext_"):
            # External PSTN Number
            phone_num = str(ext_id).replace("ext_", "")
            node_id = f"node{node_counter}"
            node_counter += 1
            ext_to_node_id_map[ext_id] = node_id
            mermaid_graph_lines.append(f'{node_id}["[External Number]<br/><b>{phone_num}</b>"]:::externalStyle')
            if parent_node_id: mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return
            
        if str(ext_id).startswith("vm_"):
            # Voicemail Box
            node_id = f"node{node_counter}"
            node_counter += 1
            ext_to_node_id_map[ext_id] = node_id
            mermaid_graph_lines.append(f'{node_id}["[Voicemail]<br/><b>End of Call</b>"]:::termStyle')
            if parent_node_id: mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return

        # Standard Extension Fetch
        ext_info = get_extension_info(ext_id)
        node_id = f"node{node_counter}"
        node_counter += 1
        ext_to_node_id_map[ext_id] = node_id
        new_history = path_history + [ext_id]

        if not ext_info:
            mermaid_graph_lines.append(f'{node_id}["[Unknown ID]<br/>{ext_id}"]:::missingStyle')
            if parent_node_id: mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return

        ext_type = ext_info.get('type', 'Unknown')
        if ext_type == 'Department':
            if rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"): ext_type = 'CallQueue'

        # Professional Labeling (No Icons)
        type_label = f"[{ext_type}]"
        name_label = escape_mermaid(ext_info.get('name', 'N/A'))
        num_label = ext_info.get('extensionNumber', '')
        
        node_label = f"{type_label}<br/><b>{name_label}</b><br/>{num_label}"
        
        style = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}.get(ext_type, 'userStyle')
        mermaid_graph_lines.append(f'{node_id}["{node_label}"]:::{style}')
        
        if parent_node_id:
            mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')

        # --- 4. Queue Members (Text List) ---
        if ext_type == 'CallQueue':
            try:
                m_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if m_resp and m_resp.get('records'):
                    members = m_resp['records']
                    m_txt = []
                    for m in members[:8]: 
                        mi = get_extension_info(m['id'])
                        if mi: m_txt.append(f"- {escape_mermaid(mi.get('name'))} ({mi.get('extensionNumber')})")
                    
                    if len(members) > 8: m_txt.append(f"... {len(members)-8} more")
                    
                    if m_txt:
                        info_id = f"info_{node_counter}"
                        node_counter += 1
                        mermaid_graph_lines.append(f'{info_id}["<b>Queue Agents:</b><br/>{ "<br/>".join(m_txt) }"]:::infoStyle')
                        mermaid_graph_lines.append(f'{node_id} -.-> {info_id}')
            except: pass

        # --- 5. Answering Rules (Where & When) ---
        if ext_type in ['User', 'CallQueue', 'Site', 'Department', 'ApplicationExtension']:
            try:
                rules_url = f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false"
                rules_resp = rc_api_call(rules_url)
                
                if rules_resp and rules_resp.get('records'):
                    config_details = []

                    for rule in rules_resp['records']:
                        if not rule.get('enabled'): continue

                        # Determine the "When"
                        r_name = rule.get('name', 'Custom Rule')
                        if rule.get('type') == 'BusinessHours': r_name = "Business Hours"
                        if rule.get('type') == 'AfterHours': r_name = "After Hours"
                        
                        action = rule.get('callHandlingAction')
                        
                        # 5a. Internal Transfers
                        if action == 'TransferToExtension':
                            target = rule.get('transfer', {}).get('extension', {})
                            if target.get('id'):
                                _trace_recursive(target['id'], node_id, r_name, new_history)
                        
                        # 5b. Unconditional Forwards
                        elif action == 'UnconditionalForwarding':
                            # Check if it goes to a number (PSTN) or extension
                            fwd_num = rule.get('unconditionalForwarding', {}).get('phoneNumber')
                            target_ext = rule.get('unconditionalForwarding', {}).get('extension', {})
                            
                            if target_ext.get('id'):
                                _trace_recursive(target_ext['id'], node_id, r_name, new_history)
                            elif fwd_num:
                                # Create a synthetic ID for external number
                                synth_id = f"ext_{fwd_num}"
                                _trace_recursive(synth_id, node_id, f"{r_name} (External)", new_history)

                        # 5c. Forward Calls (Find Me/Follow Me)
                        elif action == 'ForwardCalls':
                            # This is complex, usually involves multiple numbers. We check the forwarding rules.
                            fwd_rules = rule.get('forwarding', {}).get('rules', [])
                            found_fwd = False
                            for fwd in fwd_rules:
                                for number in fwd.get('forwardingNumbers', []):
                                    # If it's a mobile/pstn number
                                    if number.get('phoneNumber'):
                                        synth_id = f"ext_{number['phoneNumber']}"
                                        _trace_recursive(synth_id, node_id, f"{r_name} (Fwd)", new_history)
                                        found_fwd = True
                            
                            if not found_fwd:
                                config_details.append(f"{r_name}: Rings Devices")

                        # 5d. Voicemail
                        elif action == 'TakeMessagesOnly':
                            _trace_recursive(f"vm_{ext_id}", node_id, r_name, new_history)

                        # 5e. Other Actions (Announcements, etc)
                        else:
                            detail = action
                            if action == 'PlayAnnouncementOnly': detail = "Play Announcement"
                            config_details.append(f"{r_name}: <b>{detail}</b>")

                    # Attach config box for non-routing rules
                    if config_details:
                        info_id = f"rules_{node_counter}"
                        node_counter += 1
                        mermaid_graph_lines.append(f'{info_id}["<b>Configuration:</b><br/>{ "<br/>".join(config_details) }"]:::infoStyle')
                        mermaid_graph_lines.append(f'{node_id} -.-> {info_id}')
            except Exception as e:
                print(f"Error fetching rules for {ext_id}: {e}")

        # --- 6. IVR Menu Actions ---
        if ext_type == 'IvrMenu':
            try:
                ivr_data = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if ivr_data and ivr_data.get('actions'):
                    for act in ivr_data['actions']:
                        key = act.get('input', '?')
                        label = f"Key {key}"
                        
                        # Handle IVR extensions
                        if act.get('extension', {}).get('id'):
                            _trace_recursive(act['extension']['id'], node_id, label, new_history)
                        # Handle IVR external numbers
                        elif act.get('phoneNumber'):
                            synth_id = f"ext_{act['phoneNumber']}"
                            _trace_recursive(synth_id, node_id, f"{label} (Ext)", new_history)
            except: pass

    _trace_recursive(start_ext_id)
    return "\n".join(mermaid_graph_lines)
