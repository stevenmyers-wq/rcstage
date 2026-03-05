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
    """Uploads an audio file as a Voicemail greeting to the specified extension."""
    answering_rule_id = 'business-hours-rule'
    
    # RingCentral expects a multipart/form-data request with two parts:
    # 1. 'json': The metadata detailing what kind of greeting this is.
    # 2. 'attachment': The actual audio file.
    files = {
        'json': (
            'request.json', 
            '{"type":"Voicemail", "answeringRuleId":"' + answering_rule_id + '"}', 
            'application/json'
        ),
        'attachment': (file.filename, file.read(), file.content_type or 'audio/mpeg')
    }

    return rc_api_call(
        f'/restapi/v1.0/account/~/extension/{extension_id}/greeting',
        method='POST',
        raise_error=True,
        files=files
    )
