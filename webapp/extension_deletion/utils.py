"""Business logic for the Extension Deletion tool.

Fetches every extension in the account, buckets each one into a friendly,
operator-facing *category* (so unassigned Users and unassigned Limited
extensions are their own categories, exactly as they are provisioned), and
deletes a caller-chosen set of extensions one by one with cooperative stop
support.

Deleting an extension via the RingCentral API is irreversible -- there is no
undo -- so the caller (routes.py + the UI) is responsible for the preview and
the "type DELETE" confirmation gate. This module just does the work.
"""
import time
from webapp.rc_api import rc_api_call
from webapp import task_control

# Statuses that mean "no live user/device is on this extension".
# Mirrors the grouping already used in account_migration/utils.py.
UNASSIGNED_STATUSES = {'Unassigned', 'NotActivated'}


def categorize(ext):
    """Map a raw RingCentral extension record to a friendly deletion category.

    Unassigned Users and unassigned Limited extensions are deliberately kept as
    their own categories (they are provisioned as their own thing), rather than
    folded into "User"/"Limited". Any type we don't have a friendly name for
    falls back to its raw RC type string so nothing is ever silently hidden.
    """
    ext_type = ext.get('type', '') or ''
    status = ext.get('status', '') or ''
    is_unassigned = status in UNASSIGNED_STATUSES

    if ext_type == 'Site':
        return 'Site'
    if ext_type == 'User':
        return 'Unassigned User' if is_unassigned else 'User'
    if ext_type == 'Limited':
        return 'Unassigned Limited Extension' if is_unassigned else 'Limited Extension'
    if ext_type == 'Department':
        return 'Call Queue'
    if ext_type in ('Voicemail', 'MessageOnly'):
        return 'Message-Only'
    if ext_type in ('Announcement', 'AnnouncementOnly'):
        return 'Announcement-Only'
    if ext_type == 'IvrMenu':
        return 'IVR Menu'
    if ext_type == 'PagingOnly':
        return 'Paging Group'
    if ext_type == 'ParkLocation':
        return 'Park Location'
    if ext_type == 'SharedLinesGroup':
        return 'Shared Line Group'
    # Unknown / rarer type: surface it under its own raw label so the operator
    # can still see and target it rather than have it disappear.
    return ext_type or 'Unknown'


def _site_name(ext):
    """Site an extension belongs to. A Site extension *is* its own site."""
    if ext.get('type') == 'Site':
        return ext.get('name', 'Main Site')
    return (ext.get('site') or {}).get('name', 'Main Site')


def fetch_all_extensions(token):
    """Fetch every extension in the account (paginated)."""
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


def build_rows(token, sites=None, categories=None):
    """Return a filtered, UI-friendly list of extensions.

    ``sites`` / ``categories`` are lists of selected filter values; an empty or
    missing list means "no filter on that axis" (include all).
    """
    sites = set(sites or [])
    categories = set(categories or [])

    rows = []
    for ext in fetch_all_extensions(token):
        category = categorize(ext)
        site = _site_name(ext)

        if categories and category not in categories:
            continue
        if sites and site not in sites:
            continue

        rows.append({
            'id': str(ext.get('id', '')),
            'name': ext.get('name', 'Unknown'),
            'extensionNumber': ext.get('extensionNumber', ''),
            'category': category,
            'type': ext.get('type', ''),
            'status': ext.get('status', ''),
            'site': site,
        })

    rows.sort(key=lambda r: (r['site'], r['category'], str(r['extensionNumber'])))
    return rows


def available_filters(token):
    """Distinct sites and categories present in the account, for the dropdowns."""
    sites = set()
    categories = set()
    for ext in fetch_all_extensions(token):
        categories.add(categorize(ext))
        sites.add(_site_name(ext))
    return sorted(sites), sorted(categories)


def delete_extension(ext_id, token):
    """Delete a single extension, retrying politely through 429 rate limits.

    Returns ``(success: bool, message: str)``. Never raises.
    """
    endpoint = f"/restapi/v1.0/account/~/extension/{ext_id}"
    for _attempt in range(4):
        resp = rc_api_call(endpoint, method='DELETE', token=token, return_response=True)
        status_code = getattr(resp, 'status_code', None)

        if status_code == 429:
            retry_after = 10
            try:
                retry_after = int(resp.headers.get('Retry-After', 10))
            except Exception:
                pass
            time.sleep(retry_after + 1)
            continue

        if resp is not None and getattr(resp, 'ok', False):
            return True, 'Deleted'

        # 204 No Content is a success but ``ok`` already covers 2xx. Anything
        # else is a real failure -- surface the RC message.
        try:
            err = resp.json()
            msg = err.get('message') or err.get('errorCode') or str(err)
        except Exception:
            msg = f"HTTP {status_code}"
        return False, msg

    return False, 'Rate limit exceeded after retries'


def count_site_members(extensions, site_id, site_name):
    """Count extensions currently assigned to a site (excluding the site itself).

    RingCentral refuses to delete a site that still has extensions on it, so we
    use this to skip a doomed delete and tell the operator exactly how many
    extensions are in the way. Site names are unique within an account, so
    matching on the resolved site name is reliable; the site extension itself is
    excluded by id.
    """
    count = 0
    for ext in extensions:
        if str(ext.get('id', '')) == str(site_id):
            continue  # the site extension itself, not a member
        if _site_name(ext) == site_name:
            count += 1
    return count


def delete_batch(ids, token, task_id=None):
    """Generator that deletes ``ids`` one by one, yielding progress dicts.

    Sites are processed *after* every non-site extension, so if an operator
    selects a site together with its members, the members are cleared first and
    the (now empty) site deletes cleanly. Before deleting any site we re-check
    the live account: a site that still has assigned extensions is skipped with
    a clear message rather than firing a DELETE that RingCentral would reject.

    Cooperative stop: checks task_control between items so a Stop request skips
    every not-yet-deleted extension. Extensions already deleted cannot be
    recovered.
    """
    total = len(ids)
    yield {'type': 'start', 'total': total}

    # Non-sites first, sites last (so a site's members are gone before we try
    # the site). Order within each group is preserved.
    non_sites = [i for i in ids if i.get('category') != 'Site']
    sites = [i for i in ids if i.get('category') == 'Site']
    plan = non_sites + sites

    deleted = 0
    failed = 0
    skipped = 0
    cancelled = False
    current = 0
    site_snapshot = None  # live extension list, fetched lazily on the first site

    try:
        for item in plan:
            if task_control.is_stopped(task_id):
                cancelled = True
                yield {'type': 'info',
                       'message': f'Stopped by user — {total - current} remaining extension(s) skipped.'}
                break

            current += 1
            ext_id = str(item.get('id', '')).strip()
            name = item.get('name', 'Unknown')
            number = item.get('extensionNumber', '')
            is_site = item.get('category') == 'Site'
            label = f"{name} (Ext {number})" if number else name

            if not ext_id:
                failed += 1
                yield {'type': 'progress', 'current': current, 'total': total, 'id': ext_id,
                       'status': 'failed', 'label': label,
                       'message': f'✖ {label}: missing extension id'}
                continue

            # Pre-flight guard for sites: skip (don't attempt) if still occupied.
            if is_site:
                if site_snapshot is None:
                    # Fetched now — after the non-site deletions above — so the
                    # count reflects members that this batch just removed.
                    site_snapshot = fetch_all_extensions(token)
                members = count_site_members(site_snapshot, ext_id, name)
                if members > 0:
                    skipped += 1
                    yield {'type': 'progress', 'current': current, 'total': total, 'id': ext_id,
                           'status': 'skipped', 'label': label,
                           'message': (f'⏭️ Skipped {label}: site still has {members} assigned '
                                       f'extension(s) — delete or reassign them first.')}
                    continue

            success, msg = delete_extension(ext_id, token)
            if success:
                deleted += 1
                yield {'type': 'progress', 'current': current, 'total': total, 'id': ext_id,
                       'status': 'deleted', 'label': label,
                       'message': f'✅ Deleted {label}'}
            else:
                failed += 1
                yield {'type': 'progress', 'current': current, 'total': total, 'id': ext_id,
                       'status': 'failed', 'label': label,
                       'message': f'❌ {label}: {msg}'}
    finally:
        task_control.clear(task_id)

    yield {'type': 'done', 'deleted': deleted, 'failed': failed, 'skipped': skipped,
           'total': total, 'cancelled': cancelled}
