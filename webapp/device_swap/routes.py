import traceback
import pandas as pd
from flask import Blueprint, request, jsonify
from webapp.device_swap.utils import process_bulk_device_swap
from webapp.auth_utils import login_required

# Define the blueprint directly here
device_swap = Blueprint('device_swap', __name__)

@device_swap.route('/api/device_swap/bulk', methods=['POST'])
@login_required
def bulk_device_swap():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
        
    try:
        # Read the Excel file focusing strictly on the Devices tab
        df = pd.read_excel(file, sheet_name='Devices', engine='openpyxl')
        
        # Clean the dataframe to drop empty rows based on your template's columns
        df = df.dropna(subset=['Extension', 'MAC Address'])
        records = df.to_dict('records')
        
        # Execute the swaps
        results = process_bulk_device_swap(records)
        
        return jsonify({'success': True, 'results': results})
    
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': f"Failed to process file: {str(e)}"}), 500
