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
    
    # Retry logic for rate limits or temporary network blips
    retries = 3
    for i in range(retries):
        try:
            info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and 'errorCode' not in info:
                extension_cache[ext_id] = info
                return info
            elif info and info.get('errorCode') in ['CMN-102', 'OGE-101']: 
                # Resource not found or permission denied -> Return dummy object
                return {'type': 'Unknown', 'name': 'Unknown/Deleted', 'extensionNumber': '???'}
                
        except Exception as e:
            print(f"Attempt {i+1} failed for {ext_id}: {e}")
            time.sleep(1) # Wait 1 second before retry
            
    return None

def escape_mermaid(text):
    """Escapes characters in a string to be safely used in a Mermaid node."""
    if not text:
        return ""
    # Remove double quotes and sanitize
    clean = json.dumps(text).strip('"').replace('"', "'")
    return clean

def generate_mermaid_flow(start_ext_id):
    """
    Generates a Mermaid.js graph with Cycle Detection and Error Resilience.
    """
    # Clear cache at start of new request
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
        'classDef vmStyle fill:#f3e5f5,stroke:#9c27b0,stroke-width:2px,color:#4a148c,font-size:16px',
        'classDef errorStyle fill:#ffebee,stroke:#d32f2f,stroke-width:2px,color:#b71c1c,font-size:16px',
        'classDef missingStyle fill:#cfd8dc,stroke:#607d8b,stroke-width:2px,stroke-dasharray: 5 5,color:#455a64'
    ]
    
    # Maps Ext ID -> Node ID (e.g., '12345' -> 'node0')
    ext_to_node_id_map = {}
    node_counter = 0
    
    # Track visitation path to detect infinite loops (A -> B -> A)
    visited_paths = set()

    def _trace_recursive(ext_id, parent_node_id=None, link_text="", path_history=None):
        nonlocal node_counter
        
        if path_history is None:
            path_history = []
            
        # 1. CYCLE DETECTION: If we've seen this ID in our current path, stop recursion
        if ext_id in path_history:
            # We found a loop! Link back to existing node and return
            if ext_id in ext_to_node_id_map and parent_node_id:
                existing_node_id = ext_to_node_id_map[ext_id]
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)} (Loop)" --> {existing_node_id}')
            return

        # 2. ALREADY MAPPED: If node exists but isn't a loop, just link to it
        if ext_id in ext_to_node_id_map:
            existing_node_id = ext_to_node_id_map[ext_id]
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {existing_node_id}')
            return

        # 3. FETCH DATA
        ext_info = get_extension_info(ext_id)
        
        node_id = f"node{node_counter}"
        node_counter += 1
        ext_to_node_id_map[ext_id] = node_id
        
        # Add to current history for next recursion
        new_history = path_history + [ext_id]

        if not ext_info:
            # SOFT FAIL: Create a "Missing" node instead of crashing
            mermaid_graph_lines.append(f'{node_id}["❓ <b>Unknown/Deleted</b><br/>ID: {ext_id}"]')
            mermaid_graph_lines.append(f'class {node_id} missingStyle')
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            return

        # 4. DRAW NODE
        ext_type = ext_info.get('type', 'Unknown')
        ext_name = escape_mermaid(ext_info.get('name', 'N/A'))
        ext_num = ext_info.get('extensionNumber', '')
        
        # Check if Department is CallQueue
        if ext_type == 'Department':
            # Quick check if it has members (queues have members)
            if rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
                ext_type = 'CallQueue'

        icon_map = {'Site': '📍', 'IvrMenu': '🎹', 'CallQueue': '👥', 'User': '👤', 'Unknown': '🏢'}
        icon = icon_map.get(ext_type, '🏢')
        
        node_label = f"{icon} {ext_type}<br/><b>{ext_name}</b><br/>{ext_num}"
        mermaid_graph_lines.append(f'{node_id}["{node_label}"]')
        
        style_map = {'Site': 'siteStyle', 'IvrMenu': 'ivrStyle', 'CallQueue': 'queueStyle', 'User': 'userStyle'}
        mermaid_graph_lines.append(f'class {node_id} {style_map.get(ext_type, "userStyle")}')
        
        if parent_node_id:
            mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')

        # 5. RECURSE CHILDREN
        try:
            # IVR MENUS
            if ext_type == 'IvrMenu':
                menu_data = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if menu_data and menu_data.get('actions'):
                    for action in menu_data['actions']:
                        if action.get('extension', {}).get('id'):
                            key = action.get('input', '?')
                            target_id = action['extension']['id']
                            _trace_recursive(target_id, node_id, f"Press {key}", new_history)

            # OTHERS (Rules)
            # Only recurse rules for non-users to save time, or if User has forwarding
            else: 
                rules_data = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=false")
                if rules_data and rules_data.get('records'):
                    for rule in rules_data['records']:
                        if not rule.get('enabled'): continue
                        
                        rule_name = rule.get('name', 'Rule')
                        if rule.get('type') == 'BusinessHours': rule_name = "Biz Hours"
                        if rule.get('type') == 'AfterHours': rule_name = "After Hours"

                        # Forwarding/Transfer Logic
                        action = rule.get('callHandlingAction')
                        target_id = None
                        
                        if action == 'TransferToExtension':
                            target_id = rule.get('transfer', {}).get('extension', {}).get('id')
                        elif action == 'UnconditionalForwarding':
                            target_id = rule.get('unconditionalForwarding', {}).get('extension', {}).get('id')
                        
                        if target_id:
                            _trace_recursive(target_id, node_id, rule_name, new_history)

        except Exception as e:
            print(f"Error expanding node {ext_id}: {e}")

    # Start Trace
    _trace_recursive(start_ext_id)
    return "\n".join(mermaid_graph_lines)
