import time
from webapp.rc_api import rc_api_call

# Extension types that represent an actual person (a "user"). These are the
# only extensions whose regional timezone is meaningful to compare against the
# site they belong to.
USER_TYPES = {'User', 'DigitalUser', 'FlexibleUser'}


def fetch_all_extensions(token):
    """Fetches every account extension (all pages)."""
    extensions = []
    page = 1
    while True:
        resp = rc_api_call(
            f"/restapi/v1.0/account/~/extension?perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        extensions.extend(resp['records'])
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)
    return extensions


def _tz_label(tz):
    """Human-friendly timezone label from a RC timezone object."""
    if not tz:
        return ''
    name = tz.get('name', '')
    desc = tz.get('description', '')
    if name and desc:
        return f"{name} ({desc})"
    return desc or name


def build_site_timezone_map(token):
    """Returns {site_id (str): {'name': ..., 'tz_id': ..., 'tz_label': ...}}.

    The account's primary site is keyed under both its real id and the
    well-known 'main-site' alias, using the account-level regional settings as
    a fallback so users on the Main Site can always be resolved.
    """
    site_map = {}

    # Every explicitly-created Site.
    page = 1
    while True:
        resp = rc_api_call(
            f"/restapi/v1.0/account/~/sites?perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        for s in resp['records']:
            sid = str(s.get('id'))
            tz = s.get('regionalSettings', {}).get('timezone')
            # The /sites collection does not always include regionalSettings,
            # so fetch the site detail when the timezone is missing.
            if not tz:
                detail = rc_api_call(
                    f"/restapi/v1.0/account/~/sites/{sid}",
                    token=token, raise_error=False
                )
                if detail:
                    tz = detail.get('regionalSettings', {}).get('timezone')
                time.sleep(0.05)
            site_map[sid] = {
                'name': s.get('name', ''),
                'tz_id': str((tz or {}).get('id', '')),
                'tz_label': _tz_label(tz),
            }
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)

    # Account-level regional settings represent the primary ("Main") site,
    # which is not always returned by the /sites collection.
    account = rc_api_call("/restapi/v1.0/account/~", token=token, raise_error=False)
    if account:
        acc_tz = account.get('regionalSettings', {}).get('timezone')
        main_entry = {
            'name': 'Main Site',
            'tz_id': str((acc_tz or {}).get('id', '')),
            'tz_label': _tz_label(acc_tz),
        }
        site_map.setdefault('main-site', main_entry)

    return site_map


def generate_timezone_audit(token):
    """Builds the per-user timezone-vs-site audit rows."""
    extensions = fetch_all_extensions(token)
    site_map = build_site_timezone_map(token)

    audit_data = []

    for ext in extensions:
        if ext.get('type') not in USER_TYPES:
            continue
        # Skip un-activated / disabled shells with no real assignment.
        ext_id = ext.get('id')
        if not ext_id:
            continue

        detail = rc_api_call(
            f"/restapi/v1.0/account/~/extension/{ext_id}",
            token=token, raise_error=False
        )
        time.sleep(0.05)
        if not detail:
            continue

        user_tz = detail.get('regionalSettings', {}).get('timezone') or {}
        user_tz_id = str(user_tz.get('id', ''))
        user_tz_label = _tz_label(user_tz)

        site_obj = detail.get('site', {}) or {}
        site_id = str(site_obj.get('id', '')) or 'main-site'
        site_info = site_map.get(site_id)
        if site_info is None and site_id != 'main-site':
            site_info = site_map.get('main-site')
        site_info = site_info or {'name': site_obj.get('name', ''), 'tz_id': '', 'tz_label': ''}

        site_name = site_info.get('name') or site_obj.get('name', '') or 'Main Site'
        site_tz_id = site_info.get('tz_id', '')
        site_tz_label = site_info.get('tz_label', '')

        # A match requires a known timezone on both sides.
        if user_tz_id and site_tz_id:
            match = "Yes" if user_tz_id == site_tz_id else "No"
        else:
            match = "Unknown"

        audit_data.append({
            "Site": site_name,
            "Ext Number": detail.get('extensionNumber', ''),
            "Name": detail.get('name', ''),
            "Email": detail.get('contact', {}).get('email', ''),
            "User Timezone": user_tz_label,
            "Site Timezone": site_tz_label,
            "Match (Yes/No)": match,
        })

    if not audit_data:
        audit_data.append({
            "Site": "",
            "Ext Number": "",
            "Name": "No Users Found",
            "Email": "",
            "User Timezone": "",
            "Site Timezone": "",
            "Match (Yes/No)": "",
        })

    return audit_data
