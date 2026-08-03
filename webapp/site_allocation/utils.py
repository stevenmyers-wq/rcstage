import time
from webapp.rc_api import rc_api_call
from webapp import task_control


# ---------------------------------------------------------------------------
# Directory fetching
# ---------------------------------------------------------------------------

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


def fetch_sites(token):
    """Fetches every Site on the account (all pages).

    Returns a list of {'id': ..., 'name': ...}. The account's primary site is
    always represented as the well-known 'main-site' id with the name
    'Main Site' so it can be selected in the friendly template even though it is
    not always returned by the /sites collection."""
    sites = []
    page = 1
    while True:
        resp = rc_api_call(
            f"/restapi/v1.0/account/~/sites?perPage=1000&page={page}",
            token=token, raise_error=False
        )
        if not resp or 'records' not in resp:
            break
        for s in resp['records']:
            sites.append({'id': s.get('id'), 'name': s.get('name', '')})
        if not resp.get('navigation', {}).get('nextPage'):
            break
        page += 1
        time.sleep(0.05)

    # Guarantee the primary site is always selectable in the friendly view.
    if not any((s.get('id') == 'main-site') for s in sites):
        sites.insert(0, {'id': 'main-site', 'name': 'Main Site'})
    return sites


# ---------------------------------------------------------------------------
# Site-assignable extension types
# ---------------------------------------------------------------------------
# A Site can be assigned to nearly every kind of extension — not just Users.
# This covers park zones, call queues, message-only, announcement-only, virtual
# extensions, IVR menus, paging/shared-line groups and the various user
# sub-types. A `Site` extension is deliberately excluded: it *is* a site and
# cannot be re-allocated to another one. Every row is still validated live
# against the account during preview, so any type the API rejects surfaces
# before anything is written.
SITE_ASSIGNABLE_TYPES = {
    'User', 'DigitalUser', 'VirtualUser', 'FlexibleUser', 'Limited',
    'Department',          # Call Queue
    'IvrMenu',             # IVR menu / auto-receptionist
    'Announcement', 'AnnouncementOnly',   # Announcement-only
    'Voicemail', 'MessageOnly',           # Message-only
    'ParkLocation',        # Park zone / location
    'SharedLinesGroup',    # Shared lines group
    'PagingOnly',          # Paging group
}

# Friendly label shown in the template's Type column so the operator can tell
# at a glance what each extension is. Unknown types fall back to the raw API
# type string.
TYPE_LABELS = {
    'User': 'User',
    'DigitalUser': 'User',
    'VirtualUser': 'Virtual Extension',
    'FlexibleUser': 'User',
    'Limited': 'Limited Extension',
    'Department': 'Call Queue',
    'IvrMenu': 'IVR Menu',
    'Announcement': 'Announcement Only',
    'AnnouncementOnly': 'Announcement Only',
    'Voicemail': 'Message Only',
    'MessageOnly': 'Message Only',
    'ParkLocation': 'Park Location',
    'SharedLinesGroup': 'Shared Lines Group',
    'PagingOnly': 'Paging Group',
}


def _friendly_type(ext):
    """Human-readable extension type for the template Type column."""
    raw = ext.get('type') or ''
    return TYPE_LABELS.get(raw, raw or 'Unknown')


# ---------------------------------------------------------------------------
# Friendly <-> id translation helpers
# ---------------------------------------------------------------------------

def build_site_lookup(sites):
    """Maps a friendly Site name (case-insensitive) -> Site id.

    'Main Site' always resolves to the 'main-site' id."""
    lookup = {}
    for s in sites:
        name = (s.get('name') or '').strip().lower()
        if name:
            lookup[name] = s.get('id')
    # Common alias for the primary site.
    lookup.setdefault('main site', 'main-site')
    return lookup


def _extension_site_name(ext):
    """Friendly Site name for an extension. A Site-type extension is its own
    site; every other extension carries a nested site object."""
    if ext.get('type') == 'Site':
        return ext.get('name', 'Main Site')
    return (ext.get('site') or {}).get('name', 'Main Site')


def _extension_site_id(ext):
    """Current Site id for an extension (defaults to the primary site)."""
    return (ext.get('site') or {}).get('id', 'main-site')


def build_extension_lookup(extensions):
    """Maps an extension number (string) -> extension record."""
    lookup = {}
    for ext in extensions:
        num = ext.get('extensionNumber')
        if num is not None:
            lookup[str(num).strip()] = ext
    return lookup


def build_template_rows(extensions):
    """Builds the Extension | Extension Name | Type | Site rows for the template.

    Every site-assignable extension is listed — users, virtual extensions, park
    zones, call queues, message-only, announcement-only, IVR menus and paging /
    shared-line groups — since a Site can be assigned to any of them. `Site`
    extensions are excluded because a Site cannot be re-allocated to itself.

    Each row is pre-filled with the extension's name, type and current Site so
    the operator only edits the Site values that need to change. The Extension
    Name and Type columns are informational and ignored on upload."""
    rows = []
    for ext in extensions:
        if (ext.get('type') or '') not in SITE_ASSIGNABLE_TYPES:
            continue
        ext_num = ext.get('extensionNumber')
        if not ext_num:
            continue
        rows.append({
            'Extension': str(ext_num),
            'Extension Name': ext.get('name', ''),
            'Type': _friendly_type(ext),
            'Site': _extension_site_name(ext),
        })
    rows.sort(key=lambda r: (len(r['Extension']), r['Extension']))
    return rows


# ---------------------------------------------------------------------------
# Single-extension update
# ---------------------------------------------------------------------------

def update_extension_site(ext_id, site_id, token):
    """Re-allocates a single extension to a Site via the RC API.

    Returns (success: bool, message: str). Handles 429 rate limits with a short
    retry/back-off loop."""
    endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}"
    payload = {"site": {"id": str(site_id)}}

    for _ in range(4):
        resp = rc_api_call(endpoint, method="PUT", json=payload, token=token, return_response=True)
        status_code = getattr(resp, 'status_code', None)

        if status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 10)) if hasattr(resp, 'headers') else 10
            time.sleep(retry_after + 1)
            continue

        if resp and getattr(resp, 'ok', False):
            return True, "Success"

        try:
            err_data = resp.json()
            err_msg = err_data.get('message', str(err_data))
        except Exception:
            err_msg = f"HTTP {status_code}"
        return False, err_msg

    return False, "Rate limit exceeded"


# ---------------------------------------------------------------------------
# Streaming validate / apply batch
# ---------------------------------------------------------------------------

def _clean(value):
    """Normalises a spreadsheet cell to a trimmed string (handles NaN / '1001.0')."""
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text


def process_site_batch(records, token, is_preview=True, task_id=None):
    """Validates (and optionally applies) Extension -> Site re-allocations.

    Yields NDJSON-friendly progress chunks mirroring the Call Queue Manager:
      {"type": "start", ...}
      {"type": "progress", "result": {...}, "is_preview": bool}
      {"type": "done", "is_preview": bool}

    In preview mode every row is validated against the live account directory
    (extension exists, Site name resolves, whether it is actually a change) but
    nothing is written. In apply mode the same validation runs and every genuine
    change is PUT to RingCentral."""
    total = len(records)
    yield {"type": "start", "total": total, "message": "Loading account directory (extensions & sites)…"}

    try:
        extensions = fetch_all_extensions(token)
    except Exception as e:
        yield {"type": "error", "message": f"Failed to load extensions directory: {e}"}
        return
    if not extensions:
        yield {"type": "error", "message": "Could not load account extensions. Token may be invalid or expired."}
        return

    try:
        sites = fetch_sites(token)
    except Exception as e:
        yield {"type": "error", "message": f"Failed to load sites directory: {e}"}
        return

    ext_lookup = build_extension_lookup(extensions)
    site_lookup = build_site_lookup(sites)
    id_to_site_name = {s.get('id'): s.get('name') for s in sites}

    for i, row in enumerate(records):
        # Cooperative stop (apply only -- preview writes nothing). Already-applied
        # rows stand; remaining rows are skipped.
        if not is_preview and task_control.is_stopped(task_id):
            yield {"type": "cancelled", "current": i, "total": total,
                   "message": f"Stopped by user. {i} of {total} row(s) processed; the rest were skipped."}
            task_control.clear(task_id)
            return

        ext_num = _clean(row.get('Extension', ''))
        site_name = _clean(row.get('Site', ''))
        # Name is looked up from the live directory once the extension resolves;
        # it starts blank so invalid rows still render a Name cell.
        ext_name = ''

        def progress(status, message, changes=None):
            return {
                "type": "progress",
                "current": i + 1,
                "total": total,
                "result": {
                    "ext": ext_num or "N/A",
                    "name": ext_name,
                    "site": site_name,
                    "status": status,
                    "message": message,
                    "changes": changes or [],
                },
                "is_preview": is_preview,
            }

        # Blank rows are skipped silently (info).
        if not ext_num and not site_name:
            yield progress("info", "Skipped empty row")
            continue
        if not ext_num:
            yield progress("error", "Missing Extension value")
            continue
        if not site_name:
            yield progress("error", "Missing Site value")
            continue

        ext = ext_lookup.get(ext_num)
        if not ext:
            yield progress("error", f"Extension {ext_num} not found on this account")
            continue
        ext_name = ext.get('name', '')
        if ext.get('type') == 'Site':
            yield progress("error", f"Extension {ext_num} is a Site and cannot be re-allocated")
            continue

        target_site_id = site_lookup.get(site_name.lower())
        if not target_site_id:
            yield progress("error", f"Site '{site_name}' not found on this account")
            continue

        current_site_id = _extension_site_id(ext)
        current_site_name = _extension_site_name(ext)
        target_site_name = id_to_site_name.get(target_site_id, site_name)
        ext_display = ext.get('name', ext_num)

        # No-op: already on the requested site. Reported as "Already Allocated"
        # and deliberately excluded from the update payload (no PUT is sent).
        if current_site_id == target_site_id:
            yield progress("skipped", f"{ext_display}: already allocated to {current_site_name}")
            continue

        change = [{"field": "Site", "from": current_site_name, "to": target_site_name}]

        if is_preview:
            yield progress("success", f"{ext_display}: {current_site_name} → {target_site_name}", change)
            continue

        # Apply mode — perform the update.
        ok, msg = update_extension_site(ext.get('id'), target_site_id, token)
        if ok:
            yield progress("success", f"{ext_display}: allocated to {target_site_name}", change)
        else:
            yield progress("error", f"{ext_display}: failed — {msg}", change)
        time.sleep(0.05)

    task_control.clear(task_id)
    yield {"type": "done", "is_preview": is_preview}
