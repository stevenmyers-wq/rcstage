import io
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, session
from webapp.auth_utils import require_rc_token
from webapp.usage_tracking import track_usage
from . import utils

ext_num_changer_bp = Blueprint('ext_num_changer_bp', __name__, url_prefix='/api/extension_number_changer')

@ext_num_changer_bp.route('/filters', methods=['GET'])
@require_rc_token
def get_filters():
    """Returns all unique Sites and Extension Types for the UI dropdowns."""
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    try:
        extensions = utils.fetch_all_extensions(token)
        sites = set()
        types = set()
        for ext in extensions:
            ext_type = ext.get('type', '')
            if ext_type:
                types.add(ext_type)
            
            if ext_type == 'Site':
                sites.add(ext.get('name', 'Main Site'))
            else:
                sites.add(ext.get('site', {}).get('name', 'Main Site'))
        
        return jsonify({
            "sites": sorted(list(sites)),
            "types": sorted(list(types))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ext_num_changer_bp.route('/audit', methods=['POST'])
@require_rc_token
@track_usage('Extension Number Changer - Audit')
def generate_audit():
    """Generates the filtered Excel file including Site and Extension Type."""
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    data = request.get_json() or {}
    selected_sites = data.get('sites', [])
    selected_types = data.get('types', [])
    
    try:
        extensions = utils.fetch_all_extensions(token)
        audit_data = []
        
        for ext in extensions:
            ext_type = ext.get('type', '')
            if ext_type == 'Site':
                site_name = ext.get('name', 'Main Site')
            else:
                site_name = ext.get('site', {}).get('name', 'Main Site')
            
            # Apply Filters
            if selected_sites and site_name not in selected_sites:
                continue
            if selected_types and ext_type not in selected_types:
                continue
            
            audit_data.append({
                'Extension ID': ext.get('id', ''),
                'Extension Name': ext.get('name', 'Unknown'),
                'Extension Type': ext_type,
                'Site': site_name,
                'Extension Number': ext.get('extensionNumber', ''),
                'New Extension Number': ''
            })
            
        if not audit_data:
            audit_data = [{
                'Extension ID': 'No Data', 
                'Extension Name': 'No Extensions Found Matching Filters', 
                'Extension Type': '',
                'Site': '',
                'Extension Number': '', 
                'New Extension Number': ''
            }]

        df = pd.DataFrame(audit_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Extension Numbers')
            worksheet = writer.sheets['Extension Numbers']
            for column in worksheet.columns:
                length = max(len(str(cell.value) or "") for cell in column)
                worksheet.column_dimensions[column[0].column_letter].width = min(length + 5, 50)
        
        output.seek(0)
        filename = f"Extension_Number_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output, 
            download_name=filename, 
            as_attachment=True, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ext_num_changer_bp.route('/upload', methods=['POST'])
@require_rc_token
@track_usage('Extension Number Changer - Update')
def upload_updates():
    token = session.get('sm_isolated_token') or session.get('rc_access_token')
    
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    results = []
    
    for index, row in df.iterrows():
        ext_id = str(row.get('Extension ID', '')).replace('.0', '').strip()
        old_num = str(row.get('Extension Number', '')).replace('.0', '').strip()
        new_num = str(row.get('New Extension Number', '')).replace('.0', '').strip()
        name = str(row.get('Extension Name', 'Unknown')).strip()
        
        if not ext_id or ext_id.lower() == 'nan' or not new_num or new_num.lower() == 'nan':
            continue
            
        if old_num == new_num:
            results.append(f"Row {index+2} ({name}): ⏭️ Skipped (New number same as old)")
            continue

        try:
            success, msg = utils.update_extension_number(ext_id, new_num, token)
            if success:
                results.append(f"✅ Ext {name}: Changed from {old_num} to {new_num}")
            else:
                results.append(f"❌ Error on '{name}': {msg}")
        except Exception as e:
            results.append(f"❌ Error on '{name}': {str(e)}")
            
    if not results:
        results.append("No valid changes found to process. Make sure 'New Extension Number' is filled out.")
            
    return jsonify({"logs": results})
