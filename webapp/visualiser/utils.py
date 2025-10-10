# webapp/visualiser/utils.py
from webapp.rc_api import rc_api_call
from webapp.auth_utils import get_rc_access_token
import json

# Cache to avoid re-fetching the same extension info during a single trace
extension_cache = {}

def get_extension_info(ext_id):
    """Helper to get full extension info with caching."""
    if ext_id in extension_cache:
        return extension_cache[ext_id]
    info = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}")
    if info:
        extension_cache[ext_id] = info
    return info

def escape_mermaid(text):
    """Escapes characters in a string to be safely used in a Mermaid node."""
    if not text:
        return ""
    # Use JSON dumps to handle quotes, backslashes, and other special characters
    return json.dumps(text).strip('"')

def generate_mermaid_flow(start_ext_id):
    """
    Generates a complete Mermaid.js graph definition string by tracing the call flow.
    """
    mermaid_graph_lines = [
        '---',
        'title: Call Flow Diagram',
        '---',
        'graph LR', # Changed from TD to LR for Left-to-Right orientation
        # FIX: Add theme variables to increase font size for better readability
        '%%{init: { "theme": "base", "themeVariables": { "fontSize": "16px", "lineColor": "#2d3748", "primaryColor": "#f7fafc", "primaryTextColor": "#2d3748", "primaryBorderColor": "#a0aec0" } } }%%',
        'classDef siteStyle fill:#e8f5e9,stroke:#4caf50,stroke-width:2px',
        'classDef ivrStyle fill:#e3f2fd,stroke:#2196f3,stroke-width:2px',
        'classDef queueStyle fill:#fff3e0,stroke:#ff9800,stroke-width:2px',
        'classDef userStyle fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px',
        'classDef vmStyle fill:#f3e5f5,stroke:#9c27b0,stroke-width:2px',
        'classDef membersStyle fill:#e0f2f1,stroke:#009688,stroke-width:1px,color:#004d40',
        'classDef rulesStyle fill:#eeeeee,stroke:#616161,stroke-width:1px,color:#212121'
    ]
    ext_to_node_id_map = {}
    node_counter = 0

    def _trace_recursive(ext_id, parent_node_id=None, link_text=""):
        nonlocal node_counter
        
        try:
            if ext_id in ext_to_node_id_map:
                existing_node_id = ext_to_node_id_map[ext_id]
                if parent_node_id:
                     mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {existing_node_id}')
                return

            ext_info = get_extension_info(ext_id)
            if not ext_info:
                raise ValueError(f"Could not retrieve info for extension ID: {ext_id}")
            
            node_id = f"node{node_counter}"
            node_counter += 1
            ext_to_node_id_map[ext_id] = node_id

            ext_type = ext_info.get('type', 'Unknown')
            
            if ext_type == 'Department':
                if rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}"):
                    ext_type = 'CallQueue'
            
            icon = '📍' if ext_type == 'Site' else '🎹' if ext_type == 'IvrMenu' else '👨‍👩‍👧‍👦' if ext_type == 'CallQueue' else '🏢'
            node_label = f"{icon} {ext_type}: {escape_mermaid(ext_info.get('name'))}<br/><small>Ext: {ext_info.get('extensionNumber')}</small>"
            
            mermaid_graph_lines.append(f'{node_id}("{node_label}")')

            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {node_id}')
            
            style = 'siteStyle' if ext_type == 'Site' else 'ivrStyle' if ext_type == 'IvrMenu' else 'queueStyle' if ext_type == 'CallQueue' else 'userStyle'
            mermaid_graph_lines.append(f'class {node_id} {style}')

            # --- Handle Subgraphs for Members and Rules ---
            if ext_type == 'CallQueue':
                members_resp = rc_api_call(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
                if members_resp and members_resp.get('records'):
                    member_names = [escape_mermaid(get_extension_info(m['id']).get('name', 'N/A')) for m in members_resp['records']]
                    # FIX: Use <br/> for line breaks to create a vertical list
                    member_list_str = "<br/>".join(member_names)
                    subgraph_id = f"subgraph_members_{node_counter}"
                    subgraph_node_id = f"node{node_counter}"
                    node_counter += 1
                    mermaid_graph_lines.append(f'subgraph {subgraph_id} [👨‍👩‍👧‍👦 Queue Members]')
                    mermaid_graph_lines.append(f'{subgraph_node_id}["{member_list_str}"]')
                    mermaid_graph_lines.append('end')
                    mermaid_graph_lines.append(f'class {subgraph_node_id} membersStyle')
                    mermaid_graph_lines.append(f'{node_id} -.-> {subgraph_id}')

            rules_data = rc_api_call(f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule?view=Detailed&showInactive=true")
            if rules_data and rules_data.get('records'):
                inactive_rules = [escape_mermaid(r['name']) for r in rules_data['records'] if not r.get('enabled') and r.get('type') == 'Custom']
                if inactive_rules:
                    # FIX: Use <br/> for line breaks to create a vertical list
                    rule_list_str = "<br/>".join(inactive_rules)
                    subgraph_id = f"subgraph_rules_{node_counter}"
                    subgraph_node_id = f"node{node_counter}"
                    node_counter += 1
                    mermaid_graph_lines.append(f'subgraph {subgraph_id} [⚙️ Inactive Custom Rules]')
                    mermaid_graph_lines.append(f'{subgraph_node_id}["{rule_list_str}"]')
                    mermaid_graph_lines.append('end')
                    mermaid_graph_lines.append(f'class {subgraph_node_id} rulesStyle')
                    mermaid_graph_lines.append(f'{node_id} -.-> {subgraph_id}')

            # --- Handle Main Flow Logic ---
            if ext_type == 'IvrMenu':
                menu_data = rc_api_call(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")
                if menu_data and menu_data.get('actions'):
                    for action in menu_data['actions']:
                        if action.get('extension', {}).get('id'):
                            _trace_recursive(action['extension']['id'], node_id, f"Press {action['input']}")
            elif rules_data and rules_data.get('records'): # For all other types
                for rule in rules_data['records']:
                    if not rule.get('enabled'):
                        continue
                    
                    rule_name = rule.get('name') or ('Business Hours' if rule.get('type') == 'BusinessHours' else 'After Hours')
                    action = rule.get('callHandlingAction')
                    destination_ext_id = None
                    
                    if action == 'TransferToExtension' and rule.get('transfer', {}).get('extension', {}).get('id'):
                        destination_ext_id = rule.get('transfer', {}).get('extension', {}).get('id')
                    elif action == 'UnconditionalForwarding' and rule.get('unconditionalForwarding', {}).get('extension', {}).get('id'):
                         destination_ext_id = rule.get('unconditionalForwarding', {}).get('extension', {}).get('id')
                    elif action == 'TakeMessagesOnly':
                        vm_node_id = f"node{node_counter}"
                        node_counter += 1
                        mermaid_graph_lines.append(f'{vm_node_id}("📧 Voicemail")')
                        mermaid_graph_lines.append(f'class {vm_node_id} vmStyle')
                        mermaid_graph_lines.append(f'{node_id} -- "{escape_mermaid(rule_name)}" --> {vm_node_id}')
                    
                    if destination_ext_id:
                        _trace_recursive(destination_ext_id, node_id, rule_name)

        except Exception as e:
            print(f"Error processing extension {ext_id}: {e}")
            error_node_id = f"node{node_counter}"
            node_counter += 1
            mermaid_graph_lines.append(f'{error_node_id}("❌ Error<br/><small>Could not load ID {ext_id}</small>")')
            if parent_node_id:
                mermaid_graph_lines.append(f'{parent_node_id} -- "{escape_mermaid(link_text)}" --> {error_node_id}')

    extension_cache.clear()
    _trace_recursive(start_ext_id)
    return "\n".join(mermaid_graph_lines)


