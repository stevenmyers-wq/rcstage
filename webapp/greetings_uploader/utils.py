import json
from webapp.rc_api import rc_api_call

def get_message_extensions():
    """Fetches Message-Only (Voicemail) and Announcement extensions."""
    response = rc_api_call('/restapi/v1.0/account/~/extension', params={'perPage': 1000}, raise_error=True)
    if not response or 'records' not in response:
        raise Exception("Failed to fetch extensions from RingCentral.")

    valid_types = ['Voicemail', 'Announcement']
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
    ext_info = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}', method='GET', raise_error=True)
    ext_type = ext_info.get('type')
    
    if ext_type == 'Voicemail':
        greeting_type = 'Voicemail'
    elif ext_type == 'Announcement':
        greeting_type = 'Announcement'
    else:
        raise Exception(f"Unsupported extension type for this tool: {ext_type}")

    rules = rc_api_call(f'/restapi/v1.0/account/~/extension/{extension_id}/answering-rule', method='GET', raise_error=True)
    
    rule_id = None
    if rules and 'records' in rules and len(rules['records']) > 0:
        rule_id = rules['records'][0]['id']

    metadata = {
        "type": greeting_type
    }
    
    if rule_id:
        metadata["answeringRule"] = {"id": rule_id}

    files = {
        'json': ('request.json', json.dumps(metadata), 'application/json'),
        'attachment': (file.filename, file.read(), file.content_type or 'audio/mpeg')
    }

    return rc_api_call(
        f'/restapi/v1.0/account/~/extension/{extension_id}/greeting',
        method='POST',
        raise_error=True,
        files=files
    )

def set_directory_visibility(extension_id, is_hidden):
    """Updates the directory visibility (hidden status) for an extension."""
    payload = {
        "hidden": is_hidden
    }
    return rc_api_call(
        f'/restapi/v1.0/account/~/extension/{extension_id}',
        method='PUT',
        raise_error=True,
        json=payload
    )
