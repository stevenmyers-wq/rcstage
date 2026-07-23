import traceback
import json
import pandas as pd
from flask import Blueprint, request, jsonify
from webapp.device_swap.utils import process_bulk_device_update

device_swap_bp = Blueprint('device_swap_bp', __name__)

@device_swap_bp.route('/api/device_swap/bulk', methods=['POST'])
def bulk_device_swap():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
        
    try:
        # Read the Excel file, looking specifically for the first sheet
        df = pd.read_excel(file, sheet_name=0, engine='openpyxl')
        
        # Clean the dataframe to drop completely empty rows
        df = df.dropna(subset=['Extension', 'Device Type', 'MAC Address'], how='all')
        records = df.to_dict('records')
        
        # Execute the bulk updates securely via Python
        results = process_bulk_device_update(records)
        
        return jsonify({'success': True, 'results': results})
    
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': f"Failed to process file: {str(e)}"}), 500
