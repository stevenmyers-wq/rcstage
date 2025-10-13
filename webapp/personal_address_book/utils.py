# webapp/personal_address_book/utils.py
from webapp.rc_api import rc_api_call
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# --- UNCHANGED FUNCTIONS ---
def fetch_all_users():
    """Fetches all enabled user extensions, handling pagination."""
    all_users = []
    page = 1
    while True:
        params = {'type': 'User', 'status': 'Enabled', 'perPage': 1000, 'page': page}
        response_data = rc_api_call('/restapi/v1.0/account/~/extension', params=params)
        if response_data:
            if 'records' in response_data and response_data['records']:
                all_users.extend(response_data['records'])
            if response_data.get('navigation', {}).get('nextPage'):
                page += 1
            else:
                break
        else:
            print("API call to fetch users failed or returned empty. Exiting pagination loop.")
            break
    sites_data = rc_api_call('/restapi/v1.0/account/~/sites')
    site_map = {site['id']: site['name'] for site in sites_data.get('records', [])} if sites_data else {}
    for user in all_users:
        site_info = user.get('site')
        site_id = site_info.get('id') if site_info else None
        if site_id and site_id in site_map:
            user['site']['name'] = site_map[site_id]
        else:
            user['site'] = {'name': 'N/A'}
    return all_users

def get_contact_unique_key(contact):
    """Generates a consistent, unique key for a contact based on its details."""
    first_name = contact.get('firstName', '').strip().lower()
    last_name = contact.get('lastName', '').strip().lower()
    email = contact.get('email', '').strip().lower()
    home_phone = contact.get('homePhone', '').strip()
    business_phone = contact.get('businessPhone', '').strip()
    mobile_phone = contact.get('mobilePhone', '').strip()
    primary_phone = home_phone or business_phone or mobile_phone
    return f"{first_name}|{last_name}|{email}|{primary_phone}"

def fetch_contacts_for_user(user_id):
    """Fetches all personal contacts for a single user, handling pagination."""
    contacts = []
    page = 1
    while True:
        endpoint = f'/restapi/v1.0/account/~/extension/{user_id}/address-book/contact'
        params = {'perPage': 1000, 'page': page}
        try:
            response = rc_api_call(endpoint, params=params)
            if response:
                if 'records' in response and response['records']:
                    contacts.extend(response['records'])
                if not response.get('navigation', {}).get('nextPage'):
                    break
                page += 1
            else:
                print(f"API call for user {user_id} contacts failed or returned empty.")
                break
        except Exception as e:
            print(f"An unexpected error occurred while fetching contacts for user {user_id}: {e}")
            return user_id, []
    return user_id, contacts

def fetch_and_aggregate_contacts(user_ids):
    """Fetches contacts for multiple users SEQUENTIALLY and aggregates them."""
    aggregated_contacts = {}
    for user_id in user_ids:
        _user_id, contacts = fetch_contacts_for_user(user_id)
        for contact in contacts:
            key = get_contact_unique_key(contact)
            if key not in aggregated_contacts:
                aggregated_contacts[key] = {"contactData": contact, "users": []}
            aggregated_contacts[key]["users"].append({"userId": user_id, "contactId": contact['id']})
    return list(aggregated_contacts.values())

def delete_contact_entry(user_id, contact_id):
    """Deletes a single contact for a single user."""
    try:
        endpoint = f'/restapi/v1.0/account/~/extension/{user_id}/address-book/contact/{contact_id}'
        rc_api_call(endpoint, method='DELETE')
        return {"userId": user_id, "contactId": contact_id, "status": "success"}
    except Exception as e:
        return {"userId": user_id, "contactId": contact_id, "status": "error", "message": str(e)}

def delete_selected_contacts(contacts_to_delete):
    """Deletes multiple contacts across multiple users in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(delete_contact_entry, user_info['userId'], user_info['contactId'])
                   for contact_group in contacts_to_delete
                   for user_info in contact_group.get("users", [])]
        for future in as_completed(futures):
            results.append(future.result())
    return results

# --- NEW AND UPDATED FUNCTIONS ---

def bulk_add_contacts(user_ids, contacts_to_add):
    """
    Uses the RingCentral bulk upload endpoint to add contacts to multiple users in one API call.
    Returns a task ID for progress tracking.
    """
    print(f"Preparing bulk upload for {len(user_ids)} users with {len(contacts_to_add)} contacts each.")
    
    # Format the records for the bulk upload API
    records = []
    for user_id in user_ids:
        records.append({
            "extensionId": user_id,
            "contacts": contacts_to_add
        })
    
    payload = {"records": records}
    endpoint = '/restapi/v1.0/account/~/address-book-bulk-upload'
    
    try:
        response = rc_api_call(endpoint, 'POST', payload)
        if response and response.get('id'):
            print(f"Bulk upload task created with ID: {response['id']}")
            return {"task_id": response['id']}
        else:
            print(f"Failed to create bulk upload task. Response: {response}")
            return {"error": "Failed to create bulk upload task.", "details": response}
    except Exception as e:
        print(f"Exception during bulk upload: {e}")
        return {"error": str(e)}

def check_bulk_upload_status(task_id):
    """Checks the status of a given bulk upload task."""
    endpoint = f'/restapi/v1.0/account/~/address-book-bulk-upload/tasks/{task_id}'
    try:
        response = rc_api_call(endpoint)
        return response
    except Exception as e:
        print(f"Error checking task status for {task_id}: {e}")
        return {"error": str(e), "status": "Failed"}

def sync_contacts_for_user(user_id, contacts_from_csv):
    """
    Calculates which contacts to add and delete for a user.
    DOES NOT perform the actions, just returns the lists.
    """
    current_contacts_list = fetch_contacts_for_user(user_id)[1]
    current_contacts_map = {get_contact_unique_key(c): c for c in current_contacts_list}
    csv_contacts_map = {get_contact_unique_key(c): c for c in contacts_from_csv}

    contacts_to_delete = [
        c for key, c in current_contacts_map.items() if key not in csv_contacts_map
    ]
    contacts_to_add = [
        c for key, c in csv_contacts_map.items() if key not in current_contacts_map
    ]
    return contacts_to_add, contacts_to_delete

def process_contact_upload(user_ids, contacts, action):
    """Orchestrates the contact upload process using the bulk endpoint."""
    
    if action == 'add':
        # Use the new bulk add function directly
        return bulk_add_contacts(user_ids, contacts)

    elif action == 'remove':
        # Remove is still an individual operation
        aggregated_contacts = fetch_and_aggregate_contacts(user_ids)
        csv_keys = {get_contact_unique_key(c) for c in contacts}
        to_delete = [c for c in aggregated_contacts if get_contact_unique_key(c['contactData']) in csv_keys]
        delete_results = delete_selected_contacts(to_delete)
        return {"status": "Completed 'remove' operation", "details": delete_results}

    elif action == 'update':
        all_contacts_to_add = {}
        all_contacts_to_delete = []

        # 1. Calculate all changes needed for all users
        for user_id in user_ids:
            to_add, to_delete = sync_contacts_for_user(user_id, contacts)
            
            # Aggregate unique contacts to add
            for contact in to_add:
                key = get_contact_unique_key(contact)
                if key not in all_contacts_to_add:
                    all_contacts_to_add[key] = contact
            
            # Aggregate contacts to delete
            for contact in to_delete:
                # We need to find the specific instances of this contact for deletion
                # This is tricky without a full mapping, we'll delete based on the unique contact data
                 all_contacts_to_delete.append({"contactData": contact, "users": [{"userId": user_id, "contactId": contact['id']}]})

        # 2. Perform all deletions first
        if all_contacts_to_delete:
            print(f"Performing {len(all_contacts_to_delete)} deletions...")
            delete_selected_contacts(all_contacts_to_delete)

        # 3. Perform a single bulk add for all new contacts
        if all_contacts_to_add:
            unique_new_contacts = list(all_contacts_to_add.values())
            print(f"Performing bulk add for {len(unique_new_contacts)} unique new contacts across {len(user_ids)} users.")
            return bulk_add_contacts(user_ids, unique_new_contacts)
        
        return {"status": "Completed sync. No new contacts to add.", "task_id": None}

    return {"error": "Invalid action"}
