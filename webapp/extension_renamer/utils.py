def prepare_extension_for_update(current_data, first_name, last_name, ext_type):
    """
    Cleans the extension object and applies the new First/Last names.
    """
    # 1. Cleanup: Remove read-only fields that cause errors if sent back in a PUT request
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
    
    clean_first = str(first_name).strip()
    clean_last = str(last_name).strip()

    # Broad check for any "User" type (User, DigitalUser, VirtualUser, etc.)
    if 'User' in ext_type:
        # --- CRITICAL FIX FOR USERS ---
        # The 'name' field is read-only for Users (it is derived from contact info). 
        # If we send the old 'name' back, RC often ignores the contact changes.
        # We must DELETE it to force RC to regenerate the name from the new contact fields.
        if 'name' in data_to_update:
            del data_to_update['name']

        data_to_update['contact']['firstName'] = clean_first
        data_to_update['contact']['lastName'] = clean_last
        
    else:
        # For Non-Users (IVR, Call Queue, Site, etc.):
        # The display name is often stored in the root 'name' field.
        # We combine the inputs to form a single display name.
        full_display_name = f"{clean_first} {clean_last}".strip()
        
        data_to_update['contact']['firstName'] = full_display_name
        data_to_update['contact']['lastName'] = ""
        
        # Explicitly update the root name field for non-users to ensure consistency
        data_to_update['name'] = full_display_name
        
    return data_to_update
