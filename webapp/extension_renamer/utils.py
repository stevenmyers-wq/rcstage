def prepare_extension_for_update(current_data, first_name, last_name, ext_type):
    """
    Cleans the extension object and applies the new First/Last names.
    """
    # 1. Cleanup: Remove read-only fields
    forbidden_fields = [
        'id', 'uri', 'extensionNumber', 'lastModifiedTime', 'creationTime', 
        'account', 'permissions', 'profileImage', 'serviceFeatures', 
        'site', 'status', 'type'
    ]
    
    # Create a copy to avoid modifying the original dictionary reference
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
    
    # Strip whitespace to prevent " " errors
    clean_first = str(first_name).strip()
    clean_last = str(last_name).strip()

    # Broad check for any "User" type (User, DigitalUser, VirtualUser, etc.)
    if 'User' in ext_type:
        # For Users, we strictly enforce separated fields.
        # RingCentral requires a non-empty lastName for Users.
        data_to_update['contact']['firstName'] = clean_first
        data_to_update['contact']['lastName'] = clean_last
    else:
        # For IVR, Call Queue, Site, etc. -> The concept of "Last Name" doesn't strictly exist 
        # in the display. We combine them so the Excel user can use both columns if they want, 
        # or just the First Name column.
        full_display_name = f"{clean_first} {clean_last}".strip()
        
        data_to_update['contact']['firstName'] = full_display_name
        data_to_update['contact']['lastName'] = ""
        
    return data_to_update
