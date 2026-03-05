import json
from webapp.rc_api import rc_api_call

def get_message_extensions():
    """Fetches Message-Only and Announcement extensions."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        raise Exception("Failed to fetch extensions from RingCentral.")

    valid_types = ['MessageOnly', 'Announcement']
    filtered_exts = [
        {
            "id": ext['id'],
            "name": ext.get('name', 'Unnamed'),
            "extensionNumber": ext.get('extensionNumber', 'N/A'),
            "type": ext['type']
        }
        for ext in response['records'] if ext.get('type') in valid_types
    ]
    return filtered_exts

def upload_greeting_to_extension(extension_id, file):
    """Uploads an audio file as a custom greeting to the specified extension."""
    
    # 1. Fetch extension info to determine if it needs a Voicemail or Announcement greeting
    ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}', method='GET', raise_error=True)
    ext_type = ext_info.get('type')
    
    if ext_type == 'MessageOnly':
        greeting_type = 'Voicemail'
    elif ext_type == 'Announcement':
        greeting_type = 'Announcement'
    else:
        raise Exception(f"Unsupported extension type for this tool: {ext_type}")

    # 2. Fetch the answering rules for this extension to find the active rule ID
    rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=True)
    
    rule_id = None
    if rules and 'records' in rules and len(rules['records']) > 0:
        # MessageOnly and Announcement extensions generally only have one default rule
        rule_id = rules['records'][0]['id']

    # 3. Build the correctly nested JSON metadata payload
    metadata = {
        "type": greeting_type
    }
    
    # Only attach the answering rule if one exists (RingCentral requires this object structure)
    if rule_id:
        metadata["answeringRule"] = {"id": rule_id}

    # 4. Prepare the multipart/form-data files payload
    files = {
        'json': (
            'request.json', 
            json.dumps(metadata), 
            'application/json'
        ),
        'attachment': (
            file.filename, 
            file.read(), 
            file.content_type or 'audio/mpeg'
        )
    }

    # 5. Execute the upload
    return rc_api_call(
        f'/restapi/v1.0/account/~/extension/{extension_id}/greeting',
        method='POST',
        raise_error=True,
        files=files
    )
