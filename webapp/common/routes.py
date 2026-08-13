import pandas as pd
from flask import Blueprint, jsonify, request

# Shared helpers used across every module that accepts an Excel workbook upload.
common_bp = Blueprint('common', __name__, url_prefix='/api/common')

# Value returned (and understood) by the sheet-selection dropdown when the user
# uploads a CSV, which has no worksheets. Backends treat this as "no sheet".
CSV_SENTINEL = "CSV Format (No Sheets)"


@common_bp.route('/sheets', methods=['POST'])
def list_sheets():
    """List the worksheet names in an uploaded workbook so the front-end can
    offer a 'Target Worksheet' dropdown. Shared by every upload flow via the
    window.SheetPicker helper. Returns a JSON array of sheet names, or the CSV
    sentinel for a .csv upload (which has a single implicit sheet)."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    file = request.files['file']
    filename = (file.filename or '').lower()
    if filename.endswith('.csv'):
        return jsonify([CSV_SENTINEL])

    try:
        xls = pd.ExcelFile(file)
        return jsonify(xls.sheet_names)
    except Exception as e:
        return jsonify({"error": f"Failed to parse sheets: {str(e)}"}), 400
