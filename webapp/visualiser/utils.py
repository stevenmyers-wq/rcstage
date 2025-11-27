# webapp/visualiser/utils.py
import json
import time
from webapp.rc_api import rc_api_call

# Global cache to speed up repeated lookups in the same request
extension_cache = {}

def get_extension_info(ext_id):
    """
    Helper to get full extension info with caching and retry logic.
    """
    if ext_id in extension_cache:
        return extension_cache[ext_id]
    
    # Retry logic for rate limits
    retries = 3
    for i in range(retries):
        try:
            info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']: 
                # Resource not found
                return {'type': 'Unknown', 'name': 'Unknown/Deleted', 'extensionNumber': '???'}
        except Exception as e:
            time.sleep(0.5)
            
    return None

def escape_mermaid(text):
    """Escapes characters for Mermaid nodes."""
    if not text:
        return ""
    # Sanitize quotes
    return json.dumps(text).strip('"').replace('"', "'")

def generate_mermaid_flow(start_ext_id):
    """
    Generates Mermaid graph with Cycle Detection, Error Resilience, AND DETAILS.
    """
    extension_cache.clear()
    
    mermaid_graph_lines = [
        '---',
        'title: Call Flow Diagram',
        '---',
        'graph TD',
        # Styles
        'classDef siteStyle fill:#e8f5e9,stroke:#4caf50,stroke-width:2px,color:#1b5e20,font-size:16px',
        'classDef ivrStyle fill:#e3f2fd,stroke:#2196f3,stroke-width:2px,color:#0d47a1,font-size:16px',
        'classDef queueStyle fill:#fff3e0,stroke:#ff9800,stroke-width:2px,color:#e65100,font-size:16px',
        'classDef userStyle fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px,color:#424242,font-size:16px',
        'classDef membersStyle fill:#e0f2f1,stroke:#009688,stroke-width:1px,color:#004d40,font-size:14px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5'
    ]
    
    ext_to_node_id_map = {}
    node_counter = 0
    visited_paths = set()

    def _trace_recursive(ext_id, parent_node_id=None, link_text="", path_history=None):
        nonlocal node_counter
        
        if path_history is None: path_history = []
            
        # 1. CYCLE PROTECTION
        if ext_id in path_history:
            if ext_id in ext_to_node_id_map and parent_node_id:
                existing_node_id = ext_to_node_id_map[ext_id]
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)} (Loop)" --> {existing_node_id}')
            return

        # 2. ALREADY MAPPED
        if ext_id in ext_to_node_id_map:
            existing_node_id = ext_to_node_id_map[ext_id]
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {existing_node_id}')
            return

        # 3. FETCH & DRAW
        ext_info = get_extension_info(ext_id)
        node_id = f"node{node_counter}"
        node_counter += 1
        ext_to_node_id_map[ext_id] = node_id
        new_history = path_history + [ext_id]

        if not ext_info:
            mermaid_graph_lines.append(f'{node_id}["❓ <b>Unknown</b><br/>ID: {ext_id}"]')
            mermaid_graph_lines.append(f'class {node_id} missingStyle')
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return

        ext_type = ext_info.get('type', 'Unknown')
        ext_name = escape_mermaid(ext_info.get('name', 'N/A'))
        ext_num = ext_info.get('extensionNumber', '')
        
        # Determine if Department is actually a Queue
        if ext_type == 'Department':
            if rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
                ext_type = 'CallQueue'

        icon_map = {'Site': '📍', 'IvrMenu': '🎹', 'CallQueue': '👥', 'User': '👤'}
        icon = icon_map.get(ext_type, '🏢')
        
        mermaid_graph_lines.append(f'{node_id}["{icon} {ext_type}<br/><b>{ext_name}</b><br/>{ext_num}"]')
        
        style_map = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle'}
        mermaid_graph_lines.append(f'class {node_id} {style_map.get(ext_type, "userStyle")}')
        
        if parent_node_id:
            mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')

        # 4. DETAILS: Call Queue Members (Restored Feature)
        if ext_type == 'CallQueue':
            try:
                members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if members_resp and members_resp.get('records'):
                    member_names = []
                    # Limit to top 8 to prevent slowness
                    for m in members_resp['records'][:8]:
                        minfo = get_extension_info(m['id'])
                        if minfo:
                            status = "🟢" if minfo.get('status') == 'Enabled' else "🔴"
                            member_names.append(f"{status} {escape_mermaid(minfo.get('name'))}")
                    
                    if len(members_resp['records']) > 8:
                        member_names.append(f"... and {len(members_resp['records']) - 8} more")
                    
                    if member_names:
                        # Draw a subgraph for members
                        sub_id = f"sub_{node_counter}"
                        sub_node_id = f"node{node_counter}"
                        node_counter += 1
                        
                        list_str = "<br/>".join(member_names)
                        mermaid_graph_lines.append(f'subgraph {sub_id} ["Queue Agents"]')
                        mermaid_graph_lines.append(f'{sub_node_id}["{list_str}"]')
                        mermaid_graph_lines.append('end')
                        mermaid_graph_lines.append(f'class {sub_node_id} membersStyle')
                        mermaid_graph_lines.append(f'{node_id} -.-> {sub_node_id}')
            except Exception as e:
                print(f"Error fetching members: {e}")

        # 5. RECURSE CHILDREN (Flow Logic)
        try:
            if ext_type == 'IvrMenu':
                menu_data = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if menu_data and menu_data.get('actions'):
                    for action in menu_data['actions']:
                        if action.get('extension', {}).get('id'):
                            key = action.get('input', '?')
                            _trace_recursive(action['extension']['id'], node_id, f"Press {key}", new_history)
            else:
                # Check Answering Rules for forwards
                rules_data = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules_data and rules_data.get('records'):
                    for rule in rules_data['records']:
                        if not rule.get('enabled'): continue
                        
                        rule_name = rule.get('name', 'Rule')
                        if rule.get('type') == 'BusinessHours': rule_name = "Biz Hours"
                        if rule.get('type') == 'AfterHours': rule_name = "After Hours"

                        action = rule.get('callHandlingAction')
                        target_id = None
                        
                        if action == 'TransferToExtension':
                            target_id = rule.get('transfer', {}).get('extension', {}).get('id')
                        elif action == 'UnconditionalForwarding':
                            target_id = rule.get('unconditionalForwarding', {}).get('extension', {}).get('id')
                        
                        if target_id:
                            _trace_recursive(target_id, node_id, rule_name, new_history)

        except Exception as e:
            print(f"Error expanding children: {e}")

    _trace_recursive(start_ext_id)
    return "\n".join(mermaid_graph_lines)
