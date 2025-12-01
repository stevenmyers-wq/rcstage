def prepare_extension_for_update(current_data, new_name, ext_type):
    """
    Cleans the extension object and applies the new name 
    according to RingCentral rules.
    """
    # 1. Cleanup: Remove read-only fields that cause errors if sent back in a PUT request
    forbidden_fields = [
        'id', 'uri', 'extensionNumber', 'lastModifiedTime', 'creationTime', 
        'account', 'permissions', 'profileImage', 'serviceFeatures', 
        'site', 'status', 'type'
    ]
    
    # Create a copy to avoid modifying the original dictionary reference (good practice)
    data_to_update = current_data.copy()
    
    for field in forbidden_fields:
        if field in data_to_update:
            del data_to_update[field]

    # 2. Cleanup Contact Object
    if 'contact' not in data_to_update:
        data_to_update['contact'] = {}
    
    # Remove 'pronouncedName' if present (known to cause CMN-101 errors)
    if 'pronouncedName' in data_to_update['contact']:
        del data_to_update['contact']['pronouncedName']

    # 3. Apply New Name Logic
    if ext_type == 'User':
        # Split name into First/Last for Users
        # Example: "John Smith" -> First: "John", Last: "Smith"
        parts = new_name.strip().split(' ')
        last_name = parts.pop() if len(parts) > 1 else ''
        first_name = ' '.join(parts)
        
        data_to_update['contact']['firstName'] = first_name
        data_to_update['contact']['lastName'] = last_name
    else:
        # For IVR, Call Queue, Site, etc. -> Use firstName as the display name
        data_to_update['contact']['firstName'] = new_name
        # Ensure lastName is empty so it doesn't look like "Support Queue Null"
        data_to_update['contact']['lastName'] = ""
        
    return data_to_update